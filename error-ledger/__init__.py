"""error-ledger: a local ledger of the provider calls that failed.

``api_request_error`` fires for each failed provider attempt that reaches Hermes's turn loop,
with Hermes's own classification of the failure. Hermes retries, compresses or falls back on
many of them, so they scroll past or never show; ``/errors`` shows which model or provider they
came from and why. A stream Hermes reconnects inside its streaming layer is not reported.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "error-ledger"

_HELP = """\
/errors: which provider calls failed, and why

  /errors             last 24 hours, by model
  /errors session     the most recent session that had a failed call
  /errors all         everything in the ledger
  /errors 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /errors providers   add to any of the above to group by provider
  /errors reasons     add to any of the above to group by cause
  /errors recent      add to any of the above to list the newest 10 failures instead
  /errors clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"models": "model", "providers": "provider", "reasons": "reason"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_api_request_error(**payload: Any) -> None:
    """Record one failed attempt. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        ledger.append(_ledger_path(), ledger.build_row(payload))
    except Exception as exc:
        logger.debug("error-ledger: could not record provider error: %s", exc)


def _handle_errors(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"session", "all", "day", "clear", "recent", *_GROUP_WORDS}
    spans = [w for w in words if ledger.parse_span(w)]
    unknown = [w for w in words if w not in known and w not in spans]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"error-ledger: deleted {ledger.clear(path)} recorded error(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        return ledger.recent(rows, now, title=title)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "model")
    return ledger.summarize(rows, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("api_request_error", _on_api_request_error)
    ctx.register_command("errors", handler=_handle_errors,
                         args_hint="[session|all|7d] [providers|reasons|recent] | clear",
                         description="Show which provider calls failed (rate limits, overloads, ...) and why.")
