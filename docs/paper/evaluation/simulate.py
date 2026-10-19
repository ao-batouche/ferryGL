"""Check ferryGL's safety properties on random histories, for the white paper's evaluation.

Each scenario starts from random issues and pull requests in one or two repositories, then
alternates random events with runs of ferryGL's own ``sync`` code. Events happen on GitHub
(issues open and close, pull requests move, reviews are requested and given) and in Linear
(the user moves a status, hands an issue to a teammate, links an item by hand, or writes
an issue that mentions one; a teammate links the same items). Some steps change only Linear.
A share of Linear writes fail, as network errors would.

After every run, the properties of the paper's evaluation section are checked against the
in-memory Linear workspace. The script prints a summary and writes it to ``--out``.

    uv run python docs/paper/evaluation/simulate.py
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from world import (
    ME,
    STATE_TYPES,
    TEAMMATE,
    VIEWER,
    FakeGitHub,
    Faults,
    GitHubWorld,
    Issue,
    MemoryAttachment,
    MemoryLinear,
    PullRequest,
)

import ferrygl.sync as sync_module
from ferrygl.config import Config, Route, SyncOptions
from ferrygl.github import fetch_items
from ferrygl.model import Action, Change, Kind
from ferrygl.plan import PlanInput, github_state
from ferrygl.plan import plan as real_plan

OTHERS = ("bob", "carol")  # other GitHub users; their logins must never reach Linear
SECRET = "SECRET-BODY"  # in every GitHub description; must never reach Linear
TITLE_WORD = "widget"  # in every GitHub title; may reach titles, never comments
CLOSED_TYPES = {"completed", "canceled"}
EXPECTED = {  # the status for each state key of a pull request, from docs/sync-rules.md
    "pr:draft": "In Progress",
    "pr:open": "In Review",
    "pr:merged": "Done",
    "pr:closed": "Canceled",
    "involved:requested": "Todo",
    "involved:reviewed": "In Review",
    "involved:draft": "In Progress",
    "involved:open": "In Review",
    "involved:merged": "Done",
    "involved:closed": "Canceled",
}
PROPERTIES = {
    "dry_run": "A run without --apply writes nothing",
    "quiescent": "A rerun with nothing new on GitHub or in Linear writes nothing",
    "one_home": "Each GitHub item has at most one home among the user's Linear issues",
    "ownership": "Every write targets a Linear issue assigned to the user",
    "status_source": "A status moves only with a new recorded GitHub state, or a first-sync close",
    "edits_win": "When only Linear changed, a status the user set stays, bar a first-sync close",
    "coverage": "Every item in scope has a home after a run, unless it was skipped",
    "consistent": "A pull request's home records its current GitHub state and status",
    "foreign_metadata": "Link metadata that isn't ferryGL's survives every update",
    "minimal_data": "No description, other user's login, or title (in comments) reaches Linear",
    "recovery": "After failed writes, one clean run converges without duplicates",
    "order_independent": "The plan doesn't depend on the order of Linear's lookup results",
}


# Random worlds and events


def title(rng: random.Random) -> str:
    noun = rng.choice(("cache", "parser", "session", "export", "search", "billing", "upload"))
    return f"Improve the {noun} {TITLE_WORD}"


def body(rng: random.Random, refs: str = "") -> str:
    return f"{SECRET}-{rng.randrange(10**6)} cc @{rng.choice(OTHERS)}\n\n{refs}".strip()


def ref_text(rng: random.Random, pr: PullRequest, issue: Issue) -> str:
    """How a pull request's description mentions ``issue``, in one of GitHub's forms."""
    same = issue.repo == pr.repo
    style = rng.choice(("closes", "local", "repo", "url") if same else ("closes", "repo", "url"))
    if style == "closes":
        pr.closes.append((issue.repo, issue.number))
        return f"Fixes #{issue.number}" if same else f"Fixes {issue.repo}#{issue.number}"
    if style == "local":
        return f"Part of #{issue.number}"
    if style == "repo":
        return f"See {issue.repo}#{issue.number}"
    return f"Follows up {issue.url}"


def new_issue(rng: random.Random, w: GitHubWorld) -> None:
    repo = rng.choice(w.repos)
    author = ME if rng.random() < 0.3 else rng.choice(OTHERS)
    assignees = {ME} if rng.random() < 0.6 else set(rng.sample(OTHERS, rng.randint(0, 1)))
    w.issues.append(Issue(repo, w.number(repo), title(rng), author, assignees, body=body(rng)))


def new_my_pr(rng: random.Random, w: GitHubWorld) -> None:
    repo = rng.choice(w.repos)
    pr = PullRequest(repo, w.number(repo), title(rng), ME, draft=rng.random() < 0.4)
    mine = [i for i in w.issues if ME in i.assignees or i.author == ME]
    refs = []
    if mine and rng.random() < 0.75:
        for issue in rng.sample(mine, min(len(mine), 1 if rng.random() < 0.85 else 2)):
            refs.append(ref_text(rng, pr, issue))
    pr.body = body(rng, "\n".join(refs))
    pr.commit_authors = [ME]
    w.prs.append(pr)


def new_other_pr(rng: random.Random, w: GitHubWorld) -> None:
    repo = rng.choice(w.repos)
    pr = PullRequest(repo, w.number(repo), title(rng), rng.choice(OTHERS), body=body(rng))
    pr.draft = rng.random() < 0.25
    pr.commit_authors = [pr.author] * rng.randint(1, 3)
    if rng.random() < 0.4:
        pr.requested.add(ME)
    if rng.random() < 0.15:
        pr.assignees.add(ME)
    if rng.random() < 0.15:
        pr.commit_authors.append(ME)
    w.prs.append(pr)


def pick(rng: random.Random, items: list[Any]) -> Any | None:
    return rng.choice(items) if items else None


def github_event(rng: random.Random, w: GitHubWorld) -> str:
    """Apply one random change on GitHub; return its name."""
    open_prs = [p for p in w.prs if p.state == "OPEN"]
    others_open = [p for p in open_prs if p.author != ME]
    events: list[tuple[str, float]] = [
        ("new_issue", 2),
        ("new_my_pr", 2),
        ("new_other_pr", 2),
        ("pr_ready", 2),
        ("pr_to_draft", 0.5),
        ("pr_merge", 2),
        ("pr_close", 0.7),
        ("pr_reopen", 0.3),
        ("issue_close", 1.5),
        ("issue_reopen", 0.3),
        ("review_submit", 1.5),
        ("review_request", 1),
        ("review_withdraw", 0.4),
        ("assign_issue", 0.5),
        ("unassign_issue", 0.5),
        ("refs_removed", 0.4),
        ("refs_added", 0.6),
        ("commit_pushed", 0.4),
    ]
    name = rng.choices([e for e, _ in events], [weight for _, weight in events])[0]
    if name == "new_issue":
        new_issue(rng, w)
    elif name == "new_my_pr":
        new_my_pr(rng, w)
    elif name == "new_other_pr":
        new_other_pr(rng, w)
    elif name == "pr_ready" and (pr := pick(rng, [p for p in open_prs if p.draft])):
        pr.draft = False
    elif name == "pr_to_draft" and (pr := pick(rng, [p for p in open_prs if not p.draft])):
        pr.draft = True
    elif name == "pr_merge" and (pr := pick(rng, [p for p in open_prs if not p.draft])):
        pr.state = "MERGED"
        if rng.random() < 0.8:  # GitHub closes the issues a merged pull request fixes
            for repo, number in pr.closes:
                for issue in w.issues:
                    if (issue.repo, issue.number) == (repo, number) and issue.state == "OPEN":
                        issue.state, issue.reason = "CLOSED", "COMPLETED"
    elif name == "pr_close" and (pr := pick(rng, open_prs)):
        pr.state = "CLOSED"
    elif name == "pr_reopen" and (pr := pick(rng, [p for p in w.prs if p.state == "CLOSED"])):
        pr.state = "OPEN"
    elif name == "issue_close" and (i := pick(rng, [i for i in w.issues if i.state == "OPEN"])):
        i.state = "CLOSED"
        i.reason = rng.choices(("COMPLETED", "NOT_PLANNED", "DUPLICATE"), (6, 3, 1))[0]
    elif name == "issue_reopen" and (i := pick(rng, [i for i in w.issues if i.state != "OPEN"])):
        i.state, i.reason = "OPEN", "REOPENED"
    elif name == "review_submit" and (
        pr := pick(rng, [p for p in others_open if ME in p.requested])
    ):
        pr.requested.discard(ME)
        pr.reviewed_by.add(ME)
    elif name == "review_request" and (pr := pick(rng, others_open)):
        pr.requested.add(ME)
    elif name == "review_withdraw" and (pr := pick(rng, [p for p in w.prs if ME in p.requested])):
        pr.requested.discard(ME)
    elif name == "assign_issue" and (
        i := pick(rng, [i for i in w.issues if ME not in i.assignees])
    ):
        i.assignees.add(ME)
    elif name == "unassign_issue" and (i := pick(rng, [i for i in w.issues if ME in i.assignees])):
        i.assignees.discard(ME)
    elif name == "refs_removed" and (pr := pick(rng, [p for p in w.prs if p.author == ME])):
        pr.closes.clear()
        pr.body = body(rng)
    elif name == "refs_added" and (pr := pick(rng, [p for p in open_prs if p.author == ME])):
        issues = [i for i in w.issues if ME in i.assignees or i.author == ME]
        if issue := pick(rng, issues):
            pr.body += "\n" + ref_text(rng, pr, issue)
    elif name == "commit_pushed" and (pr := pick(rng, others_open)):
        pr.commit_authors.append(ME)
    else:
        return "none"
    return name


@dataclass
class UserState:
    """What the user did in Linear, so the checks know what to expect."""

    edited: set[str] = field(default_factory=set)  # issues whose status the user moved
    moved_now: dict[str, str] = field(default_factory=dict)  # this step: issue -> status
    orphans: set[str] = field(default_factory=set)  # created, but a failure lost their links
    foreign: dict[str, tuple[str, str]] = field(default_factory=dict)  # attachment -> key, value


def linear_event(
    rng: random.Random, w: GitHubWorld, lin: MemoryLinear, user: UserState, skipped: list[str]
) -> str:
    """Apply one random change by the user, or a teammate, in Linear; return its name."""
    mine = [i for i in lin.issues.values() if i.assignee == VIEWER]
    managed = [i for i in mine if i.attachments]
    urls = [i.url for i in w.issues] + [p.url for p in w.prs]
    homed = {a.url for i in mine for a in i.attachments}
    events = [
        ("move_status", 4),
        ("hand_off", 0.6),
        ("mention", 1.2),
        ("hand_link", 0.8),
        ("teammate_link", 0.8),
        ("foreign_metadata", 0.8),
    ]
    name = rng.choices([e for e, _ in events], [weight for _, weight in events])[0]
    if name == "move_status" and (issue := pick(rng, managed)):
        issue.state = rng.choice([s for s in STATE_TYPES if s != issue.state])
        user.edited.add(issue.id)
        user.moved_now[issue.id] = issue.state
    elif name == "hand_off" and (issue := pick(rng, managed)):
        issue.assignee = TEAMMATE
    elif name == "mention" and urls:
        url = rng.choice(urls)
        number = int(url.rsplit("/", 1)[1])
        if rng.random() < 0.5:
            lin.add_issue("Follow up", f"Context: {url}", rng.choice(list(STATE_TYPES)), VIEWER)
        else:
            lin.add_issue(f"Fix the thing #{number}", "", "Todo", VIEWER)
    elif name == "hand_link":
        # Resolve a skipped item, or link any item without a home, to an unlinked issue.
        url = pick(rng, skipped) or pick(rng, [u for u in urls if u not in homed])
        target = pick(rng, [i for i in mine if not i.attachments])
        if url and target and url not in homed:
            target.attachments.append(MemoryAttachment(lin.new_id("att"), url, "manual", None, {}))
        else:
            return "none"
    elif name == "teammate_link" and urls:
        meta = {"ferrygl_v": 1, "ferrygl_state": "issue:open"} if rng.random() < 0.5 else {}
        issue_id = lin.add_issue("Teammate's copy", "", "Todo", TEAMMATE)
        attachment = MemoryAttachment(lin.new_id("att"), rng.choice(urls), "PR", None, meta)
        lin.issues[issue_id].attachments.append(attachment)
    elif name == "foreign_metadata" and managed:
        attachment = rng.choice(rng.choice(managed).attachments)
        attachment.metadata["team_note"] = "kept"
        user.foreign[attachment.id] = ("team_note", "kept")
    else:
        return "none"
    return name


def random_config(rng: random.Random, repos: tuple[str, ...]) -> Config:
    flags = {
        name: rng.random() < 0.85
        for name in (
            "issues_assigned",
            "issues_created",
            "prs_created",
            "prs_assigned",
            "prs_reviewed",
            "review_requests",
            "prs_with_commits",
        )
    }
    flags["prs_created"] = flags["prs_created"] or rng.random() < 0.5
    sync = SyncOptions(**flags, comment=rng.random() < 0.5)
    return Config(routes=(Route(repos, "ENG"),), sync=sync)


# Runs and checks


class Recorder:
    """Wraps ferryGL's planner to keep the changes of the last run."""

    def __init__(self) -> None:
        self.changes: list[Change] = []
        self.inputs: list[PlanInput] = []

    def __call__(self, inp: PlanInput) -> list[Change]:
        changes = real_plan(inp)
        self.changes.extend(changes)
        self.inputs.append(inp)
        return changes


def check_order(t: Tally, inputs: list[PlanInput]) -> None:
    """The plan doesn't depend on the order in which Linear returns links or issues."""
    for inp in inputs:
        flipped = replace(
            inp,
            attachments={url: list(reversed(hits)) for url, hits in inp.attachments.items()},
            candidates=list(reversed(inp.candidates)),
        )
        if flipped == inp:
            continue
        a, b = Counter(map(_summary, real_plan(inp))), Counter(map(_summary, real_plan(flipped)))
        t.check("order_independent", a == b, f"{sorted(a - b)} vs {sorted(b - a)}")


def _summary(change: Change) -> tuple:
    issue = change.issue.identifier if change.issue else None
    links = tuple(sorted(link.url for link in change.links))
    return change.action.value, change.item.label, issue, change.status_to, links


@dataclass
class Tally:
    checks: Counter[str] = field(default_factory=Counter)
    violations: Counter[str] = field(default_factory=Counter)
    examples: dict[str, str] = field(default_factory=dict)
    actions: Counter[str] = field(default_factory=Counter)
    events: Counter[str] = field(default_factory=Counter)
    runs: int = 0
    writes: int = 0
    faults: int = 0
    failed_runs: int = 0
    orphans: int = 0  # issues created whose links a failure lost
    orphans_adopted: int = 0
    multi_riders: int = 0  # pull requests linked as riders from several Linear issues

    def check(self, prop: str, ok: bool, detail: str = "") -> None:
        self.checks[prop] += 1
        if not ok:
            self.violations[prop] += 1
            self.examples.setdefault(prop, detail)


def run(config: Config, lin: MemoryLinear, gh: FakeGitHub, apply: bool, rec: Recorder) -> int:
    rec.changes, rec.inputs = [], []
    sync_module.plan = rec
    try:
        return sync_module.run_sync(
            config,
            apply=apply,
            linear=lin,
            github_for=lambda user: gh,
            out=io.StringIO(),
            err=io.StringIO(),
            now="2026-10-19T09:00:00+00:00",
        )
    finally:
        sync_module.plan = real_plan


def snapshot(lin: MemoryLinear) -> dict[str, dict[str, Any]]:
    """Each issue's assignee, status and recorded GitHub state per link URL."""
    return {
        i.id: {
            "assignee": i.assignee,
            "state": i.state,
            "recorded": {a.url: a.metadata.get("ferrygl_state") for a in i.attachments},
        }
        for i in lin.issues.values()
    }


def check_writes(t: Tally, writes, before, faulty: bool) -> None:
    """Properties of the writes one run made."""
    links_by_issue: dict[str, list] = {}
    for write in writes:
        if write.op in ("attachmentCreate", "attachmentUpdate"):
            links_by_issue.setdefault(write.issue_id, []).append(write)
    for write in writes:
        owner = write.payload.get("assignee_id") if write.op == "issueCreate" else write.assignee
        t.check("ownership", owner == VIEWER, f"{write.op} on an issue assigned to {owner}")
        text = json.dumps(write.payload)
        leaked = [s for s in (SECRET, *OTHERS) if s in text]
        if write.op == "commentCreate" and TITLE_WORD in text:
            leaked.append("a GitHub title")
        t.check("minimal_data", not leaked, f"{write.op} carried {leaked}")
        if write.op != "issueUpdate" or faulty:
            continue
        issue_before = before[write.issue_id]
        new_state = write.payload["state"]
        changed = closing = False
        for link in links_by_issue.get(write.issue_id, []):
            url = link.payload["url"]
            previous = issue_before["recorded"].get(url)
            new = link.payload["metadata"].get("ferrygl_state")
            changed |= previous is not None and new is not None and new != previous
            closing |= (
                previous is None
                and new is not None
                and STATE_TYPES[new_state] in CLOSED_TYPES
                and STATE_TYPES[issue_before["state"]] not in CLOSED_TYPES
            )
        t.check(
            "status_source",
            changed or closing,
            f"{issue_before['state']} -> {new_state} without a new recorded state",
        )


def check_edits(t: Tally, lin: MemoryLinear, moved: dict[str, str], writes, before) -> None:
    """After a step in which only Linear changed, the statuses the user set are still set,
    unless ferryGL closed the issue on the first sync of a new link."""
    for issue_id, status in moved.items():
        issue = lin.issues[issue_id]
        if issue.assignee != VIEWER:
            continue
        first_sync = any(
            w.issue_id == issue_id
            and w.op in ("attachmentCreate", "attachmentUpdate")
            and w.payload["metadata"].get("ferrygl_state")
            and before[issue_id]["recorded"].get(w.payload["url"]) is None
            for w in writes
        )
        closed = STATE_TYPES[issue.state] in CLOSED_TYPES and first_sync
        t.check(
            "edits_win",
            issue.state == status or closed,
            f"{issue.identifier}: the user set {status}, ferryGL set {issue.state}",
        )


def check_state(t: Tally, cfg, lin: MemoryLinear, gh: FakeGitHub, rec: Recorder, user) -> None:
    """Properties of the Linear workspace after a quiescent run."""
    mine = [i for i in lin.issues.values() if i.assignee == VIEWER]
    homes: dict[str, list[str]] = {}  # links that aren't a riding pull request's
    riders: dict[str, list[str]] = {}
    for issue in mine:
        for attachment in issue.attachments:
            kind = riders if "ferrygl_linked_to" in attachment.metadata else homes
            kind.setdefault(attachment.url, []).append(issue.identifier)
    for url, identifiers in homes.items():
        t.check("one_home", len(identifiers) == 1, f"{url} is the home of {identifiers}")
    t.multi_riders += sum(len(set(ids)) > 1 for ids in riders.values())

    items = fetch_items(gh, cfg.routes[0], ME, cfg.sync)
    skipped = {c.item.key for c in rec.changes if c.action is Action.SKIP}
    for item in items:
        # A pull request that rides with a skipped issue waits for it.
        if item.key not in skipped and not {r.key for r in item.references} & skipped:
            linked = item.url in homes or item.url in riders
            t.check("coverage", linked, f"{item.label} has no Linear issue")

    by_url = {a.url: (i, a) for i in mine for a in i.attachments}
    for item in items:
        if item.kind is Kind.ISSUE or item.url not in by_url:
            continue
        issue, attachment = by_url[item.url]
        recorded = [a for a in issue.attachments if "ferrygl_state" in a.metadata]
        if "ferrygl_state" not in attachment.metadata or len(recorded) != 1:
            continue
        key = github_state(item).key
        t.check(
            "consistent",
            attachment.metadata["ferrygl_state"] == key,
            f"{item.label} records {attachment.metadata['ferrygl_state']}, GitHub is {key}",
        )
        if issue.created_for == item.url and issue.id not in user.edited | user.orphans:
            t.check(
                "consistent",
                issue.state == EXPECTED[key],
                f"{item.label} is {key} but {issue.identifier} is {issue.state}",
            )

    for issue in lin.issues.values():
        for attachment in issue.attachments:
            if attachment.id in user.foreign:
                key, value = user.foreign[attachment.id]
                t.check(
                    "foreign_metadata",
                    attachment.metadata.get(key) == value,
                    f"{key} lost from a link on {issue.identifier}",
                )


def scenario(seed: int, steps: int, fault_rate: float, t: Tally) -> None:
    rng = random.Random(seed)
    repos = ("acme/api",) if rng.random() < 0.5 else ("acme/api", "acme/web")
    world = GitHubWorld(repos)
    lin = MemoryLinear()
    gh = FakeGitHub(world)
    cfg = random_config(rng, repos)
    user = UserState()
    rec = Recorder()
    for _ in range(rng.randint(3, 10)):
        t.events[github_event(rng, world)] += 1

    for step in range(steps):
        linear_only = step > 0 and rng.random() < 0.3
        if step > 0 and not linear_only:
            for _ in range(rng.randint(1, 4)):
                t.events[github_event(rng, world)] += 1
        if step > 0:
            skipped = [c.item.url for c in rec.changes if c.action is Action.SKIP]
            for _ in range(rng.randint(1 if linear_only else 0, 2)):
                t.events[linear_event(rng, world, lin, user, skipped)] += 1

        # A preview writes nothing.
        count = step_start = len(lin.writes)
        step_before = snapshot(lin)
        run(cfg, lin, gh, False, rec)
        t.runs += 1
        t.check("dry_run", len(lin.writes) == count, "a preview wrote to Linear")
        check_order(t, rec.inputs)

        # Apply, with some writes failing.
        before = snapshot(lin)
        lin.faults = Faults(rng, fault_rate)
        status = run(cfg, lin, gh, True, rec)
        injected = lin.faults.injected
        lin.faults = None
        t.runs += 1
        t.faults += injected
        t.failed_runs += status != 0
        t.actions.update(c.action.value for c in rec.changes)
        new_writes = lin.writes[count:]
        check_writes(t, new_writes, before, faulty=injected > 0)

        if injected:
            # One clean run recovers: it adopts issues whose links weren't written.
            orphans = {i.id for i in lin.issues.values() if i.created_for and not i.attachments}
            t.orphans += len(orphans - user.orphans)
            user.orphans |= orphans
            before = snapshot(lin)
            count = len(lin.writes)
            status = run(cfg, lin, gh, True, rec)
            t.runs += 1
            t.check("recovery", status == 0, "the recovery run failed")
            check_writes(t, lin.writes[count:], before, faulty=False)
            adopted = {i for i in orphans if lin.issues[i].attachments}
            t.orphans_adopted += len(adopted)

        # Nothing new: a rerun writes nothing.
        count = len(lin.writes)
        run(cfg, lin, gh, True, rec)
        t.runs += 1
        quiet = len(lin.writes) == count
        t.check("quiescent", quiet, f"rerun wrote {[w.op for w in lin.writes[count:]]}")
        if injected:
            t.check("recovery", quiet, "not converged after one clean run")
        check_state(t, cfg, lin, gh, rec, user)
        if linear_only:
            check_edits(t, lin, user.moved_now, lin.writes[step_start:], step_before)
        user.moved_now = {}
    t.writes += len(lin.writes)


def latex(summary: dict[str, Any]) -> str:
    """The results as LaTeX macros for the paper, such as ``\\SimRuns``."""

    def name(key: str) -> str:
        return "Sim" + "".join(part.capitalize() for part in key.split("_"))

    lines = ["% Generated by evaluation/simulate.py from the same run as simulation.json."]
    numbers = {k: v for k, v in summary.items() if isinstance(v, int | float)}
    numbers |= {f"action_{k}": v for k, v in summary["actions"].items()}
    for row in summary["properties"]:
        numbers[f"checks_{row['property']}"] = row["checks"]
        numbers[f"violations_{row['property']}"] = row["violations"]
    for key, value in numbers.items():
        if key == "fault_rate":
            text = f"{value * 100:g}\\,\\%"
        elif key == "seed":
            text = str(value)
        else:
            text = f"\\num{{{value}}}"
        lines.append(f"\\newcommand{{\\{name(key)}}}{{{text}}}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenarios", type=int, default=2000)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--fault-rate", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "data")
    args = parser.parse_args(argv)

    t = Tally()
    for n in range(args.scenarios):
        scenario(args.seed + n, args.steps, args.fault_rate, t)

    rows = [
        {
            "property": prop,
            "statement": statement,
            "checks": t.checks[prop],
            "violations": t.violations[prop],
            "example": t.examples.get(prop, ""),
        }
        for prop, statement in PROPERTIES.items()
    ]
    events = Counter({name: n for name, n in t.events.items() if name != "none"})
    summary = {
        "scenarios": args.scenarios,
        "steps": args.steps,
        "fault_rate": args.fault_rate,
        "seed": args.seed,
        "runs": t.runs,
        "writes": t.writes,
        "events": events.total(),
        "checks": t.checks.total(),
        "safety_checks": t.checks.total() - t.checks["order_independent"],
        "faults_injected": t.faults,
        "runs_with_failed_writes": t.failed_runs,
        "orphans": t.orphans,
        "orphans_adopted": t.orphans_adopted,
        "multi_rider_observations": t.multi_riders,
        "actions": dict(t.actions.most_common()),
        "event_counts": dict(events.most_common()),
        "properties": rows,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "simulation.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.out / "simulation.tex").write_text(latex(summary))
    for row in rows:
        mark = "ok  " if row["violations"] == 0 else "FAIL"
        print(f"{mark} {row['checks']:>9,} checks  {row['property']:<17} {row['example']}")
    print(f"{t.runs:,} runs, {t.writes:,} writes, {t.faults:,} injected faults")
    print("actions:", dict(t.actions.most_common()))
    return 0 if not t.violations else 1


if __name__ == "__main__":
    sys.exit(main())
