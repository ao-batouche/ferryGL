# Architecture

How a run works inside ferryGL, for people who want to understand or change it. Before
changing anything, also read [AGENTS.md](../AGENTS.md): it lists the rules that keep users'
data safe.

ferryGL is a small Python package in `src/ferrygl/`, with one runtime dependency, `httpx`.

## A run, step by step

1. **Load the config** (`config.py`) into frozen dataclasses. Any error stops the run with
   exit status 2.
2. **Find the Linear API key** (`cli.py`): `LINEAR_API_KEY`, else `[linear] api_key_file`.
3. **Resolve every route's Linear setup** (`sync.py`): the API key's owner, then each route's
   team, its workflow states, the project and the label. If anything is missing, stop with
   exit status 2 before reading GitHub.
4. **For each route:**
   1. **Read GitHub** (`github.py`): resolve the token, ask whose it is, and run one search
      per enabled role. Merge the results into one item per URL, keeping every role.
   2. **Read Linear** (`linear.py`): look up the links to every item's URL with
      `attachmentsForURL`. If an item has no Linear issue of yours yet, also load your issues
      in the route's team as candidates for adoption.
   3. **Plan** (`plan.py`): a pure function from the GitHub items, the Linear links, the
      candidates and the config to a list of changes. It does no I/O.
   4. **Print** the changes and, with `--apply`, **make them** (`sync.py`, `linear.py`).
5. **Print a summary**, and exit with status 0, or 1 if a route or a write failed.

All reads for a route happen before planning, and all writes after it. That keeps the planner
pure and testable, and means `sync` without `--apply` runs every step of a real run except the
writes.

Shared types (`GitHubItem`, `LinearIssue`, `Attachment`, `Change`) live in `model.py`.

## State lives in Linear

ferryGL has no database. Each Linear issue it manages has a link (a Linear *attachment*) to the
GitHub URL, and the link's metadata records the GitHub state ferryGL last acted on. The keys
are listed in [Data and privacy](data-and-privacy.md#link-metadata).

- **Finding existing work.** Linear's `attachmentsForURL` query returns the links to a URL.
  ferryGL uses the one on an issue assigned to you. That's what makes reruns idempotent.
- **Deciding whether to move a status.** ferryGL compares `ferrygl_state` with the item's
  current state key. Only a difference moves the status, which is why manual edits survive.

The state keys come from `github_state` in `plan.py`:

| Kind | State keys |
|---|---|
| `issue` | `issue:open`, `issue:open:pr_draft`, `issue:open:pr_open`, `issue:open:pr_merged`, `issue:closed:completed`, `issue:closed:not_planned` |
| `pr` | `pr:draft`, `pr:open`, `pr:merged`, `pr:closed` |
| `involved` | `involved:requested`, `involved:reviewed`, `involved:draft`, `involved:open`, `involved:merged`, `involved:closed` |

These keys, like the metadata keys, are stored in users' workspaces, so they're a
compatibility contract: renaming one would make every recorded link look changed.

## Requests

**GitHub**, per route:

- one query for the token's login;
- one search per enabled role (up to seven), with 50 results per page;
- for the commit scan (`prs_with_commits`), 25 pull requests per page, each with its first
  100 commits. It reads every pull request in scope that you didn't open, so it's the most
  expensive part of a run. `since` bounds it.

`check` also makes one request per repository to confirm it's readable.

**Linear**, per run:

- one query for the API key's owner;
- for each route, lookups for the team, its states (once per team), the project and the
  label;
- `attachmentsForURL` for up to 25 URLs per request;
- your issues in the route's team, 50 per page, only when some item has no Linear issue of
  yours yet;
- with `--apply`, one request per write: `issueCreate`, `attachmentCreate`,
  `attachmentUpdate` or `issueUpdate`.

## Retries

Both clients send GraphQL requests through `_http.py`, with up to three attempts per request:

- **Rate limits** (HTTP 429, or GitHub's 403 with `Retry-After`) are always retried, after the
  wait `Retry-After` asks for (at most 60 seconds), or 2 and then 4 seconds.
- **Network errors and HTTP 502, 503 and 504** are retried only for idempotent requests:
  network errors after 2 and then 4 seconds, server errors after the wait `Retry-After` asks
  for, or 2 and then 4 seconds. `issueCreate` isn't idempotent: Linear may have created the issue
  before the connection failed. ferryGL reports the error instead, and the next run adopts
  the issue through the GitHub URL in its description.
- **GraphQL errors** are reported with the server's own message.

## Limits

| What | Limit |
|---|---|
| Results per GitHub search | 1,000, GitHub's own limit. Without `since`, the commit scan in a busy repository can reach it |
| Commits checked per pull request for `prs_with_commits` | The first 100, with up to 5 authors each |
| Issues a pull request will close, as listed by GitHub | The first 20. Mentions in the title and description aren't limited |
| Links read per GitHub URL, and per candidate Linear issue | 20 |
| Workflow states per Linear team | 100 |

## Testing

- `plan.py` is tested without network access in `tests/test_plan.py`. Each test states a
  scenario in plain terms: what's on GitHub, what's in Linear, and the expected changes.
- The GitHub and Linear clients are tested with `httpx.MockTransport`.
- `tests/test_sync.py` runs `check` and `sync` end to end against fakes.
- All test data is synthetic, such as `acme/api` and `ENG-1`.

```console
$ uv sync
$ uv run pytest
$ uv run ruff check && uv run ruff format --check
```
