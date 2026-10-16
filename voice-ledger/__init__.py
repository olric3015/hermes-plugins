"""voice-ledger: a local ledger of the recordings Hermes transcribes.

``pre_transcription`` fires once per recording, after Hermes has chosen the speech-to-text
provider and before that provider runs: a voice message arriving through the gateway, or a
recording from the CLI's voice mode. ``/voice-log`` shows how many there were, from where, which
provider and model handled them and how much audio that was.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import ledger

logger = logging.getLogger(__name__)

PLUGIN_NAME = "voice-ledger"

_HELP = """\
/voice-log: the voice messages and recordings Hermes transcribed

  /voice-log             last 24 hours, by speech-to-text provider
  /voice-log all         everything in the ledger
  /voice-log 7d          the last 7 days (any number of hours or days: 6h, 30d)
  /voice-log models      add to any of the above to group by model
  /voice-log sources     add to any of the above to group by where the audio came from
  /voice-log languages   add to any of the above to group by configured language
  /voice-log days        add to any of the above to group by day, newest first
  /voice-log recent      add to any of the above to list the newest 10 recordings instead
  /voice-log recent 25   the newest 25 instead (1 to 100)
  /voice-log clear       delete the ledger
"""

_TITLES = {"day": "last 24 hours", "all": "all recorded"}
_GROUP_WORDS = {"providers": "provider", "models": "model", "sources": "source",
                "languages": "language", "days": "day"}


def _ledger_path() -> Path:
    # Resolved per call: plugin_data_dir follows the active profile.
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_NAME) / ledger.FILENAME


def _on_transcription(**payload: Any) -> None:
    """Record one recording. Observer hook: returns None so the request is unchanged, and never
    raises; an unwritable ledger only logs."""
    try:
        size = ledger.audio_size(payload.get("file_path"))
        ledger.append(_ledger_path(), ledger.build_row(payload, now=time.time(), size=size))
    except Exception as exc:
        logger.debug("voice-ledger: could not record recording: %s", exc)
    return None


def _handle_voice_log(raw_args: str = "") -> Optional[str]:
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
        return f"voice-ledger: deleted {ledger.clear(path)} recorded recording(s)."
    window = "all" if "all" in words else "day"
    title, span = _TITLES[window], ledger.DAY_SECONDS
    if window == "day" and spans:
        title, span = ledger.span_title(spans[0]), ledger.parse_span(spans[0])
    now = time.time()
    rows = ledger.select(ledger.read(path), window, now, span)
    if "recent" in words:
        limit = ledger.parse_count(counts[0]) if counts else ledger.RECENT_ROWS
        return ledger.recent(rows, now, title=title, limit=limit)
    by = next((_GROUP_WORDS[w] for w in words if w in _GROUP_WORDS), "provider")
    return ledger.summarize(rows, now, title=title, by=by)


def register(ctx) -> None:
    ctx.register_hook("pre_transcription", _on_transcription)
    ctx.register_command("voice-log", handler=_handle_voice_log,
                         args_hint="[all|7d] [models|sources|languages|days|recent [N]] | clear",
                         description="Show the voice messages and recordings Hermes transcribed.")
