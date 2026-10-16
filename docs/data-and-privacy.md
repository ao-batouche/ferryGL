# Data and privacy

ferryGL is often used in private repositories that belong to a client, whose contents may be
covered by an agreement. This page lists what ferryGL reads, what it writes to Linear, and
what it keeps, so you can check them against that agreement.

## Credentials

- ferryGL uses your GitHub token and Linear API key only to call `api.github.com` and
  `api.linear.app`. It sends them nowhere else, and doesn't save them.
- It only sends read queries to GitHub, so a token with read-only access is enough. Prefer
  one.
- The Linear API key acts as you. ferryGL only changes Linear issues assigned to you.

## What it reads from GitHub

For each issue and pull request in scope: the repository, number, title, URL, state (open,
closed, merged, draft), the reason an issue was closed, and the description. For pull
requests, also the author's login and the issues GitHub says it will close.

For the commit scan (`prs_with_commits`), ferryGL reads these same fields for every pull
request in the route that you didn't open (updated since `since`), plus the logins of the
authors of its first 100 commits. It then drops the pull requests without a commit of yours.

ferryGL reads descriptions to find the issues a pull request refers to, even when it doesn't
copy them.

## What it writes to Linear

For each Linear issue it creates:

| Field | Value |
|---|---|
| Title | From [`[titles]`](configuration.md#titles). By default, the GitHub title and number |
| Description | Links to the GitHub item and to the pull requests that ride with it. The GitHub description only with `[copy] description = true` |
| Status | From the [status rules](sync-rules.md#status-rules) |
| Assignee | You |
| Team, project, label | From the route, and `[sync] label` |
| Links | One per GitHub item: its URL, a title such as "Issue #12" or "PR #12", the repository as subtitle, and ferryGL's metadata |

On existing Linear issues, ferryGL only changes the status, adds links, and writes its
metadata on the GitHub links it uses, including ones you added yourself. It keeps their title
and subtitle.
It never edits titles or descriptions, and never deletes anything.

### Link metadata

ferryGL keeps its state in the metadata of each GitHub link, under keys that start with
`ferrygl_`:

| Key | Value |
|---|---|
| `ferrygl_v` | The format version, currently `1` |
| `ferrygl_kind` | `issue`, `pr` (one you opened) or `involved` (someone else's) |
| `ferrygl_state` | The GitHub state ferryGL last acted on, such as `issue:open:pr_open` or `pr:merged` |
| `ferrygl_synced_at` | When that state was recorded, as an ISO 8601 timestamp in UTC |
| `ferrygl_linked_to` | On the link of a pull request that rides with an issue: that issue, such as `acme/api#252` |

When ferryGL updates a link, it replaces only its own keys and keeps any others.

## What it never copies

- Comments and reviews.
- GitHub labels, milestones and other fields.
- Other people's names or logins, including the authors of pull requests and commits.
- Code, commits and diffs.

If you turn on `[copy] description`, the GitHub description is copied as written, including
any names, links or details in it.

## Keeping more out of Linear

Titles are copied by default. If titles shouldn't leave GitHub, use templates without
`{title}`:

```toml
[titles]
issue = "{repo}#{number}"
pr = "{repo}#{number}"
involved = "[PR] {repo}#{number}"
```

The templates apply to every route. The repository name and number still appear in each
link and its URL.

Check that copying titles into your Linear workspace is acceptable for each repository you
configure.

## What it keeps on your machine

Nothing. ferryGL has no database, cache or log file of its own: everything it needs to
remember is in the Linear links. Its output goes to the terminal, or wherever you redirect it.
