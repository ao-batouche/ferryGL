"""In-memory stand-ins for GitHub and Linear, used by the white paper's evaluation.

``FakeGitHub`` answers the searches ferryGL runs with GraphQL-shaped nodes, and
``MemoryLinear`` keeps Linear issues, links and comments in memory. They have the same
methods as ``ferrygl.github.GitHubClient`` and ``ferrygl.linear.LinearClient``, so ferryGL's
own reading, planning and writing code runs against them unchanged. All data is synthetic.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from ferrygl.linear import LinearError, State, Team
from ferrygl.model import Attachment, LinearIssue, Link

ME = "alice"  # the GitHub login ferryGL reads as
VIEWER = "user-alice"  # the Linear user who owns the API key
TEAMMATE = "user-bob"  # another member of the Linear workspace
TEAM_ID = "team-eng"
_GITHUB_URL = re.compile(r"https://github\.com/\S+?/(?:issues|pull)/\d+")
STATE_TYPES = {
    "Backlog": "backlog",
    "Todo": "unstarted",
    "In Progress": "started",
    "In Review": "started",
    "Done": "completed",
    "Canceled": "canceled",
}


# GitHub


@dataclass
class Issue:
    repo: str
    number: int
    title: str
    author: str
    assignees: set[str] = field(default_factory=set)
    state: str = "OPEN"
    reason: str | None = None
    body: str = ""

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/issues/{self.number}"


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    author: str
    state: str = "OPEN"
    draft: bool = False
    body: str = ""
    closes: list[tuple[str, int]] = field(default_factory=list)
    assignees: set[str] = field(default_factory=set)
    reviewed_by: set[str] = field(default_factory=set)
    requested: set[str] = field(default_factory=set)
    commit_authors: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/pull/{self.number}"


@dataclass
class GitHubWorld:
    """The issues and pull requests in some repositories."""

    repos: tuple[str, ...]
    issues: list[Issue] = field(default_factory=list)
    prs: list[PullRequest] = field(default_factory=list)
    next_number: dict[str, int] = field(default_factory=dict)

    def number(self, repo: str) -> int:
        self.next_number[repo] = self.next_number.get(repo, 0) + 1
        return self.next_number[repo]


class FakeGitHub:
    """Answers ferryGL's GitHub searches from a ``GitHubWorld``. It has no write methods."""

    def __init__(self, world: GitHubWorld, login: str = ME, page_limit: int = 1000) -> None:
        self.world = world
        self.login = login
        self.page_limit = page_limit  # GitHub returns at most 1,000 results per search
        self.searches = 0

    def viewer_login(self) -> str:
        return self.login

    def can_read(self, repo: str) -> bool:
        return True

    def search(self, query: str, with_commits: bool = False) -> list[dict[str, Any]]:
        self.searches += 1
        terms = query.split()
        repos = {t[5:].lower() for t in terms if t.startswith("repo:")}
        owners = {t[5:].lower() for t in terms if t.startswith("user:")}

        def in_scope(repo: str) -> bool:
            return repo.lower() in repos or repo.split("/")[0].lower() in owners

        def value(prefix: str) -> str | None:
            return next((t[len(prefix) :] for t in terms if t.startswith(prefix)), None)

        author, not_author = value("author:"), value("-author:")
        assignee, reviewer = value("assignee:"), value("reviewed-by:")
        requested = value("user-review-requested:")
        if "is:issue" in terms:
            found = [
                issue_node(i)
                for i in self.world.issues
                if in_scope(i.repo)
                and (assignee is None or assignee in i.assignees)
                and (author is None or i.author == author)
            ]
        else:
            found = [
                pr_node(p, with_commits)
                for p in self.world.prs
                if in_scope(p.repo)
                and (author is None or p.author == author)
                and (not_author is None or p.author != not_author)
                and (assignee is None or assignee in p.assignees)
                and (reviewer is None or reviewer in p.reviewed_by)
                and (requested is None or requested in p.requested)
            ]
        return found[: self.page_limit]


def issue_node(issue: Issue) -> dict[str, Any]:
    return {
        "__typename": "Issue",
        "number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "state": issue.state,
        "stateReason": issue.reason,
        "body": issue.body,
        "repository": {"nameWithOwner": issue.repo},
    }


def pr_node(pr: PullRequest, with_commits: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {
        "__typename": "PullRequest",
        "number": pr.number,
        "title": pr.title,
        "url": pr.url,
        "state": pr.state,
        "isDraft": pr.draft,
        "body": pr.body,
        "repository": {"nameWithOwner": pr.repo},
        "author": {"login": pr.author},
        "closingIssuesReferences": {
            "nodes": [{"number": n, "repository": {"nameWithOwner": r}} for r, n in pr.closes]
        },
    }
    if with_commits:
        node["commits"] = {
            "nodes": [
                {"commit": {"authors": {"nodes": [{"user": {"login": login}}]}}}
                for login in pr.commit_authors[:100]
            ]
        }
    return node


# Linear


@dataclass
class MemoryAttachment:
    id: str
    url: str
    title: str
    subtitle: str | None
    metadata: dict[str, Any]


@dataclass
class MemoryIssue:
    id: str
    identifier: str
    title: str
    description: str
    state: str
    team_id: str
    assignee: str | None
    attachments: list[MemoryAttachment] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    created_for: str | None = None  # the GitHub URL ferryGL created this issue for


@dataclass(frozen=True)
class Write:
    """One mutation ferryGL sent, with the assignee of its issue at that moment."""

    op: str
    issue_id: str
    assignee: str | None
    payload: dict[str, Any]


class Faults:
    """Makes some writes fail, as a network error would.

    A *lost* write reaches Linear, but its response doesn't come back. A *rejected* one
    doesn't reach Linear. Both raise ``LinearError``, as the real client does once its
    retries are spent.
    """

    def __init__(self, rng: random.Random, rate: float) -> None:
        self.rng = rng
        self.rate = rate
        self.injected = 0

    def roll(self) -> str | None:
        if self.rate and self.rng.random() < self.rate:
            self.injected += 1
            return self.rng.choice(("lost", "rejected"))
        return None


class MemoryLinear:
    """A Linear workspace in memory, with the methods ferryGL's sync calls."""

    def __init__(self) -> None:
        self.issues: dict[str, MemoryIssue] = {}
        self.states = {name: State(f"state-{name}", name, t) for name, t in STATE_TYPES.items()}
        self.state_names = {s.id: s.name for s in self.states.values()}
        self.writes: list[Write] = []
        self.faults: Faults | None = None
        self._ids = 0

    def new_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}-{self._ids}"

    def add_issue(self, title: str, description: str, state: str, assignee: str | None) -> str:
        """An issue made by a person, not by ferryGL."""
        number = len(self.issues) + 1
        issue = MemoryIssue(
            self.new_id("issue"), f"ENG-{number}", title, description, state, TEAM_ID, assignee
        )
        self.issues[issue.id] = issue
        return issue.id

    # Reads

    def viewer(self) -> tuple[str, str]:
        return VIEWER, "Alice Example"

    def find_team(self, ref: str) -> Team | None:
        return Team(TEAM_ID, "ENG", "Engineering") if ref.upper() == "ENG" else None

    def team_states(self, team_id: str) -> dict[str, State]:
        return dict(self.states)

    def find_project(self, name: str, team_id: str) -> str | None:
        return None

    def find_label(self, name: str, team_id: str) -> str | None:
        return None

    def attachments_for_urls(self, urls) -> dict[str, list[Attachment]]:
        found: dict[str, list[Attachment]] = {}
        for url in dict.fromkeys(urls):
            hits = [
                Attachment(a.id, a.url, a.title, a.subtitle, dict(a.metadata), self._brief(i))
                for i in self.issues.values()
                for a in i.attachments
                if a.url == url
            ]
            found[url] = hits[:20]
        return found

    def assigned_issues(self, team_id: str, assignee_id: str) -> list[LinearIssue]:
        return [
            self._full(i)
            for i in self.issues.values()
            if i.team_id == team_id and i.assignee == assignee_id
        ]

    # Writes

    def create_issue(self, **fields: Any) -> LinearIssue:
        fault = self._fault("issueCreate", None, fields)
        if fault == "rejected":
            raise LinearError("could not reach Linear (simulated)")
        issue = MemoryIssue(
            self.new_id("issue"),
            f"ENG-{len(self.issues) + 1}",
            fields["title"],
            fields["description"],
            self.state_names[fields["state_id"]],
            fields["team_id"],
            fields["assignee_id"],
            created_for=_GITHUB_URL.search(fields["description"])[0],
        )
        self.issues[issue.id] = issue
        if fault == "lost":
            raise LinearError("could not reach Linear (simulated)")
        return self._brief(issue)

    def create_attachment(self, issue_id: str, link: Link) -> None:
        def write() -> None:
            issue = self.issues[issue_id]
            existing = next((a for a in issue.attachments if a.url == link.url), None)
            if existing is not None:  # Linear updates a link to a URL the issue already has
                existing.title, existing.metadata = link.title, dict(link.metadata)
                existing.subtitle = link.subtitle or existing.subtitle
                return
            new = MemoryAttachment(
                self.new_id("att"), link.url, link.title, link.subtitle, dict(link.metadata)
            )
            issue.attachments.append(new)

        self._write("attachmentCreate", issue_id, _link(link), write)

    def update_attachment(self, link: Link) -> None:
        issue, attachment = self._attachment(link.attachment_id)

        def write() -> None:
            attachment.title, attachment.metadata = link.title, dict(link.metadata)
            attachment.subtitle = link.subtitle or attachment.subtitle

        self._write("attachmentUpdate", issue.id, _link(link), write)

    def update_issue_state(self, issue_id: str, state_id: str) -> None:
        def write() -> None:
            self.issues[issue_id].state = self.state_names[state_id]

        self._write("issueUpdate", issue_id, {"state": self.state_names[state_id]}, write)

    def create_comment(self, issue_id: str, body: str) -> None:
        def write() -> None:
            self.issues[issue_id].comments.append(body)

        self._write("commentCreate", issue_id, {"body": body}, write)

    # Helpers

    def _fault(self, op: str, issue_id: str | None, payload: dict[str, Any]) -> str | None:
        assignee = self.issues[issue_id].assignee if issue_id else payload.get("assignee_id")
        self.writes.append(Write(op, issue_id or "", assignee, payload))
        return self.faults.roll() if self.faults else None

    def _write(self, op: str, issue_id: str, payload: dict[str, Any], apply) -> None:
        fault = self._fault(op, issue_id, payload)
        if fault != "rejected":
            apply()
        if fault:
            raise LinearError("could not reach Linear (simulated)")

    def _attachment(self, attachment_id: str | None) -> tuple[MemoryIssue, MemoryAttachment]:
        for issue in self.issues.values():
            for attachment in issue.attachments:
                if attachment.id == attachment_id:
                    return issue, attachment
        raise LinearError(f"attachment {attachment_id} not found")

    def _brief(self, issue: MemoryIssue) -> LinearIssue:
        """An issue as ``attachmentsForURL`` returns it: without description or links."""
        return LinearIssue(
            issue.id,
            issue.identifier,
            issue.title,
            issue.state,
            STATE_TYPES[issue.state],
            issue.team_id,
            issue.assignee,
        )

    def _full(self, issue: MemoryIssue) -> LinearIssue:
        """An issue as the ``issues`` query returns it: with description and links."""
        return LinearIssue(
            issue.id,
            issue.identifier,
            issue.title,
            issue.state,
            STATE_TYPES[issue.state],
            issue.team_id,
            issue.assignee,
            description=issue.description,
            attachment_urls=tuple(a.url for a in issue.attachments[:20]),
        )


def _link(link: Link) -> dict[str, Any]:
    return {
        "url": link.url,
        "title": link.title,
        "subtitle": link.subtitle,
        "metadata": dict(link.metadata),
    }
