<p align="center">
  <img src="https://raw.githubusercontent.com/ao-batouche/ferryGL/main/assets/ferrygl-logo-horizontal.png" alt="ferryGL" width="560">
</p>

# ferryGL

**Track your work in clients' GitHub repositories in your own Linear workspace.**

You contribute to GitHub repositories that belong to another organization, such as a client.
Your team tracks its work in its own Linear workspace. Linear's GitHub integration can't
bring that work across: it syncs only repositories whose GitHub organization is connected to
your workspace, and a client's usually isn't.

ferryGL fills that gap. Using only your own GitHub account and Linear API key, it copies the
issues and pull requests you work on into your Linear workspace, and keeps their status in
step with GitHub. Nobody has to install anything in the client's organization. And ferryGL is
token-free automation: it runs no AI model, so unlike an AI agent doing the same job, it
spends no tokens. If you work with an AI agent, the agent can run ferryGL for you and read
only its plan.

> **Status: early version (0.2).** `ferrygl check`, `ferrygl sync` and `ferrygl watch` work.
> They're tested with local fixtures, and their GitHub reads against a real GitHub
> repository.
>
> ferryGL is an independent project. It is not affiliated with or endorsed by Linear or GitHub.

## Why

Connecting a client's GitHub organization to your Linear workspace takes one of the client's
organization owners or repository admins, and a GitHub organization can connect to only one
Linear workspace, often the client's own. The same goes for a partner's organization or an
open-source project. Here's how the ways to track that work compare:

| | Works in a client's repositories | What each run costs |
|---|---|---|
| Linear's GitHub integration | Only if the client connects their organization to your workspace | Nothing |
| Copying by hand | Yes | Your time, each time something moves on GitHub |
| An AI agent, such as Claude | Yes | Tokens, to reread the same items and repeat the same moves |
| **ferryGL** | **Yes, with your own credentials** | **No tokens: plain API calls and fixed rules** |

An AI agent can do this job, but the job is routine: find your GitHub items, compare them
with Linear, move a few statuses. An agent spends tokens on it every run, and may not do it
the same way twice. ferryGL follows the same rules every time, shows each change before
making it, and spends no tokens, so you can keep your agent, and your tokens, for work that
needs them.

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
  `ferrygl watch` makes changes as they come, unless you add `--preview`, so preview with
  `ferrygl sync` before you start it.
- **Your edits win.** It moves a Linear status only when something changes on GitHub. If you
  move an issue by hand, it stays where you put it.
- **Only your issues.** It changes only Linear issues assigned to you, never a teammate's.
- **Nothing is deleted.** If an item leaves your scope, its Linear issue stays as it is.
- **Little is copied.** Titles, numbers, repository names, links and states. Descriptions
  only if you turn them on. Never GitHub comments or other people's names. Its own comments,
  off by default, name only GitHub items, states and statuses.

## How it works

ferryGL is a command you run by hand, on a schedule, or with `ferrygl watch`, which syncs
whenever your repositories change. It has no server and no database.

1. **Read GitHub.** For each *route* in your config (some repositories and the Linear team
   they go to), ferryGL searches GitHub once for each of your roles.
2. **Look up Linear.** It finds the Linear issues it made before by their links to GitHub, so
   running it again never creates duplicates. Before creating a new issue, it looks for one of
   your Linear issues in the route's team that already mentions the GitHub item, and links that
   one instead.
3. **Plan.** It compares GitHub with Linear and lists the changes: create an issue, link one,
   or move a status.
4. **Apply.** `ferrygl sync --apply` makes those changes in Linear, and so does
   `ferrygl watch` unless you add `--preview`. `ferrygl sync` alone writes nothing.

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

6. To keep Linear in step from then on, leave `ferrygl watch` running, or add `--bg` (Linux
   and macOS) to run it in the background. It checks GitHub every minute by default, and
   syncs a route whenever something in its repositories changes. It makes the changes as
   they come; add `--preview` to only show them. `ferrygl watch --list` shows the watches
   running, and `ferrygl watch --stop` stops one.

[Getting started](https://oussamabatouche.com/ferryGL/docs/getting-started.html) walks through the same steps in more detail.

## Documentation

The documentation is on the website, at https://oussamabatouche.com/ferryGL/. Its source is in
[`docs/`](https://github.com/ao-batouche/ferryGL/tree/main/docs).

| Page | What's in it |
|---|---|
| [Getting started](https://oussamabatouche.com/ferryGL/docs/getting-started.html) | Install, credentials, and a careful first run |
| [Configuration](https://oussamabatouche.com/ferryGL/docs/configuration.html) | Every setting in `ferrygl.toml`, and checks in your editor |
| [Sync rules](https://oussamabatouche.com/ferryGL/docs/sync-rules.html) | Which items are synced, how they map to Linear issues, and how statuses move |
| [Commands](https://oussamabatouche.com/ferryGL/docs/commands.html) | `check`, `sync` and `watch`, background watches, reading the plan, exit statuses, schedules, VS Code, troubleshooting |
| [AI agents](https://oussamabatouche.com/ferryGL/docs/ai-agents.html) | Have an AI agent run ferryGL for you, for a fraction of the tokens |
| [Data and privacy](https://oussamabatouche.com/ferryGL/docs/data-and-privacy.html) | What ferryGL reads, what it writes to Linear, and what it keeps |
| [Architecture](https://oussamabatouche.com/ferryGL/docs/architecture.html) | How a run works inside, request costs, retries and limits |

## What it doesn't do

- Write anything to GitHub.
- Sync both ways, or keep descriptions and comments in sync.
- React instantly. Webhooks would need admin access to the repository, so ferryGL polls:
  Linear catches up each time it runs, or within a minute or two with `ferrygl watch`.
- Sync every issue in a repository for a whole team. Linear's GitHub integration does that
  when you can install it.

## Citing

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23475998.svg)](https://doi.org/10.5281/zenodo.23475998)

A [white paper](https://doi.org/10.5281/zenodo.23475998), archived on Zenodo, describes
ferryGL's design and safety rules, and evaluates them. To cite ferryGL, use GitHub's
**Cite this repository** button, or the reference in
[`docs/paper`](https://github.com/ao-batouche/ferryGL/tree/main/docs/paper).

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
