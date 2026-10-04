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

  /speed          last 24 hours, by model
  /speed all      everything recorded
  /speed clear    delete the record
"""

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
    unknown = [w for w in words if w not in {"all", "clear", "day"}]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _data_path()
    if "clear" in words:
        return f"stream-speed: deleted {timing.clear(path)} recorded stream(s)."
    window = "all" if "all" in words else "day"
    rows = timing.select(timing.read(path), window, time.time())
    return timing.summarize(rows, title="all recorded" if window == "all" else "last 24 hours")


def register(ctx) -> None:
    ctx.register_hook("on_stream_start", _on_stream_start)
    ctx.register_hook("on_stream_delta", _on_stream_delta)
    ctx.register_hook("on_stream_end", _on_stream_end)
    ctx.register_command("speed", handler=_handle_speed, args_hint="[all] | clear",
                         description="Show how fast each model starts answering and how fast it writes.")
    atexit.register(_drain_at_exit)
