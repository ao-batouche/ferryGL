from __future__ import annotations

import json
import subprocess
from datetime import date

import httpx
import pytest

from ferrygl.config import Route, SyncOptions
from ferrygl.github import (
    ASSIGNED,
    AUTHORED,
    REQUESTED,
    REVIEWED,
    GitHubClient,
    GitHubError,
    fetch_items,
    parse_node,
    references,
    resolve_token,
    search_queries,
)
from ferrygl.model import ItemRef, Kind


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


def pr_node(number: int, body: str = "", closes: tuple[int, ...] = (), draft=False) -> dict:
    return {
        "__typename": "PullRequest",
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/acme/api/pull/{number}",
        "state": "OPEN",
        "isDraft": draft,
        "body": body,
        "repository": {"nameWithOwner": "acme/api"},
        "closingIssuesReferences": {
            "nodes": [{"number": n, "repository": {"nameWithOwner": "acme/api"}} for n in closes]
        },
    }


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


# Search queries


def test_search_queries_cover_each_role_with_scope_and_since():
    route = Route(repos=("acme/api", "other/*"), team="ENG")
    sync = SyncOptions(since=date(2026, 9, 1))

    queries = dict(search_queries(route, "alice", sync))

    scope = "repo:acme/api user:other updated:>=2026-09-01"
    assert queries == {
        ASSIGNED: f"is:issue assignee:alice {scope}",
        AUTHORED: f"is:pr author:alice {scope}",
        REVIEWED: f"is:pr reviewed-by:alice -author:alice {scope}",
        REQUESTED: f"is:pr user-review-requested:alice -author:alice {scope}",
    }


def test_disabled_roles_are_not_searched():
    route = Route(repos=("acme/api",), team="ENG")
    sync = SyncOptions(prs_reviewed=False, review_requests=False)

    assert [role for role, _ in search_queries(route, "alice", sync)] == [ASSIGNED, AUTHORED]


# Parsing search results


def test_parse_issue_and_pull_request_nodes():
    issue = parse_node(issue_node(12, state="CLOSED", reason="COMPLETED"), ASSIGNED)
    pr = parse_node(pr_node(15, body="Part of #12", closes=(9,), draft=True), AUTHORED)
    review = parse_node(pr_node(20), REVIEWED)

    assert (issue.kind, issue.state, issue.state_reason) == (Kind.ISSUE, "CLOSED", "COMPLETED")
    assert (pr.kind, pr.is_draft) == (Kind.PR, True)
    assert [ref.key for ref in pr.references] == ["acme/api#9", "acme/api#12"]
    assert (review.kind, review.reviewed, review.requested) == (Kind.REVIEW, True, False)
    assert review.references == ()
    assert parse_node(pr_node(20), REQUESTED).requested is True


def test_results_of_the_wrong_type_are_ignored():
    assert parse_node(pr_node(15), ASSIGNED) is None
    assert parse_node(issue_node(12), AUTHORED) is None


class FakeSearch:
    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results

    def search(self, query: str) -> list[dict]:
        role = next(role for role, marker in MARKERS.items() if marker in query)
        return self.results.get(role, [])


MARKERS = {
    ASSIGNED: "assignee:",
    AUTHORED: "is:pr author:",
    REVIEWED: "reviewed-by:",
    REQUESTED: "user-review-requested:",
}


def test_fetch_items_merges_what_each_review_search_found():
    client = FakeSearch(
        {REVIEWED: [pr_node(20), pr_node(22)], REQUESTED: [pr_node(20), pr_node(21)]}
    )
    route = Route(repos=("acme/api",), team="ENG")

    items = fetch_items(client, route, "alice", SyncOptions())

    assert [(i.number, i.kind, i.reviewed, i.requested) for i in items] == [
        (20, Kind.REVIEW, True, True),  # reviewed, then asked again
        (21, Kind.REVIEW, False, True),
        (22, Kind.REVIEW, True, False),
    ]


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


def test_search_follows_pages():
    pages = [
        {"nodes": [issue_node(1)], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}},
        {"nodes": [issue_node(2)], "pageInfo": {"hasNextPage": False, "endCursor": None}},
    ]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["variables"]["after"])
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data": {"search": pages[len(seen) - 1]}})

    client = GitHubClient("secret", http=httpx.Client(transport=httpx.MockTransport(handler)))

    assert [n["number"] for n in client.search("is:issue")] == [1, 2]
    assert seen == [None, "c1"]


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
