# hermes-plugins

Plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Each directory is one
plugin with its own README.

| Plugin | What it does |
|---|---|
| [aux-ledger](aux-ledger/README.md) | Local ledger of Hermes's auxiliary LLM calls (titling, compression, vision, approval): tokens, time and failures per task or model, shown by `/aux`. |
| [stream-speed](stream-speed/README.md) | How fast each model starts answering and how fast it writes (time to first text, characters per second), from the streaming hooks, shown by `/speed`. |

MIT licensed.
