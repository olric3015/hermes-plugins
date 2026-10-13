"""wait-ledger: a local ledger of the times Hermes stopped and waited for you.

``on_human_input_request`` fires right before Hermes blocks on a person (an approval prompt, a
clarify question, a sudo password), and ``on_human_input_resolved`` fires once when that wait
ends, with the same ``request_id`` and how it ended. The gap between them is how long Hermes
waited. ``/waits`` shows which prompts hold it up, how they end and how long it waits.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "wait-ledger"
# Open prompts kept for pairing; a request whose resolution never arrives cannot grow this forever.
MAX_OPEN = 256

_HELP = """\
/waits: how often and how long Hermes stopped to wait for you

  /waits             last 24 hours, by kind of prompt (approval, clarify, sudo)
  /waits session     the most recent session that waited
  /waits all         everything in the ledger
  /waits 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /waits platforms   add to any of the above to group by where it waited
  /waits days        add to any of the above to group by day, newest first
  /waits recent      add to any of the above to list the newest 10 waits instead
  /waits recent 25   the newest 25 instead (1 to 100)
  /waits clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"kinds": "kind", "platforms": "platform", "days": "day"}

# request_id -> when the prompt was raised. Held in memory only; nothing about the prompt is kept.
_open: Dict[str, float] = {}
_open_lock = threading.Lock()


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_request(**payload: Any) -> None:
    """Note when the prompt was raised."""
    try:
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            return
        with _open_lock:
            if len(_open) >= MAX_OPEN:
                _open.pop(next(iter(_open)))
            _open[request_id] = time.monotonic()
    except Exception as exc:
        logger.debug("wait-ledger: could not note prompt: %s", exc)


def _on_resolved(**payload: Any) -> None:
    """Record one wait. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        with _open_lock:
            opened = _open.pop(str(payload.get("request_id") or ""), None)
        wait = None if opened is None else time.monotonic() - opened
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time(), wait=wait))
    except Exception as exc:
        logger.debug("wait-ledger: could not record wait: %s", exc)


def _handle_waits(raw_args: str = "") -> Optional[str]:
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
        return f"wait-ledger: deleted {ledger.clear(path)} recorded wait(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        limit = ledger.parse_count(counts[0]) if counts else ledger.RECENT_ROWS
        return ledger.recent(rows, now, title=title, limit=limit)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "kind")
    return ledger.summarize(rows, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("on_human_input_request", _on_request)
    ctx.register_hook("on_human_input_resolved", _on_resolved)
    ctx.register_command("waits", handler=_handle_waits,
                         args_hint="[session|all|7d] [platforms|days|recent [N]] | clear",
                         description="Show how often and how long Hermes stopped to wait for you.")
