# ferryGL

Ferry your GitHub work into your own Linear workspace, from repositories you can read but
don't administer.

> **Status: design draft.** Nothing described here is implemented yet.
>
> ferryGL is an independent project. It is not affiliated with or endorsed by Linear or GitHub.

## Who it's for

You write code in someone else's GitHub organization (a client, a partner, an upstream
project), but your team plans and reports work in its own Linear workspace.

Linear's GitHub integration doesn't cover this case:

- It must be installed by an owner, or a repository admin, of the GitHub organization.
- A GitHub organization can connect to only one Linear workspace. If the organization uses
  Linear itself, your workspace can't connect to it.

So you copy issues and pull requests into Linear by hand and move their status as they
progress. ferryGL does that for you with your own credentials. It never writes to GitHub.

## What it does

On each run, ferryGL searches GitHub for your activity in the repositories you list, and
makes sure each item has exactly one Linear issue with the right status.

| GitHub item | GitHub search | Default Linear title |
|---|---|---|
| Issues assigned to you | `is:issue assignee:@me` | `{title} #{number}` |
| Pull requests you opened | `is:pr author:@me` | `{title} - #{number}` |
| Pull requests you reviewed | `is:pr reviewed-by:@me -author:@me` | `[PR] {title} - #{number}` |
| Pull requests waiting for your review | `is:pr user-review-requested:@me` | `[PR] {title} - #{number}` |

A pull request that refers to a synced issue, through a closing keyword such as
`Fixes #252` or a plain `#252` in its description, is attached to that issue's Linear item
instead of getting its own.

For example, you're assigned `acme/api#252` and open `acme/api#335`, whose description says
"Part of #252":

1. ferryGL creates one Linear issue, "Store shared configuration once #252", links both
   GitHub URLs to it and sets it to In Review.
2. #335 merges while #252 stays open. The Linear issue stays In Review, because the work
   isn't done until the GitHub issue is closed.
3. #252 is closed as completed. The Linear issue moves to Done.

## Status rules

Linear state names are configurable. These are the defaults:

| GitHub state | Linear status |
|---|---|
| Issue open, no linked pull request open | Todo |
| Issue open, a linked pull request open | In Review (In Progress if every open one is a draft) |
| Issue open, linked pull requests merged | Unchanged |
| Issue closed as completed | Done |
| Issue closed as not planned | Canceled |
| Your pull request is a draft | In Progress |
| Your pull request is open | In Review |
| Pull request merged | Done |
| Pull request closed without merging | Canceled |
| Review requested, not yet reviewed | Todo |
| You reviewed, pull request still open | In Review |

**Your edits win.** ferryGL records the GitHub state it last applied on the Linear issue's
link, and changes the Linear status only when that GitHub state changes. If you move an
issue by hand, ferryGL leaves it alone until something happens on GitHub.

**Nothing is deleted.** If an item leaves your scope, for example you're unassigned or a
review request is withdrawn, its Linear issue stays as it is.

## What data leaves GitHub

Private repositories often belong to someone else, and their contents may be covered by an
agreement. By default ferryGL copies as little as possible:

- **Copied:** title, number, repository, URL, state and your role (assignee, author or
  reviewer).
- **Not copied unless you turn it on:** descriptions, comments, labels and other people's
  names.
- **Stricter:** title templates such as `"{repo}#{number}"` keep titles out of Linear too.

Check that copying titles into your Linear workspace is acceptable for each repository you
configure.

## Setup (planned)

ferryGL needs Python 3.11 or later. You'll run it with `uvx ferrygl` or install it with
`pipx install ferrygl`.

**1. A GitHub token that can read the repositories.** ferryGL only reads issues and pull
requests. It uses `GH_TOKEN` or `GITHUB_TOKEN` when set, and otherwise asks the GitHub CLI
(`gh auth token`). A fine-grained token needs read access to Issues, Pull requests and
Metadata. Some organizations require owner approval for fine-grained tokens, or block
classic ones.

**2. A Linear API key.** Create a personal API key in
[Linear's API settings](https://linear.app/settings/api) with read and write access. You can
limit it to the teams ferryGL writes to. Put it in `LINEAR_API_KEY`.

**3. A config file**, `ferrygl.toml`. Each `[[route]]` sends some repositories to a Linear
team and, optionally, a project:

```toml
[[route]]
repos = ["acme/api", "acme/web"]
team = "ENG"                  # Linear team key
project = "Acme engagement"   # optional

[[route]]
repos = ["upstream-org/sdk"]
team = "ENG"
project = "Upstream contributions"

[sync]
issues_assigned = true
prs_authored = true
prs_reviewed = true
review_requests = true
since = "2026-09-01"          # ignore items not updated since this date
label = "ferryGL"             # optional label on every issue ferryGL creates

[titles]                      # use "{repo}#{number}" to keep titles out of Linear
issue = "{title} #{number}"
pr = "{title} - #{number}"
review = "[PR] {title} - #{number}"

[copy]
description = false

[status]                      # your team's Linear workflow state names
todo = "Todo"
in_progress = "In Progress"
in_review = "In Review"
done = "Done"
canceled = "Canceled"
```

**4. Check, preview, then apply:**

```console
$ ferrygl check
$ ferrygl sync
$ ferrygl sync --apply
```

`check` confirms that both credentials work, every repository is readable, and the Linear
teams, projects and states exist. `sync` on its own only shows the plan, for example:

```text
acme/api#252  issue   create  "Store shared configuration once #252"  -> In Review
acme/api#335  pr      link    -> Linear issue for acme/api#252
acme/api#348  review  update  Todo -> In Review
3 changes (1 create, 1 link, 1 update). Run with --apply to make them.
```

`sync --apply` makes those changes.

## Running on a schedule (planned)

- **cron on a machine you use:**

  ```text
  0 9,15 * * 1-5  ferrygl sync --apply --config ~/.config/ferrygl/ferrygl.toml
  ```

- **GitHub Actions in a repository on your own account:** a scheduled workflow with your
  GitHub token and Linear key stored as secrets. The workflow's built-in `GITHUB_TOKEN`
  can't read other organizations' repositories, so it needs your own token. A template
  workflow is planned.

## When something goes wrong

- If a repository can't be read, ferryGL reports it and skips that route. It continues with
  the others and exits with a non-zero status.
- If a configured Linear team, project or state doesn't exist, ferryGL stops before writing
  anything.
- A run that fails halfway is safe to repeat. ferryGL finds existing Linear issues by their
  GitHub link, so a rerun doesn't create duplicates.

## How it works

- **No database.** Every Linear issue ferryGL manages has a link attachment with the GitHub
  URL. ferryGL finds it with Linear's `attachmentsForURL` query, and keeps the last applied
  GitHub state in that attachment's `metadata`. Linear holds all the state.
- **Few requests.** GitHub reads use the GraphQL search API. One person's activity usually
  needs a handful of requests per run.
- **Testable core.** Planning is a pure function from GitHub items, existing Linear issues and
  config to a list of changes. It's tested with fixtures and no network access.
- **Small footprint.** Python 3.11 or later, with one runtime dependency (`httpx`).

## Not planned

- Writing anything to GitHub: comments, labels or status.
- Two-way sync, or keeping descriptions and comments in sync.
- Webhooks or a hosted service. ferryGL polls, so changes arrive on your schedule, not
  instantly.
- Syncing every issue in a repository for a whole team. Linear's GitHub integration does that
  when you can install it.

## Open questions

- Should ferryGL also pick up pull requests you pushed commits to without opening or reviewing
  them? GitHub search has no qualifier for that, so it costs extra requests.
- License: MIT or Apache-2.0.
