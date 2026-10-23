# Commands

```text
ferrygl check [--config PATH]
ferrygl sync [--apply] [--config PATH]
ferrygl watch [--preview] [--interval SECONDS] [--bg] [--config PATH]
ferrygl watch --list
ferrygl watch --stop [ID | all] [--config PATH]
ferrygl watch --log [ID] [--config PATH]
ferrygl --version
```

## `ferrygl check`

`check` confirms that everything a sync needs is in place. It writes nothing. It checks:

- that the Linear API key works, and whose it is;
- for each route, which GitHub account the token belongs to, and that every repository (or
  owner, for `owner/*`) is visible to it;
- that the route's Linear team exists and has every state named in `[status]`;
- that the project and label exist, when they're configured.

```text
✓ Linear  signed in as Alice Example
route 1: acme/api, acme/web -> ENG / Acme engagement
  ✓ GitHub  reading as alice-acme
  ✓ repo    acme/api
  ✗ repo    acme/web: not found with this token
  ✓ Linear  team ENG (Engineering)
  ✓ Linear  states Todo, In Progress, In Review, Done, Canceled
  ✓ Linear  project Acme engagement
```

GitHub answers "not found" both for repositories that don't exist and for ones the token
can't see, so a ✗ on a repository usually means a missing permission.

## `ferrygl sync`

`sync` reads GitHub and Linear, and prints the changes that would bring Linear in line with
GitHub. It writes nothing.

```text
acme/api#252  issue     update  ENG-7 Todo -> In Review
acme/api#335  pr        link    -> ENG-7
acme/api#360  issue     create  "Cache the session lookup #360" -> Todo
acme/api#348  involved  adopt   -> ENG-9
4 changes (1 create, 1 adopt, 1 link, 1 update). Run with --apply to make them.
```

Before reading GitHub, `sync` checks the Linear setup of every route. If a team, project,
state or label is missing, it stops with exit status 2 and changes nothing.

### Reading the plan

Each line shows the GitHub item, its [kind](sync-rules.md#whats-in-scope) (`issue`, `pr` or
`involved`), the action, and its details.

| Action | What happens in Linear |
|---|---|
| `create` | A new Linear issue, assigned to you, with the title and status shown and a link to GitHub |
| `adopt` | An existing Linear issue of yours that mentions the item gets a link to it. Its status changes only if a change is shown |
| `link` | A pull request is linked to the Linear issue of the issue it refers to |
| `update` | The status moves as shown, because the GitHub state changed |
| `record` | The new GitHub state is noted on the link, and the status stays as it is |
| `skip` | Nothing. ferryGL needs a decision from you, and the line says why |

[Sync rules](sync-rules.md) explains when each action happens.

With [`[sync] comment = true`](configuration.md#sync), every action except `skip` also posts a
comment on its Linear issue, and the plan shows it under the change:

```text
acme/api#252  issue     update  ENG-7 Todo -> In Review
              comment: (BY-FGL) Status Todo → In Review: acme/api#252 has a pull request open for review.
```

### `--apply`

`ferrygl sync --apply` prints the same plan, makes the changes, and prints a line such as
`created ENG-12 for acme/api#360` for each new Linear issue.

For each change, ferryGL sets the status first, then posts its comment (with
`[sync] comment = true`), and then writes the links. If the status update or the comment
fails, the new GitHub state isn't recorded, so the next run tries again. If writing the links
fails after the comment was posted, the next run comments again. A failed write is reported,
and the run carries on with the next change.

## `ferrygl watch`

`watch` keeps Linear in step with GitHub while it runs, so you don't have to run `sync`
yourself. It syncs every route, then checks GitHub every minute by default, and syncs a route
again whenever something in its repositories changed. Stop it with
[`ferrygl watch --stop`](#seeing-and-stopping-watches) from another terminal, which lets it
finish the sync it's in, or with Ctrl-C, which stops it at once.

```console
$ ferrygl watch
Watching 1 route, checking GitHub every 60 seconds. To stop it: Ctrl-C, or ferrygl watch --stop
2026-10-21T10:15:03+00:00  syncing every route
No changes.
2026-10-21T10:32:47+00:00  route 1 (acme/api -> ENG): changed on GitHub
acme/api#252  issue     update  ENG-7 Todo -> In Review
Applied 1 change (1 update).
2026-10-21T10:33:48+00:00  route 1 (acme/api -> ENG): syncing again, in case GitHub's search lagged
No changes.
```

Each sync is a `ferrygl sync --apply` of that route, with the same rules and output. Unlike
`sync`, `watch` makes the changes without asking: add `--preview` to only show them, as
`ferrygl sync` does. Preview with `ferrygl sync` before you first start `watch`. Before it
reads GitHub, it checks the Linear setup of every route, and if anything is missing, it stops
with exit status 2 and changes nothing.

- **What counts as a change.** A check reads the 20 most recently updated issues and pull
  requests in each route's repositories, updated by anyone. When they differ from what the
  check before the route's last sync found, the route syncs again. So a change that has
  nothing to do with you, such as someone else's comment, also starts a sync, which then
  finds nothing to do. A route syncs at most once per check.
- **Why a change syncs twice.** GitHub's search can take a moment to show a change, such as a
  review you just submitted. So after syncing a route for a change, `watch` syncs it once
  more at the next check. If GitHub takes longer than that, the change is picked up at the
  route's next change, or when `watch` starts again.
- **`--interval SECONDS`** sets the time between checks: 60 by default, and at least 30.
- **Your GitHub rate limit.** GitHub usually gives your account 5,000 points an hour, shared
  with your other tools, such as the GitHub CLI. A check costs 1 point per route. A sync costs
  what `ferrygl sync` costs: about 7 points per route, plus, with `prs_with_commits` on, as it
  is by default, 26 for every 25 pull requests in scope that you didn't open, and at least 26.
  In a busy repository that adds up, so after its first sync, `watch` doesn't start a sync
  while less than a quarter of the hour's points are left. It says so, keeps checking, and
  syncs again once GitHub's limit has reset. To make syncs lighter, set `since`, or turn off
  `prs_with_commits` ([Roles](configuration.md#roles)).
- **Errors.** When a check or a sync fails, for example while your machine is offline,
  `watch` reports it, keeps running, and tries again at the next check.
- **Changes made only in Linear**, such as a GitHub link you add by hand to settle a `skip`,
  are picked up at the route's next change on GitHub. Restart `watch` to pick them up sooner.
- **Changes to the config file** take effect when `watch` starts again: it reads the file
  once, when it starts.
- **One watch per config file.** Two runs that create a Linear issue for the same item at the
  same moment can both create it. So on Linux and macOS, a second `watch` of the same config
  file stops with exit status 2, and names the ID of the one already running. For the same
  reason, don't watch the same repositories into the same Linear workspace from two config
  files, and don't run `ferrygl sync --apply`, by hand or on a schedule, while a `watch`
  without `--preview` is running.
- **`--bg`** keeps `watch` running in the background after you close the terminal.
  `--list` lists the watches running, `--stop` stops one, and `--log` shows a background
  watch's log. See [Keeping watch running](#keeping-watch-running).

## Environment variables

| Variable | Meaning |
|---|---|
| `FERRYGL_CONFIG` | The config file, when `--config` isn't given |
| `XDG_CONFIG_HOME` | The base of the default config path |
| `XDG_STATE_HOME` | The base of the directory where `watch` keeps its lock and, with `--bg`, its log |
| `LINEAR_API_KEY` | Your Linear API key. Takes precedence over `[linear] api_key_file` |
| `GH_TOKEN`, `GITHUB_TOKEN` | A GitHub token for routes without `github_user` |

## Exit statuses

| Status | Meaning |
|---|---|
| 0 | Everything worked |
| 1 | A route couldn't be read, or a write failed. Other routes and changes still ran. For `check`, at least one line is ✗ |
| 2 | A setup or configuration error: an invalid config file or no Linear API key. For `sync` and `watch`, also Linear unreachable at the start, or a missing team, project, state or label. Nothing was read from GitHub or written to Linear. `check` reports those Linear problems as ✗ lines, with status 1 |
| 130 | Interrupted with Ctrl-C. Changes already made stay made, and a rerun picks up the rest |

`watch` runs until you stop it. It ends with status 0 when asked to stop (`--stop`, `kill` or
`systemctl stop`), and 130 with Ctrl-C. It ends with 2 at the start if the configuration or
the Linear setup is wrong, Linear can't be reached, or a watch of the same config file is
running. Failures after that are reported, and `watch` keeps running.

With `--bg`, `watch` exits as soon as the background watch is running, with status 0. A
problem it finds first gives 2 for the setup, or 1 if GitHub can't be read. `--list`,
`--stop` and `--log` end with 0, or 1 when there's no watch with the ID given, or no log to
show. `--bg`, `--list`, `--stop` and `--log` end with 2 on Windows, where they aren't
available.

## Running on a schedule

ferryGL polls: Linear catches up each time it runs. A run with nothing to do changes nothing,
so you can run it as often as you like. To sync as things change rather than at set times,
use [`ferrygl watch`](#ferrygl-watch).

### cron

```text
PATH=/home/alice/.local/bin:/usr/local/bin:/usr/bin:/bin
0 9,15 * * 1-5  ferrygl sync --apply >> "$HOME/.cache/ferrygl.log" 2>&1
```

cron doesn't load your shell's environment, and starts with a short `PATH`:

- Set `PATH` so cron finds `ferrygl` (`command -v ferrygl` shows where it is) and, if you use
  it, `gh`.
- Put the Linear key in `[linear] api_key_file` rather than `LINEAR_API_KEY`.
- Use `github_user` on routes, or a GitHub CLI login, rather than `GH_TOKEN`.
- Try the schedule once without `--apply`, and read the log. If it shows
  `` `gh auth token` failed ``, cron can't reach the system keyring where the GitHub CLI keeps
  your token. `gh auth login --insecure-storage` keeps it in the CLI's config file instead.

### GitHub Actions

You can run ferryGL in a scheduled workflow in a repository on your own account, with your
GitHub token and Linear API key stored as secrets:

- Pass the token as `GH_TOKEN`, and leave `github_user` unset, since that needs the GitHub CLI
  to be signed in to the account.
- The workflow's built-in `GITHUB_TOKEN` can only read its own repository, so it can't see
  other organizations' repositories. Use your own token.

A template workflow is planned.

## Keeping watch running

`ferrygl watch` runs as long as the terminal it started in. Add `--bg` to keep it running in
the background instead:

```console
$ ferrygl watch --bg
Watching in the background, as watch 12345. Its output goes to /home/alice/.local/state/ferrygl/watch-ferrygl-bf1e20bc.log.
To stop it: ferrygl watch --stop 12345
```

- **Problems show up front.** Before it goes to the background, `--bg` checks the Linear
  setup, and that GitHub can be read for every route. If anything is wrong, it says so and
  starts nothing. Then it waits two seconds, and if the background watch stops in that time,
  it shows what the watch wrote.
- **Each config file has its own log**, in `$XDG_STATE_HOME/ferrygl`, or
  `~/.local/state/ferrygl` when that isn't set, named after the config file. Each background
  watch of that config file adds to it, so it grows while you use `--bg`. Delete it while no
  watch of that config file is running to start afresh. Only you can read the folder and its
  files, since the log names GitHub items and the titles of Linear issues.
- **Credentials** come from the environment you start it in, such as `LINEAR_API_KEY` and
  `GH_TOKEN`, and never appear on its command line.
- **It doesn't come back by itself.** A background watch stops when you stop it or restart
  your machine, and on some systems when you log out. To start it when you log in, and
  restart it if it fails, make it a [service](#as-a-systemd-service) instead.
- `--bg`, `--list`, `--stop` and `--log` work on Linux and macOS. On Windows, run
  `ferrygl watch` in a window of its own, and stop it with Ctrl-C.

### Seeing and stopping watches

`ferrygl watch --list` shows the watches running on your machine, with their IDs:

```console
$ ferrygl watch --list
ID     STARTED                    RUNS IN     MODE     EVERY  CONFIG
12345  2026-10-23T09:15:03+00:00  background  apply    60s    /home/alice/.config/ferrygl/ferrygl.toml
12399  2026-10-23T09:40:11+00:00  foreground  preview  120s   /home/alice/clients/beta.toml
```

A watch's ID is its process ID. `RUNS IN` is `background` for a watch started with `--bg`,
and `foreground` for one running in a terminal or as a service. `MODE` is `apply` when it
makes changes, and `preview` when it only shows them.

```text
ferrygl watch --list               the watches running, with their IDs
ferrygl watch --stop [ID | all]    stop a watch, or all of them
ferrygl watch --log [ID]           the last 20 lines of a background watch's log
```

Without an ID, `--stop` and `--log` act on the watch of the config file in use: the one
`--config` or `FERRYGL_CONFIG` names, or the default one. So if you have one config file,
`ferrygl watch --stop` stops its watch, and `ferrygl watch --log` shows its log, even after
the watch has stopped, which is when you'll want to know why.

```console
$ ferrygl watch --stop
Stopping watch 12345 (/home/alice/.config/ferrygl/ferrygl.toml)...
Watch 12345 stopped.
```

- **Stopping is safe.** `--stop` asks the watch to stop, and the watch finishes the sync it's
  in first, so no change is left half made. `--stop` waits up to 30 seconds for it; a watch
  still busy then stops as soon as its sync ends. A plain `kill ID` does the same, and so does
  `systemctl --user stop`. Ctrl-C, on the other hand, stops a watch at once.
- **A stopped watch ends with status 0**, and its last line, in its log or terminal, ends
  with `stopped, as asked`.
- **To follow a log** as it grows, use `tail -f` on the file `--log` names.
- **`--stop` and `--log` need no credentials**, and read nothing from GitHub or Linear.

### As a systemd service

On Linux, with systemd, save this as `~/.config/systemd/user/ferrygl.service`:

```ini
[Unit]
Description=ferryGL: keep Linear in step with GitHub

[Service]
ExecStart=%h/.local/bin/ferrygl watch
Restart=on-failure
RestartSec=5min
TimeoutStopSec=5min

[Install]
WantedBy=default.target
```

Then start it, and follow its output:

```console
$ systemctl --user enable --now ferrygl
$ journalctl --user -u ferrygl -f
```

- Set `ExecStart` to the path `command -v ferrygl` shows, and leave out `--bg`: systemd keeps
  `watch` in the background itself, and its output goes to the journal.
- `ferrygl watch --list` shows the service's watch too, as running in the foreground. Its
  output is in the journal, so `ferrygl watch --log` doesn't show it.
- `systemctl --user stop ferrygl` and `ferrygl watch --stop` both stop it after its current
  sync. `TimeoutStopSec=5min` gives a slow sync time to finish before systemd ends it, and
  systemd doesn't restart a watch that stopped when asked.
- If `watch` stops on an error, for example because Linear can't be reached as you log in,
  systemd starts it again 5 minutes later. After fixing a setup problem, run
  `systemctl --user restart ferrygl`.
- Like cron, a service doesn't load your shell's environment, so follow the [cron](#cron)
  notes for the Linear key and the GitHub token. If you use the GitHub CLI, add
  `Environment=PATH=…` to `[Service]`, with the folder `command -v gh` shows, so that the
  service finds `gh`.
- The service stops when you log out, unless you run `loginctl enable-linger`.

## Running from VS Code

You can run `check` and `sync` from VS Code as
[tasks](https://code.visualstudio.com/docs/debugtest/tasks). Make them user tasks: they work in
every window, and nothing is added to the repositories you work in, which usually belong to
someone else.

1. Run **Tasks: Open User Tasks** from the command palette. If VS Code asks for a template,
   choose **Others**.
2. Replace the file's contents with these tasks, or add them to its `tasks` list:

   ```json
   {
     "version": "2.0.0",
     "tasks": [
       {
         "label": "ferryGL: check",
         "detail": "Check credentials, repositories and Linear settings. Writes nothing",
         "type": "shell",
         "command": "ferrygl check",
         "problemMatcher": []
       },
       {
         "label": "ferryGL: preview sync",
         "detail": "Show the changes a sync would make. Writes nothing",
         "type": "shell",
         "command": "ferrygl sync",
         "problemMatcher": []
       },
       {
         "label": "ferryGL: apply sync",
         "detail": "Make the changes in Linear",
         "type": "shell",
         "command": "ferrygl sync --apply",
         "problemMatcher": []
       }
     ]
   }
   ```

3. Run **Tasks: Run Task** and pick one. Its output appears in the terminal panel.

- Preview before you apply, as in a terminal: the apply task doesn't ask for confirmation.
- `"problemMatcher": []` stops VS Code from asking whether to scan the output for errors.
- Tasks get the environment VS Code started with. If you set `LINEAR_API_KEY` or `GH_TOKEN`
  after opening VS Code, restart it, or put the Linear key in `[linear] api_key_file`.

To get completion and checks while you edit `ferrygl.toml`, see
[Configuration](configuration.md#completion-and-checks-in-your-editor).

## Troubleshooting

| Message | What to do |
|---|---|
| `no Linear API key: set LINEAR_API_KEY or [linear] api_key_file` | Give ferryGL your key ([Getting started](getting-started.md#3-give-ferrygl-a-linear-api-key)) |
| `Linear rejected the API key` | The key was revoked or mistyped. Create a new one |
| `Linear team 'ENG' not found` | Check the team key or name, and that the API key can access the team |
| `team ENG has no state named 'In Review'` | Set [`[status]`](configuration.md#status) to your team's state names |
| `project '…' not found for team ENG` | Check the project name, and that the team has access to the project |
| `label '…' not found in team ENG or the workspace` | Create the label in Linear, or remove `label` |
| `` `gh auth token` failed for account alice `` | Run `gh auth login` for that account, or fix `github_user` |
| `` `gh auth token` failed for the active account `` | Run `gh auth login`, or set `GH_TOKEN` |
| `no GitHub token: set GH_TOKEN, or install the GitHub CLI …` | ferryGL couldn't run `gh`. Install the GitHub CLI and run `gh auth login`; under cron, add the folder `command -v gh` shows to `PATH`. Routes without `github_user` can use `GH_TOKEN` instead |
| `GitHub rejected the token (HTTP 401)` | The token expired or was revoked. `gh auth status` shows which |
| `acme/web: not found with this token` | The token can't see the repository: wrong account, missing fine-grained access, or the organization hasn't approved the token |
| `several of your Linear issues mention it (ENG-3, ENG-8)` | Add the GitHub link to the right Linear issue by hand, then run again |
| `ferryGL is already watching …, as watch 12345` | A watch of that config file is running. Leave it running, or stop it with `ferrygl watch --stop 12345`, then start yours |
| `no watch with ID 12345 is running` | That watch has stopped, or the ID is mistyped. `ferrygl watch --list` shows the IDs of those running |
| `the background watch stopped right away` | The lines below the message are what it wrote, and `ferrygl watch --log` shows them again. Fix what they say, then run it again |
| `waiting until …, when GitHub's rate limit resets` | `watch` leaves a quarter of your GitHub rate limit to your other tools, and syncs again after the reset. To use fewer points, set `since`, turn off `prs_with_commits`, or lengthen `--interval` |
| `watch makes the changes by default; add --preview to only show them` | `watch` has no `--apply`. Leave it out, or add `--preview` to only show the changes |
| `` Not started. Run `ferrygl check` for details. `` | `--bg` couldn't read GitHub for a route, and the lines above say which. Fix it, then start the watch again |
| `watch 12345 runs in the foreground: its output goes to the terminal or service that started it` | `--log` shows only the logs of watches started with `--bg`. Look in that terminal, or for a service, in the journal |
| `` … has no log yet. `ferrygl watch --bg` writes one. `` | No watch of that config file has run with `--bg`. If you use another config file, name it with `--config` |

**A run stopped halfway.** Run it again. ferryGL finds Linear issues by their GitHub links,
so it carries on where it stopped without creating duplicates.

**A network error while creating an issue or a comment.** ferryGL doesn't retry creating an
issue or a comment after a network or server error, because Linear may have created it anyway.
If it created the issue, the next sync finds it through the GitHub URL in its description and
adopts it. If it posted the comment, the next sync makes the change again and may post a
second comment.

**Runs are slow, or hit rate limits.** ferryGL waits and retries when GitHub or Linear asks it
to slow down. Set `since`, or turn off `prs_with_commits` ([Roles](configuration.md#roles)),
to make runs lighter.

**A second Linear issue appeared for the same item.** The first one is probably no longer
assigned to you. See [One Linear issue per item](sync-rules.md#one-linear-issue-per-item).
