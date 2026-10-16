"""Append-only JSONL ledger of the recordings Hermes transcribes, and the summaries ``/voice-log``
prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds where a recording came from, which speech-to-text provider
and model got it and how large it was, never the audio, its file name or the transcript.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FILENAME = "voice.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
MAX_RECENT_ROWS = 100
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

_lock = threading.Lock()


def _text(value: Any, limit: int = 60) -> str:
    return str(value or "").strip()[:limit]


def audio_size(file_path: Any) -> Optional[int]:
    """The recording's size in bytes from the file system (the file is never opened), or None."""
    try:
        return int(os.stat(str(file_path)).st_size) if file_path else None
    except (OSError, ValueError):
        return None


def build_row(payload: Dict[str, Any], *, now: float, size: Optional[int]) -> Dict[str, Any]:
    """One ledger row from a ``pre_transcription`` payload. The file path is left out (it can
    name the chat or the user) and so is ``prompt`` (vocabulary hints can be private)."""
    return {
        "ts": float(now),
        # gateway (a voice message), voice_mode (the CLI microphone), ...; "unknown" when the
        # caller does not say.
        "source": _text(payload.get("source"), 40) or "unknown",
        "provider": _text(payload.get("provider")) or "unknown",
        # None means the provider's own default model.
        "model": _text(payload.get("model")) or "default",
        "language": _text(payload.get("language"), 20) or "auto",
        "bytes": size,
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".voice-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append(path: Path, row: Dict[str, Any]) -> None:
    """Append ``row``; once the file passes ``MAX_BYTES`` keep its newest ``KEEP_LINES`` rows."""
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _lock:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
        if path.stat().st_size > MAX_BYTES:
            _trim(path)


def read(path: Path) -> List[Dict[str, Any]]:
    """Every parseable row, oldest first. A torn or foreign line is skipped, not fatal."""
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def clear(path: Path) -> int:
    """Delete the ledger; returns how many rows it held."""
    with _lock:
        count = len(read(path))
        path.unlink(missing_ok=True)
    return count


def parse_span(word: str) -> Optional[float]:
    """Seconds for a time-window word such as ``12h`` or ``7d``; None when ``word`` is not one."""
    number, unit = word[:-1], word[-1:]
    if unit not in _SPAN_UNITS or not (number.isascii() and number.isdigit()) or len(number) > 4:
        return None
    return int(number) * _SPAN_UNITS[unit] or None


def parse_count(word: str) -> Optional[int]:
    """A row count for ``recent`` such as ``25``, from 1 to MAX_RECENT_ROWS; None when ``word`` is not one."""
    if not (word.isascii() and word.isdigit()) or len(word) > 3:
        return None
    count = int(word)
    return count if 1 <= count <= MAX_RECENT_ROWS else None


def span_title(word: str) -> str:
    count, unit = int(word[:-1]), {"h": "hour", "d": "day"}[word[-1]]
    return f"last {count} {unit}{'' if count == 1 else 's'}"


def select(rows: List[Dict[str, Any]], window: str, now: float,
           span: float = DAY_SECONDS) -> List[Dict[str, Any]]:
    if window == "all":
        return list(rows)
    return [r for r in rows if float(r.get("ts") or 0.0) >= now - span]


def _bytes(row: Dict[str, Any]) -> Optional[int]:
    size = row.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int):
        return None
    return size


def _median(values: List[int]) -> Optional[float]:
    if not values:
        return None
    ordered, mid = sorted(values), len(values) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _size(size: Optional[float]) -> str:
    if size is None:
        return "-"
    for limit, unit in ((1024 ** 3, "GB"), (1024 ** 2, "MB"), (1024, "KB")):
        if size >= limit:
            return f"{size / limit:.1f} {unit}"
    return f"{int(size)} B"


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    for size, unit in ((DAY_SECONDS, "d"), (3600.0, "h"), (60.0, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return f"{int(seconds)}s ago"


def _table(header: List[str], body: List[List[str]]) -> List[str]:
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    out = []
    for row in [header] + body:
        cells = [row[0].ljust(widths[0])] + [c.rjust(widths[i]) for i, c in enumerate(row) if i]
        out.append("  ".join(cells).rstrip())
    return out


def local_day(ts: Any) -> str:
    """The local calendar day of a row's timestamp, as YYYY-MM-DD ("unknown" when it has none)."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def summarize(rows: List[Dict[str, Any]], now: float, *, title: str, by: str = "provider") -> str:
    """Per-``by`` recordings: how many, how much audio in total and typically, and how long ago
    the last one was.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    group with the most recordings first."""
    if not rows:
        return f"Transcribed recordings, {title}: none recorded."
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = local_day(row.get("ts")) if by == "day" else str(row.get(by) or "unknown")
        groups.setdefault(key, []).append(row)

    def cells(name: str, items: List[Dict[str, Any]]) -> List[str]:
        sizes = [s for s in (_bytes(r) for r in items) if s is not None]
        last = max(float(r.get("ts") or 0.0) for r in items)
        return [name, str(len(items)), _size(sum(sizes) if sizes else None), _size(_median(sizes)),
                _age(now - last)]

    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", rows))
    lines = [f"Transcribed recordings, {title}:", ""]
    lines += _table([by, "recordings", "audio", "median", "last"], body)
    lines += ["", "Counted when Hermes hands a recording to the speech-to-text provider, so one that then",
              "fails to transcribe still counts."]
    return "\n".join(lines)


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` recordings of ``rows``, newest first, one line each."""
    if not rows:
        return f"Transcribed recordings, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), str(r.get("source") or "unknown"),
             str(r.get("provider") or "unknown"), str(r.get("model") or "default"),
             str(r.get("language") or "auto"), _size(_bytes(r))] for r in newest]
    lines = [f"Transcribed recordings, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "source", "provider", "model", "language", "audio"], body)
    return "\n".join(lines)
