# AI agents

If you work with an AI agent, such as Claude Code, it can keep your Linear workspace in step
with GitHub by running ferryGL, instead of doing the job itself.

ferryGL is token-free automation: it runs no AI model. An agent that runs it spends its tokens
only on reading ferryGL's plan, a line per change, rather than on reading every issue and pull
request on every run.

## Why let ferryGL do the job

- **Far fewer tokens.** The [white paper](paper/ferrygl-white-paper.pdf) estimates that an
  agent doing this job itself would read and write about 92,000 to 218,000 tokens on a run
  that finds a few changes in a route of 100 items, and 55,000 to 106,000 on a run that finds
  none. An agent that runs ferryGL instead needs about 3,100 to 5,800 on a route of 100 items,
  and under 7,500 on one of 800, after the first run. Across 24 models from 10
  providers, for a team of 100 running it twice a workday, that's an estimated $596 to $59,576
  a year for an agent doing the job itself, and $59 to $6,249 for one that runs ferryGL (middle
  estimates).
- **The same result every time.** ferryGL follows fixed [sync rules](sync-rules.md), and
  leaves your own edits in Linear alone.
- **Limits the agent can't cross.** Through ferryGL, an agent can't write to GitHub, change a
  teammate's Linear issue, or delete anything, whatever it's asked or gets wrong.

## Set it up

1. Install and configure ferryGL on the machine where your agent runs commands, as in
   [Getting started](getting-started.md), and make sure `ferrygl check` works there.
2. Give ferryGL its credentials the way you would for yourself: a GitHub CLI login or
   `GH_TOKEN`, and `LINEAR_API_KEY` or `[linear] api_key_file`. Don't paste keys into the
   conversation with the agent.
3. Add instructions like these to the file your agent reads its instructions from, such as
   `CLAUDE.md` for Claude Code:

   ```markdown
   ## Syncing GitHub work into Linear

   To sync my GitHub work into Linear, run `ferrygl sync` and show me the plan.
   Run `ferrygl sync --apply` only after I approve it.
   If a line says skip, tell me what ferryGL needs.
   If the exit status is 2, report the error and stop.
   Never change GitHub or Linear yourself to do this.
   ```

Then ask the agent to sync, or let it sync when your work calls for it.

## What the agent reads

- **The plan.** `ferrygl sync` prints one line per change: the GitHub item, its kind, the
  action and its details, then a summary such as
  `4 changes (1 create, 1 adopt, 1 link, 1 update). Run with --apply to make them.`, or
  `No changes.` [Reading the plan](commands.md#reading-the-plan) explains each action.
- **The exit status.** 0 when everything worked, 1 when a route or a write failed, 2 when the
  setup or configuration is wrong and nothing was read or written, and 130 when interrupted.
  See [Exit statuses](commands.md#exit-statuses).
- **The setup.** `ferrygl check` shows which credential, repository, team or state is missing.

## When you don't need an agent

ferryGL runs on a schedule by itself, with cron or GitHub Actions
([Running on a schedule](commands.md#running-on-a-schedule)). A scheduled run spends no
tokens at all. Ask an agent to run it when you're already working with one and want the sync
done along the way.
