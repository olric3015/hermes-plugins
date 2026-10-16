"""stop-ledger: a local ledger of the turns you stopped while Hermes was still working.

``agent_loop_stopped`` fires when a running agent turn is interrupted: ``/stop`` (or Stop in the
desktop app or TUI) while the agent is working, or ``/new`` / ``/reset`` clearing a turn that is
still running. ``/stop-log`` shows how often that happens, where, and how recently.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "stop-ledger"

_HELP = """\
/stop-log: how often you stopped Hermes while it was working

  /stop-log             last 24 hours, by platform
  /stop-log session     the most recent session you stopped
  /stop-log all         everything in the ledger
  /stop-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /stop-log days        add to any of the above to group by day, newest first
  /stop-log recent      add to any of the above to list the newest 10 stops instead
  /stop-log recent 25   the newest 25 instead (1 to 100)
  /stop-log clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"platforms": "platform", "days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_stopped(**payload: Any) -> None:
    """Record one stopped turn. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time()))
    except Exception as exc:
        logger.debug("stop-ledger: could not record stop: %s", exc)


def _handle_stop_log(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"session", "all", "day", "clear", "recent", *_GROUP_WORDS}
    spans = [w for w in words if ledger.parse_span(w)]
    # A row count only means something after "recent"; anywhere else it is an unknown option.
    counts = [w for w in words if "recent" in words and ledger.parse_count(w)]
    unknown = [w for w in words if w not in known and w not in spans and w not in counts]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"stop-ledger: deleted {ledger.clear(path)} recorded stop(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        limit = ledger.parse_count(counts[0]) if counts else ledger.RECENT_ROWS
        return ledger.recent(rows, now, title=title, limit=limit)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "platform")
    return ledger.summarize(rows, now, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("agent_loop_stopped", _on_stopped)
    ctx.register_command("stop-log", handler=_handle_stop_log,
                         args_hint="[session|all|7d] [days|recent [N]] | clear",
                         description="Show how often you stopped Hermes while it was working.")
