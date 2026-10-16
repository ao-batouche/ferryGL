# Configuration

ferryGL reads one TOML file. Only `[[route]]` is required; every other setting has a
default.

## Where the file lives

ferryGL uses the first of:

1. the path given with `--config`;
2. the path in `FERRYGL_CONFIG`;
3. `$XDG_CONFIG_HOME/ferrygl/ferrygl.toml`, or `~/.config/ferrygl/ferrygl.toml` when
   `XDG_CONFIG_HOME` isn't set.

## A complete example

```toml
[[route]]
repos = ["acme/api", "acme/web"]
team = "ENG"                  # Linear team key or name
project = "Acme engagement"   # optional
github_user = "alice-acme"    # optional: read GitHub as this gh account

[[route]]
repos = ["upstream-org/*"]    # every repository of a user or organization
team = "ENG"
project = "Upstream contributions"
github_user = "alice"

[sync]
since = 2026-09-01            # ignore items not updated since this date
label = "ferryGL"             # optional: an existing label added to every issue ferryGL creates
# Roles, all true by default: issues_assigned, issues_created, prs_created, prs_assigned,
# prs_reviewed, review_requests, prs_with_commits

[titles]                      # use "{repo}#{number}" to keep titles out of Linear
issue = "{title} #{number}"
pr = "{title} - #{number}"
involved = "[PR] {title} - #{number}"

[copy]
description = false

[status]                      # your team's Linear workflow state names
todo = "Todo"
in_progress = "In Progress"
in_review = "In Review"
done = "Done"
canceled = "Canceled"

[linear]
api_key_file = "~/.config/ferrygl/linear-api-key"
```

## `[[route]]`

A route sends the issues and pull requests of some repositories to one Linear team and,
optionally, one project. Add one `[[route]]` table for each destination.

| Key | Required | Meaning |
|---|---|---|
| `repos` | yes | A list of `"owner/name"`, or `"owner/*"` for every repository of a user or organization |
| `team` | yes | The Linear team, by key (`"ENG"`) or name. Case doesn't matter |
| `project` | no | The Linear project, by name, for the issues ferryGL creates. The team must have access to it |
| `github_user` | no | The GitHub CLI account to read these repositories as. See [Getting started](getting-started.md#2-give-ferrygl-read-access-to-github) |

- Each repository belongs to exactly one route. `"acme/*"` in one route and `"acme/api"` in
  another is an error, because the same items would be planned twice. Names are compared
  without regard to case.
- Several routes can send to the same team.
- The project, like the label, is only set on Linear issues ferryGL creates. Issues it
  adopts keep their own.

## `[sync]`

| Key | Default | Meaning |
|---|---|---|
| `since` | none | Ignore items not updated on GitHub since this date |
| `label` | none | The name of an existing Linear label to add to every issue ferryGL creates |
| [role flags](#roles) | `true` | Which of your roles bring an item into Linear |

**`since`** is a TOML date (`since = 2026-09-01`) or a string (`since = "2026-09-01"`). It
narrows every GitHub search, which keeps runs fast, and matters most for the commit scan
described below. Without it, ferryGL looks at items of any age. An item that falls outside
`since` keeps its Linear issue as it is.

**`label`** must already exist, in the route's team or as a workspace label. If both exist,
the team's label is used. ferryGL doesn't create labels.

### Roles

Each flag turns one GitHub search on or off. All are on by default.

| Flag | Brings in | GitHub search |
|---|---|---|
| `issues_assigned` | Issues assigned to you | `is:issue assignee:<you>` |
| `issues_created` | Issues you created | `is:issue author:<you>` |
| `prs_created` | Pull requests you opened | `is:pr author:<you>` |
| `prs_assigned` | Someone else's pull requests assigned to you | `is:pr assignee:<you> -author:<you>` |
| `prs_reviewed` | Someone else's pull requests you've reviewed | `is:pr reviewed-by:<you> -author:<you>` |
| `review_requests` | Someone else's pull requests where your review is requested | `is:pr user-review-requested:<you> -author:<you>` |
| `prs_with_commits` | Someone else's pull requests with a commit you wrote | every pull request in scope; see below |

Every search is limited to the route's repositories (`repo:owner/name`, or `user:owner` for
`owner/*`) and, with `since`, to `updated:>=<date>`.

- Only `prs_created` finds pull requests you opened. The other pull request searches leave
  them out, so turning `prs_created` off drops your own pull requests entirely.
- `review_requests` matches requests to you personally, not requests to a team you're on.

**`prs_with_commits` is the slowest part of a run.** GitHub search can't find pull requests by
commit author, so ferryGL reads every pull request in the route that you didn't open (updated
since `since`), and checks the authors, including co-authors, of its first 100 commits. Set
`since`, or turn it off with `prs_with_commits = false` if you don't need it.

## `[titles]`

Title templates for the Linear issues ferryGL creates.

| Key | Used for | Default |
|---|---|---|
| `issue` | Issues you created or are assigned to | `"{title} #{number}"` |
| `pr` | Pull requests you opened | `"{title} - #{number}"` |
| `involved` | Someone else's pull requests you're part of | `"[PR] {title} - #{number}"` |

Templates can use `{title}`, `{number}` and `{repo}` (as `owner/name`). Any other field is an
error. The templates apply to every route.

ferryGL sets a title only when it creates the Linear issue. You can rename the issue
afterwards, and ferryGL won't change it back.

To keep GitHub titles out of Linear, use `"{repo}#{number}"`. See
[Data and privacy](data-and-privacy.md#keeping-more-out-of-linear).

## `[copy]`

| Key | Default | Meaning |
|---|---|---|
| `description` | `false` | Copy the GitHub description into the Linear issue when ferryGL creates it |

The Linear description always starts with a link to the GitHub item, and to the pull requests
that ride with it. With `description = true`, the GitHub description follows, below a
separator. It's copied once, when the issue is created, and never updated.

## `[status]`

Your team's Linear workflow state names, for each status ferryGL sets.

| Key | Default |
|---|---|
| `todo` | `"Todo"` |
| `in_progress` | `"In Progress"` |
| `in_review` | `"In Review"` |
| `done` | `"Done"` |
| `canceled` | `"Canceled"` |

Each name must exist, spelled exactly the same, in the team of every route. If routes go to
teams with different workflows, the names must exist in all of them. If one is missing,
`ferrygl check` reports it, and `ferrygl sync` stops before changing anything.

The [status rules](sync-rules.md#status-rules) say which GitHub states lead to which status.

## `[linear]`

| Key | Default | Meaning |
|---|---|---|
| `api_key_file` | none | A file holding your Linear API key. `~` is expanded |

`LINEAR_API_KEY` takes precedence over the file.

## Validation

ferryGL checks the whole file before doing anything else:

- Unknown tables and keys are errors, so a misspelled setting can't be silently ignored.
- Each error names the file and the setting, and the run exits with status 2.

`ferrygl check` goes further: it confirms that every repository is readable, and that the
teams, projects, states and label exist in Linear.
