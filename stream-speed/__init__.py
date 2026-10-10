"""stream-speed: how fast each model starts answering and how fast it writes.

``on_stream_start`` / ``on_stream_delta`` / ``on_stream_end`` are paired into one row per
streamed response (time to first text, total time, characters) and ``/speed`` prints medians per
model. Timings are taken when Hermes delivers each hook, which is off the token path, so they
run a few milliseconds behind the wire.
"""

from __future__ import annotations

import atexit
import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import timing

logger = logging.getLogger(__name__)

PLUGIN_NAME = "stream-speed"
_EXIT_DRAIN_SECONDS = 1.0

_HELP = """\
/speed: how fast each model starts answering and how fast it writes

  /speed             last 24 hours, by model
  /speed session     the most recent session that streamed a response
  /speed all         everything recorded
  /speed 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /speed providers   add to any of the above to group by provider instead of model
  /speed days        add to any of the above to group by day, newest first
  /speed clear      delete the record
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"models": "model", "providers": "provider", "days": "day"}

_tracker = timing.Tracker()


def _data_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / timing.FILENAME


def _on_stream_start(**payload: Any) -> None:
    _tracker.start(payload)


def _on_stream_delta(**payload: Any) -> None:
    _tracker.delta(payload)


def _on_stream_end(**payload: Any) -> None:
    """Close the stream and record it. Observer hook: an unwritable record only logs."""
    try:
        row = _tracker.end(payload, time.time())
        if row is not None:
            timing.append(_data_path(), row)
    except Exception as exc:
        logger.debug("stream-speed: could not record stream: %s", exc)


def _drain_at_exit() -> None:
    # Stream hooks arrive on daemon worker threads; give a stream that is still open at exit
    # (a one-shot run ending right after its answer) a bounded moment to be recorded.
    deadline = time.monotonic() + _EXIT_DRAIN_SECONDS
    while _tracker.open_count() and time.monotonic() < deadline:
        time.sleep(0.02)


def _handle_speed(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"session", "all", "clear", "day", *_GROUP_WORDS}
    spans = [w for w in words if timing.parse_span(w)]
    unknown = [w for w in words if w not in known and w not in spans]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _data_path()
    if "clear" in words:
        return f"stream-speed: deleted {timing.clear(path)} recorded stream(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], timing.DAY_SECONDS
    if window == "day" and spans:
        title, span = timing.span_title(spans[0]), timing.parse_span(spans[0])
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "model")
    rows = timing.select(timing.read(path), window, time.time(), span)
    return timing.summarize(rows, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("on_stream_start", _on_stream_start)
    ctx.register_hook("on_stream_delta", _on_stream_delta)
    ctx.register_hook("on_stream_end", _on_stream_end)
    ctx.register_command("speed", handler=_handle_speed, args_hint="[session|all|7d] [providers|days] | clear",
                         description="Show how fast each model starts answering and how fast it writes.")
    atexit.register(_drain_at_exit)
