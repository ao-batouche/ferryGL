from __future__ import annotations

import json
import subprocess
from datetime import date

import httpx
import pytest

from ferrygl.config import Route, SyncOptions
from ferrygl.github import (
    Activity,
    GitHubClient,
    GitHubError,
    fetch_items,
    has_commit_by,
    latest_activity,
    parse_node,
    references,
    resolve_token,
    searches,
)
from ferrygl.model import ItemRef, Kind, Role


def issue_node(number: int, state: str = "OPEN", reason: str | None = None) -> dict:
    return {
        "__typename": "Issue",
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/acme/api/issues/{number}",
        "state": state,
        "stateReason": reason,
        "body": "",
        "repository": {"nameWithOwner": "acme/api"},
    }


def pr_node(
    number: int,
    body: str = "",
    closes: tuple[int, ...] = (),
    draft: bool = False,
    author: str = "alice",
    committers: tuple[str, ...] | None = None,
) -> dict:
    node = {
        "__typename": "PullRequest",
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/acme/api/pull/{number}",
        "state": "OPEN",
        "isDraft": draft,
        "body": body,
        "author": {"login": author},
        "repository": {"nameWithOwner": "acme/api"},
        "closingIssuesReferences": {
            "nodes": [{"number": n, "repository": {"nameWithOwner": "acme/api"}} for n in closes]
        },
    }
    if committers is not None:
        node["commits"] = {
            "nodes": [
                {"commit": {"authors": {"nodes": [{"user": {"login": c} if c else None}]}}}
                for c in committers
            ]
        }
    return node


# References


def test_references_include_closing_urls_cross_repo_and_local_mentions():
    text = (
        "Fixes #12, builds on acme/web#3, see https://github.com/acme/api/pull/7.\n"
        "Not an HTML entity &#38; and not #12a."
    )

    refs = references("acme/api", text, closing=[ItemRef("acme/api", 12)])

    assert [ref.key for ref in refs] == ["acme/api#12", "acme/api#7", "acme/web#3"]


def test_references_ignore_repo_and_url_numbers_as_local_mentions():
    refs = references("acme/api", "https://github.com/acme/web/issues/5 and acme/web#6")

    assert [ref.key for ref in refs] == ["acme/web#5", "acme/web#6"]


# Searches


def test_searches_cover_every_role_with_scope_and_since():
    route = Route(repos=("acme/api", "other/*"), team="ENG")
    sync = SyncOptions(since=date(2026, 9, 1))

    found = searches(route, "alice", sync)

    scope = "repo:acme/api user:other updated:>=2026-09-01"
    assert [(s.role, s.query, s.issues, s.commits) for s in found] == [
        (Role.ASSIGNEE, f"is:issue assignee:alice {scope}", True, False),
        (Role.AUTHOR, f"is:issue author:alice {scope}", True, False),
        (Role.AUTHOR, f"is:pr author:alice {scope}", False, False),
        (Role.ASSIGNEE, f"is:pr assignee:alice -author:alice {scope}", False, False),
        (Role.REVIEWER, f"is:pr reviewed-by:alice -author:alice {scope}", False, False),
        (Role.REQUESTED, f"is:pr user-review-requested:alice -author:alice {scope}", False, False),
        (Role.COMMITTER, f"is:pr -author:alice {scope}", False, True),
    ]


def test_disabled_roles_are_not_searched():
    route = Route(repos=("acme/api",), team="ENG")
    sync = SyncOptions(
        issues_created=False,
        prs_assigned=False,
        prs_reviewed=False,
        review_requests=False,
        prs_with_commits=False,
    )

    found = searches(route, "alice", sync)

    assert [(s.role, s.issues) for s in found] == [(Role.ASSIGNEE, True), (Role.AUTHOR, False)]


# Parsing search results


def test_parse_issue_and_pull_request_nodes():
    issue = parse_node(issue_node(12, "CLOSED", "COMPLETED"), Role.ASSIGNEE, "alice", True)
    pr = parse_node(
        pr_node(15, "Part of #12", closes=(9,), draft=True), Role.AUTHOR, "alice", False
    )
    theirs = parse_node(pr_node(20, author="bob"), Role.REVIEWER, "alice", False)

    assert (issue.kind, issue.state, issue.state_reason) == (Kind.ISSUE, "CLOSED", "COMPLETED")
    assert issue.roles == {Role.ASSIGNEE}
    assert (pr.kind, pr.is_draft, pr.roles) == (Kind.PR, True, {Role.AUTHOR})
    assert [ref.key for ref in pr.references] == ["acme/api#9", "acme/api#12"]
    assert (theirs.kind, theirs.roles) == (Kind.INVOLVED, {Role.REVIEWER})


def test_pull_request_kind_follows_its_author_case_insensitively():
    assert parse_node(pr_node(15, author="Alice"), Role.ASSIGNEE, "alice", False).kind is Kind.PR


def test_results_of_the_wrong_type_are_ignored():
    assert parse_node(pr_node(15), Role.ASSIGNEE, "alice", issues=True) is None
    assert parse_node(issue_node(12), Role.AUTHOR, "alice", issues=False) is None


def test_commit_authors_are_matched_case_insensitively_and_ghost_authors_skipped():
    assert has_commit_by(pr_node(1, committers=("bob", "Alice")), "alice")
    assert not has_commit_by(pr_node(1, committers=("bob", "")), "alice")
    assert not has_commit_by(pr_node(1), "alice")


class FakeSearch:
    """Returns canned results for each search, matched by a distinctive query prefix."""

    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.calls: list[tuple[str, bool]] = []

    def search(self, query: str, with_commits: bool = False) -> list[dict]:
        self.calls.append((query, with_commits))
        for prefix, nodes in self.results.items():
            if query.startswith(prefix):
                return nodes
        return []


def test_fetch_items_merges_every_role_found_for_an_item():
    client = FakeSearch(
        {
            "is:issue assignee:": [issue_node(12)],
            "is:issue author:": [issue_node(12), issue_node(13)],
            "is:pr reviewed-by:": [pr_node(20, author="bob"), pr_node(22, author="bob")],
            "is:pr user-review-requested:": [pr_node(20, author="bob")],
            "is:pr -author:": [
                pr_node(22, author="bob", committers=("alice",)),
                pr_node(23, author="bob", committers=("carol",)),
            ],
        }
    )
    route = Route(repos=("acme/api",), team="ENG")

    items = fetch_items(client, route, "alice", SyncOptions())

    assert [(i.number, i.kind, sorted(i.roles)) for i in items] == [
        (12, Kind.ISSUE, [Role.ASSIGNEE, Role.AUTHOR]),
        (13, Kind.ISSUE, [Role.AUTHOR]),
        (20, Kind.INVOLVED, [Role.REQUESTED, Role.REVIEWER]),
        (22, Kind.INVOLVED, [Role.COMMITTER, Role.REVIEWER]),
    ]
    assert [with_commits for _, with_commits in client.calls] == [False] * 6 + [True]


# Tokens


def completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_token_from_environment_when_no_account_is_named():
    def run(*args, **kwargs):
        raise AssertionError("gh should not run")

    assert resolve_token(None, env={"GH_TOKEN": " t1 "}, run=run) == "t1"


def test_named_account_asks_the_github_cli_even_when_a_token_is_set():
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return completed(0, "t2\n")

    assert resolve_token("alice", env={"GH_TOKEN": "t1"}, run=run) == "t2"
    assert calls == [["gh", "auth", "token", "--user", "alice"]]


def test_token_errors_explain_what_to_do():
    def missing(*args, **kwargs):
        raise FileNotFoundError

    def failing(*args, **kwargs):
        return completed(1, stderr="no account found for alice")

    with pytest.raises(GitHubError, match="install the GitHub CLI"):
        resolve_token(None, env={}, run=missing)
    with pytest.raises(GitHubError, match="account alice: no account found"):
        resolve_token("alice", env={}, run=failing)


# HTTP client


def test_search_follows_pages_and_asks_for_commits_only_when_needed():
    pages = [
        {"nodes": [issue_node(1)], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}},
        {"nodes": [issue_node(2)], "pageInfo": {"hasNextPage": False, "endCursor": None}},
    ]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(
            (body["variables"]["after"], body["variables"]["first"], "commits" in body["query"])
        )
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data": {"search": pages[(len(seen) - 1) % 2]}})

    client = GitHubClient("secret", http=httpx.Client(transport=httpx.MockTransport(handler)))

    assert [n["number"] for n in client.search("is:issue")] == [1, 2]
    client.search("is:pr", with_commits=True)
    assert seen == [(None, 50, False), ("c1", 50, False), (None, 25, True), ("c1", 25, True)]


def test_latest_activity_reads_recent_updates_in_scope_and_the_rate_limit():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((body["query"].split("(")[0].strip(), body["variables"]))
        nodes = [
            {"url": "https://github.com/acme/api/issues/12", "updatedAt": "2026-10-21T10:02:00Z"},
            {"url": "https://github.com/other/web/pull/7", "updatedAt": "2026-10-21T10:01:00Z"},
            {},  # a result of another type
        ]
        rate = {"limit": 5000, "remaining": 4321, "resetAt": "2026-10-21T11:00:00Z"}
        return httpx.Response(200, json={"data": {"rateLimit": rate, "search": {"nodes": nodes}}})

    client = GitHubClient("t", http=httpx.Client(transport=httpx.MockTransport(handler)))

    activity = latest_activity(client, Route(repos=("acme/api", "other/*"), team="ENG"))

    assert seen == [("query", {"q": "repo:acme/api user:other sort:updated-desc", "first": 20})]
    assert activity == Activity(
        updates=frozenset(
            {
                ("https://github.com/acme/api/issues/12", "2026-10-21T10:02:00Z"),
                ("https://github.com/other/web/pull/7", "2026-10-21T10:01:00Z"),
            }
        ),
        remaining=4321,
        limit=5000,
        reset_at="2026-10-21T11:00:00Z",
    )


def test_rejected_token_is_reported_plainly():
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "Bad"}))
    client = GitHubClient("bad", http=httpx.Client(transport=transport))

    with pytest.raises(GitHubError, match="rejected the token"):
        client.viewer_login()


def test_can_read_reports_missing_repositories_as_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"repository": None}, "errors": [{"message": "Could not resolve"}]},
        )

    client = GitHubClient("t", http=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.can_read("acme/secret") is False
