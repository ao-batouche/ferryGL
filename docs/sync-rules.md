# Sync rules

This page describes what ferryGL syncs and how it decides what to change in Linear. The
status names are the defaults; [`[status]`](configuration.md#status) renames them.

## What's in scope

On each run, for each route, ferryGL collects the issues and pull requests in the route's
repositories that you're part of, updated since `since` if it's set. They come in three
kinds. The kind decides the title template and the status rules.

| Kind | What it is | Your roles on it |
|---|---|---|
| `issue` | An issue | You created it, or you're assigned to it |
| `pr` | A pull request you opened | Author |
| `involved` | Someone else's pull request | Assigned, reviewer, review requested, or you wrote one of its commits |

An item you have several roles on is still one item. [Configuration](configuration.md#roles)
lists the search behind each role and how to turn roles off.

Commenting on an item, or being mentioned in it, doesn't bring it in. Those would pull in
many items you only touched in passing.

## One Linear issue per item

Each GitHub item has at most one *home*: a Linear issue assigned to you, with a link to the
item's GitHub URL. ferryGL finds homes by asking Linear which issues link to each URL, so it
needs no database, and a rerun never creates duplicates.

- **Only your issues.** ferryGL ignores Linear issues assigned to anyone else, even if they
  link the same pull request. It never changes a teammate's issue.
- **Reassigning hands an issue off.** If you reassign a Linear issue that ferryGL manages, or
  unassign it, it's no longer yours. While the GitHub item is still in your scope, the next
  sync creates a new Linear issue for you.
- **Moving is fine.** You can move a Linear issue to another team or project: ferryGL still
  finds it by its link. Its new team needs the same state names.

## Pull requests that ride with an issue

A pull request you opened that refers to one of your synced issues doesn't get its own Linear
issue. ferryGL links it to the issue's Linear issue instead, so an issue and its fix are
tracked once.

A pull request refers to an issue when:

- GitHub lists the issue as one the pull request will close, for example through
  `Fixes #252` in its description, or
- its title or description mentions the issue as `#252`, `acme/api#252`, or the issue's full
  GitHub URL.

The issue must be one ferryGL syncs in the same route. Someone else's pull request always
gets its own Linear issue, even if it refers to your issue.

The Linear issue's status then follows the issue and its pull requests together, as the
[status rules](#status-rules) describe. The pull request's link doesn't set the status on its
own.

**Example.** You're assigned `acme/api#252`, and open `acme/api#335`, whose description says
"Part of #252":

1. ferryGL creates one Linear issue, "Store shared configuration once #252", links both
   GitHub URLs to it, and sets it to In Review.
2. #335 merges while #252 stays open. The Linear issue stays In Review, because the work
   isn't done until the GitHub issue is closed.
3. #252 is closed as completed. The Linear issue moves to Done.

Some cases are handled specially:

- If the pull request already had its own Linear issue before it referred to the issue, it
  stays there. It still counts toward the issue's status, so both Linear issues move.
- If the issue has no Linear issue of yours yet but its pull request does, the issue joins the
  pull request's Linear issue, as long as the pull request refers to no other synced issue.
  Otherwise each issue gets a new Linear issue, and the pull request keeps its own.
- If the reference is later edited out of the pull request, its link stays on the issue's
  Linear issue, and it keeps counting toward that issue's status.
- If the issue leaves your scope while the pull request is still in it, the pull request
  takes over the issue's Linear issue, as if it were its own. On the next run it only closes
  the Linear issue, if the pull request was merged or closed; after that, the pull request's
  own changes move its status.

## Status rules

ferryGL sets a status when it creates a Linear issue, and when the GitHub state changes
afterwards. In each table, the first row that matches wins.

**Issues**, with the pull requests you opened that refer to them:

| On GitHub | Linear status |
|---|---|
| Closed as completed | Done |
| Closed as not planned or duplicate | Canceled |
| Open, and a linked pull request is open and ready for review | In Review |
| Open, and every open linked pull request is a draft | In Progress |
| Open, and a linked pull request merged, none open | Unchanged (In Review for a new Linear issue) |
| Open | Todo |

**Your pull requests** (kind `pr`):

| On GitHub | Linear status |
|---|---|
| Merged | Done |
| Closed without merging | Canceled |
| Draft | In Progress |
| Open | In Review |

**Someone else's pull requests** (kind `involved`):

| On GitHub | Linear status |
|---|---|
| Merged | Done |
| Closed without merging | Canceled |
| Your review is requested, including again after you reviewed | Todo |
| You've reviewed it, and no new request is pending | In Review |
| Draft (assigned to you, or with your commits) | In Progress |
| Open (assigned to you, or with your commits) | In Review |

The review rows need their [roles](configuration.md#roles) on. With `review_requests = false`,
a new request doesn't move a pull request back to Todo. With `prs_reviewed = false`, a pull
request you reviewed follows the Draft and Open rows.

## Your edits win

On each Linear issue, ferryGL records the GitHub state it last acted on, in the metadata of
the GitHub link. On every run, it compares that with the current GitHub state:

- **Same state:** nothing changes, whatever the Linear status is. If you moved the issue by
  hand, it stays where you put it.
- **New state:** ferryGL moves the status as the tables above say, and records the new state.
  If the issue already has that status, it only records the state (`record` in the plan).

A status you set by hand lasts until something happens on GitHub, such as a pull request
opening, a new review request, or a merge.

## The first sync of a Linear issue

A link with no recorded state is new to ferryGL. That's the case for Linear issues you made by
hand that ferryGL adopts, and for GitHub links you added to your issues yourself. On that
first sync ferryGL is cautious:

- If GitHub says the work is done or canceled, and the Linear issue is still open, it moves
  the issue to Done or Canceled.
- Otherwise it leaves the status alone, and only records the GitHub state.

It never reopens a Linear issue, or moves it in any other way, on a first sync.

## Linear issues you made by hand

Before creating a Linear issue, ferryGL looks for one you already made. It checks your Linear
issues in the route's team (assigned to you, open or closed) for one that mentions the item:

- its GitHub URL is in the description or in a link, or
- its title ends with `#<number>`, when the route has a single repository (and no `owner/*`)
  and the description has no link to another GitHub issue or pull request.

For an issue, a Linear issue that mentions one of your pull requests that refers to it counts
too.

An issue never matches if it's already linked to a different GitHub item, since it's about
that item, or if it's already the home of another item.

Two more rules cover pull requests you opened:

- If nothing mentions the pull request, a Linear issue that mentions an issue it refers to
  (in the same repository) counts, unless that Linear issue is already linked to a pull
  request.
- If an issue has no Linear issue of yours but its pull request already has one, the issue
  joins that Linear issue, as long as the pull request refers to no other synced issue.

When exactly one Linear issue matches, ferryGL links the item to it (`adopt` in the plan)
instead of creating a new one. Adopting follows the first-sync rule: the status moves only if
GitHub says the work is done or canceled.

When several match, ferryGL skips the item (`skip` in the plan) and lists them. Add the GitHub
link to the right one by hand, and the next run picks it up.

## What ferryGL never does

- Write anything to GitHub.
- Change a Linear issue that isn't assigned to you.
- Delete Linear issues or links.
- Change a title or description after creating the issue.
- Reopen a Linear issue on its first sync.
- Clean up after an item leaves your scope. If you're unassigned, a review request is
  withdrawn, or `since` moves past an item, its Linear issue stays as it is. The one
  exception is an issue whose [pull request rides with it](#pull-requests-that-ride-with-an-issue)
  and stays in scope.
