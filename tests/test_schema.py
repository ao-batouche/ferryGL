"""site/ferrygl.schema.json, the JSON Schema editors use for ferrygl.toml, must agree with
config.py: the same settings and defaults, and patterns that accept what ferryGL accepts."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from ferrygl.config import (
    CATEGORIES,
    Config,
    ConfigError,
    Route,
    SyncOptions,
    Titles,
    parse,
)

SCHEMA = json.loads(
    (Path(__file__).parents[1] / "site" / "ferrygl.schema.json").read_text(encoding="utf-8")
)
ROUTE = SCHEMA["properties"]["route"]["items"]
MINIMAL = {"route": [{"repos": ["acme/api"], "team": "ENG"}]}


def table(name: str) -> dict[str, Any]:
    return ROUTE if name == "route" else SCHEMA["properties"][name]


def test_the_schema_lists_every_setting_and_no_others():
    def keys(name: str) -> set[str]:
        return set(table(name)["properties"])

    assert keys("route") == {f.name for f in fields(Route)}
    assert keys("sync") == {f.name for f in fields(SyncOptions)}
    assert keys("titles") == {f.name for f in fields(Titles)}
    assert keys("status") == set(CATEGORIES)
    assert keys("copy") == {"description"}
    assert keys("linear") == {"api_key_file"}
    # Each field of Config comes from one table; a new one needs a place in the schema.
    assert {f.name for f in fields(Config)} == {
        "routes",
        "sync",
        "titles",
        "status",
        "copy_description",
        "linear_api_key_file",
    }
    assert set(SCHEMA["properties"]) == {"route", "sync", "titles", "status", "copy", "linear"}


def test_every_table_rejects_unknown_keys_and_explains_its_settings():
    assert SCHEMA["additionalProperties"] is False
    for name, schema in SCHEMA["properties"].items():
        assert schema.get("description"), name
        assert table(name)["additionalProperties"] is False
        for key, setting in table(name)["properties"].items():
            assert setting.get("description"), f"{name}.{key}"
            assert "default" in setting or setting.get("examples"), f"{name}.{key}"


def test_the_schema_defaults_are_ferrygls_defaults():
    data: dict[str, Any] = dict(MINIMAL)
    for name in ("sync", "titles", "copy", "status"):
        data[name] = {
            key: setting["default"]
            for key, setting in table(name)["properties"].items()
            if "default" in setting
        }

    assert parse(data) == parse(MINIMAL)


def test_a_file_with_every_setting_from_the_schema_is_valid():
    def value(setting: dict[str, Any]) -> Any:
        return setting["default"] if "default" in setting else setting["examples"][0]

    route = {key: value(setting) for key, setting in ROUTE["properties"].items()}
    data = {
        name: {key: value(setting) for key, setting in table(name)["properties"].items()}
        for name in SCHEMA["properties"]
        if name != "route"
    }

    parse({"route": [route], **data})


def matches(setting: dict[str, Any], text: str) -> bool:
    patterns = [setting.get("pattern"), *(part["pattern"] for part in setting.get("allOf", []))]
    return all(re.search(pattern, text) for pattern in patterns if pattern)


def accepted(data: dict[str, Any]) -> bool:
    try:
        parse(data)
    except ConfigError:
        return False
    return True


@pytest.mark.parametrize(
    "repo",
    ["acme/api", "acme/*", "Acme-Inc/web.site_2", "acme", "acme/api/v2", "acme/a*", "a b/c"],
)
def test_the_repository_pattern_matches_ferrygls_check(repo):
    setting = ROUTE["properties"]["repos"]["items"]

    assert matches(setting, repo) == accepted({"route": [{"repos": [repo], "team": "ENG"}]})


@pytest.mark.parametrize(
    "template",
    [
        "{title} #{number}",
        "{repo}#{number}",
        "[PR] {title} - #{number:05}",
        "{title!r}",
        "{{literal braces}} {title}",
        "{titel} #{number}",
        "{}",
        "{0}",
        "{title",
        "title}",
        "   ",
    ],
)
def test_the_title_pattern_matches_ferrygls_check(template):
    setting = table("titles")["properties"]["issue"]

    assert matches(setting, template) == accepted({**MINIMAL, "titles": {"issue": template}})


@pytest.mark.parametrize("since", ["2026-09-01", "soon", "2026-9-1", "September 2026"])
def test_the_since_pattern_matches_ferrygls_check(since):
    setting = table("sync")["properties"]["since"]

    assert matches(setting, since) == accepted({**MINIMAL, "sync": {"since": since}})
