"""Append-only JSONL ledger of approval decisions, and the summaries ``/approval-log`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds the rule that asked, where it asked, the answer and how
long the answer took, and never the command or its description.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "approvals.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

APPROVED = frozenset({"once", "session", "always", "smart_approve"})
DENIED = frozenset({"deny", "smart_deny"})
UNANSWERED = frozenset({"timeout", "cancelled", "notify_failed"})

_lock = threading.Lock()


def _text(value: Any, limit: int = 80) -> str:
    return str(value or "")[:limit]


def outcome(choice: str) -> str:
    """approved, denied, unanswered (nobody answered, the command did not run) or other."""
    if choice in APPROVED:
        return "approved"
    if choice in DENIED:
        return "denied"
    if choice in UNANSWERED or choice.startswith("transport_"):
        return "unanswered"
    return "other"


def build_row(payload: Dict[str, Any], *, now: float, wait: Optional[float]) -> Dict[str, Any]:
    """One ledger row from a ``post_approval_response`` payload.

    ``wait`` is the time since the matching ``pre_approval_request``, or None when there was
    none. The command and its description are left out: they can carry secrets."""
    choice = _text(payload.get("choice"), 40) or "unknown"
    return {
        "ts": float(now),
        "session_id": _text(payload.get("session_id"), 200),
        # cli, gateway, smart, transport:<name>, mcp-elicitation/<server>, ...
        "surface": _text(payload.get("surface")) or "unknown",
        # The rule that asked, e.g. "recursive delete": a fixed label, not the command.
        "pattern": _text(payload.get("pattern_key")) or "unknown",
        "choice": choice,
        "outcome": outcome(choice),
        "decided_by": _text(payload.get("decided_by"), 40),
        "coalesced": payload.get("coalesced") is True,
        "wait_s": None if wait is None else round(max(0.0, float(wait)), 3),
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".approvals-", suffix=".tmp")
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


def _human_wait(row: Dict[str, Any]) -> Optional[float]:
    """How long a person took to answer, or None: smart-mode verdicts and unanswered prompts
    are not a person's answer time."""
    wait = row.get("wait_s")
    if row.get("decided_by") or row.get("outcome") not in ("approved", "denied"):
        return None
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


def summarize(rows: List[Dict[str, Any]], *, title: str, by: str = "pattern") -> str:
    """Per-``by`` approval prompts: how many, how each ended, and how long a person took.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    largest group first."""
    if not rows:
        return f"Approvals, {title}: none recorded."
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = local_day(row.get("ts")) if by == "day" else str(row.get(by) or "unknown")
        groups.setdefault(key, []).append(row)

    def cells(name: str, items: List[Dict[str, Any]]) -> List[str]:
        ended = [str(r.get("outcome")) for r in items]
        waits = [w for w in (_human_wait(r) for r in items) if w is not None]
        return [name, str(len(items)), str(ended.count("approved")), str(ended.count("denied")),
                str(ended.count("unanswered")), _duration(_median(waits))]

    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", rows))
    lines = [f"Approvals, {title}:", ""]
    lines += _table([by, "asked", "approved", "denied", "unanswered", "median wait"], body)
    lines += [""]
    if any(r.get("decided_by") for r in rows):
        lines += ["Smart-mode verdicts count as approved or denied; median wait is people's answers only."]
    else:
        lines += ["Unanswered: timed out, withdrawn or never delivered; the command did not run."]
    return "\n".join(lines)


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` decisions of ``rows``, newest first, one line each."""
    if not rows:
        return f"Approvals, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), str(r.get("pattern") or "unknown"),
             str(r.get("surface") or "unknown"), str(r.get("choice") or "unknown"),
             _duration(r["wait_s"] if isinstance(r.get("wait_s"), (int, float))
                       and not isinstance(r.get("wait_s"), bool) else None)] for r in newest]
    lines = [f"Approvals, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "pattern", "surface", "answer", "wait"], body)
    return "\n".join(lines)
