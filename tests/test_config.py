from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ferrygl.config import ConfigError, StatusNames, Titles, load, parse

MINIMAL = {"route": [{"repos": ["acme/api"], "team": "ENG"}]}


def test_minimal_config_uses_defaults():
    config = parse(MINIMAL)

    [route] = config.routes
    assert route.repos == ("acme/api",) and route.team == "ENG"
    assert route.project is None and route.github_user is None
    assert route.single_repo == "acme/api"
    assert config.titles == Titles()
    assert config.status == StatusNames()
    assert config.sync.issues_assigned and config.sync.review_requests
    assert config.copy_description is False
    assert config.sync.comment is False


def test_full_config_file(tmp_path: Path):
    path = tmp_path / "ferrygl.toml"
    path.write_text(
        """
[[route]]
repos = ["acme/api", "acme/web"]
team = "ENG"
project = "Acme engagement"
github_user = "alice-work"

[[route]]
repos = ["upstream/*"]
team = "ENG"

[sync]
since = 2026-09-01
label = "ferryGL"
review_requests = false
comment = true

[titles]
issue = "{repo}#{number}"

[copy]
description = true

[status]
in_review = "Review"

[linear]
api_key_file = "~/linear.key"
""",
        encoding="utf-8",
    )

    config = load(path)

    assert config.routes[0].github_user == "alice-work"
    assert config.routes[0].single_repo is None
    assert config.routes[1].single_repo is None
    assert config.sync.since == date(2026, 9, 1)
    assert config.sync.review_requests is False
    assert config.sync.comment is True
    assert config.titles.issue == "{repo}#{number}"
    assert config.titles.pr == Titles().pr
    assert config.status.in_review == "Review"
    assert config.copy_description is True
    assert config.linear_api_key_file == Path("~/linear.key").expanduser()


def test_since_accepts_a_string_date():
    config = parse({**MINIMAL, "sync": {"since": "2026-09-01"}})

    assert config.sync.since == date(2026, 9, 1)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "add at least one"),
        ({**MINIMAL, "extra": 1}, "unknown setting `extra`"),
        ({"route": [{"repos": [], "team": "ENG"}]}, "non-empty list"),
        ({"route": [{"repos": ["acme"], "team": "ENG"}]}, '"acme" is not'),
        ({"route": [{"repos": ["acme/api"]}]}, "`team` must name"),
        ({"route": [{"repos": ["acme/api"], "team": "ENG", "colour": 1}]}, "`colour`"),
        ({**MINIMAL, "sync": {"since": "soon"}}, "must be a date"),
        ({**MINIMAL, "sync": {"label": ""}}, "non-empty string"),
        ({**MINIMAL, "sync": {"comment": "yes"}}, "`comment` must be true or false"),
        ({**MINIMAL, "titles": {"issue": "{name}"}}, "uses {name}"),
        ({**MINIMAL, "copy": {"description": "yes"}}, "true or false"),
    ],
)
def test_invalid_configs_are_explained(data, message):
    with pytest.raises(ConfigError, match=message):
        parse(data)


@pytest.mark.parametrize(
    "repos",
    [
        (["acme/api"], ["acme/api"]),
        (["acme/*"], ["acme/api"]),
        (["Acme/API"], ["acme/api"]),
        (["acme/api", "acme/api"], ["acme/web"]),
    ],
)
def test_a_repository_can_belong_to_one_route_only(repos):
    data = {"route": [{"repos": r, "team": "ENG"} for r in repos]}

    with pytest.raises(ConfigError, match="overlap"):
        parse(data)


def test_missing_file_is_reported(tmp_path: Path):
    with pytest.raises(ConfigError, match="file not found"):
        load(tmp_path / "nope.toml")
