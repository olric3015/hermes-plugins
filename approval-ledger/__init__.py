"""approval-ledger: a local ledger of the approval prompts Hermes raised, and how they ended.

``pre_approval_request`` fires when Hermes is about to ask for approval of a dangerous command
(or a protected write, an MCP consent, a smart-mode verdict), and ``post_approval_response``
when the answer, a timeout or a withdrawal settles it. Both run on the thread that waits for the
answer, so the gap between them is how long the answer took. ``/approval-log`` shows which rules
ask most, how they end and how long you take to answer.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "approval-ledger"

_HELP = """\
/approval-log: which approval prompts Hermes raised, and how they ended

  /approval-log             last 24 hours, by the rule that asked
  /approval-log session     the most recent session that asked
  /approval-log all         everything in the ledger
  /approval-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /approval-log surfaces    add to any of the above to group by where it asked
  /approval-log days        add to any of the above to group by day, newest first
  /approval-log recent      add to any of the above to list the newest 10 decisions instead
  /approval-log clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"patterns": "pattern", "surfaces": "surface", "days": "day"}

# The open prompt of this thread: (surface, pattern_key, session_key) and when it was raised.
# Held in memory only, so the session key never reaches the ledger.
_open = threading.local()


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _prompt_key(payload: dict) -> tuple:
    return (str(payload.get("surface") or ""), str(payload.get("pattern_key") or ""),
            str(payload.get("session_key") or ""))


def _on_pre_approval_request(**payload: Any) -> None:
    """Note when the prompt was raised. A later request on this thread replaces it: a smart
    verdict that escalates, or a coalesced wait that turns into a fresh prompt, has no answer."""
    try:
        _open.prompt = (_prompt_key(payload), time.monotonic())
    except Exception as exc:
        logger.debug("approval-ledger: could not note approval request: %s", exc)


def _on_post_approval_response(**payload: Any) -> None:
    """Record one decision. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        opened, _open.prompt = getattr(_open, "prompt", None), None
        wait = time.monotonic() - opened[1] if opened and opened[0] == _prompt_key(payload) else None
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time(), wait=wait))
    except Exception as exc:
        logger.debug("approval-ledger: could not record approval decision: %s", exc)


def _handle_approval_log(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"session", "all", "day", "clear", "recent", *_GROUP_WORDS}
    spans = [w for w in words if ledger.parse_span(w)]
    unknown = [w for w in words if w not in known and w not in spans]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"approval-ledger: deleted {ledger.clear(path)} recorded decision(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        return ledger.recent(rows, now, title=title)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "pattern")
    return ledger.summarize(rows, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("pre_approval_request", _on_pre_approval_request)
    ctx.register_hook("post_approval_response", _on_post_approval_response)
    ctx.register_command("approval-log", handler=_handle_approval_log,
                         args_hint="[session|all|7d] [surfaces|days|recent] | clear",
                         description="Show which approval prompts Hermes raised, how they ended and how long you took.")
