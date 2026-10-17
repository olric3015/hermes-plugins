# edit-ledger

A local ledger of the turns in which Hermes's agent changed files, and an `/edit-log` command
that shows which models change files, how many per turn and of which file types.

When a turn in which the agent changed files is about to finish, Hermes fires `pre_verify` with
the list of files that turn changed. edit-ledger subscribes to it and keeps one row per turn:
the model, the platform, how many files and their types (`.py`, `.md`, `.tsx`, ...). You can see
afterwards that one model touches four files a turn and another one, that most of this week's
changes were Markdown, or how many turns changed code in a session.

The plugin only watches. `pre_verify` is also how a plugin can keep the agent going (to run a
check first); edit-ledger always returns nothing, which lets the turn finish exactly as it would
without it, and does not change what any other `pre_verify` plugin decides.

## Install

```
hermes plugins install edit-ledger --enable
```

Requires Hermes 0.21.5 or newer. No API key, no configuration, no dependencies.

## Use

```
/edit-log             last 24 hours, by model
/edit-log session     the most recent session
/edit-log all         everything in the ledger
/edit-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
/edit-log types       add to any of the above to list file types (or platforms, days)
/edit-log recent      add to any of the above to list the newest 10 turns instead
/edit-log recent 25   the newest 25 instead (1 to 100)
/edit-log clear       delete the ledger
```

Example (`/edit-log all`, from the end-to-end test: two `hermes chat` turns, the first writing a
`.py` and a `.md` file, the second writing two `.py` files):

```
Turns that changed files, all recorded:

model       turns  files  median  top type    last
fake-model      2      4       2       .py  3s ago

Counts files changed with write_file and patch, not by terminal commands.
```

- **turns** counts turns that changed at least one file, and **files** the files they changed.
- **median** is the middle number of files per turn.
- **top type** is the file type changed most often, and **last** how long ago the newest turn
  was. The model with the most turns comes first.

`/edit-log types` lists file types instead. From the same run:

```
Turns that changed files, all recorded, by file type:

type  files  turns     last
.py       3      2   3s ago
.md       1      1  11s ago
```

`/edit-log recent` lists the newest ten turns of the chosen window, newest first
(`/edit-log recent 25` lists 25, up to 100), with the three most changed types of each:

```
Turns that changed files, all recorded, newest 2 of 2:

when          model  platform  files         types
3s ago   fake-model       cli      2         .py 2
11s ago  fake-model       cli      2  .md 1, .py 1
```

A file type is the file's extension in lower case; `(none)` is a file without one (`Makefile`,
`.env`) and `(other)` an extension that is not a short plain one.

`/edit-log days` puts one day on each line, newest day first; days are your computer's local
calendar days. A time window counts back from now (`/edit-log 6h`, `/edit-log 7d types`).
`session` and `all` take precedence over a time window. The ledger keeps its newest 5,000 rows,
so a long window on a busy install reaches back only as far as those rows do.

## What it sees and what it does not

- Hermes lists the files changed with its `write_file` and `patch` tools. A file changed by a
  terminal command (`sed -i`, a formatter, `git checkout`) is not on that list.
- A turn is counted when it is about to finish with an answer. A turn that is stopped or fails
  before that is not counted.
- If another plugin keeps the agent going (a `pre_verify` "continue"), Hermes fires the hook
  again in the same turn with the full list so far; edit-ledger updates that turn instead of
  counting a second one.

## What is recorded

One JSON line per hook call in `<HERMES_HOME>/plugin-data/edit-ledger/edits.jsonl` (per
profile):

`ts`, `session`, `platform`, `model`, `attempt`, `files`, `types`.

`types` maps each file type to how many files of it the turn changed. No path, file name or
anything the agent wrote is stored. `session` is the first 12 hex digits of a SHA-256 of
Hermes's session id, enough to tell sessions apart; `attempt` is Hermes's count of times the turn
was kept going.

The file is bounded: once it passes 2 MB it is rewritten to its newest 5,000 rows.

## Security and footprint

- `register()` only registers one hook (`pre_verify`) and one command (`/edit-log`).
- Observer only: the hook always returns nothing, so it never keeps the agent going or changes
  a turn. Each hook call appends one line to the ledger, and nothing else.
- No network access, no subprocesses, no downloads, no credentials read. It never opens the
  changed files.
- Writes only `edits.jsonl` under its own plugin data directory. `/edit-log clear` deletes it.
- The hook never raises: if the ledger cannot be written, the failure is logged at debug level.
- Standard library only.

## Development

```
python -m pytest tests                                            # unit tests, no Hermes needed
PYTHONPATH=<hermes checkout> python tests/e2e/edit_ledger_e2e.py   # real hermes chat turns
hermes plugins validate edit-ledger
```

## Changes

- **1.0.0**: first release.

MIT licensed.
