from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from test_github import issue_node
from test_sync import FakeGitHub, FakeLinear, make_config

from ferrygl.cli import main
from ferrygl.config import Route
from ferrygl.github import Activity, GitHubError
from ferrygl.sync import EXIT_FAILED, EXIT_OK, EXIT_SETUP
from ferrygl.watch import preflight, run_watch

API, WEB = Route(("acme/api",), "ENG"), Route(("acme/web",), "ENG")
RESET_AT = "2026-10-21T11:00:00Z"


class WatchedGitHub(FakeGitHub):
    """A GitHub whose repositories the test changes between checks."""

    def __init__(self, **kwargs) -> None:
        super().__init__(results={"assignee:": [issue_node(12)]}, **kwargs)
        self.edits: dict[str, int] = {}  # repository -> how many times something in it changed
        self.remaining = 5000
        self.unreachable = False
        self.stop_requested = False  # as `ferrygl watch --stop` would ask

    def activity(self, query):
        if self.unreachable:
            raise GitHubError("could not reach https://api.github.com/graphql")
        updates = {
            (f"https://github.com/{repo}/issues/1", str(count))
            for repo, count in self.edits.items()
            if f"repo:{repo}" in query
        }
        return Activity(frozenset(updates), self.remaining, 5000, RESET_AT)


class Stop(Exception):
    """Ends a watch once the test's checks are done."""


def watch(config, github, steps=(), linear=None, apply=False):
    """Run `watch`. Before each check, apply the next of ``steps`` to ``github``; after the
    last one, stop."""
    out, err = io.StringIO(), io.StringIO()
    pending = list(steps)

    def sleep(seconds):
        assert seconds == 60
        if not pending:
            raise Stop
        pending.pop(0)(github)

    try:
        code = run_watch(
            config,
            apply=apply,
            interval=60,
            linear=linear or FakeLinear(),
            github_for=lambda user: github,
            out=out,
            err=err,
            clock=lambda: datetime(2026, 10, 21, 10, 0, tzinfo=UTC),
            sleep=sleep,
            stopping=lambda: github.stop_requested,
        )
    except Stop:
        code = None
    return code, out.getvalue(), err.getvalue()


def events(out: str) -> list[str]:
    """The lines `watch` prints about each sync or wait, without their timestamp."""
    stamp = "2026-10-21T10:00:00+00:00  "
    return [line.removeprefix(stamp) for line in out.splitlines() if line.startswith(stamp)]


def change(repo):
    def step(github):
        github.edits[repo] = github.edits.get(repo, 0) + 1

    return step


def nothing(github):
    pass


def test_watch_syncs_every_route_then_a_route_that_changed_and_then_once_more():
    # Nothing happens on GitHub at the first check; then something changes in acme/web.
    code, out, _ = watch(
        make_config(API, WEB), WatchedGitHub(), [nothing, change("acme/web")] + [nothing] * 2
    )

    assert code is None  # still watching
    assert events(out) == [
        "syncing every route",
        "route 2 (acme/web -> ENG): changed on GitHub",
        "route 2 (acme/web -> ENG): syncing again, in case GitHub's search lagged",
    ]


def test_a_preview_watch_shows_the_changes_and_writes_nothing():
    linear = FakeLinear()

    _, out, _ = watch(make_config(API), WatchedGitHub(), [change("acme/api")], linear=linear)

    assert "Previewing: changes are shown, not made. Leave out --preview to make them." in out
    assert out.count('acme/api#12  issue     create  "Issue 12 #12" -> Todo') == 2
    assert out.count("1 change (1 create). Leave out --preview to make them.") == 2
    assert "--apply" not in out
    assert linear.writes == []


def test_watch_makes_the_changes():
    linear = FakeLinear()

    _, out, _ = watch(make_config(API), WatchedGitHub(), linear=linear, apply=True)

    assert "created ENG-101 for acme/api#12" in out
    assert [write[0] for write in linear.writes] == ["create_issue", "create_attachment"]


def test_a_failed_sync_is_tried_again_at_the_next_check():
    def break_github_and_change(github):
        github.fail = True
        change("acme/api")(github)

    def mend_github(github):
        github.fail = False

    code, out, err = watch(
        make_config(API), WatchedGitHub(), [break_github_and_change, mend_github, nothing]
    )

    assert code is None
    assert "error: route 1 (acme/api -> ENG): GitHub rejected the token" in err
    assert events(out) == [
        "syncing every route",
        "route 1 (acme/api -> ENG): changed on GitHub",
        "route 1 (acme/api -> ENG): changed on GitHub",
        "route 1 (acme/api -> ENG): syncing again, in case GitHub's search lagged",
    ]


def test_a_failed_check_is_reported_and_watching_goes_on():
    def lose_network(github):
        github.unreachable = True

    def regain_network_and_change(github):
        github.unreachable = False
        change("acme/api")(github)

    code, out, err = watch(
        make_config(API), WatchedGitHub(), [lose_network, regain_network_and_change]
    )

    assert code is None
    assert "error: route 1 (acme/api -> ENG): could not reach" in err
    assert events(out) == ["syncing every route", "route 1 (acme/api -> ENG): changed on GitHub"]


def test_watch_waits_for_the_rate_limit_to_reset_when_little_is_left():
    def change_with_little_left(github):
        change("acme/api")(github)
        github.remaining = 1000  # less than a quarter of 5,000

    def reset(github):
        github.remaining = 5000

    _, out, _ = watch(make_config(API), WatchedGitHub(), [change_with_little_left, nothing, reset])

    assert events(out) == [
        "syncing every route",
        f"route 1 (acme/api -> ENG): waiting until {RESET_AT}, when GitHub's rate limit resets; "
        "1000 of 5000 points are left",
        "route 1 (acme/api -> ENG): changed on GitHub",
    ]


def test_a_setup_problem_at_the_start_stops_watching_before_reading_github():
    class Unread(WatchedGitHub):
        def activity(self, query):
            raise AssertionError("GitHub should not be read")

        def search(self, query, with_commits=False):
            raise AssertionError("GitHub should not be read")

    code, _, err = watch(make_config(API), Unread(), linear=FakeLinear(states=["Todo"]))

    assert code == EXIT_SETUP
    assert "Nothing changed" in err


def test_the_interval_spares_the_github_rate_limit(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["watch", "--interval", "10"])

    assert exit.value.code == 2
    assert "--interval: must be at least 30 seconds" in capsys.readouterr().err


def test_preflight_finds_problems_before_going_to_the_background():
    unreachable = WatchedGitHub()
    unreachable.unreachable = True
    err = io.StringIO()

    def check(linear, github):
        return preflight(make_config(API), linear=linear, github_for=lambda u: github, err=err)

    assert check(FakeLinear(), WatchedGitHub()) == EXIT_OK
    assert check(FakeLinear(states=["Todo"]), WatchedGitHub()) == EXIT_SETUP
    assert check(FakeLinear(), unreachable) == EXIT_FAILED
    assert "error: route 1 (acme/api -> ENG): could not reach" in err.getvalue()
    assert "Not started." in err.getvalue()


def test_a_watch_asked_to_stop_while_it_waits_stops_before_its_next_check():
    def ask_to_stop(github):
        github.stop_requested = True
        change("acme/api")(github)

    code, out, _ = watch(make_config(API), WatchedGitHub(), [ask_to_stop])

    assert code == EXIT_OK
    assert events(out) == ["syncing every route", "stopped, as asked"]


def test_a_watch_asked_to_stop_during_a_sync_finishes_the_sync_first():
    class AskedDuringSync(WatchedGitHub):
        def search(self, query, with_commits=False):
            self.stop_requested = True
            return super().search(query, with_commits)

    linear = FakeLinear()

    # The first sync's searches ask it to stop.
    code, out, _ = watch(make_config(API), AskedDuringSync(), linear=linear, apply=True)

    assert code == EXIT_OK
    assert events(out) == ["syncing every route", "stopped, as asked"]
    assert "Applied 1 change (1 create)." in out
    assert [write[0] for write in linear.writes] == ["create_issue", "create_attachment"]
