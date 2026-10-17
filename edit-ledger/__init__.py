"""edit-ledger: a local ledger of the turns in which Hermes's agent changed files.

``pre_verify`` fires when a turn in which the agent changed files (with ``write_file`` or
``patch``) is about to finish. The plugin only watches: it always returns None, which lets the
turn finish exactly as it would without it. ``/edit-log`` shows which models change files, how
many per turn and of which types.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "edit-ledger"

_HELP = """\
/edit-log: which models change files, how many per turn and of which types

  /edit-log             last 24 hours, by model
  /edit-log session     the most recent session
  /edit-log all         everything in the ledger
  /edit-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /edit-log types       add to any of the above to list file types (or platforms, days)
  /edit-log recent      add to any of the above to list the newest 10 turns instead
  /edit-log recent 25   the newest 25 instead (1 to 100)
  /edit-log clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"models": "model", "platforms": "platform", "types": "type", "days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_pre_verify(**payload: Any) -> None:
    """Record one turn that changed files. Observer only: returns None, so the turn finishes as it
    would without the plugin; never raises, an unwritable ledger only logs."""
    try:
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time()))
    except Exception as exc:
        logger.debug("edit-ledger: could not record a turn: %s", exc)
    return None


def _handle_edit_log(raw_args: str = "") -> Optional[str]:
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
        return f"edit-ledger: deleted {ledger.clear(path)} recorded row(s)."
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
    ctx.register_hook("pre_verify", _on_pre_verify)
    ctx.register_command("edit-log", handler=_handle_edit_log,
                         args_hint="[session|all|7d] [types|platforms|days|recent [N]] | clear",
                         description="Show which models change files, how many per turn and of which types.")
