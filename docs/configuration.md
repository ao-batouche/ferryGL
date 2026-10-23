# Configuration

ferryGL reads one TOML file. Only `[[route]]` is required; every other setting has a
default.

## Where the file lives

ferryGL uses the first of:

1. the path given with `--config`;
2. the path in `FERRYGL_CONFIG`;
3. `$XDG_CONFIG_HOME/ferrygl/ferrygl.toml`, or `~/.config/ferrygl/ferrygl.toml` when
   `XDG_CONFIG_HOME` isn't set.

A running [`ferrygl watch`](commands.md#ferrygl-watch) reads the file once, when it starts.
After you edit it, stop the watch and start it again.

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
comment = false               # true: comment "(BY-FGL) ..." on each Linear issue ferryGL changes
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

- List each repository once, across all routes. `"acme/*"` and `"acme/api"` together, in one
  route or in two, is an error, because the same items would be planned twice. Names are
  compared without regard to case.
- Several routes can send to the same team.
- The project, like the label, is only set on Linear issues ferryGL creates. Issues it
  adopts keep their own.

## `[sync]`

| Key | Default | Meaning |
|---|---|---|
| `since` | none | Ignore items not updated on GitHub since this date |
| `label` | none | The name of an existing Linear label to add to every issue ferryGL creates |
| `comment` | `false` | Post a comment starting with `(BY-FGL)` on a Linear issue each time ferryGL changes it |
| [role flags](#roles) | `true` | Which of your roles bring an item into Linear |

**`since`** is a TOML date (`since = 2026-09-01`) or a string (`since = "2026-09-01"`). It
narrows every GitHub search, which keeps runs fast, and matters most for the commit scan
described below. Without it, ferryGL looks at items of any age. An item that falls outside
`since` keeps its Linear issue as it is.

**`label`** must already exist, in the route's team or as a workspace label. If both exist,
the team's label is used. ferryGL doesn't create labels.

**`comment`** marks every change ferryGL makes in Linear. With `comment = true`, each `create`,
`adopt`, `link`, `update` and `record` in the [plan](commands.md#reading-the-plan) also posts a
comment on its Linear issue. The comment starts with `(BY-FGL)`, so you can tell ferryGL's
changes from your own, and says what changed and why:

```text
(BY-FGL) Created from acme/api#12. Status Todo: acme/api#12 is open.
(BY-FGL) Linked acme/api#15, a pull request that refers to acme/api#12.
(BY-FGL) Status In Review → Done: acme/api#16 was merged.
(BY-FGL) Status stays In Review: acme/api#12's pull request was merged, and the issue is still open.
```

- Comments name only GitHub items (`owner/repo#number`), their GitHub state and Linear status
  names. They never include titles or descriptions.
- `ferrygl sync`, and `ferrygl watch --preview`, print each comment under its change, so you
  can read them before any is posted. `ferrygl watch` posts them as it syncs.
- They're posted as you, the API key's owner. Linear notifies anyone else subscribed to the
  issue, as it would for a comment you wrote.
- If you limited the API key's permissions, make sure it can create comments.

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
since `since`), and checks the authors, including co-authors, of its first 100 commits (up to
5 authors each). Set `since`, or turn it off with `prs_with_commits = false` if you don't need
it.

A commit counts only if GitHub links its author email to your account, as it does when the
commit shows your avatar on GitHub. If you commit with a work email, add it to your GitHub
account.

## `[titles]`

Title templates for the Linear issues ferryGL creates.

| Key | Used for | Default |
|---|---|---|
| `issue` | Issues you created or are assigned to | `"{title} #{number}"` |
| `pr` | Pull requests you opened | `"{title} - #{number}"` |
| `involved` | Someone else's pull requests you're part of | `"[PR] {title} - #{number}"` |

Templates can use `{title}`, `{number}` and `{repo}` (as `owner/name`). Any other field, or a
template ferryGL can't fill in, is an error, reported before anything is read from GitHub. The
templates apply to every route.

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
`ferrygl check` reports it, and `ferrygl sync` and `ferrygl watch` stop before changing
anything.

The [status rules](sync-rules.md#status-rules) say which GitHub states lead to which status.

## `[linear]`

| Key | Default | Meaning |
|---|---|---|
| `api_key_file` | none | A file holding your Linear API key. `~` is expanded |

`LINEAR_API_KEY` takes precedence over the file.

## Validation

ferryGL checks the whole file before doing anything else, and stops at the first problem:

- Unknown tables and keys are errors, so a misspelled setting can't be silently ignored.
- Each error names the file and the setting, and the run exits with status 2.

`ferrygl check` goes further: it confirms that every repository is readable, and that the
teams, projects, states and label exist in Linear.

## Completion and checks in your editor

ferryGL publishes a [JSON Schema for `ferrygl.toml`](https://oussamabatouche.com/ferryGL/ferrygl.schema.json).
With it, your editor completes setting names, shows what each one does and its default, and
marks unknown keys and invalid values as you type.

In VS Code, install the
[Even Better TOML](https://marketplace.visualstudio.com/items?itemName=tamasfe.even-better-toml)
extension, and start the file with this line:

```toml
#:schema https://oussamabatouche.com/ferryGL/ferrygl.schema.json

[[route]]
repos = ["acme/api"]
team = "ENG"
```

Other editors that use [Taplo](https://taplo.tamasfe.dev/), the TOML language server behind
Even Better TOML, read the same line.

To leave the file as it is, associate the schema in your VS Code user settings instead:

```json
"evenBetterToml.schema.associations": {
  ".*/ferrygl\\.toml$": "https://oussamabatouche.com/ferryGL/ferrygl.schema.json"
}
```

The key is a regular expression matched against the file's location, so this one applies to
every file named `ferrygl.toml`.

- Use the `#:schema` comment, not a `"$schema"` key: ferryGL rejects unknown keys.
- The schema can't check everything. ferryGL itself reports routes that overlap, and
  `ferrygl check` confirms what exists in Linear.
