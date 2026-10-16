"""Read and validate ``ferrygl.toml``."""

from __future__ import annotations

import os
import re
import string
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

CATEGORIES = ("todo", "in_progress", "in_review", "done", "canceled")
TITLE_FIELDS = frozenset({"title", "number", "repo"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/([A-Za-z0-9_.-]+|\*)$")


class ConfigError(Exception):
    """The configuration file is missing or invalid."""


@dataclass(frozen=True)
class Route:
    """Repositories whose items go to one Linear team and, optionally, one project."""

    repos: tuple[str, ...]  # "owner/name", or "owner/*" for every repository of an owner
    team: str  # Linear team key or name
    project: str | None = None
    github_user: str | None = None  # read GitHub as this GitHub CLI account

    @property
    def name(self) -> str:
        target = self.team if self.project is None else f"{self.team} / {self.project}"
        return f"{', '.join(self.repos)} -> {target}"

    @property
    def single_repo(self) -> str | None:
        """The repository, when the route names exactly one and no wildcard."""
        if len(self.repos) == 1 and not self.repos[0].endswith("/*"):
            return self.repos[0]
        return None


SYNC_FLAGS = (
    "issues_assigned",
    "issues_created",
    "prs_created",
    "prs_assigned",
    "prs_reviewed",
    "review_requests",
    "prs_with_commits",
)


@dataclass(frozen=True)
class SyncOptions:
    """Which of your roles on GitHub bring an item into Linear. All are on by default."""

    issues_assigned: bool = True
    issues_created: bool = True
    prs_created: bool = True
    prs_assigned: bool = True  # someone else's pull request assigned to you
    prs_reviewed: bool = True
    review_requests: bool = True
    prs_with_commits: bool = True  # someone else's pull request with a commit you wrote
    since: date | None = None
    label: str | None = None
    comment: bool = False  # add a (BY-FGL) comment to each Linear issue ferryGL changes


@dataclass(frozen=True)
class Titles:
    issue: str = "{title} #{number}"
    pr: str = "{title} - #{number}"
    involved: str = "[PR] {title} - #{number}"


@dataclass(frozen=True)
class StatusNames:
    """Your Linear team's workflow state names for each status ferryGL sets."""

    todo: str = "Todo"
    in_progress: str = "In Progress"
    in_review: str = "In Review"
    done: str = "Done"
    canceled: str = "Canceled"

    def name(self, category: str) -> str:
        return getattr(self, category)

    def all(self) -> tuple[str, ...]:
        return tuple(self.name(category) for category in CATEGORIES)


@dataclass(frozen=True)
class Config:
    routes: tuple[Route, ...]
    sync: SyncOptions = field(default_factory=SyncOptions)
    titles: Titles = field(default_factory=Titles)
    status: StatusNames = field(default_factory=StatusNames)
    copy_description: bool = False
    linear_api_key_file: Path | None = None


def default_path() -> Path:
    """``$FERRYGL_CONFIG``, else ``ferrygl/ferrygl.toml`` in the user's config directory."""
    if explicit := os.environ.get("FERRYGL_CONFIG"):
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "ferrygl" / "ferrygl.toml"


def load(path: Path) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror}") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse(data, source=str(path))


def parse(data: dict[str, Any], source: str = "config") -> Config:
    _check_keys(data, {"route", "sync", "titles", "copy", "status", "linear"}, source)
    raw_routes = data.get("route")
    if not raw_routes:
        raise ConfigError(f"{source}: add at least one [[route]] table")
    if not isinstance(raw_routes, list):
        raise ConfigError(f"{source}: write routes as [[route]] tables")
    routes = tuple(_route(raw, f"{source}: route {n}") for n, raw in enumerate(raw_routes, 1))
    _check_overlap(routes, source)

    copy = _table(data, "copy", source)
    _check_keys(copy, {"description"}, f"{source}: [copy]")
    linear = _table(data, "linear", source)
    _check_keys(linear, {"api_key_file"}, f"{source}: [linear]")
    key_file = _optional_str(linear, "api_key_file", f"{source}: [linear]")
    return Config(
        routes=routes,
        sync=_sync(_table(data, "sync", source), f"{source}: [sync]"),
        titles=_titles(_table(data, "titles", source), f"{source}: [titles]"),
        status=_status(_table(data, "status", source), f"{source}: [status]"),
        copy_description=_bool(copy, "description", False, f"{source}: [copy]"),
        linear_api_key_file=Path(key_file).expanduser() if key_file else None,
    )


def _route(raw: Any, where: str) -> Route:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be a table")
    _check_keys(raw, {"repos", "team", "project", "github_user"}, where)
    repos = raw.get("repos")
    if not isinstance(repos, list) or not repos or not all(isinstance(r, str) for r in repos):
        raise ConfigError(f'{where}: `repos` must be a non-empty list such as ["owner/name"]')
    for repo in repos:
        if not _REPO.match(repo):
            raise ConfigError(f'{where}: "{repo}" is not "owner/name" or "owner/*"')
    team = raw.get("team")
    if not isinstance(team, str) or not team.strip():
        raise ConfigError(f"{where}: `team` must name a Linear team, by key or name")
    return Route(
        repos=tuple(repos),
        team=team.strip(),
        project=_optional_str(raw, "project", where),
        github_user=_optional_str(raw, "github_user", where),
    )


def _check_overlap(routes: tuple[Route, ...], source: str) -> None:
    """Each repository must belong to exactly one route, or its items would be planned twice."""
    entries = [(n, repo) for n, route in enumerate(routes, 1) for repo in route.repos]
    for i, (n, a) in enumerate(entries):
        for m, b in entries[i + 1 :]:
            if _covers(a, b) or _covers(b, a):
                raise ConfigError(
                    f'{source}: "{a}" (route {n}) and "{b}" (route {m}) overlap; '
                    "send each repository to exactly one route"
                )


def _covers(pattern: str, repo: str) -> bool:
    owner, name = pattern.lower().split("/", 1)
    return pattern.lower() == repo.lower() or (name == "*" and repo.lower().startswith(f"{owner}/"))


def _sync(raw: dict[str, Any], where: str) -> SyncOptions:
    flags = SYNC_FLAGS
    _check_keys(raw, {*flags, "since", "label", "comment"}, where)
    since = raw.get("since")
    if isinstance(since, datetime):
        since = since.date()
    elif isinstance(since, str):
        try:
            since = date.fromisoformat(since)
        except ValueError:
            raise ConfigError(f"{where}: `since` must be a date such as 2026-09-01") from None
    elif since is not None and not isinstance(since, date):
        raise ConfigError(f"{where}: `since` must be a date such as 2026-09-01")
    return SyncOptions(
        **{flag: _bool(raw, flag, True, where) for flag in flags},
        since=since,
        label=_optional_str(raw, "label", where),
        comment=_bool(raw, "comment", False, where),
    )


def _titles(raw: dict[str, Any], where: str) -> Titles:
    _check_keys(raw, {"issue", "pr", "involved"}, where)
    defaults = Titles()
    values = {}
    for key in ("issue", "pr", "involved"):
        value = raw.get(key, getattr(defaults, key))
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}: `{key}` must be a non-empty string")
        try:
            fields = _template_fields(value)
        except ValueError as exc:
            raise ConfigError(f"{where}: `{key}`: {exc}") from None
        unknown = sorted(fields - TITLE_FIELDS)
        if unknown:
            shown = ", ".join("{" + f + "}" for f in unknown)
            raise ConfigError(
                f"{where}: `{key}` uses {shown}; use {{title}}, {{number}} or {{repo}}"
            )
        # Fill it once with values of the types planning uses, so a bad conversion or format
        # spec, such as {title!x} or {number:abc}, stops the run now, not after reading GitHub.
        try:
            value.format(title="", number=0, repo="")
        except ValueError as exc:
            raise ConfigError(f"{where}: `{key}`: {exc}") from None
        values[key] = value
    return Titles(**values)


def _template_fields(template: str) -> set[str]:
    """The fields a template uses, including those nested in a format spec: {title:{number}}."""
    fields = set()
    for _, name, spec, _ in string.Formatter().parse(template):
        if name is not None:
            fields |= {name, *_template_fields(spec or "")}
    return fields


def _status(raw: dict[str, Any], where: str) -> StatusNames:
    _check_keys(raw, set(CATEGORIES), where)
    defaults = StatusNames()
    values = {}
    for category in CATEGORIES:
        value = raw.get(category, defaults.name(category))
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}: `{category}` must name a Linear workflow state")
        values[category] = value.strip()
    return StatusNames(**values)


def _table(data: dict[str, Any], key: str, source: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{source}: `{key}` must be a [{key}] table")
    return value


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        names = ", ".join(f"`{k}`" for k in unknown)
        raise ConfigError(f"{where}: unknown setting {names}")


def _optional_str(table: dict[str, Any], key: str, where: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: `{key}` must be a non-empty string")
    return value.strip()


def _bool(table: dict[str, Any], key: str, default: bool, where: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: `{key}` must be true or false")
    return value
