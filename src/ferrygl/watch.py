"""Run ``watch``: sync, then check GitHub at an interval and sync each route that changed.

GitHub reports changes as they happen only through webhooks, which need admin access to the
repository and would be a write to it, so ``watch`` polls instead. A check is one small
search per route, for the most recently updated issues and pull requests in its repositories.
A route syncs again only when those differ from what it saw before its last sync.

Starting a watch in the background, listing and stopping watches are in ``jobs.py``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TextIO

from ferrygl.config import Config, Route
from ferrygl.github import Activity, GitHubError, latest_activity
from ferrygl.linear import LinearClient
from ferrygl.sync import EXIT_FAILED, EXIT_OK, EXIT_SETUP, GitHubFactory, resolve_setup, run_sync

DEFAULT_INTERVAL = 60  # seconds between checks
MIN_INTERVAL = 30
# The share of the hourly GitHub rate limit that `watch` leaves for your other tools, which
# draw on the same points.
RESERVE = 0.25
PREVIEW_HINT = "Leave out --preview to make them."


def run_watch(
    config: Config,
    *,
    apply: bool,
    interval: int,
    linear: LinearClient,
    github_for: GitHubFactory,
    out: TextIO,
    err: TextIO,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    stopping: Callable[[], bool] = lambda: False,
) -> int:
    """Sync every route, then sync each route again whenever its repositories change.

    Runs until interrupted, or until ``stopping()``, which it asks between syncs, so that it
    never stops halfway through one. Returns ``EXIT_OK`` once stopped, or ``EXIT_SETUP``,
    having changed nothing, on a setup problem at the start.
    """
    # As with `sync`, a setup problem stops everything before GitHub is read.
    if resolve_setup(config, linear, err) is None:
        return EXIT_SETUP
    count = len(config.routes)
    print(
        f"Watching {count} {'route' if count == 1 else 'routes'}, checking GitHub every "
        f"{interval} seconds. To stop it: Ctrl-C, or ferrygl watch --stop",
        file=out,
    )
    if not apply:
        print(f"Previewing: changes are shown, not made. {PREVIEW_HINT}", file=out)

    def sync(routes: set[int] | None, why: str) -> int:
        now = clock().isoformat(timespec="seconds")
        print(f"{now}  {why}", file=out)
        return run_sync(
            config,
            apply=apply,
            linear=linear,
            github_for=github_for,
            out=out,
            err=err,
            now=now,
            routes=routes,
            hint=PREVIEW_HINT,
        )

    # Checked before syncing, so that a change made during a sync shows at the next check.
    first = {n: _check(n, route, github_for, err) for n, route in enumerate(config.routes, 1)}
    code = sync(None, "syncing every route")
    if code == EXIT_SETUP:
        return code
    # Route number -> the latest updates checked before its last successful sync.
    synced = {n: a.updates for n, a in first.items() if a} if code == EXIT_OK else {}
    # Routes to sync once more, because GitHub's search can take a moment to show a change
    # that a check has already seen.
    recheck: set[int] = set()
    waiting: set[int] = set()  # routes waiting for the GitHub rate limit to reset

    while not stopping():
        # Flushed so that a log shows each check as it happens.
        out.flush()
        err.flush()
        sleep(interval)
        for number, route in enumerate(config.routes, 1):
            if stopping():
                break
            activity = _check(number, route, github_for, err)
            if activity is None:
                continue
            changed = activity.updates != synced.get(number)
            if not changed and number not in recheck:
                continue
            label = f"route {number} ({route.name})"
            if activity.remaining < activity.limit * RESERVE:
                if number not in waiting:
                    waiting.add(number)
                    now = clock().isoformat(timespec="seconds")
                    print(
                        f"{now}  {label}: waiting until {activity.reset_at}, when GitHub's "
                        f"rate limit resets; {activity.remaining} of {activity.limit} points "
                        "are left",
                        file=out,
                    )
                continue
            waiting.discard(number)
            why = (
                "changed on GitHub" if changed else "syncing again, in case GitHub's search lagged"
            )
            if sync({number}, f"{label}: {why}") == EXIT_OK:
                synced[number] = activity.updates
                if changed:
                    recheck.add(number)
                else:
                    recheck.discard(number)

    print(f"{clock().isoformat(timespec='seconds')}  stopped, as asked", file=out)
    out.flush()
    return EXIT_OK


def preflight(
    config: Config, *, linear: LinearClient, github_for: GitHubFactory, err: TextIO
) -> int:
    """Check, before going to the background, what a background watch would otherwise only
    report in its log: the Linear setup, and that GitHub can be read for every route."""
    if resolve_setup(config, linear, err) is None:
        return EXIT_SETUP
    checks = [_check(n, route, github_for, err) for n, route in enumerate(config.routes, 1)]
    if not all(checks):
        print("Not started. Run `ferrygl check` for details.", file=err)
        return EXIT_FAILED
    return EXIT_OK


def _check(number: int, route: Route, github_for: GitHubFactory, err: TextIO) -> Activity | None:
    """The route's latest updates on GitHub, or None, reported, if GitHub can't be read."""
    try:
        return latest_activity(github_for(route.github_user), route)
    except GitHubError as exc:
        print(f"error: route {number} ({route.name}): {exc}", file=err)
        return None
