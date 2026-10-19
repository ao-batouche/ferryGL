"""Count the GitHub requests and rate-limit points of one route, for the white paper.

ferryGL's own ``GitHubClient`` runs against a mock of GitHub's GraphQL endpoint
(``httpx.MockTransport``) that pages through a synthetic repository the way GitHub does,
including its limit of 1,000 results per search. The repository holds a fixed set of the
user's own items and a growing number of other people's pull requests, which only the
commit scan (``prs_with_commits``) reads in full.

    uv run python docs/paper/evaluation/costs.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import httpx
from world import ME, FakeGitHub, GitHubWorld, Issue, PullRequest

from ferrygl import github
from ferrygl.config import Route, SyncOptions
from ferrygl.github import GitHubClient, fetch_items

SEARCH_LIMIT = 1000  # GitHub returns at most 1,000 results for a search
COMMITS_PER_PR = 100
assert "closingIssuesReferences(first: 20)" in github.SEARCH
assert f"commits(first: {COMMITS_PER_PR})" in github.SEARCH_WITH_COMMITS
ROUTE = Route(("acme/api",), "ENG")


def point_cost(with_commits: bool, page_size: int) -> int:
    """GitHub's documented cost of one search page: the requests needed to fill every
    connection, assuming each reaches its ``first`` limit, divided by 100 and rounded,
    and at least 1."""
    requests = 1 + page_size  # the search, and each result's closingIssuesReferences
    if with_commits:
        # Each pull request's commits(first: 100), and each commit's authors(first: 5).
        requests += page_size + page_size * COMMITS_PER_PR
    return max(1, round(requests / 100))


def world(others: int) -> GitHubWorld:
    """The user's own items, and ``others`` pull requests by other people, of which some
    request the user's review, assign them, were reviewed by them, or carry their commits."""
    w = GitHubWorld(("acme/api",))
    for n in range(12):
        author = ME if n % 3 == 0 else "bob"
        w.issues.append(Issue("acme/api", w.number("acme/api"), f"Issue {n}", author, {ME}))
    for n in range(8):
        pr = PullRequest("acme/api", w.number("acme/api"), f"PR {n}", ME, commit_authors=[ME])
        w.prs.append(pr)
    for n in range(others):
        pr = PullRequest("acme/api", w.number("acme/api"), f"PR {n}", "bob")
        pr.commit_authors = ["bob"] * 3 + ([ME] if n % 20 == 0 else [])
        if n % 10 == 1:
            pr.requested.add(ME)
        if n % 10 == 2:
            pr.reviewed_by.add(ME)
        if n % 20 == 3:
            pr.assignees.add(ME)
        w.prs.append(pr)
    return w


def measure(others: int, commit_scan: bool) -> dict[str, int]:
    fake = FakeGitHub(world(others), page_limit=SEARCH_LIMIT)
    counts = {"requests": 0, "points": 0, "mutations": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        counts["requests"] += 1
        counts["mutations"] += query.lstrip().startswith("mutation")
        if "search(" not in query:
            counts["points"] += 1
            return httpx.Response(200, json={"data": {"viewer": {"login": ME}}})
        first, start = variables["first"], int(variables["after"] or 0)
        nodes = fake.search(variables["q"], with_commits="commits(" in query)
        page = nodes[start : start + first]
        counts["points"] += point_cost("commits(" in query, first)
        info = {"hasNextPage": start + first < len(nodes), "endCursor": str(start + first)}
        return httpx.Response(200, json={"data": {"search": {"pageInfo": info, "nodes": page}}})

    client = GitHubClient("token", http=httpx.Client(transport=httpx.MockTransport(handle)))
    sync = SyncOptions(prs_with_commits=commit_scan)
    items = fetch_items(client, ROUTE, client.viewer_login(), sync)
    return {**counts, "items": len(items)}


def pages(results: int, size: int) -> int:
    """Requests for one search: at least one, and none past the 1,000th result."""
    return max(1, math.ceil(min(results, SEARCH_LIMIT) / size))


def model_requests(others: int, commit_scan: bool) -> int:
    """Requests per route, from the page sizes in ferrygl.github: one for the login, then
    one search per role, paged."""
    found = {
        "issues_assigned": 12,
        "issues_created": 4,
        "prs_created": 8,
        "prs_assigned": others // 20 + (others % 20 > 3),
        "prs_reviewed": others // 10 + (others % 10 > 2),
        "review_requests": others // 10 + (others % 10 > 1),
    }
    searches = sum(pages(n, github.PAGE_SIZE) for n in found.values())
    scan = pages(others, github.COMMITS_PAGE_SIZE) if commit_scan else 0
    return 1 + searches + scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "data")
    args = parser.parse_args(argv)
    rows = []
    for others in [*range(0, 200, 25), *range(200, 1601, 100)]:
        row: dict[str, int] = {"others": others}
        for scan in (True, False):
            measured = measure(others, scan)
            suffix = "scan" if scan else "noscan"
            assert measured["mutations"] == 0, "ferryGL sent a mutation to GitHub"
            assert measured["requests"] == model_requests(others, scan), (others, scan)
            row |= {f"{key}_{suffix}": value for key, value in measured.items()}
        rows.append(row)
        print(row)
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "github-cost.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("points per page:", point_cost(False, github.PAGE_SIZE), "and with commits,", end=" ")
    print(point_cost(True, github.COMMITS_PAGE_SIZE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
