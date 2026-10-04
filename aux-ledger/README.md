# aux-ledger

A local ledger of the LLM calls Hermes makes behind a turn, and a `/aux` command that shows what
they cost.

Hermes calls a model for more than your turns: session titles, context compression, vision,
approval checks and other auxiliary tasks each make their own requests, with retries and
fallbacks. Those requests do not pass through the main-loop `pre_api_request` /
`post_api_request` hooks, so usage plugins built on them do not count this traffic. aux-ledger
subscribes to `post_auxiliary_call` and records one row per provider attempt.

## Install

```
hermes plugins install aux-ledger --enable
```

Requires Hermes 0.21.5 or newer (the first release with the auxiliary-call hooks). No API key,
no configuration, no dependencies.

## Use

```
/aux             last 24 hours, by task
/aux session     the most recent session that made an auxiliary call
/aux all         everything in the ledger
/aux models      add to any of the above to group by model instead of task
/aux clear       delete the ledger
```

Example (`/aux all`, from the end-to-end test: one titling call, one compression call that
succeeded and three attempts of one that failed):

```
Auxiliary LLM calls, all recorded:

task              calls  failed  input  output  time
compression           4       3  1,000      50  0.0s
title_generation      1       0    321      12  1.1s
total                 5       3  1,321      62  1.1s

3 call(s) reported no token usage (streamed or failed).
```

- **calls** counts provider attempts, so a retried call shows up once per attempt.
- **input** is everything the provider read: fresh input plus cache reads and cache writes.
- **time** is the wall time of the attempts.
- Streamed and failed attempts carry no usage from Hermes; they are counted, with zero tokens,
  and the footer says how many there were.

## What is recorded

One JSON line per attempt in `<HERMES_HOME>/plugin-data/aux-ledger/calls.jsonl` (per profile):

`ts`, `session_id`, `aux_task`, `provider`, `model`, `response_model`, `retry_count`,
`streaming`, `duration_s`, `error_type`, `has_usage`, `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`.

Not recorded: prompts, system prompts, responses, error messages (only the exception class
name), base URLs, API keys.

The file is bounded: once it passes 2 MB it is rewritten to its newest 5,000 rows.

## Security and footprint

- `register()` only registers one hook (`post_auxiliary_call`) and one command (`/aux`).
- No network access, no subprocesses, no downloads, no credentials read.
- Writes only `calls.jsonl` under its own plugin data directory. `/aux clear` deletes it.
- Reads no Hermes state other than the hook payload it is handed.
- The hook is observer-only and never raises: if the ledger cannot be written, the auxiliary
  call proceeds and the failure is logged at debug level.
- Standard library only.

## Development

```
python -m pytest tests                                   # unit tests, no Hermes needed
PYTHONPATH=<hermes checkout> python tests/e2e/hermes_e2e.py   # real loader + real auxiliary client
hermes plugins validate aux-ledger
```

MIT licensed.
