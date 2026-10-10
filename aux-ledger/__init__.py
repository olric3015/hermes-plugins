"""aux-ledger: a local ledger of the LLM calls Hermes makes behind a turn.

``post_auxiliary_call`` records one row per provider attempt of an auxiliary call (titling,
compression, vision, approval, ...) and ``/aux`` prints what those calls cost. The main-loop
``*_api_request`` hooks never see this traffic, so usage plugins built on them leave it out.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "aux-ledger"

_HELP = """\
/aux: what Hermes's auxiliary LLM calls cost

  /aux             last 24 hours, by task
  /aux session     the most recent session that made an auxiliary call
  /aux all         everything in the ledger
  /aux 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /aux models      add to any of the above to group by model instead of task
  /aux providers   add to any of the above to group by provider instead of task
  /aux days        add to any of the above to group by day, newest first
  /aux clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}
_GROUP_WORDS = {"models": "model", "providers": "provider", "days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_post_auxiliary_call(**payload: Any) -> None:
    """Record one attempt. Observer hook: never raises, an unwritable ledger only logs."""
    try:
        ledger.append(_ledger_path(), ledger.build_row(payload))
    except Exception as exc:
        logger.debug("aux-ledger: could not record auxiliary call: %s", exc)


def _handle_aux(raw_args: str = "") -> Optional[str]:
    words = (raw_args or "").lower().split()
    known = {"session", "all", "clear", "day", *_GROUP_WORDS}
    spans = [w for w in words if ledger.parse_span(w)]
    unknown = [w for w in words if w not in known and w not in spans]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"aux-ledger: deleted {ledger.clear(path)} recorded call(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "aux_task")
    rows = ledger.select(ledger.read(path), window, time.time(), span)
    return ledger.summarize(rows, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("post_auxiliary_call", _on_post_auxiliary_call)
    ctx.register_command("aux", handler=_handle_aux, args_hint="[session|all|7d] [models|providers|days] | clear",
                         description="Show what Hermes's auxiliary LLM calls (titling, compression, ...) cost.")
