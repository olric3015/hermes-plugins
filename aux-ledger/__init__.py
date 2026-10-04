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
  /aux models      add to any of the above to group by model instead of task
  /aux clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "session": "latest session", "all": "all recorded"}


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
    known = {"session", "all", "models", "clear", "day"}
    unknown = [w for w in words if w not in known]
    if unknown:
        return _HELP if unknown[0] in {"help", "-h", "--help"} else f"Unknown option: {unknown[0]}\n\n{_HELP}"
    path = _ledger_path()
    if "clear" in words:
        return f"aux-ledger: deleted {ledger.clear(path)} recorded call(s)."
    window = "session" if "session" in words else "all" if "all" in words else "day"
    rows = ledger.select(ledger.read(path), window, time.time())
    return ledger.summarize(rows, title=_TITLES[window], by="model" if "models" in words else "aux_task")


def register(ctx) -> None:
    ctx.register_hook("post_auxiliary_call", _on_post_auxiliary_call)
    ctx.register_command("aux", handler=_handle_aux, args_hint="[session|all] [models] | clear",
                         description="Show what Hermes's auxiliary LLM calls (titling, compression, ...) cost.")
