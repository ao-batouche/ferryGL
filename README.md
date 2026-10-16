# ferryGL

Ferry your GitHub work into your own Linear workspace, from repositories you can read but
don't administer.

> **Status: early version.** `ferrygl check` and `ferrygl sync` work, and are tested with
> local fixtures. There's no release on PyPI yet.
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
makes sure each item has exactly one Linear issue, assigned to you, with the right status.

| GitHub item | GitHub search | Default Linear title |
|---|---|---|
| Issues assigned to you | `is:issue assignee:<you>` | `{title} #{number}` |
| Pull requests you opened | `is:pr author:<you>` | `{title} - #{number}` |
| Pull requests you reviewed | `is:pr reviewed-by:<you> -author:<you>` | `[PR] {title} - #{number}` |
| Pull requests waiting for your review | `is:pr user-review-requested:<you>` | `[PR] {title} - #{number}` |

A pull request you opened that refers to an issue assigned to you, through a closing keyword
such as `Fixes #252` or a plain `#252` in its title or description, is linked to that
issue's Linear issue instead of getting its own. Pull requests you review always get their
own Linear issue.

For example, you're assigned `acme/api#252` and open `acme/api#335`, whose description says
"Part of #252":

1. ferryGL creates one Linear issue, "Store shared configuration once #252", links both
   GitHub URLs to it and sets it to In Review.
2. #335 merges while #252 stays open. The Linear issue stays In Review, because the work
   isn't done until the GitHub issue is closed.
3. #252 is closed as completed. The Linear issue moves to Done.

ferryGL only changes Linear issues assigned to you. If a teammate's Linear issue links the
same pull request, it's left alone.

## Status rules

Linear state names are configurable. These are the defaults:

| GitHub state | Linear status |
|---|---|
| Issue open, no linked pull request open | Todo |
| Issue open, a linked pull request open | In Review (In Progress if every open one is a draft) |
| Issue open, linked pull requests merged | Unchanged |
| Issue closed as completed | Done |
| Issue closed as not planned or duplicate | Canceled |
| Your pull request is a draft | In Progress |
| Your pull request is open | In Review |
| Pull request merged | Done |
| Pull request closed without merging | Canceled |
| Your review is requested, including again after you reviewed | Todo |
| You reviewed, no new request, pull request still open | In Review |

**Your edits win.** ferryGL records the GitHub state it last acted on in the link's metadata
on the Linear issue, and changes the Linear status only when that GitHub state changes. If
you move an issue by hand, ferryGL leaves it alone until something happens on GitHub.

**Nothing is deleted.** If an item leaves your scope, for example you're unassigned or a
review request is withdrawn, its Linear issue stays as it is.

## Linear issues you already made by hand

Before creating a Linear issue, ferryGL looks for one of yours in the route's team that
already mentions the item:

- its GitHub URL is in the description or links, or
- its title ends with `#<number>`, when the route has a single repository and the
  description mentions no other GitHub item.

A Linear issue already linked to a different GitHub item is about that item, and never
matches. Two more rules cover hand-made issues for pull requests:

- If nothing mentions a pull request you opened, a Linear issue that mentions an issue it
  refers to counts, unless that Linear issue is linked to a pull request.
- If an issue assigned to you has no Linear issue but its pull request already has one, the
  issue joins that Linear issue.

When exactly one issue matches, ferryGL links it instead of creating a new one (`adopt` in
the plan). It doesn't change that issue's status unless GitHub says the work is done or
canceled. When several match, ferryGL skips the item and lists them, so you can link the
right one by hand.

## What data leaves GitHub

Private repositories often belong to someone else, and their contents may be covered by an
agreement. By default ferryGL copies as little as possible:

- **Copied:** title, number, repository, URL, state and your role (assignee, author or
  reviewer).
- **Not copied unless you turn it on:** descriptions. Comments, labels and other people's
  names are never copied.
- **Stricter:** title templates such as `"{repo}#{number}"` keep titles out of Linear too.

Check that copying titles into your Linear workspace is acceptable for each repository you
configure.

## Setup

ferryGL needs Python 3.11 or later. Until the first release, install it from the repository:

```console
$ uv tool install git+https://github.com/ao-batouche/ferryGL
```

**1. A GitHub token that can read the repositories.** ferryGL only reads issues and pull
requests. For each route it uses, in order:

- the GitHub CLI account named by the route's `github_user` (`gh auth token --user <name>`),
- otherwise `GH_TOKEN` or `GITHUB_TOKEN`,
- otherwise the GitHub CLI's active account.

`github_user` is for people with more than one GitHub account, such as a work account for a
client's organization and a personal one for open source. A fine-grained token needs read
access to Issues, Pull requests and Metadata. Some organizations require owner approval for
fine-grained tokens, or block classic ones.

**2. A Linear API key.** Create a personal API key in
[Linear's API settings](https://linear.app/settings/api) with read and write access. You can
limit it to the teams ferryGL writes to. Put it in `LINEAR_API_KEY`, or in a file named by
`[linear] api_key_file`.

**3. A config file.** ferryGL reads `~/.config/ferrygl/ferrygl.toml`, or the file given by
`--config` or `FERRYGL_CONFIG`. Each `[[route]]` sends some repositories to a Linear team and,
optionally, a project:

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
# issues_assigned, prs_authored, prs_reviewed and review_requests are all true by default

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

[linear]
api_key_file = "~/.config/ferrygl/linear-api-key"
```

**4. Check, preview, then apply:**

```console
$ ferrygl check
$ ferrygl sync
$ ferrygl sync --apply
```

`check` confirms that both credentials work, every repository is readable, and the Linear
teams, projects, states and label exist. `sync` on its own only prints the plan, for
example:

```text
acme/api#252  issue   update  ENG-7 Todo -> In Review
acme/api#335  pr      link    -> ENG-7
acme/api#348  review  adopt   -> ENG-9
3 changes (1 adopt, 1 link, 1 update). Run with --apply to make them.
```

`sync --apply` makes those changes and prints the identifier of each Linear issue it
creates. Run the preview first: the first sync is the one most likely to find Linear issues
you made by hand, and it records the current GitHub state on each of them (`record` in the
plan).

## Running on a schedule

- **cron on a machine you use:**

  ```text
  0 9,15 * * 1-5  ferrygl sync --apply >> ~/.cache/ferrygl.log 2>&1
  ```

  With `[linear] api_key_file` and `github_user` set, cron needs no extra environment.

- **GitHub Actions in a repository on your own account:** a scheduled workflow with your
  GitHub token and Linear key stored as secrets. The workflow's built-in `GITHUB_TOKEN`
  can't read other organizations' repositories, so it needs your own token. A template
  workflow is planned.

## When something goes wrong

- If a configured Linear team, project, state or label doesn't exist, `sync` stops before
  reading GitHub or writing anything, and exits with status 2. `ferrygl check` shows what's
  missing.
- If a route's repositories can't be read, ferryGL reports it, skips that route, continues
  with the others, and exits with status 1.
- If a write fails, ferryGL reports it and continues. A run that fails halfway is safe to
  repeat: ferryGL finds Linear issues by their GitHub links, so a rerun doesn't create
  duplicates.
- Creating a Linear issue is never retried after a network error, because Linear may have
  created it anyway. If it did, the next sync adopts it through the GitHub URL in its
  description.

## How it works

- **No database.** Every Linear issue ferryGL manages has a link with the GitHub URL.
  ferryGL finds it with Linear's `attachmentsForURL` query, and keeps the last GitHub state
  it acted on in the link's `metadata`, as flat `ferrygl_*` keys such as `ferrygl_state`.
  Linear holds all the state.
- **Few requests.** GitHub reads use the GraphQL search API: up to four searches per route,
  plus one request per 50 results.
- **Testable core.** Planning (`ferrygl.plan`) is a pure function from GitHub items, existing
  Linear issues and config to a list of changes. It's tested without network access.
- **Small footprint.** One runtime dependency, `httpx`.

## Development

```console
$ uv sync
$ uv run pytest
$ uv run ruff check && uv run ruff format --check
```

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

## License

[MIT](LICENSE)
