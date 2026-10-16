from __future__ import annotations

import io
from pathlib import Path

import pytest
from builders import STATE_TYPES, TEAM_ID, VIEWER, attachment, linear_issue, pr
from test_github import issue_node, pr_node

from ferrygl.cli import linear_api_key
from ferrygl.config import Config, ConfigError, Route, SyncOptions
from ferrygl.github import GitHubError
from ferrygl.linear import LinearError, State, Team
from ferrygl.model import LinearIssue
from ferrygl.sync import EXIT_FAILED, EXIT_OK, EXIT_SETUP, run_check, run_sync


class FakeLinear:
    def __init__(self, attachments=None, candidates=(), states=None, comments_fail=False) -> None:
        self.attachments = attachments or {}
        self.candidates = list(candidates)
        names = states or list(STATE_TYPES)
        self.states = {n: State(f"state-{n}", n, STATE_TYPES.get(n, "started")) for n in names}
        self.comments_fail = comments_fail
        self.writes: list[tuple] = []
        self.created = 0

    def viewer(self):
        return VIEWER, "Alice"

    def find_team(self, ref):
        return Team(TEAM_ID, "ENG", "Engineering") if ref == "ENG" else None

    def team_states(self, team_id):
        return self.states

    def find_project(self, name, team_id):
        return "project-1" if name == "Acme" else None

    def find_label(self, name, team_id):
        return "label-1" if name == "ferryGL" else None

    def attachments_for_urls(self, urls):
        return {url: self.attachments.get(url, []) for url in urls}

    def assigned_issues(self, team_id, assignee_id):
        return self.candidates

    def create_issue(self, **fields):
        self.created += 1
        self.writes.append(("create_issue", fields))
        identifier = f"ENG-{100 + self.created}"
        return LinearIssue(
            f"id-{identifier}", identifier, fields["title"], "Todo", "unstarted", TEAM_ID, VIEWER
        )

    def create_attachment(self, issue_id, link):
        self.writes.append(("create_attachment", issue_id, link.url))

    def update_attachment(self, link):
        self.writes.append(("update_attachment", link.attachment_id))

    def update_issue_state(self, issue_id, state_id):
        self.writes.append(("update_issue_state", issue_id, state_id))

    def create_comment(self, issue_id, body):
        if self.comments_fail:
            raise LinearError("Linear reported commentCreate as unsuccessful")
        self.writes.append(("create_comment", issue_id, body))


class FakeGitHub:
    def __init__(self, login="alice", results=None, readable=True, fail=False) -> None:
        self.login = login
        self.results = results or {}
        self.readable = readable
        self.fail = fail

    def viewer_login(self):
        if self.fail:
            raise GitHubError("GitHub rejected the token (HTTP 401)")
        return self.login

    def can_read(self, repo):
        return self.readable

    def search(self, query, with_commits=False):
        for marker, nodes in self.results.items():
            if marker in query:
                return nodes
        return []


def make_config(*routes: Route, **sync) -> Config:
    return Config(routes=routes or (Route(("acme/api",), "ENG"),), sync=SyncOptions(**sync))


def sync(config, linear, github, apply=False):
    out, err = io.StringIO(), io.StringIO()
    github_for = github if callable(github) else (lambda user: github)
    code = run_sync(
        config, apply=apply, linear=linear, github_for=github_for, out=out, err=err, now="t"
    )
    return code, out.getvalue(), err.getvalue()


def test_dry_run_prints_the_plan_and_writes_nothing():
    github = FakeGitHub(results={"assignee:": [issue_node(12)], "is:pr author:": [pr_node(15)]})
    linear = FakeLinear()

    code, out, _ = sync(make_config(), linear, github)

    assert code == EXIT_OK
    assert 'acme/api#12  issue     create  "Issue 12 #12" -> Todo' in out
    assert 'acme/api#15  pr        create  "PR 15 - #15" -> In Review' in out
    assert "2 changes (2 create). Run with --apply to make them." in out
    assert linear.writes == []


def test_apply_creates_issues_with_state_project_label_and_links():
    github = FakeGitHub(results={"assignee:": [issue_node(12)]})
    linear = FakeLinear()
    config = make_config(Route(("acme/api",), "ENG", project="Acme"), label="ferryGL")

    code, out, _ = sync(config, linear, github, apply=True)

    assert code == EXIT_OK
    kind, fields = linear.writes[0]
    assert kind == "create_issue"
    assert fields["state_id"] == "state-Todo"
    assert fields["assignee_id"] == VIEWER
    assert fields["project_id"] == "project-1"
    assert fields["label_ids"] == ["label-1"]
    assert linear.writes[1] == (
        "create_attachment",
        "id-ENG-101",
        "https://github.com/acme/api/issues/12",
    )
    assert "created ENG-101 for acme/api#12" in out
    assert "Applied 1 change (1 create)." in out


def test_apply_updates_status_before_recording_the_github_state():
    item = pr(15, state="MERGED")
    home = linear_issue("ENG-1", state="In Review")
    linear = FakeLinear(attachments={item.url: [attachment(item, home, recorded="pr:open")]})
    merged = {**pr_node(15), "state": "MERGED"}

    code, out, _ = sync(
        make_config(), linear, FakeGitHub(results={"is:pr author:": [merged]}), apply=True
    )

    assert code == EXIT_OK
    assert linear.writes == [
        ("update_issue_state", "id-ENG-1", "state-Done"),
        ("update_attachment", "att-ENG-1-15"),
    ]
    assert "ENG-1 In Review -> Done" in out


def merged_pr_on_eng_1(**fake):
    """acme/api#15 was merged on GitHub; ENG-1 is In Review with the link recorded as open."""
    item = pr(15, state="MERGED")
    home = linear_issue("ENG-1", state="In Review")
    link = attachment(item, home, recorded="pr:open")
    linear = FakeLinear(attachments={item.url: [link]}, **fake)
    github = FakeGitHub(results={"is:pr author:": [{**pr_node(15), "state": "MERGED"}]})
    return linear, github


def test_preview_shows_each_comment_and_writes_nothing():
    linear, github = merged_pr_on_eng_1()

    code, out, _ = sync(make_config(comment=True), linear, github)

    assert code == EXIT_OK
    assert "comment: (BY-FGL) Status In Review → Done: acme/api#15 was merged." in out
    assert linear.writes == []


def test_apply_comments_after_the_status_and_before_recording_the_github_state():
    linear, github = merged_pr_on_eng_1()

    code, _, _ = sync(make_config(comment=True), linear, github, apply=True)

    assert code == EXIT_OK
    assert linear.writes == [
        ("update_issue_state", "id-ENG-1", "state-Done"),
        ("create_comment", "id-ENG-1", "(BY-FGL) Status In Review → Done: acme/api#15 was merged."),
        ("update_attachment", "att-ENG-1-15"),
    ]


def test_a_failed_comment_leaves_the_github_state_unrecorded_for_the_next_run():
    linear, github = merged_pr_on_eng_1(comments_fail=True)

    code, _, err = sync(make_config(comment=True), linear, github, apply=True)

    assert code == EXIT_FAILED
    assert linear.writes == [("update_issue_state", "id-ENG-1", "state-Done")]
    assert "acme/api#15: update failed: Linear reported commentCreate" in err


def test_apply_comments_on_a_new_issue_before_linking_it():
    github = FakeGitHub(results={"assignee:": [issue_node(12)]})
    linear = FakeLinear()

    code, _, _ = sync(make_config(comment=True), linear, github, apply=True)

    assert code == EXIT_OK
    assert [write[0] for write in linear.writes] == [
        "create_issue",
        "create_comment",
        "create_attachment",
    ]
    assert linear.writes[1][1:] == (
        "id-ENG-101",
        "(BY-FGL) Created from acme/api#12. Status Todo: acme/api#12 is open.",
    )


def test_a_failed_comment_on_a_new_issue_names_the_issue():
    github = FakeGitHub(results={"assignee:": [issue_node(12)]})
    linear = FakeLinear(comments_fail=True)

    code, _, err = sync(make_config(comment=True), linear, github, apply=True)

    assert code == EXIT_FAILED
    assert [write[0] for write in linear.writes] == ["create_issue"]
    assert "acme/api#12: create failed: ENG-101 was created, but Linear reported" in err


def test_missing_linear_settings_stop_before_reading_github():
    linear = FakeLinear(states=["Todo", "Done"])

    def github_for(user):
        raise AssertionError("GitHub should not be read")

    code, _, err = sync(
        make_config(Route(("acme/api",), "ENG", project="Nope")), linear, github_for
    )

    assert code == EXIT_SETUP
    assert "no state named 'In Progress', 'In Review', 'Canceled'" in err
    assert "project 'Nope' not found" in err
    assert "Nothing changed" in err


def test_a_failing_route_does_not_stop_the_others():
    good = FakeGitHub(results={"assignee:": [issue_node(12)]})
    bad = FakeGitHub(fail=True)
    config = make_config(
        Route(("acme/api",), "ENG", github_user="broken"),
        Route(("acme/web",), "ENG"),
    )

    code, out, err = sync(config, FakeLinear(), lambda user: bad if user == "broken" else good)

    assert code == EXIT_FAILED
    assert "route 1 (acme/api -> ENG): GitHub rejected the token" in err
    assert "acme/api#12" in out  # route 2's search returns the same fake node


def test_nothing_to_do():
    code, out, _ = sync(make_config(), FakeLinear(), FakeGitHub())

    assert code == EXIT_OK
    assert out.strip() == "No changes."


def test_check_reports_each_setting():
    out = io.StringIO()
    config = make_config(Route(("acme/api",), "ENG", project="Acme"), label="missing")

    code = run_check(config, linear=FakeLinear(), github_for=lambda u: FakeGitHub(), out=out)

    text = out.getvalue()
    assert code == EXIT_FAILED
    assert "✓ Linear  signed in as Alice" in text
    assert "✓ GitHub  reading as alice" in text
    assert "✓ repo    acme/api" in text
    assert "✓ Linear  project Acme" in text
    assert "✗ Linear  label 'missing' not found" in text


def test_linear_api_key_comes_from_the_environment_or_a_file(tmp_path: Path):
    key_file = tmp_path / "linear.key"
    key_file.write_text("lin_from_file\n", encoding="utf-8")
    config = Config(routes=(Route(("acme/api",), "ENG"),), linear_api_key_file=key_file)

    assert linear_api_key(config, env={"LINEAR_API_KEY": "lin_env"}) == "lin_env"
    assert linear_api_key(config, env={}) == "lin_from_file"
    with pytest.raises(ConfigError, match="set LINEAR_API_KEY"):
        linear_api_key(Config(routes=config.routes), env={})
