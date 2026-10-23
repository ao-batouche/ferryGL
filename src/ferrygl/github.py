"""Read your issues and pull requests through GitHub's GraphQL API. Never writes."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx

from ferrygl._http import HttpError, post_json
from ferrygl.config import Route, SyncOptions
from ferrygl.model import GitHubItem, ItemRef, Kind, Role

GITHUB_GRAPHQL = "https://api.github.com/graphql"
PAGE_SIZE = 50

_ISSUE_FIELDS = "number title url state stateReason body repository { nameWithOwner }"
_PR_FIELDS = """
  number title url state isDraft body
  repository { nameWithOwner }
  author { login }
  closingIssuesReferences(first: 20) { nodes { number repository { nameWithOwner } } }
"""
_COMMIT_AUTHORS = """
  commits(first: 100) { nodes { commit { authors(first: 5) { nodes { user { login } } } } } }
"""


def _search_query(pr_extra: str = "") -> str:
    return f"""
query($q: String!, $first: Int!, $after: String) {{
  search(query: $q, type: ISSUE, first: $first, after: $after) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{
      __typename
      ... on Issue {{ {_ISSUE_FIELDS} }}
      ... on PullRequest {{ {_PR_FIELDS} {pr_extra} }}
    }}
  }}
}}
"""


SEARCH = _search_query()
# Pull requests with the authors of their first 100 commits. Smaller pages keep each
# request within GitHub's limits.
SEARCH_WITH_COMMITS = _search_query(_COMMIT_AUTHORS)
COMMITS_PAGE_SIZE = 25

_REPOSITORY = "query($o: String!, $n: String!) { repository(owner: $o, name: $n) { id } }"
_OWNER = "query($o: String!) { repositoryOwner(login: $o) { id } }"

# The most recently updated results of a search, for `watch`, with the token's rate limit.
_LATEST = """
query($q: String!, $first: Int!) {
  rateLimit { limit remaining resetAt }
  search(query: $q, type: ISSUE, first: $first) {
    nodes {
      ... on Issue { url updatedAt }
      ... on PullRequest { url updatedAt }
    }
  }
}
"""
# More than one, so that an update GitHub's search indexes late still shows up among them.
LATEST_SIZE = 20

_URL_REF = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
_REPO_REF = re.compile(r"(?<![\w./-])([\w.-]+/[\w.-]+)#(\d+)\b")
_LOCAL_REF = re.compile(r"(?<![\w/#&])#(\d+)\b")


class GitHubError(Exception):
    """GitHub couldn't be reached, rejected the token, or returned an error."""


@dataclass(frozen=True)
class Activity:
    """The latest updates in some repositories, and what's left of the token's rate limit."""

    updates: frozenset[tuple[str, str]]  # (URL, updatedAt) of the most recently updated items
    remaining: int  # rate-limit points left until ``reset_at``
    limit: int  # points per hour
    reset_at: str  # ISO timestamp


Runner = Callable[..., subprocess.CompletedProcess[str]]


def resolve_token(
    user: str | None, env: Mapping[str, str] = os.environ, run: Runner = subprocess.run
) -> str:
    """Find a GitHub token.

    With ``user``, ask the GitHub CLI for that account's token. Without it, use
    ``GH_TOKEN`` or ``GITHUB_TOKEN``, then the CLI's active account.
    """
    if user is None:
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            if token := env.get(name, "").strip():
                return token
    args = ["gh", "auth", "token", *(["--user", user] if user else [])]
    try:
        result = run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise GitHubError(
            "no GitHub token: set GH_TOKEN, or install the GitHub CLI and run `gh auth login`"
        ) from None
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        who = f"account {user}" if user else "the active account"
        detail = result.stderr.strip() or "no token returned"
        raise GitHubError(f"`gh auth token` failed for {who}: {detail}")
    return token


class GitHubClient:
    def __init__(
        self,
        token: str,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(timeout=30)
        self._headers = {"Authorization": f"Bearer {token}", "User-Agent": "ferryGL"}
        self._sleep = sleep

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body = self._request(query, variables or {})
        if errors := body.get("errors"):
            raise GitHubError("; ".join(e.get("message", "unknown error") for e in errors))
        return body["data"]

    def viewer_login(self) -> str:
        return self.graphql("query { viewer { login } }")["viewer"]["login"]

    def can_read(self, repo: str) -> bool:
        """Whether the token can see ``owner/name``, or the owner of ``owner/*``."""
        owner, name = repo.split("/", 1)
        if name == "*":
            body = self._request(_OWNER, {"o": owner})
            return bool((body.get("data") or {}).get("repositoryOwner"))
        body = self._request(_REPOSITORY, {"o": owner, "n": name})
        return bool((body.get("data") or {}).get("repository"))

    def search(self, query: str, with_commits: bool = False) -> list[dict[str, Any]]:
        """All results of a search. ``with_commits`` adds each pull request's commit authors."""
        text, size = (
            (SEARCH_WITH_COMMITS, COMMITS_PAGE_SIZE) if with_commits else (SEARCH, PAGE_SIZE)
        )
        nodes: list[dict[str, Any]] = []
        after = None
        while True:
            page = self.graphql(text, {"q": query, "first": size, "after": after})["search"]
            nodes.extend(node for node in page["nodes"] if node)
            if not page["pageInfo"]["hasNextPage"]:
                return nodes
            after = page["pageInfo"]["endCursor"]

    def activity(self, query: str) -> Activity:
        """The most recently updated results of a search, by anyone, and the rate limit."""
        data = self.graphql(_LATEST, {"q": f"{query} sort:updated-desc", "first": LATEST_SIZE})
        rate = data["rateLimit"]
        return Activity(
            updates=frozenset((n["url"], n["updatedAt"]) for n in data["search"]["nodes"] if n),
            remaining=rate["remaining"],
            limit=rate["limit"],
            reset_at=rate["resetAt"],
        )

    def _request(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        try:
            return post_json(self._http, GITHUB_GRAPHQL, payload, self._headers, sleep=self._sleep)
        except HttpError as exc:
            if exc.status == 401:
                raise GitHubError(
                    "GitHub rejected the token (HTTP 401); check it with `gh auth status`"
                ) from None
            raise GitHubError(str(exc)) from None


@dataclass(frozen=True)
class Search:
    """One GitHub search and the role it finds you in."""

    role: Role
    query: str
    issues: bool  # results are issues; otherwise pull requests
    commits: bool = False  # keep only pull requests with a commit you wrote


def scope(route: Route) -> str:
    """Search qualifiers for the route's repositories."""
    # "user:" matches the repositories of an organization as well as a user.
    return " ".join(
        f"user:{repo.split('/')[0]}" if repo.endswith("/*") else f"repo:{repo}"
        for repo in route.repos
    )


def latest_activity(client: GitHubClient, route: Route) -> Activity:
    """The latest updates to issues and pull requests in the route's repositories."""
    return client.activity(scope(route))


def searches(route: Route, login: str, sync: SyncOptions) -> list[Search]:
    """The GitHub searches for one route."""
    since = f" updated:>={sync.since.isoformat()}" if sync.since else ""
    others = f"-author:{login}"
    wanted = [
        (sync.issues_assigned, Search(Role.ASSIGNEE, f"is:issue assignee:{login}", True)),
        (sync.issues_created, Search(Role.AUTHOR, f"is:issue author:{login}", True)),
        (sync.prs_created, Search(Role.AUTHOR, f"is:pr author:{login}", False)),
        (sync.prs_assigned, Search(Role.ASSIGNEE, f"is:pr assignee:{login} {others}", False)),
        (sync.prs_reviewed, Search(Role.REVIEWER, f"is:pr reviewed-by:{login} {others}", False)),
        (
            sync.review_requests,
            Search(Role.REQUESTED, f"is:pr user-review-requested:{login} {others}", False),
        ),
        # GitHub can't search by commit author within pull requests, so this scans every
        # pull request in scope and checks who wrote its commits.
        (sync.prs_with_commits, Search(Role.COMMITTER, f"is:pr {others}", False, commits=True)),
    ]
    return [
        replace(search, query=f"{search.query} {scope(route)}{since}")
        for enabled, search in wanted
        if enabled
    ]


def fetch_items(
    client: GitHubClient, route: Route, login: str, sync: SyncOptions
) -> list[GitHubItem]:
    """Your issues and pull requests in ``route``'s repositories, one item per URL, with
    every role you have on each."""
    found: dict[str, GitHubItem] = {}
    for search in searches(route, login, sync):
        for node in client.search(search.query, with_commits=search.commits):
            if search.commits and not has_commit_by(node, login):
                continue
            item = parse_node(node, search.role, login, issues=search.issues)
            if item is None:
                continue
            previous = found.get(item.key)
            if previous is not None:
                item = replace(previous, roles=previous.roles | item.roles)
            found[item.key] = item
    return sorted(found.values(), key=lambda i: (i.repo.lower(), i.number))


def parse_node(node: dict[str, Any], role: Role, login: str, issues: bool) -> GitHubItem | None:
    """Turn a search result into an item, or None if it isn't the expected type."""
    typename = node.get("__typename")
    if typename != ("Issue" if issues else "PullRequest"):
        return None
    repo = node["repository"]["nameWithOwner"]
    body = node.get("body") or ""
    common = {
        "repo": repo,
        "number": node["number"],
        "title": node["title"],
        "url": node["url"],
        "state": node["state"],
        "body": body,
        "roles": frozenset({role}),
    }
    if issues:
        return GitHubItem(kind=Kind.ISSUE, state_reason=node.get("stateReason"), **common)
    author = (node.get("author") or {}).get("login") or ""
    closing = [
        ItemRef(ref["repository"]["nameWithOwner"], ref["number"])
        for ref in (node.get("closingIssuesReferences") or {}).get("nodes") or []
    ]
    return GitHubItem(
        kind=Kind.PR if author.lower() == login.lower() else Kind.INVOLVED,
        is_draft=bool(node.get("isDraft")),
        references=references(repo, f"{node['title']}\n{body}", closing),
        **common,
    )


def has_commit_by(node: dict[str, Any], login: str) -> bool:
    """Whether ``login`` wrote, or co-wrote, one of the pull request's first 100 commits."""
    for commit in (node.get("commits") or {}).get("nodes") or []:
        for author in commit["commit"]["authors"]["nodes"]:
            if ((author.get("user") or {}).get("login") or "").lower() == login.lower():
                return True
    return False


def references(repo: str, text: str, closing: Iterable[ItemRef] = ()) -> tuple[ItemRef, ...]:
    """Issues a pull request refers to: closing references, then mentions in ``text``.

    Recognizes ``#12``, ``owner/name#12`` and full GitHub issue or pull request URLs.
    """
    found: dict[str, ItemRef] = {}

    def add(ref: ItemRef) -> None:
        found.setdefault(ref.key, ref)

    for ref in closing:
        add(ref)
    for match in _URL_REF.finditer(text):
        add(ItemRef(match[1], int(match[2])))
    text = _URL_REF.sub(" ", text)
    for match in _REPO_REF.finditer(text):
        add(ItemRef(match[1], int(match[2])))
    text = _REPO_REF.sub(" ", text)
    for match in _LOCAL_REF.finditer(text):
        add(ItemRef(repo, int(match[1])))
    return tuple(found.values())
