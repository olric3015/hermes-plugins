"""command-ledger: a local ledger of the slash commands you run in Hermes.

``pre_command`` fires just before a recognized slash command's handler runs, in the CLI and on
the messaging gateway. Each one becomes a row: which command, where, and a short hash of the
session. ``/command-log`` shows which commands you use most, in how many sessions, and when last.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "command-ledger"
COMMAND = "command-log"

_HELP = """\
/command-log: which slash commands you run, and how often

  /command-log             last 24 hours, by command
  /command-log session     the most recent session that ran one
  /command-log all         everything in the ledger
  /command-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /command-log platforms   add to any of the above to group by platform (cli, telegram, ...)
  /command-log days        add to any of the above to group by day, newest first
  /command-log recent      add to any of the above to list the newest 10 commands instead
  /command-log clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"commands": "command", "platforms": "platform", "surfaces": "surface", "days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_pre_command(**payload: Any) -> None:
    """Record one command. Observer hook: never raises, an unwritable ledger only logs.
    Reading the ledger is not using Hermes, so /command-log itself is not counted."""
    try:
        if str(payload.get("command") or "").lstrip("/") == COMMAND:
            return
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time()))
    except Exception as exc:
        logger.debug("command-ledger: could not record command: %s", exc)


def _handle_command_log(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"session", "all", "day", "clear", "recent", *_GROUP_WORDS}
    spans = [w for w in words if ledger.parse_span(w)]
    unknown = [w for w in words if w not in known and w not in spans]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"command-ledger: deleted {ledger.clear(path)} recorded command(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        return ledger.recent(rows, now, title=title)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "command")
    return ledger.summarize(rows, title=title, now=now, by=by)


def register(ctx) -> None:
    ctx.register_hook("pre_command", _on_pre_command)
    ctx.register_command(COMMAND, handler=_handle_command_log,
                         args_hint="[session|all|7d] [platforms|days|recent] | clear",
                         description="Show which slash commands you run, how often and when last.")
