"""Append-only JSONL ledger of the turns in which Hermes's agent changed files, and the summaries
``/edit-log`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds when a turn that changed files was about to finish, which
model and platform it ran on, how many files it changed and their file types (extensions), never
a file name, a path or anything the agent wrote. Sessions are kept as short hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "edits.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
MAX_RECENT_ROWS = 100
MAX_TYPES = 20
NO_TYPE = "(none)"
OTHER_TYPE = "(other)"
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}
_EXT = re.compile(r"\.[a-z0-9_+-]{1,10}")

_lock = threading.Lock()


def _text(value: Any, limit: int = 80) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def tag(value: Any) -> str:
    """A short, stable stand-in for a session id ("" when there is none)."""
    key = str(value or "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""


def file_type(path: Any) -> str:
    """The extension of a path's last component, lower-cased (``.py``); ``(none)`` when it has
    none (``Makefile``, ``.env``) and ``(other)`` when it is not a plain short extension."""
    # Split on both separators by hand, then posixpath: the same answer on every OS for paths from
    # any OS (ntpath would also split on "\\" and hide a missing split).
    name = re.split(r"[\\/]", str(path or ""))[-1]
    ext = posixpath.splitext(name)[1].lower()
    if not ext:
        return NO_TYPE
    return ext if _EXT.fullmatch(ext) else OTHER_TYPE


def count_types(paths: Iterable[Any]) -> Dict[str, int]:
    """Files per type, the ``MAX_TYPES`` most common kept and the rest folded into ``(other)``."""
    counts: Dict[str, int] = {}
    for path in paths:
        kind = file_type(path)
        counts[kind] = counts.get(kind, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    kept = dict(ranked[:MAX_TYPES])
    rest = sum(n for _, n in ranked[MAX_TYPES:])
    if rest:
        kept[OTHER_TYPE] = kept.get(OTHER_TYPE, 0) + rest
    return kept


def build_row(payload: Dict[str, Any], *, now: float) -> Dict[str, Any]:
    """One ledger row from a ``pre_verify`` payload. Only how many files changed and their types
    are kept, never a path, a file name or the answer."""
    paths = payload.get("changed_paths")
    paths = [p for p in paths if p] if isinstance(paths, (list, tuple)) else []
    return {
        "ts": float(now),
        "session": tag(payload.get("session_id")),
        "platform": _text(payload.get("platform"), 40) or "unknown",
        "model": _text(payload.get("model")) or "unknown",
        "attempt": _int(payload.get("attempt")),
        "files": len(paths),
        "types": count_types(paths),
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".edits-", suffix=".tmp")
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


def _types(row: Dict[str, Any]) -> Dict[str, int]:
    types = row.get("types")
    if not isinstance(types, dict):
        return {}
    return {str(k): _int(v) for k, v in types.items() if _int(v)}


def turns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold rows into turns, oldest first.

    Hermes calls the hook when a turn that changed files is about to finish. If another check
    keeps the agent going, it calls it again in the same turn with a higher ``attempt`` and the
    turn's full file list so far; such a row replaces the files of that session's current turn
    instead of counting a new one. A row with ``attempt`` 0 starts a turn."""
    out: List[Dict[str, Any]] = []
    current: Dict[str, int] = {}
    for row in rows:
        session = str(row.get("session") or "")
        index = current.get(session)
        if _int(row.get("attempt")) and index is not None:
            turn = out[index]
            turn.update(ts=row.get("ts"), files=_int(row.get("files")), types=_types(row))
            continue
        # ts stays as stored: a row without one is an "unknown" day, not 1970.
        turn = {"ts": row.get("ts"), "session": session,
                "platform": str(row.get("platform") or "unknown"), "model": str(row.get("model") or "unknown"),
                "files": _int(row.get("files")), "types": _types(row)}
        out.append(turn)
        if session:
            current[session] = len(out) - 1
    return out


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
    """The local calendar day of a timestamp, as YYYY-MM-DD ("unknown" when it has none)."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _ts(turn: Dict[str, Any]) -> float:
    try:
        return float(turn.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _num(value: float) -> str:
    """2 for 2.0, 2.5 for 2.5: a median of an even count can fall between two files."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _merge_types(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for turn in items:
        for kind, n in turn["types"].items():
            merged[kind] = merged.get(kind, 0) + n
    return merged


def _top(types: Dict[str, int]) -> str:
    """The type with the most files (alphabetically first on a tie), "-" when there is none."""
    return min(types.items(), key=lambda kv: (-kv[1], kv[0]))[0] if types else "-"


def summarize(rows: List[Dict[str, Any]], now: float, *, title: str, by: str = "model") -> str:
    """Per-``by`` turns that changed files: how many, how many files in all, the median per turn,
    the most changed file type and how long ago the last one was.

    ``by="type"`` lists file types instead (files of that type, turns that changed one);
    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    group with the most turns first."""
    folded = turns(rows)
    if not folded:
        return f"Turns that changed files, {title}: none recorded."
    if by == "type":
        return _summarize_types(folded, now, title)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for turn in folded:
        key = local_day(turn["ts"]) if by == "day" else str(turn.get(by) or "unknown")
        groups.setdefault(key, []).append(turn)

    def cells(name: str, items: List[Dict[str, Any]]) -> List[str]:
        counts = [t["files"] for t in items]
        return [name, str(len(items)), str(sum(counts)), _num(statistics.median(counts)),
                _top(_merge_types(items)), _age(now - max(_ts(t) for t in items))]

    if by == "day":
        # Turns without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", folded))
    lines = [f"Turns that changed files, {title}:", ""]
    lines += _table([by, "turns", "files", "median", "top type", "last"], body)
    lines += ["", "Counts files changed with write_file and patch, not by terminal commands."]
    return "\n".join(lines)


def _summarize_types(folded: List[Dict[str, Any]], now: float, title: str) -> str:
    stats: Dict[str, List[float]] = {}
    for turn in folded:
        for kind, n in turn["types"].items():
            files, count, last = stats.get(kind, [0, 0, 0.0])
            stats[kind] = [files + n, count + 1, max(last, _ts(turn))]
    ordered = sorted(stats.items(), key=lambda kv: (-kv[1][0], kv[0]))
    body = [[kind, str(int(files)), str(int(count)), _age(now - last)] for kind, (files, count, last) in ordered]
    lines = [f"Turns that changed files, {title}, by file type:", ""]
    lines += _table(["type", "files", "turns", "last"], body)
    lines += ["", "Counts files changed with write_file and patch, not by terminal commands."]
    return "\n".join(lines)


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` turns that changed files, newest first, one line each."""
    folded = turns(rows)
    if not folded:
        return f"Turns that changed files, {title}: none recorded."
    newest = folded[-limit:][::-1]
    body = []
    for t in newest:
        ranked = sorted(t["types"].items(), key=lambda kv: (-kv[1], kv[0]))
        shown = ", ".join(f"{kind} {n}" for kind, n in ranked[:3]) + (" ..." if len(ranked) > 3 else "")
        body.append([_age(now - _ts(t)), t["model"], t["platform"], str(t["files"]), shown or "-"])
    lines = [f"Turns that changed files, {title}, newest {len(newest)} of {len(folded)}:", ""]
    lines += _table(["when", "model", "platform", "files", "types"], body)
    return "\n".join(lines)
