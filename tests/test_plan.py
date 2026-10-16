from __future__ import annotations

import pytest
from builders import (
    NOW,
    OTHER,
    VIEWER,
    attachment,
    config,
    involved,
    issue,
    linear_issue,
    pr,
    review,
)

from ferrygl.config import Config, SyncOptions
from ferrygl.model import Action, Attachment, Change, GitHubItem, LinearIssue, Role
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
    return link.metadata["ferrygl_state"]


# New items


def test_new_assigned_issue_is_created_as_todo():
    [change] = run([issue(12)])

    assert summary([change]) == [("create", "acme/api#12", None, "Todo")]
    assert change.title == "Issue 12 #12"
    assert "https://github.com/acme/api/issues/12" in change.description
    [link] = change.links
    assert link.url == "https://github.com/acme/api/issues/12"
    assert recorded_state(link) == "issue:open"
    assert link.metadata["ferrygl_synced_at"] == NOW
    assert all(isinstance(v, str | int) for v in link.metadata.values())


def test_issue_and_the_pull_request_that_refers_to_it_share_one_linear_issue():
    [change] = run([issue(12), pr(15, refs=(12,))])

    assert summary([change]) == [("create", "acme/api#12", None, "In Review")]
    assert [link.url for link in change.links] == [
        "https://github.com/acme/api/issues/12",
        "https://github.com/acme/api/pull/15",
    ]
    assert change.links[1].metadata["ferrygl_linked_to"] == "acme/api#12"
    assert "ferrygl_state" not in change.links[1].metadata


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


def test_a_new_review_request_after_your_review_moves_it_back_to_todo():
    home = linear_issue("ENG-1", state="In Review")
    item = review(20, reviewed=True, requested=True)

    changes = run([item], [attachment(item, home, recorded="involved:reviewed")])

    assert summary(changes) == [("update", "acme/api#20", "ENG-1", "Todo")]


def test_issue_you_created_is_synced_like_an_assigned_one():
    [change] = run([issue(12, roles=frozenset({Role.AUTHOR}))])

    assert summary([change]) == [("create", "acme/api#12", None, "Todo")]
    assert change.links[0].metadata["ferrygl_kind"] == "issue"


def test_pull_request_with_your_commits_gets_an_involved_issue():
    [change] = run([involved(40, Role.COMMITTER)])

    assert summary([change]) == [("create", "acme/api#40", None, "In Review")]
    assert change.title == "[PR] PR 40 - #40"
    assert change.links[0].metadata["ferrygl_kind"] == "involved"


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

    old = {"x": 1, "ferrygl_stale": "gone"}
    [change] = run([item], [attachment(item, home, recorded="pr:open", metadata=old)])

    assert change.links[0].metadata["x"] == 1
    assert "ferrygl_stale" not in change.links[0].metadata
    assert recorded_state(change.links[0]) == "pr:merged"


def test_linear_issue_assigned_to_someone_else_is_not_touched():
    theirs = linear_issue("ENG-7", state="Todo", assignee=OTHER)
    item = review(20, reviewed=True)

    changes = run([item], [attachment(item, theirs, recorded="involved:requested")])

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


def test_adoption_skips_an_issue_about_another_repository():
    # Your Linear issue for upstream/lib#15 ends in "#15" too.
    theirs = linear_issue(
        "ENG-50",
        state="In Review",
        title="Upstream fix - #15",
        description="Pull request: https://github.com/upstream/lib/pull/15",
        urls=("https://github.com/upstream/lib/pull/15",),
    )
    mentions_only = linear_issue(
        "ENG-51", title="Upstream fix - #15", description="https://github.com/upstream/lib/pull/15"
    )

    changes = run([pr(15, state="MERGED")], candidates=[theirs, mentions_only])

    assert summary(changes) == [("create", "acme/api#15", None, "Done")]


def test_pull_request_fallback_ignores_numbers_of_known_pull_requests():
    # #20 is a pull request you reviewed, not an issue.
    tracks_review = linear_issue("ENG-3", title="Review the checkpoints page - #20")

    changes = run([pr(30, refs=(20,)), review(20, reviewed=True)], candidates=[tracks_review])

    assert summary(changes) == [
        ("create", "acme/api#30", None, "In Review"),
        ("adopt", "acme/api#20", "ENG-3", None),
    ]


def test_pull_request_fallback_skips_a_linear_issue_linked_to_a_pull_request():
    pr_item = linear_issue(
        "ENG-3", title="Old PR #88", urls=("https://github.com/acme/api/pull/88",)
    )
    issue_item = linear_issue(
        "ENG-4", title="Issue #88", urls=("https://github.com/acme/api/issues/88",)
    )

    assert summary(run([pr(89, refs=(88,))], candidates=[pr_item])) == [
        ("create", "acme/api#89", None, "In Review")
    ]
    assert summary(run([pr(89, refs=(88,))], candidates=[issue_item])) == [
        ("adopt", "acme/api#89", "ENG-4", None)
    ]


def test_issue_joins_the_linear_issue_its_pull_request_already_has():
    own = linear_issue("ENG-3", state="In Review")
    items = [issue(12), pr(15, refs=(12,))]

    [change] = run(items, [attachment(items[1], own, recorded="pr:open")])

    assert summary([change]) == [("adopt", "acme/api#12", "ENG-3", None)]
    assert [link.url for link in change.links] == ["https://github.com/acme/api/issues/12"]


@pytest.mark.parametrize(
    "item",
    [pr(15), pr(15, draft=True), pr(15, state="MERGED"), pr(15, state="CLOSED")],
    ids=["open", "draft", "merged", "closed"],
)
def test_a_pull_request_whose_issue_left_your_scope_leaves_its_linear_issue_alone(item):
    # PR 15 rode with #12 on ENG-7. You were then unassigned from #12, so it's no longer
    # synced, while PR 15 still is. ENG-7 is #12's, so PR 15 doesn't move it.
    home = linear_issue("ENG-7", state="In Review")
    rider_meta = {"ferrygl_v": 1, "ferrygl_kind": "pr", "ferrygl_linked_to": "acme/api#12"}

    assert run([item], [attachment(item, home, metadata=rider_meta)]) == []


def test_a_pull_request_rides_again_when_its_issue_comes_back_into_scope():
    # Same as above, then you're assigned to #12 again: the two share ENG-7 once more.
    home = linear_issue("ENG-7", state="In Review")
    items = [issue(12), pr(15, state="MERGED", refs=(12,))]
    rider_meta = {"ferrygl_v": 1, "ferrygl_kind": "pr", "ferrygl_linked_to": "acme/api#12"}
    links = [
        attachment(items[0], home, recorded="issue:open:pr_open"),
        attachment(items[1], home, metadata=rider_meta),
    ]

    # #12 is still open, so the merged fix doesn't make ENG-7 done.
    assert summary(run(items, links)) == [("record", "acme/api#12", "ENG-7", None)]


def test_pull_request_whose_link_sits_on_an_issue_home_still_rides_with_it():
    # The "#12" reference was edited out of PR 15, but its link stays on ENG-7.
    home = linear_issue("ENG-7", state="In Review")
    items = [issue(12), pr(15)]
    rider_meta = {"ferrygl_v": 1, "ferrygl_kind": "pr", "ferrygl_linked_to": "acme/api#12"}
    links = [
        attachment(items[0], home, recorded="issue:open:pr_open"),
        attachment(items[1], home, metadata=rider_meta),
    ]

    assert run(items, links) == []


def test_pull_request_with_its_own_linear_issue_stays_there():
    issue_home = linear_issue("ENG-1", state="In Review")
    pr_home = linear_issue("ENG-9", state="In Review")
    items = [issue(12), pr(15, refs=(12,))]
    links = [
        attachment(items[0], issue_home, recorded="issue:open:pr_open"),
        attachment(items[1], pr_home, recorded="pr:open"),
    ]

    assert run(items, links) == []


# Comments, with [sync] comment on

COMMENTS = config(sync=SyncOptions(comment=True))


def comments(changes: list[Change]) -> list[str | None]:
    return [change.comment for change in changes]


def test_no_comments_by_default():
    home = linear_issue("ENG-1", state="Todo")
    items = [issue(12), pr(15, refs=(12,)), pr(16)]

    changes = run(items, [attachment(items[0], home, recorded="issue:open")])

    assert [c.action.value for c in changes] == ["update", "link", "create"]
    assert comments(changes) == [None, None, None]


def test_comment_on_a_new_issue_names_its_items_and_status():
    changes = run([issue(12), pr(15, refs=(12,))], cfg=COMMENTS)

    assert comments(changes) == [
        "(BY-FGL) Created from acme/api#12 and acme/api#15. "
        "Status In Review: acme/api#12 has a pull request open for review."
    ]


def test_comments_on_a_status_change_and_a_new_link():
    home = linear_issue("ENG-1", state="Todo")
    items = [issue(12), pr(15, refs=(12,))]

    changes = run(items, [attachment(items[0], home, recorded="issue:open")], cfg=COMMENTS)

    assert comments(changes) == [
        "(BY-FGL) Status Todo → In Review: acme/api#12 has a pull request open for review.",
        "(BY-FGL) Linked acme/api#15, a pull request that refers to acme/api#12.",
    ]


def test_comment_when_a_pull_request_is_merged():
    home = linear_issue("ENG-1", state="In Review")
    item = pr(15, state="MERGED")

    changes = run([item], [attachment(item, home, recorded="pr:open")], cfg=COMMENTS)

    assert comments(changes) == ["(BY-FGL) Status In Review → Done: acme/api#15 was merged."]


def test_comment_when_only_the_github_state_is_recorded():
    home = linear_issue("ENG-1", state="In Review")
    items = [issue(12), pr(15, state="MERGED", refs=(12,))]
    links = [
        attachment(items[0], home, recorded="issue:open:pr_open"),
        attachment(items[1], home),
    ]

    changes = run(items, links, cfg=COMMENTS)

    assert summary(changes) == [("record", "acme/api#12", "ENG-1", None)]
    assert comments(changes) == [
        "(BY-FGL) Status stays In Review: "
        "acme/api#12's pull request was merged, and the issue is still open."
    ]


def test_comment_on_an_adopted_issue_that_github_has_closed():
    mine = linear_issue("ENG-3", state="In Progress", title="Fix the importer #12")
    item = issue(12, state="CLOSED", reason="DUPLICATE")

    changes = run([item], candidates=[mine], cfg=COMMENTS)

    assert summary(changes) == [("adopt", "acme/api#12", "ENG-3", "Canceled")]
    assert comments(changes) == [
        "(BY-FGL) Linked acme/api#12. "
        "Status In Progress → Canceled: acme/api#12 was closed as duplicate."
    ]


def test_skipped_items_get_no_comment():
    mentions = "https://github.com/acme/api/pull/15"
    candidates = [linear_issue("ENG-3", description=mentions), linear_issue("ENG-8", title="#15")]

    changes = run([pr(15)], candidates=candidates, cfg=COMMENTS)

    assert [(c.action.value, c.comment) for c in changes] == [("skip", None)]


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
        (review(3, state="MERGED"), [], "involved:merged", "done", "done"),
        (involved(3, Role.COMMITTER), [], "involved:open", "in_review", "in_review"),
        (
            involved(3, Role.ASSIGNEE, draft=True),
            [],
            "involved:draft",
            "in_progress",
            "in_progress",
        ),
        (
            involved(3, Role.COMMITTER, Role.REVIEWER),
            [],
            "involved:reviewed",
            "in_review",
            "in_review",
        ),
        (involved(3, Role.REVIEWER, Role.REQUESTED), [], "involved:requested", "todo", "todo"),
    ],
)
def test_github_state(item, linked, key, on_change, on_create):
    state = github_state(item, linked)

    assert (state.key, state.on_change, state.on_create) == (key, on_change, on_create)


@pytest.mark.parametrize(
    ("item", "linked", "said"),
    [
        (issue(1), [], "acme/api#1 is open"),
        (issue(1), [pr(2, draft=True)], "acme/api#1 has a draft pull request"),
        (issue(1, state="CLOSED", reason="COMPLETED"), [], "acme/api#1 was closed as completed"),
        (
            issue(1, state="CLOSED", reason="NOT_PLANNED"),
            [],
            "acme/api#1 was closed as not planned",
        ),
        (pr(2, draft=True), [], "acme/api#2 is a draft"),
        (pr(2), [], "acme/api#2 is open for review"),
        (pr(2, state="CLOSED"), [], "acme/api#2 was closed without being merged"),
        (review(3), [], "a review is requested on acme/api#3"),
        (review(3, reviewed=True), [], "acme/api#3 was reviewed"),
    ],
)
def test_github_state_summary_for_comments(item, linked, said):
    assert github_state(item, linked).summary == said
