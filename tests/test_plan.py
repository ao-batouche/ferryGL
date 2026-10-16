from __future__ import annotations

import pytest
from builders import (
    NOW,
    OTHER,
    VIEWER,
    attachment,
    config,
    issue,
    linear_issue,
    pr,
    review,
)

from ferrygl.config import Config
from ferrygl.model import Action, Attachment, Change, GitHubItem, LinearIssue
from ferrygl.plan import PlanInput, github_state, plan


def run(
    items: list[GitHubItem],
    attachments: list[Attachment] = (),
    candidates: list[LinearIssue] = (),
    cfg: Config | None = None,
) -> list[Change]:
    cfg = cfg or config()
    by_url: dict[str, list[Attachment]] = {}
    for found in attachments:
        by_url.setdefault(found.url, []).append(found)
    return plan(
        PlanInput(
            route=cfg.routes[0],
            config=cfg,
            viewer_id=VIEWER,
            items=items,
            attachments=by_url,
            candidates=candidates,
            now=NOW,
        )
    )


def summary(changes: list[Change]) -> list[tuple]:
    return [
        (c.action.value, c.item.label, c.issue.identifier if c.issue else None, c.status_to)
        for c in changes
    ]


def recorded_state(link) -> str:
    return link.metadata["ferrygl"]["state"]


# New items


def test_new_assigned_issue_is_created_as_todo():
    [change] = run([issue(12)])

    assert summary([change]) == [("create", "acme/api#12", None, "Todo")]
    assert change.title == "Issue 12 #12"
    assert "https://github.com/acme/api/issues/12" in change.description
    [link] = change.links
    assert link.url == "https://github.com/acme/api/issues/12"
    assert recorded_state(link) == "issue:open"
    assert link.metadata["ferrygl"]["synced_at"] == NOW


def test_issue_and_the_pull_request_that_refers_to_it_share_one_linear_issue():
    [change] = run([issue(12), pr(15, refs=(12,))])

    assert summary([change]) == [("create", "acme/api#12", None, "In Review")]
    assert [link.url for link in change.links] == [
        "https://github.com/acme/api/issues/12",
        "https://github.com/acme/api/pull/15",
    ]
    assert change.links[1].metadata["ferrygl"]["linked_to"] == "acme/api#12"


def test_pull_request_without_a_synced_issue_gets_its_own_linear_issue():
    [change] = run([pr(15, refs=(99,))])

    assert summary([change]) == [("create", "acme/api#15", None, "In Review")]
    assert change.title == "PR 15 - #15"


def test_draft_pull_request_is_in_progress():
    assert summary(run([pr(15, draft=True)])) == [("create", "acme/api#15", None, "In Progress")]


def test_review_request_then_review():
    [requested] = run([review(20)])
    [reviewed] = run([review(20, reviewed=True)])

    assert requested.title == "[PR] PR 20 - #20"
    assert requested.status_to == "Todo"
    assert reviewed.status_to == "In Review"


def test_title_templates_come_from_config():
    from ferrygl.config import Titles

    cfg = config(titles=Titles(issue="{repo}#{number}"))
    [change] = run([issue(12)], cfg=cfg)

    assert change.title == "acme/api#12"


# Existing Linear issues


def test_nothing_changes_when_the_github_state_is_unchanged():
    home = linear_issue("ENG-1", state="In Review")
    items = [issue(12), pr(15, refs=(12,))]
    links = [
        attachment(items[0], home, recorded="issue:open:pr_open"),
        attachment(items[1], home),
    ]

    assert run(items, links) == []


def test_status_follows_a_github_change_and_the_new_pull_request_is_linked():
    home = linear_issue("ENG-1", state="Todo")
    items = [issue(12), pr(15, refs=(12,))]

    changes = run(items, [attachment(items[0], home, recorded="issue:open")])

    assert summary(changes) == [
        ("update", "acme/api#12", "ENG-1", "In Review"),
        ("link", "acme/api#15", "ENG-1", None),
    ]
    assert changes[0].status_from == "Todo"
    assert recorded_state(changes[0].links[0]) == "issue:open:pr_open"
    assert changes[0].links[0].attachment_id == "att-ENG-1-12"


def test_merged_fix_leaves_an_open_issue_in_review():
    home = linear_issue("ENG-1", state="In Review")
    items = [issue(12), pr(15, state="MERGED", refs=(12,))]
    links = [
        attachment(items[0], home, recorded="issue:open:pr_open"),
        attachment(items[1], home),
    ]

    [change] = run(items, links)

    assert summary([change]) == [("record", "acme/api#12", "ENG-1", None)]
    assert recorded_state(change.links[0]) == "issue:open:pr_merged"


@pytest.mark.parametrize(
    ("reason", "status"),
    [("COMPLETED", "Done"), ("NOT_PLANNED", "Canceled"), ("DUPLICATE", "Canceled")],
)
def test_closed_issue_moves_to_done_or_canceled(reason, status):
    home = linear_issue("ENG-1", state="In Review")
    item = issue(12, state="CLOSED", reason=reason)

    changes = run([item], [attachment(item, home, recorded="issue:open:pr_merged")])

    assert summary(changes) == [("update", "acme/api#12", "ENG-1", status)]


def test_status_set_by_hand_is_left_alone_until_github_changes():
    home = linear_issue("ENG-1", state="Done")
    item = pr(15)

    assert run([item], [attachment(item, home, recorded="pr:open")]) == []


def test_other_metadata_on_the_link_is_kept():
    home = linear_issue("ENG-1", state="In Review")
    item = pr(15, state="MERGED")

    [change] = run([item], [attachment(item, home, recorded="pr:open", metadata={"x": 1})])

    assert change.links[0].metadata["x"] == 1
    assert recorded_state(change.links[0]) == "pr:merged"


def test_linear_issue_assigned_to_someone_else_is_not_touched():
    theirs = linear_issue("ENG-7", state="Todo", assignee=OTHER)
    item = review(20, reviewed=True)

    changes = run([item], [attachment(item, theirs, recorded="review:requested")])

    assert summary(changes) == [("create", "acme/api#20", None, "In Review")]


# Linear issues ferryGL hasn't synced before


def test_first_sync_of_a_hand_made_issue_only_records_the_state():
    home = linear_issue("ENG-1", state="Todo")
    item = pr(15)

    [change] = run([item], [attachment(item, home)])

    assert summary([change]) == [("record", "acme/api#15", "ENG-1", None)]


def test_first_sync_closes_a_hand_made_issue_when_github_is_done():
    home = linear_issue("ENG-1", state="In Review")
    item = pr(15, state="MERGED")

    changes = run([item], [attachment(item, home)])

    assert summary(changes) == [("update", "acme/api#15", "ENG-1", "Done")]


def test_first_sync_never_reopens_an_issue_closed_by_hand():
    home = linear_issue("ENG-1", state="Done")
    item = pr(15)

    assert summary(run([item], [attachment(item, home)])) == [
        ("record", "acme/api#15", "ENG-1", None)
    ]


# Adopting Linear issues that mention an item but have no link yet


def test_adopts_your_issue_whose_description_mentions_the_url():
    mine = linear_issue("ENG-3", description="See https://github.com/acme/api/pull/15#event-1")

    [change] = run([pr(15)], candidates=[mine])

    assert summary([change]) == [("adopt", "acme/api#15", "ENG-3", None)]
    assert [link.url for link in change.links] == ["https://github.com/acme/api/pull/15"]
    assert recorded_state(change.links[0]) == "pr:open"


def test_adopts_by_title_number_when_the_route_has_one_repository():
    mine = linear_issue("ENG-3", title="Fix the importer - #15")

    assert summary(run([pr(15)], candidates=[mine])) == [("adopt", "acme/api#15", "ENG-3", None)]


def test_title_number_is_ignored_when_the_route_has_several_repositories():
    mine = linear_issue("ENG-3", title="Fix the importer - #15")
    cfg = config("acme/api", "acme/web")

    assert summary(run([pr(15)], candidates=[mine], cfg=cfg))[0][0] == "create"


def test_a_longer_number_in_a_url_is_not_a_mention():
    other = linear_issue("ENG-3", description="https://github.com/acme/api/issues/120")

    assert summary(run([issue(12)], candidates=[other]))[0][0] == "create"


def test_several_matching_issues_need_a_decision():
    a = linear_issue("ENG-3", description="https://github.com/acme/api/issues/12")
    b = linear_issue("ENG-4", title="Same thing #12")

    [change] = run([issue(12)], candidates=[a, b])

    assert change.action is Action.SKIP
    assert "ENG-3, ENG-4" in change.note


def test_issue_adopts_the_hand_made_item_of_its_pull_request():
    mine = linear_issue("ENG-3", description="https://github.com/acme/api/pull/15")

    [change] = run([issue(12), pr(15, refs=(12,))], candidates=[mine])

    assert summary([change]) == [("adopt", "acme/api#12", "ENG-3", None)]
    assert [link.url for link in change.links] == [
        "https://github.com/acme/api/issues/12",
        "https://github.com/acme/api/pull/15",
    ]


def test_pull_request_adopts_the_linear_issue_of_the_issue_it_refers_to():
    # acme/api#88 isn't assigned to you, but your Linear issue tracks it.
    mine = linear_issue("ENG-3", title="Report the step graph #88")

    [change] = run([pr(89, refs=(88,))], candidates=[mine])

    assert summary([change]) == [("adopt", "acme/api#89", "ENG-3", None)]


def test_a_direct_mention_wins_over_a_referenced_issue():
    tracks_issue = linear_issue("ENG-3", title="Report the step graph #88")
    tracks_pr = linear_issue("ENG-4", description="https://github.com/acme/api/pull/89")

    [change] = run([pr(89, refs=(88,))], candidates=[tracks_issue, tracks_pr])

    assert summary([change]) == [("adopt", "acme/api#89", "ENG-4", None)]


def test_adopting_moves_an_open_linear_issue_to_done_when_github_is_done():
    mine = linear_issue("ENG-3", state="In Review", title="Old work #12")

    [change] = run([issue(12, state="CLOSED", reason="COMPLETED")], candidates=[mine])

    assert summary([change]) == [("adopt", "acme/api#12", "ENG-3", "Done")]
    assert change.status_from == "In Review"


def test_an_issue_already_home_to_another_item_is_not_adopted():
    home = linear_issue("ENG-3", title="Both #15", urls=("https://github.com/acme/api/pull/16",))
    items = [pr(15), pr(16)]

    changes = run(items, [attachment(items[1], home, recorded="pr:open")], candidates=[home])

    assert summary(changes) == [("create", "acme/api#15", None, "In Review")]


def test_pull_request_with_its_own_linear_issue_stays_there():
    own = linear_issue("ENG-9", state="In Review")
    items = [issue(12), pr(15, refs=(12,))]

    changes = run(items, [attachment(items[1], own, recorded="pr:open")])

    assert summary(changes) == [("create", "acme/api#12", None, "In Review")]
    assert [link.url for link in changes[0].links] == ["https://github.com/acme/api/issues/12"]


# GitHub state derivation


@pytest.mark.parametrize(
    ("item", "linked", "key", "on_change", "on_create"),
    [
        (issue(1), [], "issue:open", "todo", "todo"),
        (issue(1), [pr(2, draft=True)], "issue:open:pr_draft", "in_progress", "in_progress"),
        (issue(1), [pr(2, draft=True), pr(3)], "issue:open:pr_open", "in_review", "in_review"),
        (issue(1), [pr(2, state="MERGED")], "issue:open:pr_merged", None, "in_review"),
        (issue(1), [pr(2, state="CLOSED")], "issue:open", "todo", "todo"),
        (pr(2, state="CLOSED"), [], "pr:closed", "canceled", "canceled"),
        (review(3, state="MERGED"), [], "review:merged", "done", "done"),
    ],
)
def test_github_state(item, linked, key, on_change, on_create):
    state = github_state(item, linked)

    assert (state.key, state.on_change, state.on_create) == (key, on_change, on_create)
