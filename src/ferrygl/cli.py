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
from ferrygl.linear import LinearClient
from ferrygl.sync import EXIT_SETUP, GitHubFactory, run_check, run_sync


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load(args.config or default_path())
        api_key = linear_api_key(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SETUP

    linear = LinearClient(api_key)
    github_for = _github_factory()
    try:
        if args.command == "check":
            return run_check(config, linear=linear, github_for=github_for, out=sys.stdout)
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
    return parser
