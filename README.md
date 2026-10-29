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
| [wait-ledger](wait-ledger/README.md) | Local ledger of the times Hermes stopped to wait for you (approval prompts, clarify questions, password prompts): how often, how they ended and how long it waited, shown by `/waits`. |
| [stop-ledger](stop-ledger/README.md) | Local ledger of the turns you stopped while Hermes was still working (/stop, or /new during a running turn): how often, where and how recently, shown by `/stop-log`. |
| [voice-ledger](voice-ledger/README.md) | Local ledger of the voice messages and recordings Hermes transcribes: how many, from where, by which speech-to-text provider and model, and how much audio, shown by `/voice-log`. |
| [interim-ledger](interim-ledger/README.md) | Local ledger of the messages models write in the middle of a turn, before their final answer (the text that comes with a tool call): which models write them, how many per turn and how long, shown by `/interim-log`. |
| [edit-ledger](edit-ledger/README.md) | Local ledger of the turns in which the agent changed files (write_file, patch): which models change files, how many per turn and of which file types, shown by `/edit-log`. Never stores a path or file name. |
| [reaction-ledger](reaction-ledger/README.md) | Local ledger of the emoji reactions people leave on messages in your Telegram chats with Hermes: which emoji, on how many messages, in how many chats and how recently, shown by `/reaction-log`. |
| [unicode-ref](unicode-ref/README.md) | `@unicode:` references that spell text out character by character for the model: code points, Unicode names, and notes on invisible characters, text-direction controls, unusual spaces and lookalike letters. |
| [timestamp-ref](timestamp-ref/README.md) | `@ts:` references that turn Unix timestamps and written dates into readable times for the model: UTC, local time, ISO 8601, weekday, how long ago, and the same moment in seconds, milliseconds and microseconds. |
| [jwt-ref](jwt-ref/README.md) | `@jwt:` references that decode a JSON Web Token for the model: header, claims, expiry and issue times in UTC with how long ago, and warnings such as `alg: none`. Signatures are never verified. |
| [crontab-ref](crontab-ref/README.md) | `@crontab:` references that explain a cron expression for the model: what it means in words, field by field, whether Hermes's `/cron` accepts it, and its next runs in Hermes's timezone, read by croniter like Hermes's own scheduler. |
| [b64-ref](b64-ref/README.md) | `@b64:` references that decode base64, base64url, hex and percent-encoded text for the model: text as text, JSON pretty-printed, gzip inflated, and binary named by its format with a hex dump of its first bytes. |
| [uuid-ref](uuid-ref/README.md) | `@uuid:` references that decode a UUID or ULID for the model: version and variant in words, the creation time a v1, v6, v7 or ULID carries, and whether a v1 node is a real network card address. |
| [chmod-ref](chmod-ref/README.md) | `@chmod:` references that explain a file permission mode for the model: octal and ls -l forms, each class's rights in words, setuid, setgid and sticky, what a chmod change like u+x,go-w does to a mode, and what a umask gives new files. |

MIT licensed.
