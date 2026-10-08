# hermes-plugins

Plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Each directory is one
plugin with its own README.

| Plugin | What it does |
|---|---|
| [aux-ledger](aux-ledger/README.md) | Local ledger of Hermes's auxiliary LLM calls (titling, compression, vision, approval): tokens, time and failures per task or model, shown by `/aux`. |
| [stream-speed](stream-speed/README.md) | How fast each model starts answering and how fast it writes (time to first text, characters per second), from the streaming hooks, shown by `/speed`. |
| [error-ledger](error-ledger/README.md) | Local ledger of failed provider calls (rate limits, overloads, timeouts): which model or provider they came from and why, shown by `/errors`. |
| [approval-ledger](approval-ledger/README.md) | Local ledger of Hermes's approval prompts: which rules ask most, how the prompts end and how long you take to answer, shown by `/approval-log`. |
| [command-ledger](command-ledger/README.md) | Local ledger of the slash commands you run in Hermes: which ones, how often, in how many sessions and when last, shown by `/command-log`. |

MIT licensed.
