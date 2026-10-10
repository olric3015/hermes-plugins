"""Append-only JSONL ledger of auxiliary LLM calls, and the summaries ``/aux`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds identifiers, timings and token counts and never request or
response text.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "calls.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                 "reasoning_tokens")
# What the provider read: fresh input plus cache reads and cache writes.
_PROMPT_FIELDS = ("input_tokens", "cache_read_tokens", "cache_write_tokens")
_lock = threading.Lock()


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def build_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    """One ledger row from a ``post_auxiliary_call`` payload."""
    usage = payload.get("usage")
    row: Dict[str, Any] = {
        "ts": float(payload.get("ended_at") or 0.0),
        "session_id": _text(payload.get("session_id")),
        "aux_task": _text(payload.get("aux_task")) or "unknown",
        "provider": _text(payload.get("provider")),
        "model": _text(payload.get("model")),
        "response_model": _text(payload.get("response_model")),
        "retry_count": _count(payload.get("retry_count")),
        "streaming": bool(payload.get("streaming")),
        "duration_s": round(max(0.0, float(payload.get("api_duration") or 0.0)), 3),
        # The exception class only: the message can carry provider or user text.
        "error_type": _text(payload.get("error_type")) or None,
        "has_usage": isinstance(usage, dict),
    }
    for field in _TOKEN_FIELDS:
        row[field] = _count(usage.get(field)) if isinstance(usage, dict) else 0
    return row


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".calls-", suffix=".tmp")
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
    """The session of the newest row that ran inside a turn ("" when none did)."""
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


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m{rest:02d}s"


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


def summarize(rows: List[Dict[str, Any]], *, title: str, by: str = "aux_task") -> str:
    """Per-``by`` totals (calls, failures, input and output tokens, wall time) as plain text.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    group with the most tokens first."""
    if not rows:
        return f"Auxiliary LLM calls, {title}: none recorded."
    groups: Dict[str, Dict[str, float]] = {}
    for row in rows:
        key = local_day(row.get("ts")) if by == "day" else str(row.get(by) or "unknown")
        g = groups.setdefault(key, {"calls": 0, "failed": 0, "in": 0, "out": 0, "secs": 0.0})
        g["calls"] += 1
        g["failed"] += 1 if row.get("error_type") else 0
        g["in"] += sum(_count(row.get(f)) for f in _PROMPT_FIELDS)
        g["out"] += _count(row.get("output_tokens"))
        g["secs"] += float(row.get("duration_s") or 0.0)
    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-(kv[1]["in"] + kv[1]["out"]), kv[0]))
    total = {k: sum(g[k] for g in groups.values()) for k in ("calls", "failed", "in", "out", "secs")}

    def cells(name: str, g: Dict[str, float]) -> List[str]:
        return [name, f"{int(g['calls'])}", f"{int(g['failed'])}", f"{int(g['in']):,}",
                f"{int(g['out']):,}", _duration(g["secs"])]

    label = "task" if by == "aux_task" else by
    body = [cells(name, g) for name, g in ordered] + [cells("total", total)]
    lines = [f"Auxiliary LLM calls, {title}:", ""]
    lines += _table([label, "calls", "failed", "input", "output", "time"], body)
    silent = sum(1 for r in rows if not r.get("has_usage"))
    if silent:
        lines += ["", f"{silent} call(s) reported no token usage (streamed or failed)."]
    return "\n".join(lines)


def data_path(data_dir: Optional[Path]) -> Optional[Path]:
    return None if data_dir is None else Path(data_dir) / FILENAME
