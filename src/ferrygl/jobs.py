"""The watches running on this machine: start one in the background, list, stop, read logs.

Each watch holds an exclusive ``flock`` on a lock file named after its config file, in the
state directory, and writes into it what it is: its ID (the process ID), config file and
options. The operating system releases the lock when the process ends, however it ends, so a
lock file that nobody holds belongs to a watch that has stopped, and is ignored.

Locks, and running in the background, need POSIX: Linux or macOS.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any, TextIO

from ferrygl.sync import EXIT_FAILED, EXIT_OK

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

SUPPORTED = fcntl is not None
# How long `--bg` waits to see whether the background watch stops right away.
SETTLE_SECONDS = 2.0
# How long `--stop` waits for a watch to finish what it's doing and stop.
STOP_SECONDS = 30.0
# Listing watches holds each lock for an instant, so a watch that starts at that moment tries
# again for this long before deciding that another watch has its lock.
LOCK_SECONDS = 0.2
LOG_LINES = 20  # shown by `--log`


class JobError(Exception):
    """A watch of the same config file is running, or a lock or log can't be written."""


@dataclass(frozen=True)
class Job:
    """A running watch, as it describes itself in its lock file."""

    pid: int  # also the watch's ID
    config: str  # the config file's full path
    apply: bool
    interval: int
    started: str  # ISO timestamp in UTC
    log: str | None = None  # set when it runs in the background

    @property
    def background(self) -> bool:
        return self.log is not None


def state_dir(env: Mapping[str, str] = os.environ) -> Path:
    """``$XDG_STATE_HOME/ferrygl``, else ``~/.local/state/ferrygl``: where watches keep their
    locks and logs."""
    base = env.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "ferrygl"


class Jobs:
    """The watches running on this machine, found through their lock files in ``directory``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def lock(self, config: Path) -> Path:
        return self.directory / f"{_name(config)}.lock"

    def log(self, config: Path) -> Path:
        return self.directory / f"{_name(config)}.log"

    @contextmanager
    def hold(self, job: Job) -> Iterator[None]:
        """Record ``job`` as running until the block ends.

        Raises JobError if a watch of the same config file is running: two could each create
        a Linear issue for the same new item at the same moment.
        """
        if fcntl is None:  # Windows has no flock, so nothing stops a second watch there.
            yield
            return
        path = self.lock(Path(job.config))
        try:
            # Appending, so that each write after truncating lands at the start.
            file = os.fdopen(_private(path, os.O_RDWR | os.O_APPEND), "a+", encoding="utf-8")
        except OSError as exc:
            raise JobError(f"{path}: {exc.strerror}") from None
        with file:
            if not _flock(file, fcntl.LOCK_EX, LOCK_SECONDS):
                other = _read(file)
                message = f"ferryGL is already watching {job.config}"
                if other:
                    message += (
                        f", as watch {other.pid}. Stop it first: ferrygl watch --stop {other.pid}"
                    )
                raise JobError(message)
            file.truncate(0)
            file.write(json.dumps(asdict(job)))
            file.flush()
            yield

    def running(self) -> list[Job]:
        """The watches running now, oldest first."""
        held = (self._holder(path) for path in self.directory.glob("watch-*.lock"))
        return sorted((job for job in held if job), key=lambda job: job.started)

    def is_running(self, job: Job) -> bool:
        holder = self._holder(self.lock(Path(job.config)))
        return holder is not None and holder.pid == job.pid

    def _holder(self, path: Path) -> Job | None:
        """The watch holding the lock at ``path``, if one does."""
        if fcntl is None:
            return None
        try:
            file = path.open("r", encoding="utf-8")
        except OSError:
            return None
        with file:
            if _flock(file, fcntl.LOCK_SH, 0):  # Nobody holds it: that watch has stopped.
                fcntl.flock(file, fcntl.LOCK_UN)
                return None
            return _read(file)


def format_jobs(jobs: Sequence[Job]) -> list[str]:
    """``jobs`` as a table, one line per watch."""
    rows = [("ID", "STARTED", "RUNS IN", "MODE", "EVERY", "CONFIG")]
    for job in jobs:
        where = "background" if job.background else "foreground"
        mode = "apply" if job.apply else "preview"
        rows.append((str(job.pid), job.started, where, mode, f"{job.interval}s", job.config))
    # Every column but the last, the config file, is padded to its widest cell.
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return [
        "  ".join([*(row[i].ljust(width) for i, width in enumerate(widths)), row[-1]])
        for row in rows
    ]


def stop(
    job: Job,
    jobs: Jobs,
    *,
    wait: float = STOP_SECONDS,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Ask ``job`` to stop, and wait up to ``wait`` seconds for it. True once it has.

    The watch finishes the sync it's in before stopping, so that no change is left half made.
    """
    try:
        kill(job.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = clock() + wait
    while jobs.is_running(job):
        if clock() >= deadline:
            return False
        sleep(0.1)
    return True


def last_lines(path: Path, count: int = LOG_LINES) -> list[str]:
    with path.open(encoding="utf-8", errors="replace") as file:
        return [line.rstrip("\n") for line in deque(file, maxlen=count)]


class StopRequest:
    """Turns SIGTERM, which ``ferrygl watch --stop`` sends, into a request that the watch
    stops between syncs, rather than in the middle of a write to Linear."""

    def __init__(self) -> None:
        self.requested = False
        signal.signal(signal.SIGTERM, self._request)

    def _request(self, signum: int, frame: Any) -> None:
        self.requested = True

    def __call__(self) -> bool:
        return self.requested

    def sleep(self, seconds: float) -> None:
        """Sleep, but wake within half a second of a stop request."""
        end = time.monotonic() + seconds
        while not self.requested and (left := end - time.monotonic()) > 0:
            time.sleep(min(left, 0.5))


def background_command(config: Path, *, apply: bool, interval: int, log: Path) -> list[str]:
    """``ferrygl watch`` with the same options, run by this Python.

    Unbuffered, so the log keeps output and errors in order. Credentials reach it through the
    environment rather than the command line, which other users of the machine can see.
    """
    return [
        sys.executable,
        "-u",
        "-m",
        "ferrygl",
        "watch",
        "--config",
        str(config.resolve()),
        "--interval",
        str(interval),
        *([] if apply else ["--preview"]),
        "--background-log",
        str(log),
    ]


def start_in_background(
    command: Sequence[str],
    *,
    log: Path,
    out: TextIO,
    err: TextIO,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    settle: float = SETTLE_SECONDS,
) -> int:
    """Start ``command`` detached from the terminal, appending its output to ``log``.

    Waits ``settle`` seconds, so that a watch that stops right away is reported here, with
    what it wrote, rather than only in the log. Returns an exit status.
    """
    try:
        file = os.fdopen(_private(log, os.O_WRONLY | os.O_APPEND), "ab")
    except OSError as exc:
        raise JobError(f"{log}: {exc.strerror}") from None
    with file:
        start = file.tell()
        # A new session has no controlling terminal, so closing this one doesn't stop it.
        process = popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        code = process.wait(timeout=settle)
    except subprocess.TimeoutExpired:
        print(
            f"Watching in the background, as watch {process.pid}. Its output goes to {log}.",
            file=out,
        )
        print(f"To stop it: ferrygl watch --stop {process.pid}", file=out)
        return EXIT_OK
    with log.open("rb") as written:
        written.seek(start)
        output = written.read().decode("utf-8", errors="replace").rstrip()
    print(f"error: the background watch stopped right away. It wrote:\n{output}", file=err)
    return code if code > 0 else EXIT_FAILED


def _private(path: Path, flags: int) -> int:
    """Open ``path``, creating it and its folder readable by you alone: a log names GitHub
    items and Linear issue titles, which often belong to a client."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return os.open(path, flags | os.O_CREAT, 0o600)


def _name(config: Path) -> str:
    """``watch-<config file name>-<hash of its full path>``: a lock and a log per config file."""
    path = config.expanduser().resolve()
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:8]
    return f"watch-{path.stem}-{digest}"


def _flock(file: IO[str], kind: int, seconds: float) -> bool:
    """Take a ``flock`` of ``kind`` within ``seconds``. False if another process holds it."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(file, kind | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def _read(file: IO[str]) -> Job | None:
    """The job a lock file describes, or None while it's being written."""
    file.seek(0)
    try:
        return Job(**json.loads(file.read()))
    except (ValueError, TypeError):
        return None
