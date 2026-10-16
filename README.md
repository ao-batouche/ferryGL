<p align="center">
  <img src="https://raw.githubusercontent.com/ao-batouche/ferryGL/main/assets/ferrygl-logo-horizontal.png" alt="ferryGL" width="560">
</p>

# ferryGL

ferryGL copies the GitHub issues and pull requests you work on into your own Linear
workspace, and keeps their status up to date. It works in repositories you can read but
don't administer, using only your own credentials.

> **Status: early version (0.2).** `ferrygl check` and `ferrygl sync` work, and are tested
> with local fixtures and against a real GitHub repository.
>
> ferryGL is an independent project. It is not affiliated with or endorsed by Linear or GitHub.

## Why

You write code in someone else's GitHub organization (a client, a partner, an open-source
project), but your team plans its work in its own Linear workspace. Linear's GitHub
integration can't connect the two: an admin of the GitHub organization has to install it,
and an organization can connect to only one Linear workspace.

So you copy issues and pull requests into Linear by hand, and move their status as they
progress. ferryGL does that for you.

## What it does

Each time you run it, ferryGL:

1. **Finds your GitHub work** in the repositories you list: issues you created or are
   assigned to, pull requests you opened, and other people's pull requests you're assigned
   to, reviewed, were asked to review, or wrote commits for.
2. **Gives each item one Linear issue**, assigned to you and linked back to GitHub. A pull
   request you open for one of your issues joins that issue's Linear issue, so an issue and
   its fix are tracked once.
3. **Keeps the status in step with GitHub:**

   | On GitHub | In Linear |
   |---|---|
   | Issue open, or your review is requested | Todo |
   | Pull request in draft | In Progress |
   | Pull request open, or you've reviewed it | In Review |
   | Pull request merged, or issue closed as completed | Done |
   | Pull request closed unmerged, or issue closed as not planned | Canceled |

   An issue's Linear issue follows its pull request until the issue itself is closed. The
   [sync rules](https://oussamabatouche.com/ferryGL/docs/sync-rules.html#status-rules) cover every case, and the status names are
   configurable.

It's built to be safe in repositories that belong to someone else:

- **Read-only on GitHub.** It never comments, labels or changes anything there.
- **Preview first.** `ferrygl sync` only shows what it would do. `ferrygl sync --apply` does it.
- **Your edits win.** It moves a Linear status only when something changes on GitHub. If you
  move an issue by hand, it stays where you put it.
- **Only your issues.** It changes only Linear issues assigned to you, never a teammate's.
- **Nothing is deleted.** If an item leaves your scope, its Linear issue stays as it is.
- **Little is copied.** Titles, numbers, repository names, links and states. Descriptions
  only if you turn them on. Never GitHub comments or other people's names. Its own comments,
  off by default, name only GitHub items, states and statuses.

## How it works

ferryGL is a command you run by hand or on a schedule. It has no server and no database.

1. **Read GitHub.** For each *route* in your config (some repositories and the Linear team
   they go to), ferryGL searches GitHub once for each of your roles.
2. **Look up Linear.** It finds the Linear issues it made before by their links to GitHub, so
   running it again never creates duplicates. Before creating a new issue, it looks for one of
   your Linear issues in the route's team that already mentions the GitHub item, and links that
   one instead.
3. **Plan.** It compares GitHub with Linear and lists the changes: create an issue, link one,
   or move a status.
4. **Apply.** With `--apply`, it makes those changes in Linear.

On each Linear issue, ferryGL records the last GitHub state it acted on in the metadata of
the GitHub link. That's how it tells whether something changed on GitHub since the last run,
and why it leaves your own edits alone.

## Quick start

You need Python 3.11 or later, a GitHub account that can read the repositories, and a Linear
workspace.

1. Install ferryGL (or use `uv tool install ferrygl`, or `pip install ferrygl`):

   ```console
   $ pipx install ferrygl
   ```

2. Sign in to GitHub with the [GitHub CLI](https://cli.github.com/) (`gh auth login`), or set
   `GH_TOKEN` to a token that can read the repositories.

3. Create a personal API key in Linear's
   [Security & access settings](https://linear.app/settings/account/security), and set
   `LINEAR_API_KEY` to it.

4. Write `~/.config/ferrygl/ferrygl.toml`:

   ```toml
   [[route]]
   repos = ["acme/api", "acme/web"]   # or "acme/*" for every repository of an owner
   team = "ENG"                       # Linear team key or name

   [sync]
   since = 2026-09-01                 # ignore items not updated since this date
   ```

5. Check the setup, preview the changes, then apply them:

   ```console
   $ ferrygl check
   $ ferrygl sync
   $ ferrygl sync --apply
   ```

   The preview lists each change:

   ```text
   acme/api#252  issue     update  ENG-7 Todo -> In Review
   acme/api#335  pr        link    -> ENG-7
   acme/api#348  involved  adopt   -> ENG-9
   3 changes (1 adopt, 1 link, 1 update). Run with --apply to make them.
   ```

[Getting started](https://oussamabatouche.com/ferryGL/docs/getting-started.html) walks through the same steps in more detail.

## Documentation

The documentation is on the website, at https://oussamabatouche.com/ferryGL/. Its source is in
[`docs/`](https://github.com/ao-batouche/ferryGL/tree/main/docs).

| Page | What's in it |
|---|---|
| [Getting started](https://oussamabatouche.com/ferryGL/docs/getting-started.html) | Install, credentials, and a careful first run |
| [Configuration](https://oussamabatouche.com/ferryGL/docs/configuration.html) | Every setting in `ferrygl.toml`, and checks in your editor |
| [Sync rules](https://oussamabatouche.com/ferryGL/docs/sync-rules.html) | Which items are synced, how they map to Linear issues, and how statuses move |
| [Commands](https://oussamabatouche.com/ferryGL/docs/commands.html) | `check` and `sync`, reading the plan, exit statuses, schedules, VS Code, troubleshooting |
| [Data and privacy](https://oussamabatouche.com/ferryGL/docs/data-and-privacy.html) | What ferryGL reads, what it writes to Linear, and what it keeps |
| [Architecture](https://oussamabatouche.com/ferryGL/docs/architecture.html) | How a run works inside, request costs, retries and limits |

## What it doesn't do

- Write anything to GitHub.
- Sync both ways, or keep descriptions and comments in sync.
- React instantly. There are no webhooks: Linear catches up each time ferryGL runs.
- Sync every issue in a repository for a whole team. Linear's GitHub integration does that
  when you can install it.

## Contributing

```console
$ uv sync
$ uv run pytest
$ uv run ruff check && uv run ruff format --check
```

Read [AGENTS.md](https://github.com/ao-batouche/ferryGL/blob/main/AGENTS.md) before changing ferryGL. It lists the rules that keep users'
data safe, and how changes are tested and reviewed.

## License

[MIT](https://github.com/ao-batouche/ferryGL/blob/main/LICENSE)
