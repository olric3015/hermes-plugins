"""Append-only JSONL ledger of the mid-turn messages Hermes's models write, and the summaries
``/interim-log`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds when a model wrote a mid-turn message, which model, where,
at which step of the turn and how long it was, never the text itself. Sessions and turns are kept
as short hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "interim.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
MAX_RECENT_ROWS = 100
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

_lock = threading.Lock()


def _text(value: Any, limit: int = 80) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def tag(value: Any) -> str:
    """A short, stable stand-in for a session or turn id ("" when there is none).

    The ledger only needs to tell sessions and turns apart, and Hermes's ids can be long (a turn
    id starts with its session id), so it keeps 12 hex digits of a SHA-256 of the whole id."""
    key = str(value or "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""


def build_row(payload: Dict[str, Any], *, now: float) -> Dict[str, Any]:
    """One ledger row from an ``on_interim_message`` payload. Only the length of the message is
    kept, never its text."""
    text = payload.get("text")
    return {
        "ts": float(now),
        "session": tag(payload.get("session_id")),
        "turn": tag(payload.get("turn_id")),
        "surface": _text(payload.get("surface"), 40) or "unknown",
        "model": _text(payload.get("model")) or "unknown",
        "provider": _text(payload.get("provider"), 40) or "unknown",
        "iteration": _int(payload.get("iteration")),
        "chars": len(text) if isinstance(text, str) else 0,
        "streamed": bool(payload.get("already_streamed")),
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".interim-", suffix=".tmp")
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


def last_session(rows: Iterable[Dict[str, Any]]) -> str:
    """The session of the newest row that carries one ("" when none does)."""
    found = ""
    for row in rows:
        if row.get("session"):
            found = str(row["session"])
    return found


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
    if window == "session":
        session = last_session(rows)
        return [r for r in rows if session and r.get("session") == session]
    return [r for r in rows if float(r.get("ts") or 0.0) >= now - span]


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


def turn_key(row: Dict[str, Any], index: int) -> str:
    """Which turn a row belongs to. A row without a turn id counts as a turn of its own."""
    turn = str(row.get("turn") or "")
    return f"{row.get('session') or ''}/{turn}" if turn else f"#{index}"


def summarize(rows: List[Dict[str, Any]], now: float, *, title: str, by: str = "model") -> str:
    """Per-``by`` mid-turn messages: how many, in how many turns, how many per such turn, their
    median length and how long ago the last one was.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    group with the most messages first."""
    if not rows:
        return f"Mid-turn messages, {title}: none recorded."
    groups: Dict[str, List[tuple]] = {}
    for index, row in enumerate(rows):
        key = local_day(row.get("ts")) if by == "day" else str(row.get(by) or "unknown")
        groups.setdefault(key, []).append((index, row))

    def cells(name: str, items: List[tuple]) -> List[str]:
        turns = {turn_key(row, index) for index, row in items}
        lengths = [_int(row.get("chars")) for _, row in items]
        last = max(float(row.get("ts") or 0.0) for _, row in items)
        return [name, str(len(items)), str(len(turns)), f"{len(items) / len(turns):.1f}",
                str(round(statistics.median(lengths))), _age(now - last)]

    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", list(enumerate(rows))))
    lines = [f"Mid-turn messages, {title}:", ""]
    lines += _table([by, "messages", "turns", "per turn", "median chars", "last"], body)
    lines += ["", "Counted even where display.interim_assistant_messages hides them."]
    return "\n".join(lines)


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` mid-turn messages of ``rows``, newest first, one line each."""
    if not rows:
        return f"Mid-turn messages, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), str(r.get("model") or "unknown"),
             str(r.get("surface") or "unknown"), str(_int(r.get("iteration"))),
             str(_int(r.get("chars"))), "yes" if r.get("streamed") else "no"] for r in newest]
    lines = [f"Mid-turn messages, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "model", "surface", "step", "chars", "streamed"], body)
    return "\n".join(lines)
