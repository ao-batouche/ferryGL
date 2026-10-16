"""Decide what to change in Linear. Pure: reads nothing and writes nothing.

Each GitHub item gets one "home": the Linear issue, assigned to you, that has a link to
the item's URL. Issues assigned to you carry the pull requests that refer to them, so an
issue and its fix share one Linear issue.

ferryGL stores the GitHub state it last acted on in the link's metadata, as flat
``ferrygl_*`` keys, and moves the Linear status only when that GitHub state changes. A
status you set by hand is left alone until something happens on GitHub.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ferrygl.config import Config, Route
from ferrygl.model import (
    Action,
    Attachment,
    Change,
    GitHubItem,
    Kind,
    LinearIssue,
    Link,
    Role,
    item_key,
)

META_PREFIX = "ferrygl_"
META_VERSION = 1
COMMENT_MARKER = "(BY-FGL)"  # starts every comment ferryGL posts, so people can find them
TERMINAL = frozenset({"done", "canceled"})
LINEAR_CLOSED_TYPES = frozenset({"completed", "canceled", "duplicate"})

_TITLE_NUMBER = re.compile(r"#(\d+)\s*$")
_GITHUB_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)")


@dataclass(frozen=True)
class GitHubState:
    key: str  # recorded on the Linear link, such as "issue:open:pr_open"
    on_change: str | None  # status category when this state is new; None keeps the status
    on_create: str  # status category for a new Linear issue
    summary: str  # for comments, such as "acme/api#12 was merged"


def github_state(item: GitHubItem, linked_prs: Sequence[GitHubItem] = ()) -> GitHubState:
    """Where ``item`` stands on GitHub, and the Linear status that follows from it."""
    label = item.label
    if item.kind is Kind.ISSUE:
        if item.state == "CLOSED":
            if item.state_reason in ("NOT_PLANNED", "DUPLICATE"):
                reason = item.state_reason.lower().replace("_", " ")
                closed = f"{label} was closed as {reason}"
                return GitHubState("issue:closed:not_planned", "canceled", "canceled", closed)
            closed = f"{label} was closed as completed"
            return GitHubState("issue:closed:completed", "done", "done", closed)
        open_prs = [pr for pr in linked_prs if pr.state == "OPEN"]
        if any(not pr.is_draft for pr in open_prs):
            summary = f"{label} has a pull request open for review"
            return GitHubState("issue:open:pr_open", "in_review", "in_review", summary)
        if open_prs:
            summary = f"{label} has a draft pull request"
            return GitHubState("issue:open:pr_draft", "in_progress", "in_progress", summary)
        if any(pr.state == "MERGED" for pr in linked_prs):
            # The fix landed but the issue is still open: the work isn't done yet.
            summary = f"{label}'s pull request was merged, and the issue is still open"
            return GitHubState("issue:open:pr_merged", None, "in_review", summary)
        return GitHubState("issue:open", "todo", "todo", f"{label} is open")

    kind = item.kind.value
    if item.state == "MERGED":
        return GitHubState(f"{kind}:merged", "done", "done", f"{label} was merged")
    if item.state == "CLOSED":
        summary = f"{label} was closed without being merged"
        return GitHubState(f"{kind}:closed", "canceled", "canceled", summary)
    if item.kind is Kind.INVOLVED:
        # A request, including one after your review, means it's your turn.
        if Role.REQUESTED in item.roles:
            summary = f"a review is requested on {label}"
            return GitHubState("involved:requested", "todo", "todo", summary)
        if Role.REVIEWER in item.roles:
            summary = f"{label} was reviewed"
            return GitHubState("involved:reviewed", "in_review", "in_review", summary)
    # Your pull request, or one you're assigned to or commit to, follows its own state.
    if item.is_draft:
        return GitHubState(f"{kind}:draft", "in_progress", "in_progress", f"{label} is a draft")
    return GitHubState(f"{kind}:open", "in_review", "in_review", f"{label} is open for review")


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
        self.involved = [i for i in inp.items if i.kind is Kind.INVOLVED]
        self.known_prs = {i.key for i in inp.items if i.kind is not Kind.ISSUE}
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
        issue_homes = {
            issue.key: home.issue if (home := self.home(issue.url)) else None
            for issue in self.issues
        }
        # An issue without a Linear issue joins the one its pull request already has.
        joins: dict[str, LinearIssue] = {}
        for pr in self.prs:
            targets = self.refs[pr.key]
            own = self.home(pr.url)
            if own is not None and len(targets) == 1 and issue_homes[targets[0]] is None:
                joins.setdefault(targets[0], own.issue)

        # Linear issue ID -> the synced issue whose home it is.
        owners = {
            linear.id: key for key, linear in {**joins, **issue_homes}.items() if linear is not None
        }
        riders: dict[str, list[GitHubItem]] = defaultdict(list)
        solo_prs = []
        for pr in self.prs:
            targets = self.refs[pr.key]
            own = self.home(pr.url)
            target_ids = {t.id for k in targets if (t := issue_homes[k] or joins.get(k))}
            # A pull request rides with its issue unless it already has a Linear issue of
            # its own elsewhere.
            if targets and (own is None or own.issue.id in target_ids):
                for key in targets:
                    riders[key].append(pr)
            elif own is not None and own.issue.id in owners:
                # Its link sits on an issue's Linear issue (say, the reference was edited
                # out): keep one source for that Linear issue's status.
                key = owners[own.issue.id]
                riders[key].append(pr)
                self.linked[key].append(pr)
            elif own is not None and "linked_to" in _recorded(own.metadata):
                # It rode with an issue that has left your scope. That Linear issue is the
                # issue's, so it stays as it is rather than follow the pull request alone.
                continue
            else:
                solo_prs.append(pr)

        changes: list[Change] = []
        for issue in self.issues:
            linked, rides, join = self.linked[issue.key], riders[issue.key], joins.get(issue.key)
            changes += self.unit(issue, linked, rides, join)
        for item in [*solo_prs, *self.involved]:
            changes += self.unit(item, (), ())
        return changes

    def home(self, url: str) -> Attachment | None:
        """The link to ``url`` on a Linear issue assigned to you."""
        for attachment in self.inp.attachments.get(url, ()):
            if attachment.issue.assignee_id == self.inp.viewer_id:
                return attachment
        return None

    def attached(self, url: str, issue: LinearIssue) -> bool:
        return url in issue.attachment_urls or any(
            a.issue.id == issue.id for a in self.inp.attachments.get(url, ())
        )

    def unit(
        self,
        item: GitHubItem,
        linked_prs: Sequence[GitHubItem],
        riders: Sequence[GitHubItem],
        join: LinearIssue | None = None,
    ) -> list[Change]:
        state = github_state(item, linked_prs)
        home = self.home(item.url)
        if home is not None:
            return self.existing(item, state, home, riders)
        if join is not None:
            return [self.adopt(item, state, join, riders)]
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
            if "state" not in recorded:
                # First time ferryGL acts on this link: only close the issue if GitHub is done.
                category = self.closing_category(state.on_change, issue)
            status_to = self.target(category, issue)
            record = Link(
                url=item.url,
                title=home.title,
                subtitle=home.subtitle,
                metadata=self.metadata(home.metadata, item, state),
                attachment_id=home.id,
            )
            if status_to:
                said = f"Status {issue.state_name} → {status_to}: {state.summary}."
            else:
                said = f"Status stays {issue.state_name}: {state.summary}."
            changes.append(
                Change(
                    Action.UPDATE if status_to else Action.RECORD,
                    item,
                    issue=issue,
                    status_from=issue.state_name if status_to else None,
                    status_to=status_to,
                    links=(record,),
                    comment=self.comment(said),
                )
            )
        for pr in riders:
            if not self.attached(pr.url, issue):
                said = f"Linked {pr.label}, a pull request that refers to {item.label}."
                changes.append(
                    Change(
                        Action.LINK,
                        pr,
                        issue=issue,
                        links=(self.rider_link(pr, item),),
                        comment=self.comment(said),
                    )
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
        if matches:
            return [self.adopt(item, state, matches[0], riders)]
        status_to = self.names.name(state.on_create)
        sources = _join([item.label, *(pr.label for pr in riders)])
        return [
            Change(
                Action.CREATE,
                item,
                title=self.title(item),
                description=self.description(item, riders),
                status_to=status_to,
                links=(
                    self.primary_link(item, state),
                    *(self.rider_link(pr, item) for pr in riders),
                ),
                comment=self.comment(
                    f"Created from {sources}.", f"Status {status_to}: {state.summary}."
                ),
            )
        ]

    def adopt(
        self,
        item: GitHubItem,
        state: GitHubState,
        issue: LinearIssue,
        riders: Sequence[GitHubItem],
    ) -> Change:
        """Link ``item`` to an existing Linear issue that has no link to it yet."""
        self.claimed.add(issue.id)
        new_riders = [pr for pr in riders if not self.attached(pr.url, issue)]
        links = [self.primary_link(item, state)]
        links += [self.rider_link(pr, item) for pr in new_riders]
        status_to = self.target(self.closing_category(state.on_create, issue), issue)
        said = [f"Linked {_join([item.label, *(pr.label for pr in new_riders)])}."]
        if status_to:
            said.append(f"Status {issue.state_name} → {status_to}: {state.summary}.")
        return Change(
            Action.ADOPT,
            item,
            issue=issue,
            status_from=issue.state_name if status_to else None,
            status_to=status_to,
            links=tuple(links),
            comment=self.comment(*said),
        )

    def candidates_for(self, item: GitHubItem, riders: Sequence[GitHubItem]) -> list[LinearIssue]:
        """Your Linear issues that mention ``item``, or a pull request riding with it.

        A mention is the GitHub URL in the description or links, or, when the route has a
        single repository, a title ending in ``#<number>``. A pull request that nothing
        mentions falls back to the issues it refers to, since the Linear issue tracking an
        issue is the natural home for its fix.
        """
        direct = self.mentioning([item, *riders])
        if direct or item.kind is not Kind.PR:
            return direct
        refs = [
            r
            for r in item.references
            if r.repo.lower() == item.repo.lower() and r.key not in self.known_prs
        ]
        return self.mentioning([_ref_item(item.repo, r.number) for r in refs], pr_links=False)

    def mentioning(self, items: Sequence[GitHubItem], pr_links: bool = True) -> list[LinearIssue]:
        """Unclaimed candidates that mention one of ``items`` and nothing else on GitHub.

        A candidate already linked to a different GitHub item is about that item. With
        ``pr_links`` false, a candidate linked to any pull request is skipped too.
        """
        keys = {i.key for i in items}
        numbers = {i.number for i in items}
        by_title = self.inp.route.single_repo is not None
        found = []
        for candidate in self.inp.candidates:
            if candidate.id in self.claimed:
                continue
            if _github_keys(candidate.attachment_urls) - keys:
                continue
            if not pr_links and any("/pull/" in u for u in candidate.attachment_urls):
                continue
            by_url = any(
                i.url in candidate.attachment_urls or _mentions(candidate.description, i.url)
                for i in items
            )
            by_number = (
                by_title
                and _title_number(candidate.title) in numbers
                and _github_keys([candidate.description]) <= keys
            )
            if by_url or by_number:
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

    def comment(self, *sentences: str) -> str | None:
        """The comment to post with a change, or None when `[sync] comment` is off. It uses
        only what ferryGL already writes to Linear: item numbers, states and status names."""
        if not self.inp.config.sync.comment:
            return None
        return " ".join((COMMENT_MARKER, *sentences))

    def metadata(
        self, existing: dict[str, Any], item: GitHubItem, state: GitHubState
    ) -> dict[str, Any]:
        """``existing`` metadata with ferryGL's keys replaced. Values are strings or numbers,
        as Linear expects."""
        kept = {k: v for k, v in (existing or {}).items() if not str(k).startswith(META_PREFIX)}
        return {**kept, **_meta(item.kind.value, state=state.key, synced_at=self.inp.now)}

    def primary_link(self, item: GitHubItem, state: GitHubState) -> Link:
        return Link(
            url=item.url,
            title=_link_title(item),
            subtitle=item.repo,
            metadata=self.metadata({}, item, state),
        )

    def rider_link(self, pr: GitHubItem, issue: GitHubItem) -> Link:
        return Link(
            url=pr.url,
            title=_link_title(pr),
            subtitle=pr.repo,
            metadata=_meta(pr.kind.value, linked_to=issue.label),
        )

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


def _meta(kind: str, **fields: str) -> dict[str, Any]:
    meta: dict[str, Any] = {f"{META_PREFIX}v": META_VERSION, f"{META_PREFIX}kind": kind}
    meta.update({f"{META_PREFIX}{name}": value for name, value in fields.items()})
    return meta


def _join(words: Sequence[str]) -> str:
    """``a``, ``a and b``, or ``a, b and c``."""
    if len(words) < 2:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _recorded(metadata: Any) -> dict[str, Any]:
    """ferryGL's own metadata on a link, without the prefix."""
    if not isinstance(metadata, dict):
        return {}
    return {
        key[len(META_PREFIX) :]: value
        for key, value in metadata.items()
        if isinstance(key, str) and key.startswith(META_PREFIX)
    }


def _ref_item(repo: str, number: int) -> GitHubItem:
    """A referenced issue, known only by its number."""
    url = f"https://github.com/{repo}/issues/{number}"
    return GitHubItem(Kind.ISSUE, repo, number, title="", url=url, state="OPEN")


def _github_keys(texts: Iterable[str]) -> set[str]:
    return {item_key(m[1], int(m[2])) for text in texts for m in _GITHUB_URL.finditer(text)}


def _mentions(text: str, url: str) -> bool:
    return re.search(re.escape(url) + r"(?!\d)", text) is not None


def _title_number(title: str) -> int | None:
    match = _TITLE_NUMBER.search(title)
    return int(match[1]) if match else None


def _link_title(item: GitHubItem) -> str:
    return f"{'Issue' if item.kind is Kind.ISSUE else 'PR'} #{item.number}"
