from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import ferrygl.cli
from ferrygl.cli import main
from ferrygl.jobs import (
    SUPPORTED,
    Job,
    JobError,
    Jobs,
    background_command,
    format_jobs,
    last_lines,
    start_in_background,
    state_dir,
    stop,
)
from ferrygl.sync import EXIT_FAILED, EXIT_OK, EXIT_SETUP

posix_only = pytest.mark.skipif(not SUPPORTED, reason="needs Linux or macOS")


def job(config: Path | str, pid: int | None = None, started="2026-10-23T09:00:00+00:00", **kw):
    return Job(
        pid=pid or os.getpid(),
        config=str(config),
        apply=kw.get("apply", True),
        interval=kw.get("interval", 60),
        started=started,
        log=kw.get("log"),
    )


def test_locks_and_logs_live_in_the_state_directory():
    assert state_dir({"XDG_STATE_HOME": "/srv/state"}) == Path("/srv/state/ferrygl")
    assert state_dir({}) == Path("~/.local/state/ferrygl").expanduser()


def test_each_config_file_has_its_own_lock_and_log(tmp_path: Path):
    jobs = Jobs(tmp_path)

    assert jobs.lock(Path("/home/alice/acme.toml")).name.startswith("watch-acme-")
    assert jobs.lock(Path("/home/alice/acme.toml")) != jobs.lock(Path("/srv/acme.toml"))
    assert jobs.log(Path("/srv/acme.toml")).suffix == ".log"


# Running watches


@posix_only
def test_one_watch_per_config_file_and_the_list_shows_each_until_it_ends(tmp_path: Path):
    jobs = Jobs(tmp_path / "state")
    acme = job("/home/alice/acme.toml", started="2026-10-23T09:00:00+00:00")
    beta = job("/home/alice/beta.toml", started="2026-10-23T08:00:00+00:00", log="/x/b.log")

    with jobs.hold(acme):
        again = pytest.raises(
            JobError,
            match=f"already watching /home/alice/acme.toml, as watch {acme.pid}. "
            f"Stop it first: ferrygl watch --stop {acme.pid}",
        )
        with again, jobs.hold(acme):
            pass
        with jobs.hold(beta):  # another config file watches alongside
            assert jobs.running() == [beta, acme]  # oldest first
            assert jobs.is_running(acme)
        assert jobs.running() == [acme]

    assert jobs.running() == []  # its lock file stays, but nobody holds it


@posix_only
def test_locks_and_logs_are_readable_by_you_alone(tmp_path: Path):
    jobs = Jobs(tmp_path / "state" / "ferrygl")
    config = tmp_path / "acme.toml"

    with jobs.hold(job(config, started="2026-10-23T09:00:00+00:00")):
        pass
    again = job(config, started="2026-10-23T10:00:00+00:00")
    with jobs.hold(again):  # the lock file now holds this watch alone
        assert jobs.running() == [again]
    start(tmp_path, log=jobs.log(config))

    assert jobs.directory.stat().st_mode & 0o777 == 0o700
    assert jobs.lock(config).stat().st_mode & 0o777 == 0o600
    assert jobs.log(config).stat().st_mode & 0o777 == 0o600


def test_the_list_is_a_table_with_ids():
    lines = format_jobs(
        [
            job("/home/alice/acme.toml", pid=12345, log="/state/watch-acme.log"),
            job("/home/alice/beta.toml", pid=678, apply=False, interval=120),
        ]
    )

    assert lines == [
        "ID     STARTED                    RUNS IN     MODE     EVERY  CONFIG",
        "12345  2026-10-23T09:00:00+00:00  background  apply    60s    /home/alice/acme.toml",
        "678    2026-10-23T09:00:00+00:00  foreground  preview  120s   /home/alice/beta.toml",
    ]


CHILD = """
import os, sys
from pathlib import Path
from ferrygl.jobs import Job, Jobs, StopRequest

stopping = StopRequest()
job = Job(os.getpid(), sys.argv[2], True, 60, "2026-10-23T09:00:00+00:00")
with Jobs(Path(sys.argv[1])).hold(job):
    print("watching", flush=True)
    while not stopping():
        stopping.sleep(60)
    print("stopped between syncs", flush=True)
"""


@pytest.fixture
def child(tmp_path: Path):
    """A real process registered as a watch of ``acme.toml``, which stops when asked."""
    state, config = tmp_path / "state" / "ferrygl", str((tmp_path / "acme.toml").resolve())
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(state), config], stdout=subprocess.PIPE, text=True
    )
    assert process.stdout.readline() == "watching\n"
    yield process, Jobs(state), config
    process.kill()
    process.wait()


@posix_only
def test_stop_asks_a_watch_to_stop_and_waits_until_it_has(child):
    process, jobs, config = child
    [running] = jobs.running()
    assert (running.pid, running.config) == (process.pid, config)

    began = time.monotonic()
    assert stop(running, jobs) is True

    assert time.monotonic() - began < 5
    assert process.wait(timeout=5) == 0
    assert process.stdout.read() == "stopped between syncs\n"
    assert jobs.running() == []


@posix_only
def test_stop_says_when_a_watch_is_still_finishing_its_sync(tmp_path: Path):
    jobs = Jobs(tmp_path)
    busy = job(tmp_path / "acme.toml")

    with jobs.hold(busy):
        assert stop(busy, jobs, wait=0.2, kill=lambda pid, sig: None) is False


def test_a_watch_that_already_ended_counts_as_stopped(tmp_path: Path):
    def gone(pid, sig):
        raise ProcessLookupError

    assert stop(job(tmp_path / "acme.toml"), Jobs(tmp_path), kill=gone) is True


def test_last_lines_of_a_log(tmp_path: Path):
    log = tmp_path / "watch.log"
    log.write_text("".join(f"line {n}\n" for n in range(1, 31)), encoding="utf-8")

    assert last_lines(log) == [f"line {n}" for n in range(11, 31)]


# Starting in the background


def test_the_background_watch_runs_with_the_same_options_and_no_credentials():
    command = background_command(
        Path("ferrygl.toml"), apply=True, interval=90, log=Path("/state/w.log")
    )

    assert command == [
        sys.executable,
        "-u",
        "-m",
        "ferrygl",
        "watch",
        "--config",
        str(Path("ferrygl.toml").resolve()),
        "--interval",
        "90",
        "--background-log",
        "/state/w.log",
    ]
    preview = background_command(
        Path("ferrygl.toml"), apply=False, interval=90, log=Path("/state/w.log")
    )
    assert "--preview" in preview


class FakeProcess:
    pid = 4321

    def __init__(self, code: int | None) -> None:
        self.code = code

    def wait(self, timeout):
        if self.code is None:
            raise subprocess.TimeoutExpired("ferrygl", timeout)
        return self.code


def start(tmp_path, code=None, writes=b"", log=None):
    """Start a fake background watch that writes ``writes`` and exits with ``code``, or
    keeps running if ``code`` is None."""
    calls = []

    def popen(command, **options):
        calls.append((command, options))
        options["stdout"].write(writes)
        return FakeProcess(code)

    if log is None:
        log = tmp_path / "watch.log"
        log.write_bytes(b"an earlier watch\n")
    out, err = io.StringIO(), io.StringIO()
    result = start_in_background(["ferrygl", "watch"], log=log, out=out, err=err, popen=popen)
    return result, calls, out.getvalue(), err.getvalue(), log


def test_a_background_watch_is_detached_from_the_terminal_with_its_output_in_the_log(
    tmp_path: Path,
):
    code, calls, out, _, log = start(tmp_path, writes=b"Watching 1 route\n")

    assert code == EXIT_OK
    [(command, options)] = calls
    assert command == ["ferrygl", "watch"]
    assert options["start_new_session"] is True
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.STDOUT
    assert log.read_text() == "an earlier watch\nWatching 1 route\n"
    assert out == (
        f"Watching in the background, as watch 4321. Its output goes to {log}.\n"
        "To stop it: ferrygl watch --stop 4321\n"
    )


def test_a_background_watch_that_stops_right_away_is_reported_here(tmp_path: Path):
    code, _, out, err, _ = start(tmp_path, code=2, writes=b"error: ferryGL is already watching\n")

    assert code == EXIT_SETUP
    assert out == ""
    assert err == (
        "error: the background watch stopped right away. It wrote:\n"
        "error: ferryGL is already watching\n"
    )


# The command line


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    """A config file, a Linear key, and a state directory of the test's own."""
    config = tmp_path / "acme.toml"
    config.write_text('[[route]]\nrepos = ["acme/api"]\nteam = "ENG"\n', encoding="utf-8")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return config


@posix_only
def test_list_shows_the_running_watches(setup: Path, capsys):
    assert main(["watch", "--list"]) == EXIT_OK
    assert capsys.readouterr().out == "No watch is running.\n"

    with Jobs(state_dir()).hold(job(setup.resolve())):
        assert main(["watch", "--list"]) == EXIT_OK

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("ID ")
    assert lines[1].startswith(str(os.getpid()))
    assert lines[1].endswith(str(setup.resolve()))


@posix_only
def test_stop_with_an_id_stops_that_watch(child, monkeypatch, capsys):
    process, jobs, _ = child
    monkeypatch.setenv("XDG_STATE_HOME", str(jobs.directory.parent))

    assert main(["watch", "--stop", str(process.pid)]) == EXIT_OK

    assert process.wait(timeout=5) == 0
    assert f"Watch {process.pid} stopped." in capsys.readouterr().out


@posix_only
def test_stop_without_an_id_stops_the_watch_of_the_config_file(child, monkeypatch, capsys):
    process, jobs, config = child
    monkeypatch.setenv("XDG_STATE_HOME", str(jobs.directory.parent))

    assert main(["watch", "--stop", "--config", config]) == EXIT_OK

    assert process.wait(timeout=5) == 0
    assert f"Watch {process.pid} stopped." in capsys.readouterr().out


@posix_only
def test_stop_when_nothing_is_running(setup: Path, capsys):
    assert main(["watch", "--stop", "--config", str(setup)]) == EXIT_OK
    assert main(["watch", "--stop", "all"]) == EXIT_OK
    assert main(["watch", "--stop", "999999"]) == EXIT_FAILED

    out, err = capsys.readouterr()
    assert out == f"No watch of {setup.resolve()} is running.\nNo watch is running.\n"
    assert err == "error: no watch with ID 999999 is running. `ferrygl watch --list` shows them.\n"


@posix_only
def test_log_shows_the_end_of_the_config_files_log_even_after_its_watch_stopped(
    setup: Path, capsys
):
    assert main(["watch", "--log", "--config", str(setup)]) == EXIT_FAILED
    assert "has no log yet. `ferrygl watch --bg` writes one." in capsys.readouterr().err

    log = Jobs(state_dir()).log(setup)
    log.parent.mkdir(parents=True)
    log.write_text("Watching 1 route\nerror: GitHub rejected the token\n", encoding="utf-8")

    assert main(["watch", "--log", "--config", str(setup)]) == EXIT_OK
    assert capsys.readouterr().out == (
        f"==> {log} <==\nWatching 1 route\nerror: GitHub rejected the token\n"
    )


@posix_only
def test_log_of_a_watch_in_the_foreground_points_there(setup: Path, capsys):
    with Jobs(state_dir()).hold(job(setup.resolve())):
        assert main(["watch", "--log", str(os.getpid())]) == EXIT_FAILED

    assert "runs in the foreground: its output goes to the terminal or service" in (
        capsys.readouterr().err
    )


@posix_only
def test_a_second_watch_of_the_same_config_stops_before_reading_anything(setup: Path, capsys):
    with Jobs(state_dir()).hold(job(setup.resolve())):
        for extra in ([], ["--bg"]):
            assert main(["watch", "--config", str(setup), *extra]) == EXIT_SETUP

    err = capsys.readouterr().err
    assert err.count(f"already watching {setup.resolve()}, as watch {os.getpid()}") == 2


def test_managing_watches_takes_no_start_options(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["watch", "--list", "--preview"])

    assert exit.value.code == 2
    assert "--preview and --interval are for starting a watch" in capsys.readouterr().err


class QuietStop:
    """Stands in for StopRequest, which would replace the test process's SIGTERM handler."""

    def __call__(self):
        return False

    def sleep(self, seconds):
        pass


def test_watch_makes_the_changes_unless_asked_to_preview(setup: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(ferrygl.cli, "StopRequest", QuietStop)
    monkeypatch.setattr(ferrygl.cli, "run_watch", lambda config, **kw: calls.append(kw) or 0)

    assert main(["watch", "--config", str(setup)]) == EXIT_OK
    assert main(["watch", "--preview", "--config", str(setup)]) == EXIT_OK

    assert [kw["apply"] for kw in calls] == [True, False]


def test_watch_explains_that_it_needs_no_apply(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["watch", "--apply"])

    assert exit.value.code == 2
    assert "watch makes the changes by default; add --preview" in capsys.readouterr().err


def test_where_watches_cannot_be_managed_it_says_so(setup: Path, monkeypatch, capsys):
    monkeypatch.setattr(ferrygl.cli, "SUPPORTED", False)

    assert main(["watch", "--bg", "--config", str(setup)]) == EXIT_SETUP
    assert main(["watch", "--list"]) == EXIT_SETUP
    err = capsys.readouterr().err
    assert "--bg works on Linux and macOS" in err
    assert "--list works on Linux and macOS" in err
