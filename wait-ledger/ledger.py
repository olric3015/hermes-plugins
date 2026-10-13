"""Append-only JSONL ledger of the times Hermes stopped to wait for you, and the summaries ``/waits`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds what kind of prompt it was, how it ended and how long it
waited, and never the prompt text or the answer.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "waits.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
MAX_RECENT_ROWS = 100
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

# How a wait ended, from the ``outcome`` each prompt reports (approval choices, clarify replies,
# sudo password prompts). Anything else is "other".
ANSWERED = frozenset({"once", "session", "always", "deny", "submitted", "provided"})
SKIPPED = frozenset({"skipped"})
UNANSWERED = frozenset({"timeout", "timed_out", "cancelled", "notify_failed", "undelivered"})

_lock = threading.Lock()


def _text(value: Any, limit: int = 80) -> str:
    return str(value or "")[:limit]


def result(outcome: str) -> str:
    """answered, skipped (an empty answer), unanswered (nobody answered), error or other."""
    if outcome in ANSWERED:
        return "answered"
    if outcome in SKIPPED:
        return "skipped"
    if outcome in UNANSWERED or outcome.startswith("transport_"):
        return "unanswered"
    if outcome == "error":
        return "error"
    return "other"


def build_row(payload: Dict[str, Any], *, now: float, wait: Optional[float]) -> Dict[str, Any]:
    """One ledger row from an ``on_human_input_resolved`` payload.

    ``wait`` is the time since the matching ``on_human_input_request``, or None when there was
    none. The prompt text and the session key are left out: a command or a question can carry
    private details, and the answer never reaches the hook at all."""
    outcome = _text(payload.get("outcome"), 40) or "unknown"
    return {
        "ts": float(now),
        "session_id": _text(payload.get("session_id"), 200),
        "platform": _text(payload.get("platform"), 40) or "unknown",
        # approval, clarify, sudo: a fixed label, not the prompt.
        "kind": _text(payload.get("kind"), 40) or "unknown",
        "outcome": outcome,
        "result": result(outcome),
        "wait_s": None if wait is None else round(max(0.0, float(wait)), 3),
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".waits-", suffix=".tmp")
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


def last_session_id(rows: Iterable[Dict[str, Any]]) -> str:
    """The session of the newest row that carries one ("" when none does)."""
    found = ""
    for row in rows:
        if row.get("session_id"):
            found = str(row["session_id"])
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
        session_id = last_session_id(rows)
        return [r for r in rows if session_id and r.get("session_id") == session_id]
    return [r for r in rows if float(r.get("ts") or 0.0) >= now - span]


def _wait(row: Dict[str, Any]) -> Optional[float]:
    wait = row.get("wait_s")
    if isinstance(wait, bool) or not isinstance(wait, (int, float)):
        return None
    return float(wait)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered, mid = sorted(values), len(values) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}m"


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


def summarize(rows: List[Dict[str, Any]], *, title: str, by: str = "kind") -> str:
    """Per-``by`` waits: how many, how they ended, and how long Hermes waited in total, typically
    and at most.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    group that waited longest in total first."""
    if not rows:
        return f"Waits, {title}: none recorded."
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = local_day(row.get("ts")) if by == "day" else str(row.get(by) or "unknown")
        groups.setdefault(key, []).append(row)

    def total(items: List[Dict[str, Any]]) -> float:
        return sum(w for w in (_wait(r) for r in items) if w is not None)

    def cells(name: str, items: List[Dict[str, Any]]) -> List[str]:
        ended = [str(r.get("result")) for r in items]
        waits = [w for w in (_wait(r) for r in items) if w is not None]
        return [name, str(len(items)), str(ended.count("answered")), str(ended.count("unanswered")),
                _duration(sum(waits) if waits else None), _duration(_median(waits)),
                _duration(max(waits) if waits else None)]

    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-total(kv[1]), -len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", rows))
    lines = [f"Waits, {title}:", ""]
    lines += _table([by, "waits", "answered", "unanswered", "total wait", "median", "longest"], body)
    lines += ["", "Unanswered: timed out, withdrawn or never delivered. An empty answer or a failed prompt",
              "counts in waits only."]
    return "\n".join(lines)


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` waits of ``rows``, newest first, one line each."""
    if not rows:
        return f"Waits, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), str(r.get("kind") or "unknown"),
             str(r.get("platform") or "unknown"), str(r.get("outcome") or "unknown"),
             _duration(_wait(r))] for r in newest]
    lines = [f"Waits, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "kind", "platform", "ended", "wait"], body)
    return "\n".join(lines)
