"""Small builders for synthetic GitHub items and Linear issues used across the tests."""

from __future__ import annotations

from ferrygl.config import Config, Route
from ferrygl.model import Attachment, GitHubItem, ItemRef, Kind, LinearIssue, Role

REPO = "acme/api"
VIEWER = "user-me"
OTHER = "user-other"
TEAM_ID = "team-eng"
NOW = "2026-10-16T09:00:00+00:00"

STATE_TYPES = {
    "Todo": "unstarted",
    "In Progress": "started",
    "In Review": "started",
    "Done": "completed",
    "Canceled": "canceled",
}


def url(kind: Kind, number: int, repo: str = REPO) -> str:
    path = "issues" if kind is Kind.ISSUE else "pull"
    return f"https://github.com/{repo}/{path}/{number}"


def issue(
    number: int,
    state: str = "OPEN",
    reason: str | None = None,
    repo: str = REPO,
    roles: frozenset[Role] = frozenset({Role.ASSIGNEE}),
) -> GitHubItem:
    return GitHubItem(
        kind=Kind.ISSUE,
        repo=repo,
        number=number,
        title=f"Issue {number}",
        url=url(Kind.ISSUE, number, repo),
        state=state,
        state_reason=reason,
        roles=roles,
    )


def pr(
    number: int,
    state: str = "OPEN",
    draft: bool = False,
    refs: tuple[int, ...] = (),
    repo: str = REPO,
) -> GitHubItem:
    return GitHubItem(
        kind=Kind.PR,
        repo=repo,
        number=number,
        title=f"PR {number}",
        url=url(Kind.PR, number, repo),
        state=state,
        is_draft=draft,
        references=tuple(ItemRef(repo, ref) for ref in refs),
        roles=frozenset({Role.AUTHOR}),
    )


def involved(number: int, *roles: Role, state: str = "OPEN", draft: bool = False) -> GitHubItem:
    """Someone else's pull request, with your roles on it."""
    return GitHubItem(
        kind=Kind.INVOLVED,
        repo=REPO,
        number=number,
        title=f"PR {number}",
        url=url(Kind.PR, number),
        state=state,
        is_draft=draft,
        roles=frozenset(roles or {Role.REQUESTED}),
    )


def review(
    number: int, state: str = "OPEN", reviewed: bool = False, requested: bool | None = None
) -> GitHubItem:
    """Someone else's pull request you reviewed, or whose review is requested from you."""
    requested = (not reviewed) if requested is None else requested
    roles = [Role.REVIEWER] * reviewed + [Role.REQUESTED] * requested
    return involved(number, *roles, state=state)


def linear_issue(
    identifier: str,
    state: str = "Todo",
    assignee: str | None = VIEWER,
    title: str | None = None,
    description: str = "",
    urls: tuple[str, ...] = (),
) -> LinearIssue:
    return LinearIssue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=title or f"Linear {identifier}",
        state_name=state,
        state_type=STATE_TYPES[state],
        team_id=TEAM_ID,
        assignee_id=assignee,
        description=description,
        attachment_urls=urls,
    )


def attachment(
    item: GitHubItem,
    on: LinearIssue,
    recorded: str | None = None,
    metadata: dict | None = None,
) -> Attachment:
    meta = dict(metadata or {})
    if recorded is not None:
        meta.update(ferrygl_v=1, ferrygl_kind=item.kind.value, ferrygl_state=recorded)
    return Attachment(
        id=f"att-{on.identifier}-{item.number}",
        url=item.url,
        title=f"#{item.number}",
        subtitle=item.repo,
        metadata=meta,
        issue=on,
    )


def config(*repos: str, **options) -> Config:
    route = Route(repos=repos or (REPO,), team="ENG")
    return Config(routes=(route,), **options)
