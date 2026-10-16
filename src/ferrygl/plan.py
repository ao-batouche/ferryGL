"""Decide what to change in Linear. Pure: reads nothing and writes nothing.

Each GitHub item gets one "home": the Linear issue, assigned to you, that has a link to
the item's URL. Issues assigned to you carry the pull requests that refer to them, so an
issue and its fix share one Linear issue.

ferryGL stores the GitHub state it last acted on in the link's metadata, under the
``ferrygl`` key, and moves the Linear status only when that GitHub state changes. A status
you set by hand is left alone until something happens on GitHub.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ferrygl.config import Config, Route
from ferrygl.model import Action, Attachment, Change, GitHubItem, Kind, LinearIssue, Link

META_KEY = "ferrygl"
META_VERSION = 1
TERMINAL = frozenset({"done", "canceled"})
LINEAR_CLOSED_TYPES = frozenset({"completed", "canceled", "duplicate"})

_TITLE_NUMBER = re.compile(r"#(\d+)\s*$")


@dataclass(frozen=True)
class GitHubState:
    key: str  # recorded on the Linear link, such as "issue:open:pr_open"
    on_change: str | None  # status category when this state is new; None keeps the status
    on_create: str  # status category for a new Linear issue


def github_state(item: GitHubItem, linked_prs: Sequence[GitHubItem] = ()) -> GitHubState:
    """Where ``item`` stands on GitHub, and the Linear status that follows from it."""
    if item.kind is Kind.ISSUE:
        if item.state == "CLOSED":
            if item.state_reason in ("NOT_PLANNED", "DUPLICATE"):
                return GitHubState("issue:closed:not_planned", "canceled", "canceled")
            return GitHubState("issue:closed:completed", "done", "done")
        open_prs = [pr for pr in linked_prs if pr.state == "OPEN"]
        if any(not pr.is_draft for pr in open_prs):
            return GitHubState("issue:open:pr_open", "in_review", "in_review")
        if open_prs:
            return GitHubState("issue:open:pr_draft", "in_progress", "in_progress")
        if any(pr.state == "MERGED" for pr in linked_prs):
            # The fix landed but the issue is still open: the work isn't done yet.
            return GitHubState("issue:open:pr_merged", None, "in_review")
        return GitHubState("issue:open", "todo", "todo")

    kind = item.kind.value
    if item.state == "MERGED":
        return GitHubState(f"{kind}:merged", "done", "done")
    if item.state == "CLOSED":
        return GitHubState(f"{kind}:closed", "canceled", "canceled")
    if item.kind is Kind.PR:
        if item.is_draft:
            return GitHubState("pr:draft", "in_progress", "in_progress")
        return GitHubState("pr:open", "in_review", "in_review")
    if item.reviewed:
        return GitHubState("review:reviewed", "in_review", "in_review")
    return GitHubState("review:requested", "todo", "todo")


@dataclass(frozen=True)
class PlanInput:
    route: Route
    config: Config
    viewer_id: str  # the Linear user ferryGL acts for
    items: Sequence[GitHubItem]
    attachments: Mapping[str, Sequence[Attachment]]  # Linear links by GitHub URL
    candidates: Sequence[LinearIssue] = ()  # the route team's issues assigned to the viewer
    now: str = ""  # timestamp recorded with each state


def plan(inp: PlanInput) -> list[Change]:
    """The changes that bring Linear in line with ``inp.items``, in display order."""
    return _Planner(inp).run()


class _Planner:
    def __init__(self, inp: PlanInput) -> None:
        self.inp = inp
        self.names = inp.config.status
        self.issues = [i for i in inp.items if i.kind is Kind.ISSUE]
        self.prs = [i for i in inp.items if i.kind is Kind.PR]
        self.reviews = [i for i in inp.items if i.kind is Kind.REVIEW]
        issue_keys = {issue.key for issue in self.issues}
        # Pull request key -> keys of synced issues it refers to.
        self.refs = {
            pr.key: [r.key for r in pr.references if r.key in issue_keys] for pr in self.prs
        }
        self.linked: dict[str, list[GitHubItem]] = defaultdict(list)
        for pr in self.prs:
            for key in self.refs[pr.key]:
                self.linked[key].append(pr)
        # Linear issues that are already home to an item can't be adopted for another one.
        self.claimed = {home.issue.id for item in inp.items if (home := self.home(item.url))}

    def run(self) -> list[Change]:
        issue_homes = {issue.key: self.home(issue.url) for issue in self.issues}
        riders: dict[str, list[GitHubItem]] = defaultdict(list)
        solo_prs = []
        for pr in self.prs:
            targets = self.refs[pr.key]
            own = self.home(pr.url)
            target_homes = {issue_homes[k].issue.id for k in targets if issue_homes.get(k)}
            # A pull request rides with its issue unless it already has a Linear issue of
            # its own elsewhere.
            if targets and (own is None or own.issue.id in target_homes):
                for key in targets:
                    riders[key].append(pr)
            else:
                solo_prs.append(pr)

        changes: list[Change] = []
        for issue in self.issues:
            changes += self.unit(issue, self.linked[issue.key], riders[issue.key])
        for item in [*solo_prs, *self.reviews]:
            changes += self.unit(item, (), ())
        return changes

    def home(self, url: str) -> Attachment | None:
        """The link to ``url`` on a Linear issue assigned to you."""
        for attachment in self.inp.attachments.get(url, ()):
            if attachment.issue.assignee_id == self.inp.viewer_id:
                return attachment
        return None

    def unit(
        self, item: GitHubItem, linked_prs: Sequence[GitHubItem], riders: Sequence[GitHubItem]
    ) -> list[Change]:
        state = github_state(item, linked_prs)
        home = self.home(item.url)
        if home is not None:
            return self.existing(item, state, home, riders)
        return self.new(item, state, riders)

    def existing(
        self,
        item: GitHubItem,
        state: GitHubState,
        home: Attachment,
        riders: Sequence[GitHubItem],
    ) -> list[Change]:
        changes = []
        issue = home.issue
        recorded = _recorded(home.metadata)
        if recorded.get("state") != state.key:
            category = state.on_change
            if not recorded:
                # First sync of an issue made by hand: only close it if GitHub is done.
                category = self.closing_category(state.on_change, issue)
            status_to = self.target(category, issue)
            record = Link(
                url=item.url,
                title=home.title,
                subtitle=home.subtitle,
                metadata=self.metadata(home.metadata, item, state),
                attachment_id=home.id,
            )
            changes.append(
                Change(
                    Action.UPDATE if status_to else Action.RECORD,
                    item,
                    issue=issue,
                    status_from=issue.state_name if status_to else None,
                    status_to=status_to,
                    links=(record,),
                )
            )
        for pr in riders:
            if not any(a.issue.id == issue.id for a in self.inp.attachments.get(pr.url, ())):
                changes.append(
                    Change(Action.LINK, pr, issue=issue, links=(self.rider_link(pr, item),))
                )
        return changes

    def new(
        self, item: GitHubItem, state: GitHubState, riders: Sequence[GitHubItem]
    ) -> list[Change]:
        matches = self.candidates_for(item, riders)
        if len(matches) > 1:
            names = ", ".join(c.identifier for c in matches)
            note = f"several of your Linear issues mention it ({names}); link it to one by hand"
            return [Change(Action.SKIP, item, note=note)]

        links = (self.primary_link(item, state), *(self.rider_link(pr, item) for pr in riders))
        if matches:
            issue = matches[0]
            self.claimed.add(issue.id)
            status_to = self.target(self.closing_category(state.on_create, issue), issue)
            return [
                Change(
                    Action.ADOPT,
                    item,
                    issue=issue,
                    status_from=issue.state_name if status_to else None,
                    status_to=status_to,
                    links=tuple(link for link in links if link.url not in issue.attachment_urls),
                )
            ]
        return [
            Change(
                Action.CREATE,
                item,
                title=self.title(item),
                description=self.description(item, riders),
                status_to=self.names.name(state.on_create),
                links=links,
            )
        ]

    def candidates_for(self, item: GitHubItem, riders: Sequence[GitHubItem]) -> list[LinearIssue]:
        """Your Linear issues that mention ``item``, or a pull request riding with it.

        A mention is the GitHub URL in the description or links. When the route has a
        single repository, a title ending in ``#<number>`` also counts. A pull request
        with nothing mentioning it directly falls back to the issues it refers to, since
        a Linear issue tracking an issue is the natural home for its fix.
        """
        direct = self.mentioning(
            [item.url, *(pr.url for pr in riders)], {item.number, *(pr.number for pr in riders)}
        )
        if direct or item.kind is not Kind.PR:
            return direct
        refs = [r for r in item.references if r.repo.lower() == item.repo.lower()]
        urls = [
            f"https://github.com/{item.repo}/{path}/{r.number}"
            for r in refs
            for path in ("issues", "pull")
        ]
        return self.mentioning(urls, {r.number for r in refs})

    def mentioning(self, urls: Sequence[str], numbers: set[int]) -> list[LinearIssue]:
        by_title = self.inp.route.single_repo is not None
        found = []
        for candidate in self.inp.candidates:
            if candidate.id in self.claimed:
                continue
            by_url = any(
                url in candidate.attachment_urls or _mentions(candidate.description, url)
                for url in urls
            )
            if by_url or (by_title and _title_number(candidate.title) in numbers):
                found.append(candidate)
        return found

    def closing_category(self, category: str | None, issue: LinearIssue) -> str | None:
        """``category`` if it closes an issue that's still open in Linear, else None."""
        if category in TERMINAL and issue.state_type not in LINEAR_CLOSED_TYPES:
            return category
        return None

    def target(self, category: str | None, issue: LinearIssue) -> str | None:
        """The state name to move ``issue`` to, or None if it's already there."""
        if category is None:
            return None
        name = self.names.name(category)
        return name if name != issue.state_name else None

    def metadata(
        self, existing: dict[str, Any], item: GitHubItem, state: GitHubState
    ) -> dict[str, Any]:
        merged = dict(existing or {})
        merged[META_KEY] = {
            "v": META_VERSION,
            "kind": item.kind.value,
            "state": state.key,
            "synced_at": self.inp.now,
        }
        return merged

    def primary_link(self, item: GitHubItem, state: GitHubState) -> Link:
        return Link(
            url=item.url,
            title=_link_title(item),
            subtitle=item.repo,
            metadata=self.metadata({}, item, state),
        )

    def rider_link(self, pr: GitHubItem, issue: GitHubItem) -> Link:
        meta = {"v": META_VERSION, "kind": pr.kind.value, "linked_to": issue.label}
        return Link(url=pr.url, title=_link_title(pr), subtitle=pr.repo, metadata={META_KEY: meta})

    def title(self, item: GitHubItem) -> str:
        template = getattr(self.inp.config.titles, item.kind.value)
        return template.format(title=item.title, number=item.number, repo=item.repo)

    def description(self, item: GitHubItem, riders: Sequence[GitHubItem]) -> str:
        noun = "GitHub issue" if item.kind is Kind.ISSUE else "Pull request"
        lines = [f"{noun}: [{item.url}]({item.url})"]
        lines += [f"Pull request: [{pr.url}]({pr.url})" for pr in riders]
        text = "\n\n".join(lines)
        if self.inp.config.copy_description and item.body.strip():
            text += "\n\n---\n\n" + item.body.strip()
        return text


def _recorded(metadata: Any) -> dict[str, Any]:
    value = metadata.get(META_KEY) if isinstance(metadata, dict) else None
    return value if isinstance(value, dict) else {}


def _mentions(text: str, url: str) -> bool:
    return re.search(re.escape(url) + r"(?!\d)", text) is not None


def _title_number(title: str) -> int | None:
    match = _TITLE_NUMBER.search(title)
    return int(match[1]) if match else None


def _link_title(item: GitHubItem) -> str:
    return f"{'Issue' if item.kind is Kind.ISSUE else 'PR'} #{item.number}"
