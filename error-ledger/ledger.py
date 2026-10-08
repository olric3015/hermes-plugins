"""Append-only JSONL ledger of failed provider attempts, and the summaries ``/errors`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds identifiers, the HTTP status, Hermes's own classification
and timings, and never the error message or the request.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "errors.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

_lock = threading.Lock()


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _status(value: Any) -> Optional[int]:
    """An HTTP status, or None: some providers put a string error code in this field."""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float)) and 100 <= int(value) <= 599:
        return int(value)
    return None


def build_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    """One ledger row from an ``api_request_error`` payload."""
    error = payload.get("error")
    retryable = payload.get("retryable")
    return {
        "ts": float(payload.get("ended_at") or 0.0),
        "session_id": _text(payload.get("session_id")),
        "platform": _text(payload.get("platform"), 60),
        "provider": _text(payload.get("provider")) or "unknown",
        "model": _text(payload.get("model")) or "unknown",
        "api_mode": _text(payload.get("api_mode"), 60),
        "status": _status(payload.get("status_code")),
        # Hermes's classification (rate_limit, overloaded, context_overflow, ...).
        "reason": _text(payload.get("reason"), 60) or "unknown",
        # The exception class only: the message can carry provider or user text.
        "error_type": _text(error.get("type") if isinstance(error, dict) else "", 80),
        "retryable": retryable if isinstance(retryable, bool) else None,
        "retry_count": _count(payload.get("retry_count")),
        "duration_s": round(max(0.0, float(payload.get("api_duration") or 0.0)), 3),
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".errors-", suffix=".tmp")
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


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    for size, unit in ((DAY_SECONDS, "d"), (3600.0, "h"), (60.0, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return f"{int(seconds)}s ago"


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` failed attempts of ``rows``, newest first, one line each."""
    if not rows:
        return f"Provider errors, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), str(r.get("model") or "unknown"),
             str(r.get("provider") or "unknown"), str(r.get("reason") or "unknown"),
             str(r["status"]) if isinstance(r.get("status"), int) else "-",
             {True: "yes", False: "no"}.get(r.get("retryable"), "-")] for r in newest]
    lines = [f"Provider errors, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "model", "provider", "reason", "status", "retryable"], body)
    return "\n".join(lines)


def _table(header: List[str], body: List[List[str]]) -> List[str]:
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    out = []
    for row in [header] + body:
        cells = [row[0].ljust(widths[0])] + [c.rjust(widths[i]) for i, c in enumerate(row) if i]
        out.append("  ".join(cells).rstrip())
    return out


def _top(counter: Counter) -> str:
    """The most common value with its count; ties go to the name that sorts first."""
    if not counter:
        return "-"
    name, count = min(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return f"{name} ({count})"


def local_day(ts: Any) -> str:
    """The local calendar day of a row's timestamp, as YYYY-MM-DD ("unknown" when it has none)."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def summarize(rows: List[Dict[str, Any]], *, title: str, by: str = "model") -> str:
    """Per-``by`` failed attempts: how many, how many Hermes could retry, the commonest cause.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    largest group first."""
    if not rows:
        return f"Provider errors, {title}: none recorded."
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = local_day(row.get("ts")) if by == "day" else str(row.get(by) or "unknown")
        groups.setdefault(key, []).append(row)
    # Grouped by reason, the second column names where it happened; otherwise why.
    other = "model" if by == "reason" else "reason"

    def cells(name: str, items: List[Dict[str, Any]]) -> List[str]:
        return [name, str(len(items)), str(sum(1 for r in items if r.get("retryable") is True)),
                _top(Counter(str(r.get(other) or "unknown") for r in items)),
                _top(Counter(int(r["status"]) for r in items if isinstance(r.get("status"), int)))]

    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", rows))
    lines = [f"Provider errors, {title}:", ""]
    lines += _table([by, "errors", "retryable", f"top {other}", "top status"], body)
    lines += ["", "One row per failed attempt Hermes reported; the retry after it may have succeeded."]
    return "\n".join(lines)
