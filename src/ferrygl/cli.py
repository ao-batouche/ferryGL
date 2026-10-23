"""The ``ferrygl`` command."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ferrygl import __version__
from ferrygl.config import Config, ConfigError, default_path, load
from ferrygl.github import GitHubClient, resolve_token
from ferrygl.jobs import (
    SUPPORTED,
    Job,
    JobError,
    Jobs,
    StopRequest,
    background_command,
    format_jobs,
    last_lines,
    start_in_background,
    state_dir,
    stop,
)
from ferrygl.linear import LinearClient
from ferrygl.sync import EXIT_FAILED, EXIT_OK, EXIT_SETUP, GitHubFactory, run_check, run_sync
from ferrygl.watch import DEFAULT_INTERVAL, MIN_INTERVAL, preflight, run_watch


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    path = args.config or default_path()
    if args.command == "watch":
        if args.apply:
            parser.error("watch makes the changes by default; add --preview to only show them")
        managing = args.list or args.stop is not None or args.log is not None
        if managing and (args.preview or args.interval is not None):
            parser.error("--preview and --interval are for starting a watch")
        if managing:
            try:
                return _manage(args, path)
            except KeyboardInterrupt:
                return 130
        args.interval = args.interval or DEFAULT_INTERVAL
        # `watch` makes the changes unless asked only to show them.
        args.apply = not args.preview
    try:
        config = load(path)
        api_key = linear_api_key(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SETUP

    linear = LinearClient(api_key)
    github_for = _github_factory()
    try:
        if args.command == "check":
            return run_check(config, linear=linear, github_for=github_for, out=sys.stdout)
        if args.command == "watch":
            return _watch(args, path, config, linear, github_for)
        return run_sync(
            config,
            apply=args.apply,
            linear=linear,
            github_for=github_for,
            out=sys.stdout,
            err=sys.stderr,
            now=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except KeyboardInterrupt:
        return 130


def linear_api_key(config: Config, env: Mapping[str, str] = os.environ) -> str:
    """``LINEAR_API_KEY``, else the contents of ``[linear] api_key_file``."""
    if key := env.get("LINEAR_API_KEY", "").strip():
        return key
    path = config.linear_api_key_file
    if path is None:
        raise ConfigError("no Linear API key: set LINEAR_API_KEY or [linear] api_key_file")
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror}") from None
    if not key:
        raise ConfigError(f"{path}: the Linear API key file is empty")
    return key


def _watch(
    args: argparse.Namespace,
    path: Path,
    config: Config,
    linear: LinearClient,
    github_for: GitHubFactory,
) -> int:
    """Run a watch here, or with ``--bg``, start one in the background."""
    if args.bg and not SUPPORTED:
        return _unsupported("--bg")
    jobs = Jobs(state_dir())
    job = Job(
        pid=os.getpid(),
        config=str(path.resolve()),
        apply=args.apply,
        interval=args.interval,
        started=datetime.now(UTC).isoformat(timespec="seconds"),
        log=str(args.background_log) if args.background_log else None,
    )
    try:
        with jobs.hold(job):
            if not args.bg:
                stopping = StopRequest()
                return run_watch(
                    config,
                    apply=args.apply,
                    interval=args.interval,
                    linear=linear,
                    github_for=github_for,
                    out=sys.stdout,
                    err=sys.stderr,
                    sleep=stopping.sleep,
                    stopping=stopping,
                )
        # No watch of this config is running. The background one takes the lock itself.
        code = preflight(config, linear=linear, github_for=github_for, err=sys.stderr)
        if code != EXIT_OK:
            return code
        log = jobs.log(path)
        command = background_command(path, apply=args.apply, interval=args.interval, log=log)
        code = start_in_background(command, log=log, out=sys.stdout, err=sys.stderr)
    except JobError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SETUP
    if code == EXIT_OK and not args.apply:
        print(
            "Previewing: changes are shown in the log, not made. To make them, stop it and run "
            "`ferrygl watch --bg`.",
        )
    return code


def _manage(args: argparse.Namespace, path: Path) -> int:
    """``watch --list``, ``--stop`` and ``--log``, which need neither the config nor
    credentials. Without an ID, ``--stop`` and ``--log`` act on the watch of ``path``."""
    option = "--list" if args.list else "--stop" if args.stop is not None else "--log"
    if not SUPPORTED:
        return _unsupported(option)
    jobs = Jobs(state_dir())
    running = jobs.running()
    if args.list:
        for line in format_jobs(running) if running else ["No watch is running."]:
            print(line)
        return EXIT_OK

    ident = args.stop if args.stop is not None else args.log
    config = str(path.resolve())
    if ident == "":
        chosen = [job for job in running if job.config == config]
    elif ident == "all" and args.stop is not None:
        chosen = running
    elif ident.isdigit() and any(job.pid == int(ident) for job in running):
        chosen = [job for job in running if job.pid == int(ident)]
    else:
        print(
            f"error: no watch with ID {ident} is running. `ferrygl watch --list` shows them.",
            file=sys.stderr,
        )
        return EXIT_FAILED

    if args.log is not None:
        if chosen and not chosen[0].background:
            print(
                f"error: watch {chosen[0].pid} runs in the foreground: its output goes to the "
                "terminal or service that started it.",
                file=sys.stderr,
            )
            return EXIT_FAILED
        log = Path(chosen[0].log) if chosen and chosen[0].log else jobs.log(path)
        if not log.exists():
            print(
                f"error: {config} has no log yet. `ferrygl watch --bg` writes one.", file=sys.stderr
            )
            return EXIT_FAILED
        print(f"==> {log} <==")
        for line in last_lines(log):
            print(line)
        return EXIT_OK

    if not chosen:
        print("No watch is running." if ident == "all" else f"No watch of {config} is running.")
    for job in chosen:
        print(f"Stopping watch {job.pid} ({job.config})...", flush=True)
        if stop(job, jobs):
            print(f"Watch {job.pid} stopped.")
        else:
            print(f"Watch {job.pid} is finishing a sync, and will stop when it's done.")
    return EXIT_OK


def _unsupported(option: str) -> int:
    print(
        f"error: {option} works on Linux and macOS. Here, run `ferrygl watch` in a window of "
        "its own, and stop it with Ctrl-C.",
        file=sys.stderr,
    )
    return EXIT_SETUP


def _github_factory() -> GitHubFactory:
    clients: dict[str | None, GitHubClient] = {}

    def github_for(user: str | None) -> GitHubClient:
        if user not in clients:
            clients[user] = GitHubClient(resolve_token(user))
        return clients[user]

    return github_for


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ferrygl",
        description="Copy your GitHub issues and pull requests into your own Linear workspace.",
    )
    parser.add_argument("--version", action="version", version=f"ferrygl {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    config_help = f"configuration file (default: {default_path()})"

    check = commands.add_parser(
        "check", help="check credentials, repositories and Linear settings; writes nothing"
    )
    check.add_argument("--config", type=Path, help=config_help)

    sync = commands.add_parser(
        "sync", help="show the changes a sync would make; --apply makes them in Linear"
    )
    sync.add_argument("--config", type=Path, help=config_help)
    sync.add_argument("--apply", action="store_true", help="make the changes in Linear")

    watch = commands.add_parser(
        "watch",
        help="sync, then sync again whenever your repositories change; --preview only shows "
        "the changes",
    )
    watch.add_argument("--config", type=Path, help=config_help)
    watch.add_argument(
        "--preview", action="store_true", help="show the changes without making them in Linear"
    )
    # Not an option of `watch`, but `sync --apply` makes it easy to type: explain instead.
    watch.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    watch.add_argument(
        "--interval",
        type=_interval,
        metavar="SECONDS",
        help=f"time between checks of GitHub (default: {DEFAULT_INTERVAL}, at least "
        f"{MIN_INTERVAL})",
    )
    actions = watch.add_mutually_exclusive_group()
    actions.add_argument(
        "--bg",
        action="store_true",
        help=f"keep watching in the background, with the output in a log in {state_dir()}",
    )
    actions.add_argument(
        "--list", action="store_true", help="list the watches running, with their IDs"
    )
    actions.add_argument(
        "--stop",
        nargs="?",
        const="",
        metavar="ID",
        help="stop the watch with this ID, or `all`; without an ID, the watch of the config file",
    )
    actions.add_argument(
        "--log",
        nargs="?",
        const="",
        metavar="ID",
        help="show the end of a background watch's log; without an ID, the config file's watch",
    )
    # Set on the watch that `--bg` starts, so that `--list` can say where its output goes.
    watch.add_argument("--background-log", type=Path, help=argparse.SUPPRESS)
    return parser


def _interval(text: str) -> int:
    try:
        seconds = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number of seconds") from None
    if seconds < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(
            f"must be at least {MIN_INTERVAL} seconds, to spare your GitHub rate limit"
        )
    return seconds
