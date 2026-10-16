# stop-ledger

A local ledger of the turns you stopped while Hermes was still working, and a `/stop-log`
command that shows how often that happens, on which platform, in how many sessions and how
recently.

Stopping a turn halfway usually means something went wrong: the agent went off in the wrong
direction, took too long, or was about to do something you did not want. stop-ledger subscribes
to `agent_loop_stopped`, which Hermes fires when a running turn is interrupted, and keeps one row
per stop. You can see afterwards that you stopped Hermes six times on Telegram today, that most
of those were `/new` rather than `/stop`, or that the desktop app gets stopped far more than
your phone.

## Install

```
hermes plugins install stop-ledger --enable
```

Requires Hermes 0.21.5 or newer. No API key, no configuration, no dependencies.

## Use

```
/stop-log             last 24 hours, by platform
/stop-log session     the most recent session you stopped
/stop-log all         everything in the ledger
/stop-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
/stop-log days        add to any of the above to group by day, newest first
/stop-log recent      add to any of the above to list the newest 10 stops instead
/stop-log recent 25   the newest 25 instead (1 to 100)
/stop-log clear       delete the ledger
```

Example (`/stop-log all`, from the end-to-end test: on Telegram a running turn stopped with
`/stop` and another cleared with `/new`, and one turn stopped from the TUI):

```
Stopped turns, all recorded:

platform  stops  by /stop  by /new  sessions    last
telegram      2         1        1         1  3s ago
tui           1         1        0         1  3s ago
total         3         2        1         2  3s ago

Only turns that were still running count: a /stop or /new between turns is not a stop.
```

- **platform** is where the turn ran: a messaging platform (`telegram`, `discord`, ...) or
  `tui` for the TUI and the desktop app.
- **stops** counts stopped turns. **by /stop** is a `/stop` (or the Stop button in the TUI or
  the desktop app); **by /new** is a `/new` or `/reset` that cleared a turn still running.
- **sessions** is how many different sessions those stops were in, and **last** how long ago
  the newest one was. The platform with the most stops comes first.

`/stop-log recent` lists the newest ten stops of the chosen window, newest first
(`/stop-log recent 25` lists 25, up to 100). From the same run:

```
Stopped turns, all recorded, newest 3 of 3:

when    platform  cause             detail       session
3s ago       tui   stop  session_interrupt  504e634656fb
3s ago  telegram    new        new_command  5b8bb83e3f6b
3s ago  telegram   stop       stop_command  5b8bb83e3f6b
```

**detail** is Hermes's own label for the stop (`stop_command`, `new_command`,
`session_interrupt`, ...), and **session** a short hash that tells sessions apart without
naming them.

`/stop-log days` puts one day on each line, newest day first; days are your computer's local
calendar days. A time window counts back from now (`/stop-log 6h`, `/stop-log 7d days`). The
ledger keeps its newest 5,000 rows, so a long window on a busy install reaches back only as far
as those rows do.

## What it does not see

- The classic CLI: Hermes has no interrupt hook there (Ctrl+C in the CLI is not recorded).
- A `/stop` when no turn is running yet, and a `/new` between turns: nothing was interrupted.
- In the TUI and the desktop app, Hermes reports every interrupt of a running turn the same way,
  including one it makes itself (for example when a disconnected client's session is cleaned
  up), so those are counted as stops too.

## What is recorded

One JSON line per stopped turn in `<HERMES_HOME>/plugin-data/stop-ledger/stops.jsonl` (per
profile):

`ts`, `session`, `platform`, `cause`, `detail`.

`session` is the first 12 hex digits of a SHA-256 of the session key, not the key itself: a
gateway session key names the chat and the user. Nothing said in the chat reaches the hook.

The file is bounded: once it passes 2 MB it is rewritten to its newest 5,000 rows.

## Security and footprint

- `register()` only registers one hook (`agent_loop_stopped`) and one command (`/stop-log`).
- Observer only: it cannot delay or undo a stop. Each hook call appends one line to the ledger,
  and nothing else.
- No network access, no subprocesses, no downloads, no credentials read.
- Writes only `stops.jsonl` under its own plugin data directory. `/stop-log clear` deletes it.
- The hook never raises: if the ledger cannot be written, the failure is logged at debug level
  and the stop goes ahead.
- Standard library only.

## Development

```
python -m pytest tests                                            # unit tests, no Hermes needed
PYTHONPATH=<hermes checkout> python tests/e2e/stop_ledger_e2e.py   # Hermes's own interrupt code
hermes plugins validate stop-ledger
```

## Changes

- **1.0.0**: first release.

MIT licensed.
