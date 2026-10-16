"""Run ``check`` and ``sync``: read GitHub, plan, print, and optionally apply to Linear."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TextIO

from ferrygl.config import Config, Route
from ferrygl.github import GitHubClient, GitHubError, fetch_items
from ferrygl.linear import LinearClient, LinearError, State, Team
from ferrygl.model import Action, Change
from ferrygl.plan import PlanInput, plan

GitHubFactory = Callable[[str | None], GitHubClient]

EXIT_OK, EXIT_FAILED, EXIT_SETUP = 0, 1, 2


@dataclass
class RouteTarget:
    """A route with its Linear team, project and label resolved."""

    number: int
    route: Route
    team: Team | None = None
    project_id: str | None = None
    label_id: str | None = None
    found: list[str] = field(default_factory=list)  # what checked out, for `check`
    problems: list[str] = field(default_factory=list)


def resolve_target(
    number: int,
    route: Route,
    config: Config,
    linear: LinearClient,
    states: dict[str, dict[str, State]],
) -> RouteTarget:
    """Look up the route's Linear team, states, project and label, noting what's missing."""
    target = RouteTarget(number, route)
    team = linear.find_team(route.team)
    if team is None:
        target.problems.append(f"Linear team {route.team!r} not found")
        return target
    target.team = team
    target.found.append(f"team {team.key} ({team.name})")
    if team.id not in states:
        states[team.id] = linear.team_states(team.id)
    missing = [name for name in config.status.all() if name not in states[team.id]]
    if missing:
        target.problems.append(
            f"team {team.key} has no state named {', '.join(map(repr, missing))}; "
            "set [status] to your team's state names"
        )
    else:
        target.found.append("states " + ", ".join(config.status.all()))
    if route.project:
        target.project_id = linear.find_project(route.project, team.id)
        if target.project_id is None:
            target.problems.append(f"project {route.project!r} not found for team {team.key}")
        else:
            target.found.append(f"project {route.project}")
    if config.sync.label:
        target.label_id = linear.find_label(config.sync.label, team.id)
        if target.label_id is None:
            target.problems.append(
                f"label {config.sync.label!r} not found in team {team.key} or the workspace; "
                "create it in Linear or remove `label`"
            )
        else:
            target.found.append(f"label {config.sync.label}")
    return target


def run_check(
    config: Config, *, linear: LinearClient, github_for: GitHubFactory, out: TextIO
) -> int:
    """Print whether credentials, repositories and Linear settings work. Writes nothing."""
    ok = True

    def line(good: bool, label: str, detail: str, indent: str = "  ") -> None:
        nonlocal ok
        ok = ok and good
        print(f"{indent}{'✓' if good else '✗'} {label:<7} {detail}", file=out)

    try:
        _, name = linear.viewer()
    except LinearError as exc:
        line(False, "Linear", str(exc), indent="")
        return EXIT_FAILED
    line(True, "Linear", f"signed in as {name}", indent="")

    states: dict[str, dict[str, State]] = {}
    for number, route in enumerate(config.routes, 1):
        print(f"route {number}: {route.name}", file=out)
        try:
            github = github_for(route.github_user)
            line(True, "GitHub", f"reading as {github.viewer_login()}")
            for repo in route.repos:
                readable = github.can_read(repo)
                line(readable, "repo", repo if readable else f"{repo}: not found with this token")
        except GitHubError as exc:
            line(False, "GitHub", str(exc))
        try:
            target = resolve_target(number, route, config, linear, states)
        except LinearError as exc:
            line(False, "Linear", str(exc))
            continue
        for found in target.found:
            line(True, "Linear", found)
        for problem in target.problems:
            line(False, "Linear", problem)
    return EXIT_OK if ok else EXIT_FAILED


def run_sync(
    config: Config,
    *,
    apply: bool,
    linear: LinearClient,
    github_for: GitHubFactory,
    out: TextIO,
    err: TextIO,
    now: str,
) -> int:
    """Plan every route and print the changes; with ``apply``, make them in Linear."""
    states: dict[str, dict[str, State]] = {}
    try:
        viewer_id, _ = linear.viewer()
        targets = [
            resolve_target(n, route, config, linear, states)
            for n, route in enumerate(config.routes, 1)
        ]
    except LinearError as exc:
        print(f"error: {exc}", file=err)
        return EXIT_SETUP
    problems = [f"route {t.number}: {p}" for t in targets for p in t.problems]
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=err)
        print("Nothing changed. Run `ferrygl check` for details.", file=err)
        return EXIT_SETUP

    failures = 0
    counts: Counter[str] = Counter()
    for target in targets:
        try:
            changes = _plan_route(target, config, viewer_id, linear, github_for, states, now)
        except (GitHubError, LinearError) as exc:
            print(f"error: route {target.number} ({target.route.name}): {exc}", file=err)
            failures += 1
            continue
        _print_changes(changes, out)
        counts.update(change.action.value for change in changes)
        if apply:
            failures += _apply_all(changes, target, viewer_id, linear, states, out, err)

    _print_summary(counts, apply, failures, out)
    return EXIT_FAILED if failures else EXIT_OK


def _plan_route(
    target: RouteTarget,
    config: Config,
    viewer_id: str,
    linear: LinearClient,
    github_for: GitHubFactory,
    states: dict[str, dict[str, State]],
    now: str,
) -> list[Change]:
    assert target.team is not None
    github = github_for(target.route.github_user)
    items = fetch_items(github, target.route, github.viewer_login(), config.sync)
    attachments = linear.attachments_for_urls(item.url for item in items)
    mine = {
        url
        for url, hits in attachments.items()
        if any(a.issue.assignee_id == viewer_id for a in hits)
    }
    candidates = []
    if any(item.url not in mine for item in items):
        candidates = linear.assigned_issues(target.team.id, viewer_id)
    for hits in attachments.values():
        for attachment in hits:
            team_id = attachment.issue.team_id
            if attachment.issue.assignee_id == viewer_id and team_id not in states:
                states[team_id] = linear.team_states(team_id)
    return plan(
        PlanInput(
            route=target.route,
            config=config,
            viewer_id=viewer_id,
            items=items,
            attachments=attachments,
            candidates=candidates,
            now=now,
        )
    )


def _apply_all(
    changes: Sequence[Change],
    target: RouteTarget,
    viewer_id: str,
    linear: LinearClient,
    states: dict[str, dict[str, State]],
    out: TextIO,
    err: TextIO,
) -> int:
    failures = 0
    for change in changes:
        try:
            created = _apply(change, target, viewer_id, linear, states)
        except LinearError as exc:
            failures += 1
            print(f"error: {change.item.label}: {change.action.value} failed: {exc}", file=err)
            continue
        if created:
            print(f"created {created} for {change.item.label}", file=out)
    return failures


def _apply(
    change: Change,
    target: RouteTarget,
    viewer_id: str,
    linear: LinearClient,
    states: dict[str, dict[str, State]],
) -> str | None:
    """Make one change. Returns the identifier of a created Linear issue."""
    if change.action is Action.SKIP:
        return None
    if change.action is Action.CREATE:
        assert target.team is not None and change.title and change.status_to
        issue = linear.create_issue(
            team_id=target.team.id,
            title=change.title,
            description=change.description or "",
            state_id=_state_id(states, target.team.id, change.status_to),
            assignee_id=viewer_id,
            project_id=target.project_id,
            label_ids=[target.label_id] if target.label_id else [],
        )
        for link in change.links:
            linear.create_attachment(issue.id, link)
        return issue.identifier

    assert change.issue is not None
    # Status first: if it fails, the GitHub state isn't recorded and the next run retries.
    if change.status_to:
        state_id = _state_id(states, change.issue.team_id, change.status_to)
        linear.update_issue_state(change.issue.id, state_id)
    for link in change.links:
        if link.attachment_id:
            linear.update_attachment(link)
        else:
            linear.create_attachment(change.issue.id, link)
    return None


def _state_id(states: dict[str, dict[str, State]], team_id: str, name: str) -> str:
    state = states.get(team_id, {}).get(name)
    if state is None:
        raise LinearError(f"the issue's team has no state named {name!r}")
    return state.id


def format_change(change: Change, width: int = 0) -> str:
    issue = change.issue.identifier if change.issue else ""
    moved = f"{change.status_from} -> {change.status_to}" if change.status_to else ""
    if change.action is Action.CREATE:
        detail = f'"{change.title}" -> {change.status_to}'
    elif change.action in (Action.ADOPT, Action.LINK):
        detail = f"-> {issue}" + (f" ({moved})" if moved else "")
    elif change.action is Action.UPDATE:
        detail = f"{issue} {moved}"
    elif change.action is Action.RECORD:
        detail = f"{issue} (GitHub state noted, status unchanged)"
    else:
        detail = change.note or ""
    kind, action = change.item.kind.value, change.action.value
    return f"{change.item.label:<{width}}  {kind:<8}  {action:<6}  {detail}"


def _print_changes(changes: Sequence[Change], out: TextIO) -> None:
    width = max((len(change.item.label) for change in changes), default=0)
    for change in changes:
        print(format_change(change, width), file=out)


def _print_summary(counts: Counter[str], applied: bool, failures: int, out: TextIO) -> None:
    total = sum(counts.values())
    if total == 0:
        print("No changes.", file=out)
        return
    order = [action.value for action in Action]
    parts = ", ".join(f"{counts[a]} {a}" for a in order if counts[a])
    noun = "change" if total == 1 else "changes"
    if not applied:
        print(f"{total} {noun} ({parts}). Run with --apply to make them.", file=out)
    elif failures:
        print(f"{total} {noun} ({parts}); {failures} failed, see the errors above.", file=out)
    else:
        print(f"Applied {total} {noun} ({parts}).", file=out)
