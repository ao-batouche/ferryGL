# ferryGL documentation

ferryGL tracks your work in other organizations' GitHub repositories, such as a client's, in
your own Linear workspace.

Linear's GitHub integration syncs only repositories whose GitHub organization is connected to
your workspace, and a client's usually isn't. ferryGL uses your own GitHub account and Linear
API key instead, so nobody has to install anything in the client's organization. And because
it makes plain API calls with fixed rules, it spends no tokens, unlike an AI agent doing the
same job.

The [README](../README.md) explains why ferryGL exists, what it does and how it works. These
pages cover the details.

| Page | Read it when you want to |
|---|---|
| [Getting started](getting-started.md) | Install ferryGL, set up its credentials, and do a careful first run |
| [Configuration](configuration.md) | Look up a setting in `ferrygl.toml`, or have your editor check the file |
| [Sync rules](sync-rules.md) | Know which items are synced, how they map to Linear issues, and how statuses move |
| [Commands](commands.md) | Read `check` and `sync` output, run on a schedule or from VS Code, or fix an error |
| [Data and privacy](data-and-privacy.md) | Check what ferryGL reads, writes to Linear, and keeps |
| [Architecture](architecture.md) | Understand how a run works inside, its request costs, retries and limits |

Contributors should also read [AGENTS.md](../AGENTS.md).
