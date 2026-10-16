# Getting started

Install ferryGL, connect it to GitHub and Linear, and run a first sync you can check before
anything changes. Each step shows the commands to run and what they print.

## What you need

- Python 3.11 or later.
- A GitHub account that can read the repositories you work in.
- A Linear account in the workspace your work should go to, and the key of your team: the
  `ENG` in an issue identifier such as `ENG-123`.

## 1. Install

```console
$ pipx install ferrygl
$ ferrygl --version
ferrygl 0.2.1
```

`uv tool install ferrygl` and `pip install ferrygl` work too. If your shell can't find
`ferrygl` afterwards, run `pipx ensurepath` and open a new terminal.

To try unreleased changes, install from the repository instead:

```console
$ uv tool install git+https://github.com/ao-batouche/ferryGL
```

## 2. Give ferryGL read access to GitHub

ferryGL only reads GitHub, as you. The simplest way to give it access is the
[GitHub CLI](https://cli.github.com/): sign in, then confirm the account.

```console
$ gh auth login
$ gh auth status
github.com
  ✓ Logged in to github.com account alice (keyring)
  - Active account: true
  - Git operations protocol: https
  - Token: gho_************************************
  - Token scopes: 'gist', 'read:org', 'repo'
```

`gh auth login` asks a few questions: choose GitHub.com, and log in with a web browser.
`gh auth status` should name the account that can see your repositories.

**More than one GitHub account?** Many people have a work account for a client's
organization and a personal one for open source. Run `gh auth login` once for each, then set
`github_user` on each route in step 4, so ferryGL reads each repository as the right account.

**Using a token instead of the CLI?** Set `GH_TOKEN` to a fine-grained personal access token
with read access to *Issues*, *Pull requests* and *Metadata* on the repositories. Some
organizations require an owner to approve fine-grained tokens, or block classic tokens. A
fine-grained token reads the repositories of one owner only, so if your routes cover several
organizations, use the GitHub CLI and set `github_user` on each route.

For each route, ferryGL finds its token this way:

1. With `github_user`, it asks the GitHub CLI for that account's token
   (`gh auth token --user <name>`), and uses only that. The environment variables below are
   ignored, and if the CLI has no token for the account, the route stops with an error.
2. Otherwise it uses `GH_TOKEN`, then `GITHUB_TOKEN`,
3. and then the GitHub CLI's active account (`gh auth token`).

## 3. Give ferryGL a Linear API key

ferryGL acts in Linear as you, with a personal API key. You don't need to be a workspace
admin to create one, and ferryGL only ever changes Linear issues assigned to you.

### Create the key

1. In Linear, open **Settings › Account › Security & access**
   ([open it](https://linear.app/settings/account/security)).
2. Under **Personal API keys**, create a new key and name it `ferryGL`.
3. Give it **Read** and **Write** access, or full access. You can limit it to the teams
   ferryGL writes to.
4. Copy the key. It starts with `lin_api_`, and Linear shows it only once.

> If Linear won't let you create a key, your workspace admins have turned off member API
> keys. Ask one of them to turn on **Member API keys** in Settings › Administration › API.

### Save it for ferryGL

Save the key in a file that only you can read:

```console
$ mkdir -p ~/.config/ferrygl
$ (umask 077; cat > ~/.config/ferrygl/linear-api-key)
```

The second command waits for input: paste the key, press Enter, then Ctrl-D. This keeps the
key out of your shell history, and `umask 077` makes the file private. Check it:

```console
$ ls -l ~/.config/ferrygl/linear-api-key
-rw------- 1 alice alice 49 Oct 16 09:00 /home/alice/.config/ferrygl/linear-api-key
```

`-rw-------` means only you can read it. In step 4, `[linear] api_key_file` points ferryGL
at this file. A file also works from cron, which doesn't load your shell's environment.

**Prefer an environment variable?** Set `LINEAR_API_KEY` instead, for example in CI or for
one terminal session. If both are set, `LINEAR_API_KEY` wins.

```console
$ export LINEAR_API_KEY=lin_api_...
```

## 4. Write a config file

Create `~/.config/ferrygl/ferrygl.toml` with your repository and team. Start with one route
and a `since` date, so the first run only looks at recent work:

```toml
[[route]]
repos = ["acme/api"]   # owner/name, as in the repository's GitHub URL
team = "ENG"           # your Linear team's key

[sync]
since = 2026-09-01     # skip items not updated since this date

[linear]
api_key_file = "~/.config/ferrygl/linear-api-key"
```

- A route sends the issues and pull requests of some repositories to one Linear team, and
  optionally one project. You can add more routes later.
- If your team's workflow states aren't named Todo, In Progress, In Review, Done and Canceled,
  set `[status]` now.
- To keep the file elsewhere, pass `--config <path>` or set `FERRYGL_CONFIG`. When
  `XDG_CONFIG_HOME` is set, the default is `$XDG_CONFIG_HOME/ferrygl/ferrygl.toml`.
- Editing in VS Code? One line at the top of the file gives you
  [completion and checks](configuration.md#completion-and-checks-in-your-editor).

[Configuration](configuration.md) lists every setting.

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

`check` writes nothing. The first line confirms your Linear key, and each route shows the
GitHub account ferryGL reads as, every repository it can see, and the Linear team and states.
Fix every ✗ before going on. The common ones:

| You see | What to do |
|---|---|
| `error: no Linear API key: set LINEAR_API_KEY or [linear] api_key_file` | Save the key ([step 3](#3-give-ferrygl-a-linear-api-key)), and check `api_key_file` |
| `error: /home/alice/.config/ferrygl/ferrygl.toml: file not found` | Save the config file at that path ([step 4](#4-write-a-config-file)) |
| `✗ Linear  Linear rejected the API key: …` | The key was mistyped or revoked. Create a new one |
| `✗ repo    acme/api: not found with this token` | The GitHub account can't see the repository. Check `gh auth status`, or set `github_user` |
| `✗ Linear  team ENG has no state named 'In Review'; …` | Set `[status]` to your team's state names |

[Commands](commands.md#troubleshooting) covers the rest.

## 6. Preview the first sync

```console
$ ferrygl sync
acme/api#9   issue     adopt   -> ENG-4
acme/api#12  issue     create  "Fix the login redirect #12" -> In Review
acme/api#18  pr        create  "Retry failed imports - #18" -> In Progress
acme/api#21  involved  create  "[PR] Upgrade httpx - #21" -> Todo
4 changes (3 create, 1 adopt). Run with --apply to make them.
```

Without `--apply`, `sync` only prints what it would do. Each line shows the GitHub item, its
kind, the action and its details. In this example:

- `acme/api#9` is an issue you opened. Your Linear issue ENG-4 already mentions it, so
  ferryGL adopts ENG-4 instead of creating another.
- `acme/api#12` is assigned to you, and your open pull request `acme/api#15` fixes it. They
  share one new Linear issue, In Review.
- `acme/api#18` is your draft pull request, so its new issue is In Progress.
- `acme/api#21` is someone else's pull request waiting for your review, so it's in Todo.

The first sync is the one most likely to meet Linear issues you made by hand, so read it
carefully:

- **create**: check the titles, because they're copied into Linear.
- **adopt**: check that ferryGL picked the right Linear issue.
- **skip**: ferryGL needs a decision from you, and the line says why. For example, several of
  your Linear issues mention the same item: link the right one by hand, then preview again.

[Reading the plan](commands.md#reading-the-plan) describes every action.

## 7. Apply it

```console
$ ferrygl sync --apply
acme/api#9   issue     adopt   -> ENG-4
acme/api#12  issue     create  "Fix the login redirect #12" -> In Review
acme/api#18  pr        create  "Retry failed imports - #18" -> In Progress
acme/api#21  involved  create  "[PR] Upgrade httpx - #21" -> Todo
created ENG-101 for acme/api#12
created ENG-102 for acme/api#18
created ENG-103 for acme/api#21
Applied 4 changes (3 create, 1 adopt).
```

ferryGL prints the plan again, makes the changes, and names each Linear issue it creates.
Run it again at any time: it finds what it made before through the GitHub links, so it never
creates duplicates.

```console
$ ferrygl sync
No changes.
```

## Next steps

- [Run it on a schedule](commands.md#running-on-a-schedule) to keep Linear up to date.
- Widen `since`, add routes, or turn roles on and off in [Configuration](configuration.md).
- Read the [sync rules](sync-rules.md) to see exactly how statuses move.
