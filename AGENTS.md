# Working on ferryGL

These rules are for anyone changing ferryGL, including coding agents. Read the
[README](README.md) and the pages in [docs/](docs/) first: together they are the user-facing
contract for behavior, configuration and limits. This file adds the invariants, layout and
workflow that keep changes safe.

ferryGL copies every issue and pull request a person's GitHub account is part of (created,
assigned, reviewed, review requested, or with their commits) one way into their own Linear
workspace, using only their own credentials. Its users usually work in repositories owned by someone else, often private,
so the safety rules below matter more than features.

## Invariants

Don't break these without an explicit decision from the project owner.

1. **GitHub is read-only.** `src/ferrygl/github.py` only runs GraphQL queries. Never add a
   mutation, comment, label or status write to GitHub.
2. **Only the user's Linear issues.** ferryGL changes a Linear issue only if it is assigned
   to the API key's owner (`viewer_id`). It never deletes issues or links, and never edits
   titles or descriptions after creating an issue.
3. **The user's edits win.** A Linear status moves only when the GitHub state recorded on the
   link changes. A link with no recorded state is a first sync, which may close an issue
   (done or canceled) but never reopens or otherwise moves it.
4. **One home per item, one status source per Linear issue.** Each GitHub URL maps to at most
   one of the user's Linear issues. A pull request that rides with an issue doesn't set that
   Linear issue's status on its own.
5. **Reruns are idempotent.** Existing Linear issues are found by their GitHub links
   (`attachmentsForURL`), so a rerun never duplicates. Only idempotent requests are retried
   after network errors or 5xx responses; `issueCreate` is not.
6. **The planner is pure.** `src/ferrygl/plan.py` does no I/O. All reads happen before
   planning, and all writes happen after it, in `src/ferrygl/sync.py`.
7. **Copy as little as possible.** By default only titles, numbers, repositories, URLs, states
   and the user's role reach Linear. Descriptions are copied only when `[copy] description`
   is on. Never copy comments or other people's names.
8. **No surprise writes.** `ferrygl sync` without `--apply` must write nothing. A setup
   problem (missing team, project, state or label) stops the run before GitHub is read or
   Linear is written.

## Retained data and public contracts

Users' Linear workspaces keep what ferryGL writes, so treat these as compatibility contracts:

- **Link metadata** uses flat keys with string or number values, because Linear documents
  attachment metadata that way:
  - `ferrygl_v`: format version, currently `1`
  - `ferrygl_kind`: `issue`, `pr` (one you opened) or `involved` (someone else's)
  - `ferrygl_state`: the GitHub state key ferryGL last acted on
  - `ferrygl_synced_at`: ISO timestamp of that state
  - `ferrygl_linked_to`: on links of pull requests that ride with an issue, such as
    `owner/repo#12`

  ferryGL replaces only its own `ferrygl_*` keys and keeps any others.
- **State keys** (`github_state` in `plan.py`): `issue:open`, `issue:open:pr_draft`,
  `issue:open:pr_open`, `issue:open:pr_merged`, `issue:closed:completed`,
  `issue:closed:not_planned`, `pr:draft`, `pr:open`, `pr:merged`, `pr:closed`,
  `involved:requested`, `involved:reviewed`, `involved:draft`, `involved:open`,
  `involved:merged`, `involved:closed`. Renaming one makes every recorded link look changed.
  Add a key, or bump `ferrygl_v` and handle the old value.
- **Config keys** (`config.py`): unknown keys are rejected, so adding a key is safe but
  renaming or removing one breaks existing files. Keep the old name working, with a clear
  error or warning, for at least one release.
- **CLI:** commands `check` and `sync [--apply]`, `--config`, `FERRYGL_CONFIG`,
  `LINEAR_API_KEY`, and exit statuses 0 (ok), 1 (a route or write failed) and 2 (setup or
  config error).

## Layout

| Path | Responsibility |
|---|---|
| `src/ferrygl/config.py` | Parse and validate `ferrygl.toml` into frozen dataclasses |
| `src/ferrygl/github.py` | Resolve tokens, run one GitHub search per role, scan commit authors, parse items |
| `src/ferrygl/linear.py` | Linear GraphQL reads and writes with a personal API key |
| `src/ferrygl/plan.py` | Pure planning: GitHub items + existing Linear state -> changes |
| `src/ferrygl/sync.py` | `check` and `sync`: resolve routes, plan, print, apply |
| `src/ferrygl/model.py` | Shared types: `Kind`, `Role`, `GitHubItem`, `LinearIssue`, `Attachment`, `Change` |
| `src/ferrygl/_http.py` | JSON POST with retries for both clients |
| `tests/builders.py` | Builders for synthetic items, Linear issues and links |
| `docs/` | User documentation: setup, configuration, sync rules, commands, data, architecture |
| `site/` | The website: a hand-written home page, and `build.py`, which renders `docs/` into pages |

## Development

```console
$ uv sync
$ uv run pytest
$ uv run ruff check && uv run ruff format --check
```

- Python 3.11 is the oldest supported version, and CI also runs 3.14. Don't use syntax newer
  than 3.11.
- The only runtime dependency is `httpx`. Discuss any new dependency with the project owner
  before adding it.
- Match the existing style: small modules, frozen dataclasses, type hints, comments that
  explain why, and error messages that tell the user what to do next.

## Testing

- Every change to planning behavior needs a test in `tests/test_plan.py` that states the
  scenario in plain terms: what's on GitHub, what's in Linear, and the expected changes.
- Client changes are tested with `httpx.MockTransport`; end-to-end behavior uses the fakes in
  `tests/test_sync.py`.
- **Use synthetic data only** (`acme/api`, `ENG-1`). Never put real repository names, issue
  titles, Linear identifiers, tokens or API responses from a user's workspace in tests,
  fixtures, docs, commits or PR descriptions. Users' repositories are often private and
  belong to their clients.
- Check new or changed Linear queries against Linear's published schema
  (`packages/sdk/src/schema.graphql` in `linear/linear` on GitHub) for field names, argument
  names and types such as `String!` vs `ID!`.
- Live checks: run read-only commands (`ferrygl check`, `ferrygl sync` without `--apply`)
  against a real workspace only with its owner's credentials and permission. Never run
  `--apply` against a workspace unless its owner asks for that specific run.

## Documentation

The documentation is part of every behavior change. Update it in the same change when you
alter the status rules, adoption rules, config keys, defaults, exit statuses, data copied or
supported limits. Describe current behavior only: history belongs in Git, and progress notes
belong in issues and pull requests. Mark planned features as planned.

The README stays short: what ferryGL does, how it works, and a quick start. Details go in
`docs/`:

| Page | Covers |
|---|---|
| `docs/getting-started.md` | Install, credentials, first run |
| `docs/configuration.md` | Every config key and its default |
| `docs/sync-rules.md` | Roles, homes, riding pull requests, status rules, adoption |
| `docs/commands.md` | `check`, `sync`, plan actions, exit statuses, scheduling, errors |
| `docs/data-and-privacy.md` | What is read, written to Linear, and kept; link metadata |
| `docs/architecture.md` | Run steps, state keys, request costs, retries, limits |

The website (https://oussamabatouche.com/ferryGL/) is rebuilt from `docs/` whenever `docs/`
or `site/` changes on `main`, so a page changed in `docs/` needs no other edit. A new page
needs a link in `docs/README.md`, which sets the order of the sidebar. The home page,
`site/index.html`, is written by hand: update it when the README's summary, status table or
quick start changes. To preview, run `python3 site/build.py` and
`python3 -m http.server --directory _site`. The build fails on a link to a missing page or
anchor, and pull requests that touch `docs/` or `site/` run it.

## Git and GitHub

- Work on a branch and open a pull request against `main`. Keep each pull request to one
  concern, and say in its description what changed for users and how it was tested.
- Never push to `main` directly. GitHub can't enforce this while the repository is private on
  a free plan, so a local `pre-push` hook may block it; don't bypass it with `--no-verify`.
- This checkout may have a repo-local git identity and credential helper. Don't change them.
  When the machine has several GitHub accounts, run `gh` as the repository owner's account,
  for example `GH_TOKEN=$(gh auth token --user <owner>) gh ...`.
- Don't add AI or tool attribution to commits, pull requests or comments: no `Co-Authored-By`
  trailers or "generated with" footers. Work is published under the owner's name.
- These need the project owner's explicit approval each time: merging to `main`, making the
  repository public, publishing a release or a PyPI package, and changing the license.

## Releasing

Releases need the project owner's approval each time.

1. In a pull request, set `__version__` in `src/ferrygl/__init__.py` (the only place the
   version lives) and update the README and `docs/` for anything users will notice. Merge it.
2. Tag the merge commit on `main` with `v<version>`, such as `v0.2.0`, and push the tag.
3. `.github/workflows/release.yml` tests and builds the package, checks that the tag matches
   the version, publishes to PyPI through trusted publishing (no token is stored), and
   creates a GitHub release with the built files.

PyPI never accepts the same version twice. If a release is broken, publish a new patch
version rather than trying to replace it.

## Working agreement

- Settle routine implementation choices yourself, within these rules.
- Bring consequential decisions (a contract change, a new dependency, anything that changes
  what reaches a user's Linear workspace) to the owner with a recommendation, the tradeoff and
  the evidence.
- When handing work back, say what changed, how it was tested, what wasn't tested, and the
  next decision needed.

## Current state

- `check` and `sync` are implemented and tested with fixtures. GitHub reads have been
  exercised against a real repository. Reads and writes against a real Linear workspace
  haven't been exercised yet, so do a read-only preview before the first release.
- Planned: a scheduled GitHub Actions template.
- Finding pull requests with the user's commits scans every pull request in scope, because
  GitHub search has no commit-author qualifier for pull requests. Keep that scan bounded by
  `since`, and watch its cost when changing it.
- Open question: whether comments or mentions should count as roles.
