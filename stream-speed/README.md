# stream-speed

Records how fast each model starts answering and how fast it writes, and shows it with `/speed`.

Hermes fires three observer hooks around every streamed model response: `on_stream_start`,
`on_stream_delta` and `on_stream_end`. stream-speed pairs them into one row per response:
the time until the first visible text, the total time, and how many characters arrived. `/speed`
turns those rows into medians per model, so you can see which model or provider keeps you
waiting before the first word and which one writes slowly once it starts.

## Install

```
hermes plugins install stream-speed --enable
```

Requires Hermes 0.21.5 or newer. No API key, no configuration, no dependencies.

## Use

```
/speed          last 24 hours, by model
/speed all      everything recorded
/speed clear    delete the record
```

Example (`/speed all`, from the end-to-end test: two one-shot turns against a loopback provider
that sends 16 characters every 0.15 s):

```
Streaming speed, all recorded:

model       streams  failed  first text    p90  chars/s
fake-model        2       0       0.92s  1.00s      111
```

- **first text** is the median time from the start of the streaming request to the first
  visible text; **p90** is the slow end of the same number.
- **chars/s** is the median writing speed between the first text and the end of the stream.
  It counts characters, not tokens: the streaming hooks carry no token counts.
- **failed** counts streams that errored or did not finish.
- Streams with no visible text (a response that is only tool calls) are counted, shown with no
  first-text time, and mentioned in a footer line.

## How accurate it is

Hermes delivers stream hooks off the token path, on a queue with its own worker thread per
hook, so each timestamp is taken when the event is delivered, a few milliseconds after the wire.
**first text** starts when Hermes begins the streaming request, so it includes connection setup
as well as the provider's own delay; that is the wait you actually sit through. In the test
above the provider's 107 chars/s is measured as 110-111. Reasoning deltas are ignored: first
text means text you can read.

If the first-delta event is still queued when the end event arrives, the plugin waits up to
0.25 s for it; a stream whose order still cannot be established is recorded with an unknown
first-text time and is never reported as zero.

## What is recorded

One JSON line per streamed response in `<HERMES_HOME>/plugin-data/stream-speed/streams.jsonl`
(per profile):

`ts`, `session_id`, `iteration`, `provider`, `model`, `surface`, `ttft_s`, `duration_s`,
`chars`, `finished`, `failed`.

Not recorded: the streamed text, reasoning text, prompts, error messages (only a failed flag).
The file is bounded: once it passes 2 MB it is rewritten to its newest 5,000 rows.

## Security and footprint

- `register()` only registers three hooks and one command (`/speed`), plus an `atexit` handler
  that waits at most one second for a stream still open at exit (so a one-shot run records its
  last answer).
- No network access, no subprocesses, no downloads, no credentials read.
- Writes only `streams.jsonl` under its own plugin data directory. `/speed clear` deletes it.
- The streamed text passes through the hook payloads in memory; only its length is kept.
- Hooks are observer-only and never raise: an unwritable record is logged at debug level.
- In memory it tracks at most 256 open streams and drops any older than an hour.
- Standard library only.

## Development

```
python -m pytest tests
PYTHONPATH=<hermes checkout> python tests/e2e/stream_speed_e2e.py
hermes plugins validate stream-speed
```

MIT licensed.
