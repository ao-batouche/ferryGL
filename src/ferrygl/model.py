"""Plain data passed between the GitHub reader, the planner and the Linear writer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    """What a GitHub item is to you."""

    ISSUE = "issue"  # an issue assigned to you
    PR = "pr"  # a pull request you opened
    REVIEW = "review"  # someone else's pull request you reviewed or were asked to review


def item_key(repo: str, number: int) -> str:
    """Case-insensitive identity of a GitHub issue or pull request."""
    return f"{repo.lower()}#{number}"


@dataclass(frozen=True)
class ItemRef:
    """An issue or pull request number in a repository, such as ``acme/api#252``."""

    repo: str
    number: int

    @property
    def key(self) -> str:
        return item_key(self.repo, self.number)


@dataclass(frozen=True)
class GitHubItem:
    kind: Kind
    repo: str
    number: int
    title: str
    url: str
    state: str  # "OPEN", "CLOSED" or "MERGED"
    state_reason: str | None = None  # issues: "COMPLETED", "NOT_PLANNED", "DUPLICATE", ...
    is_draft: bool = False
    body: str = ""
    references: tuple[ItemRef, ...] = ()  # pull requests: issues they close or mention
    reviewed: bool = False  # reviews: you submitted at least one review
    requested: bool = False  # reviews: your review is currently requested

    @property
    def key(self) -> str:
        return item_key(self.repo, self.number)

    @property
    def label(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str  # such as "ENG-12"
    title: str
    state_name: str
    state_type: str  # Linear workflow state type: "unstarted", "started", "completed", ...
    team_id: str
    assignee_id: str | None
    description: str = ""
    attachment_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class Attachment:
    """A link on a Linear issue."""

    id: str
    url: str
    title: str
    subtitle: str | None
    metadata: dict[str, Any]
    issue: LinearIssue


class Action(StrEnum):
    CREATE = "create"  # create a Linear issue
    ADOPT = "adopt"  # link an existing Linear issue of yours that mentions the item
    LINK = "link"  # attach a pull request to the Linear issue of the issue it refers to
    UPDATE = "update"  # change a Linear issue's status
    RECORD = "record"  # remember a GitHub state change that leaves the status alone
    SKIP = "skip"  # needs a decision from you


@dataclass(frozen=True)
class Link:
    """A GitHub link to create on, or update in, a Linear issue."""

    url: str
    title: str
    subtitle: str | None
    metadata: dict[str, Any]
    attachment_id: str | None = None  # set when updating an existing link


@dataclass(frozen=True)
class Change:
    action: Action
    item: GitHubItem
    issue: LinearIssue | None = None  # the existing Linear issue; None for CREATE and SKIP
    title: str | None = None  # CREATE only
    description: str | None = None  # CREATE only
    status_from: str | None = None
    status_to: str | None = None  # Linear state name to set
    links: tuple[Link, ...] = ()
    note: str | None = None
