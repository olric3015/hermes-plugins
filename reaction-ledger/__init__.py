"""reaction-ledger: a local ledger of the emoji reactions left in your Telegram chats with Hermes.

``gateway_platform_event`` fires with ``event_type="reaction"`` when an authorized Telegram user
sets, changes or removes the reactions on a message. ``/reaction-log`` shows which emoji were
used, on how many messages, and how recently, counting each message once with the reactions it
has now.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "reaction-ledger"

_HELP = """\
/reaction-log: the emoji reactions left in your Telegram chats with Hermes

  /reaction-log             last 24 hours, by emoji
  /reaction-log all         everything in the ledger
  /reaction-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /reaction-log days        add to any of the above to group by day, newest first
  /reaction-log recent      add to any of the above to list the newest 10 reaction changes instead
  /reaction-log recent 25   the newest 25 instead (1 to 100)
  /reaction-log clear       delete the ledger
"""

_GROUP_WORDS = {"days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_platform_event(platform: Any = None, event_type: Any = None, payload: Any = None, **_: Any) -> None:
    """Record one reaction change. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        row = ledger.build_row(platform, event_type, payload, now=time.time())
        if row is not None:
            ledger.append(_ledger_path(), row)
    except Exception as exc:
        logger.debug("reaction-ledger: could not record reaction: %s", exc)


def _handle_reaction_log(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"all", "day", "clear", "recent", *_GROUP_WORDS}
    spans = [w for w in words if ledger.parse_span(w)]
    # A row count only means something after "recent"; anywhere else it is an unknown option.
    counts = [w for w in words if "recent" in words and ledger.parse_count(w)]
    unknown = [w for w in words if w not in known and w not in spans and w not in counts]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"reaction-ledger: deleted {ledger.clear(path)} recorded reaction change(s)."
    title, span = "last 24 hours", ledger.DAY_SECONDS
    if "all" in words:
        title, span = "all recorded", None
    elif spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), now, span)
    if "recent" in words:
        limit = ledger.parse_count(counts[0]) if counts else ledger.RECENT_ROWS
        return ledger.recent(rows, now, title=title, limit=limit)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "emoji")
    return ledger.summarize(rows, now, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("gateway_platform_event", _on_platform_event)
    ctx.register_command("reaction-log", handler=_handle_reaction_log,
                         args_hint="[all|7d] [days|recent [N]] | clear",
                         description="Show the emoji reactions left in your Telegram chats with Hermes.")
