"""Read your issues and pull requests through GitHub's GraphQL API. Never writes."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

import httpx

from ferrygl._http import HttpError, post_json
from ferrygl.config import Route, SyncOptions
from ferrygl.model import GitHubItem, ItemRef, Kind

GITHUB_GRAPHQL = "https://api.github.com/graphql"
PAGE_SIZE = 50

SEARCH = """
query($q: String!, $first: Int!, $after: String) {
  search(query: $q, type: ISSUE, first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      __typename
      ... on Issue {
        number title url state stateReason body
        repository { nameWithOwner }
      }
      ... on PullRequest {
        number title url state isDraft body
        repository { nameWithOwner }
        closingIssuesReferences(first: 20) { nodes { number repository { nameWithOwner } } }
      }
    }
  }
}
"""

_REPOSITORY = "query($o: String!, $n: String!) { repository(owner: $o, name: $n) { id } }"
_OWNER = "query($o: String!) { repositoryOwner(login: $o) { id } }"

# Searches, keyed by the role they find you in.
ASSIGNED, AUTHORED, REVIEWED, REQUESTED = "assigned", "authored", "reviewed", "requested"

_URL_REF = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")
_REPO_REF = re.compile(r"(?<![\w./-])([\w.-]+/[\w.-]+)#(\d+)\b")
_LOCAL_REF = re.compile(r"(?<![\w/#&])#(\d+)\b")


class GitHubError(Exception):
    """GitHub couldn't be reached, rejected the token, or returned an error."""


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

    def search(self, query: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        after = None
        while True:
            page = self.graphql(SEARCH, {"q": query, "first": PAGE_SIZE, "after": after})["search"]
            nodes.extend(node for node in page["nodes"] if node)
            if not page["pageInfo"]["hasNextPage"]:
                return nodes
            after = page["pageInfo"]["endCursor"]

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


def search_queries(route: Route, login: str, sync: SyncOptions) -> list[tuple[str, str]]:
    """The GitHub searches for one route, as ``(role, query)`` pairs."""
    scope = " ".join(
        f"org:{repo.split('/')[0]}" if repo.endswith("/*") else f"repo:{repo}"
        for repo in route.repos
    )
    since = f" updated:>={sync.since.isoformat()}" if sync.since else ""
    wanted = [
        (sync.issues_assigned, ASSIGNED, f"is:issue assignee:{login}"),
        (sync.prs_authored, AUTHORED, f"is:pr author:{login}"),
        (sync.prs_reviewed, REVIEWED, f"is:pr reviewed-by:{login} -author:{login}"),
        (sync.review_requests, REQUESTED, f"is:pr user-review-requested:{login} -author:{login}"),
    ]
    return [(role, f"{terms} {scope}{since}") for enabled, role, terms in wanted if enabled]


def fetch_items(
    client: GitHubClient, route: Route, login: str, sync: SyncOptions
) -> list[GitHubItem]:
    """Your issues and pull requests in ``route``'s repositories, one item per URL."""
    found: dict[str, GitHubItem] = {}
    for role, query in search_queries(route, login, sync):
        for node in client.search(query):
            item = parse_node(node, role)
            if item is None:
                continue
            previous = found.get(item.key)
            if previous is not None and previous.kind is Kind.REVIEW:
                item = replace(item, reviewed=previous.reviewed or item.reviewed)
            found[item.key] = item
    return sorted(found.values(), key=lambda i: (i.repo.lower(), i.number))


def parse_node(node: dict[str, Any], role: str) -> GitHubItem | None:
    """Turn a search result into an item, or None if it isn't the expected type."""
    typename = node.get("__typename")
    repo = node["repository"]["nameWithOwner"]
    body = node.get("body") or ""
    common = {
        "repo": repo,
        "number": node["number"],
        "title": node["title"],
        "url": node["url"],
        "state": node["state"],
        "body": body,
    }
    if role == ASSIGNED:
        if typename != "Issue":
            return None
        return GitHubItem(kind=Kind.ISSUE, state_reason=node.get("stateReason"), **common)
    if typename != "PullRequest":
        return None
    if role == AUTHORED:
        closing = [
            ItemRef(ref["repository"]["nameWithOwner"], ref["number"])
            for ref in (node.get("closingIssuesReferences") or {}).get("nodes") or []
        ]
        return GitHubItem(
            kind=Kind.PR,
            is_draft=bool(node.get("isDraft")),
            references=references(repo, f"{node['title']}\n{body}", closing),
            **common,
        )
    return GitHubItem(
        kind=Kind.REVIEW,
        is_draft=bool(node.get("isDraft")),
        reviewed=role == REVIEWED,
        **common,
    )


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
