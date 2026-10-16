# Getting started

This guide installs ferryGL, sets up its two credentials, and walks through a careful first
run.

## What you need

- Python 3.11 or later.
- A GitHub account that can read the repositories you work in.
- A Linear workspace where you can create issues, with the team (and optionally the project)
  your work should go to.

## 1. Install

Install the `ferrygl` command with any of:

```console
$ pipx install ferrygl
$ uv tool install ferrygl
$ pip install ferrygl
```

To try unreleased changes, install from the repository instead:

```console
$ uv tool install git+https://github.com/ao-batouche/ferryGL
```

`ferrygl --version` confirms it's installed.

## 2. Give ferryGL read access to GitHub

ferryGL only reads issues and pull requests. For each route, it takes a GitHub token from the
first of:

1. the GitHub CLI account named by the route's `github_user` (`gh auth token --user <name>`).
   When `github_user` is set, the environment variables below are ignored for that route;
2. `GH_TOKEN`, then `GITHUB_TOKEN`;
3. the GitHub CLI's active account (`gh auth token`).

The simplest setup is the [GitHub CLI](https://cli.github.com/):

```console
$ gh auth login
$ gh auth status
```

**More than one GitHub account?** Many people have a work account for a client's
organization and a personal one for open source. Sign in to both with `gh auth login`, then
set `github_user` on each route so ferryGL reads each repository as the right account.

**Using a token instead of the CLI?** A fine-grained personal access token needs read access
to *Issues*, *Pull requests* and *Metadata* on the repositories. Some organizations require
an owner to approve fine-grained tokens, or block classic tokens. If `ferrygl check` can't see
a repository, check that first.

## 3. Give ferryGL a Linear API key

Create a personal API key in Linear's
[Security & access settings](https://linear.app/settings/account/security) (Settings >
Account > Security & access) with read and write access. You can limit it to the teams
ferryGL writes to.

You don't need to be a workspace admin. If Linear won't let you create a key there, your
workspace admins have turned off member API keys: ask one of them to turn on *Member API keys*
in Settings > Administration > API.

Give it to ferryGL in one of two ways:

- set `LINEAR_API_KEY`, or
- save it in a file only you can read (`chmod 600`), and point `[linear] api_key_file` at it.
  This suits cron, which doesn't load your shell's environment.

If both are set, `LINEAR_API_KEY` wins.

ferryGL acts as the key's owner, and only changes Linear issues assigned to that person.

## 4. Write a config file

ferryGL reads `~/.config/ferrygl/ferrygl.toml`, or `$XDG_CONFIG_HOME/ferrygl/ferrygl.toml`
when `XDG_CONFIG_HOME` is set. To use another file, pass `--config <path>` or set
`FERRYGL_CONFIG`.

Start small, with one route and a `since` date so the first run only looks at recent work:

```toml
[[route]]
repos = ["acme/api"]
team = "ENG"

[sync]
since = 2026-09-01

[linear]
api_key_file = "~/.config/ferrygl/linear-api-key"
```

A route sends the issues and pull requests of some repositories to one Linear team, and
optionally one project. You can add more routes later.

If your team's workflow states aren't named Todo, In Progress, In Review, Done and Canceled,
set `[status]` now. [Configuration](configuration.md) lists every setting.

## 5. Check the setup

```console
$ ferrygl check
✓ Linear  signed in as Alice Example
route 1: acme/api -> ENG
  ✓ GitHub  reading as alice
  ✓ repo    acme/api
  ✓ Linear  team ENG (Engineering)
  ✓ Linear  states Todo, In Progress, In Review, Done, Canceled
```

`check` writes nothing. Fix every ✗ before going on: [Commands](commands.md#troubleshooting)
explains the common ones.

## 6. Preview the first sync

```console
$ ferrygl sync
```

Without `--apply`, `sync` only prints what it would do. Read the first preview carefully. The
first sync is the one most likely to meet Linear issues you made by hand:

- `create` makes a new Linear issue. Check the titles, because they're copied into Linear.
- `adopt` links an existing Linear issue of yours that already mentions the GitHub item.
  Check that it picked the right one.
- `record` notes the current GitHub state on one of your issues without moving its status.
- `skip` means ferryGL needs a decision from you, for example because several of your Linear
  issues mention the same item. Link the right one by hand, then preview again.

[Reading the plan](commands.md#reading-the-plan) describes every action.

## 7. Apply it

```console
$ ferrygl sync --apply
```

ferryGL makes the changes and prints the identifier of each Linear issue it creates. You can
run it again at any time: it finds what it made before, so it never creates duplicates.

## Next steps

- [Run it on a schedule](commands.md#running-on-a-schedule) to keep Linear up to date.
- Widen `since`, add routes, or turn roles on and off in [Configuration](configuration.md).
- Read the [sync rules](sync-rules.md) to see exactly how statuses move.
