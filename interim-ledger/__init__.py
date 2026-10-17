"""interim-ledger: a local ledger of the mid-turn messages Hermes's models write.

``on_interim_message`` fires when a model writes a message in the middle of a turn, before its
final answer: the "Let me check the config first" text that comes with a tool call. On a chat
platform each one can arrive as a message of its own. ``/interim-log`` shows which models write
them, how many per turn and how long they are.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "interim-ledger"

_HELP = """\
/interim-log: which models write messages in the middle of a turn, and how many

  /interim-log             last 24 hours, by model
  /interim-log session     the most recent session
  /interim-log all         everything in the ledger
  /interim-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /interim-log providers   add to any of the above to group by provider (or surfaces, days)
  /interim-log recent      add to any of the above to list the newest 10 messages instead
  /interim-log recent 25   the newest 25 instead (1 to 100)
  /interim-log clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"models": "model", "providers": "provider", "surfaces": "surface", "days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_interim(**payload: Any) -> None:
    """Record one mid-turn message. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time()))
    except Exception as exc:
        logger.debug("interim-ledger: could not record a message: %s", exc)


def _handle_interim_log(raw_args: str = "") -> Optional[str]:
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
        return f"interim-ledger: deleted {ledger.clear(path)} recorded message(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        limit = ledger.parse_count(counts[0]) if counts else ledger.RECENT_ROWS
        return ledger.recent(rows, now, title=title, limit=limit)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "model")
    return ledger.summarize(rows, now, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("on_interim_message", _on_interim)
    ctx.register_command("interim-log", handler=_handle_interim_log,
                         args_hint="[session|all|7d] [providers|surfaces|days|recent [N]] | clear",
                         description="Show which models write messages in the middle of a turn, and how many.")
