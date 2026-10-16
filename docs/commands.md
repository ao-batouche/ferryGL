# Commands

```text
ferrygl check [--config PATH]
ferrygl sync [--apply] [--config PATH]
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

## Environment variables

| Variable | Meaning |
|---|---|
| `FERRYGL_CONFIG` | The config file, when `--config` isn't given |
| `XDG_CONFIG_HOME` | The base of the default config path |
| `LINEAR_API_KEY` | Your Linear API key. Takes precedence over `[linear] api_key_file` |
| `GH_TOKEN`, `GITHUB_TOKEN` | A GitHub token for routes without `github_user` |

## Exit statuses

| Status | Meaning |
|---|---|
| 0 | Everything worked |
| 1 | A route couldn't be read, or a write failed. Other routes and changes still ran. For `check`, at least one line is ✗ |
| 2 | A setup or configuration error: an invalid config file or no Linear API key. For `sync`, also Linear unreachable at the start, or a missing team, project, state or label. Nothing was read from GitHub or written to Linear. `check` reports those Linear problems as ✗ lines, with status 1 |
| 130 | Interrupted with Ctrl-C. Changes already made stay made, and a rerun picks up the rest |

## Running on a schedule

ferryGL polls: Linear catches up each time it runs. A run with nothing to do changes nothing,
so you can run it as often as you like.

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

### GitHub Actions

You can run ferryGL in a scheduled workflow in a repository on your own account, with your
GitHub token and Linear API key stored as secrets:

- Pass the token as `GH_TOKEN`, and leave `github_user` unset, since that needs the GitHub CLI
  to be signed in to the account.
- The workflow's built-in `GITHUB_TOKEN` can only read its own repository, so it can't see
  other organizations' repositories. Use your own token.

A template workflow is planned.

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
| `no GitHub token: set GH_TOKEN, or install the GitHub CLI …` | Set `GH_TOKEN`, or install the GitHub CLI and run `gh auth login` |
| `GitHub rejected the token (HTTP 401)` | The token expired or was revoked. `gh auth status` shows which |
| `acme/web: not found with this token` | The token can't see the repository: wrong account, missing fine-grained access, or the organization hasn't approved the token |
| `several of your Linear issues mention it (ENG-3, ENG-8)` | Add the GitHub link to the right Linear issue by hand, then run again |

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
