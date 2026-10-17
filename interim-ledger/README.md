# interim-ledger

A local ledger of the messages models write in the middle of a turn, before their final answer,
and an `/interim-log` command that shows which models write them, how many per turn and how
long they are.

Many models say something before they call a tool: "Let me check the config first", "Now I'll
run the tests". Hermes calls these mid-turn (interim) messages. In the terminal they scroll past
with the tool output, but on Telegram, Discord or Slack each one can arrive as a message of its
own, so a chatty model fills the chat with them. interim-ledger subscribes to
`on_interim_message`, which Hermes fires for each of them, and keeps one row per message. You can
see afterwards that one model writes three of them per turn and another almost none, and decide
whether to switch models or turn them off with `display.interim_assistant_messages`.

## Install

```
hermes plugins install interim-ledger --enable
```

Requires Hermes 0.21.5 or newer. No API key, no configuration, no dependencies.

## Use

```
/interim-log             last 24 hours, by model
/interim-log session     the most recent session
/interim-log all         everything in the ledger
/interim-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
/interim-log providers   add to any of the above to group by provider (or surfaces, days)
/interim-log recent      add to any of the above to list the newest 10 messages instead
/interim-log recent 25   the newest 25 instead (1 to 100)
/interim-log clear       delete the ledger
```

Example (`/interim-log all`, from the end-to-end test: two `hermes chat` turns, the first with one
message before its tool call, the second with two):

```
Mid-turn messages, all recorded:

model       messages  turns  per turn  median chars    last
fake-model         3      2       1.5            57  3s ago

Counted even where display.interim_assistant_messages hides them.
```

- **messages** counts mid-turn messages, and **turns** the turns that had at least one.
- **per turn** is messages divided by turns: how many a model writes when it writes any.
- **median chars** is the middle length of those messages, in characters.
- **last** is how long ago the newest one was. The model with the most messages comes first.

Hermes fires the hook whether or not your chat shows these messages, so a model's habit is
counted even where `display.interim_assistant_messages` is off.

`/interim-log recent` lists the newest ten messages of the chosen window, newest first
(`/interim-log recent 25` lists 25, up to 100). From the same run:

```
Mid-turn messages, all recorded, newest 3 of 3:

when         model  surface  step  chars  streamed
3s ago  fake-model      cli     2     52        no
3s ago  fake-model      cli     1     57        no
7s ago  fake-model      cli     1     57        no
```

**surface** is where the turn ran (`cli`, `telegram`, `discord`, ...), **step** which model call
of the turn wrote it, and **streamed** whether Hermes reports the text as already streamed live.

`/interim-log days` puts one day on each line, newest day first; days are your computer's local
calendar days. A time window counts back from now (`/interim-log 6h`, `/interim-log 7d days`).
`session` and `all` take precedence over a time window. The ledger keeps its newest 5,000 rows,
so a long window on a busy install reaches back only as far as those rows do.

## What it does not see

- A turn whose model calls tools without writing anything first: there is no message to count.
- The final answer of a turn: only messages before it are mid-turn messages.

## What is recorded

One JSON line per mid-turn message in
`<HERMES_HOME>/plugin-data/interim-ledger/interim.jsonl` (per profile):

`ts`, `session`, `turn`, `surface`, `model`, `provider`, `iteration`, `chars`, `streamed`.

The text of the message is never written, only its length. `session` and `turn` are the first 12
hex digits of a SHA-256 of Hermes's session and turn ids, enough to tell them apart.

The file is bounded: once it passes 2 MB it is rewritten to its newest 5,000 rows.

## Security and footprint

- `register()` only registers one hook (`on_interim_message`) and one command (`/interim-log`).
- Observer only: Hermes runs the hook on its own background thread, so it cannot delay, change
  or hide a message. Each hook call appends one line to the ledger, and nothing else.
- No network access, no subprocesses, no downloads, no credentials read.
- Writes only `interim.jsonl` under its own plugin data directory. `/interim-log clear` deletes
  it.
- The hook never raises: if the ledger cannot be written, the failure is logged at debug level.
- Standard library only.

## Development

```
python -m pytest tests                                               # unit tests, no Hermes needed
PYTHONPATH=<hermes checkout> python tests/e2e/interim_ledger_e2e.py   # real hermes chat turns
hermes plugins validate interim-ledger
```

## Changes

- **1.0.0**: first release.

MIT licensed.
