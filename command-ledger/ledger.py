"""Append-only JSONL ledger of slash commands, and the summaries ``/command-log`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds which command ran, where, and a short hash of the session
it ran in; never the command's arguments or the session key itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "commands.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}

_lock = threading.Lock()


def _text(value: Any, limit: int = 60) -> str:
    return str(value or "").strip().lstrip("/")[:limit]


def session_tag(session_key: Any) -> str:
    """A short, stable stand-in for a session key ("" when there is none).

    Gateway session keys name the chat and the user; the ledger only needs to tell sessions
    apart, so it keeps 12 hex digits of a SHA-256 instead."""
    key = str(session_key or "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""


def build_row(payload: Dict[str, Any], *, now: float) -> Dict[str, Any]:
    """One ledger row from a ``pre_command`` payload. ``args_raw`` is left out: it can carry
    model names, file paths, prompts or secrets."""
    command = _text(payload.get("command")) or "unknown"
    typed = _text(payload.get("alias_used")).lower()
    return {
        "ts": float(now),
        "session": session_tag(payload.get("session_key")),
        "surface": _text(payload.get("surface"), 20) or "unknown",  # cli or gateway
        "platform": _text(payload.get("platform"), 40) or "unknown",  # cli, telegram, discord, ...
        "command": command,
        # The word typed when it was an alias (/exit for quit); "" when the command was typed as is.
        "alias": typed if typed and typed != command else "",
    }


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".commands-", suffix=".tmp")
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


def summarize(rows: List[Dict[str, Any]], *, title: str, now: float, by: str = "command") -> str:
    """Per-``by`` command use: how often, its share, in how many sessions, and when last.

    ``by="day"`` groups by local calendar day, newest day first; every other grouping puts the
    largest group first."""
    if not rows:
        return f"Slash commands, {title}: none recorded."
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if by == "day":
            groups.setdefault(local_day(row.get("ts")), []).append(row)
            continue
        name = str(row.get(by) or "unknown")
        groups.setdefault(f"/{name}" if by == "command" else name, []).append(row)

    def cells(name: str, items: List[Dict[str, Any]]) -> List[str]:
        sessions = {r.get("session") for r in items if r.get("session")}
        newest = max(float(r.get("ts") or 0.0) for r in items)
        return [name, str(len(items)), f"{100 * len(items) / len(rows):.0f}%", str(len(sessions)),
                _age(now - newest)]

    if by == "day":
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
    else:
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    body = [cells(name, items) for name, items in ordered]
    if len(ordered) > 1:
        body.append(cells("total", rows))
    lines = [f"Slash commands, {title}:", ""]
    lines += _table([by, "uses", "share", "sessions", "last used"], body)
    lines += ["", "The CLI counts Hermes's built-in commands; messaging platforms add plugin commands."]
    return "\n".join(lines)


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` commands of ``rows``, newest first, one line each."""
    if not rows:
        return f"Slash commands, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), f"/{r.get('command') or 'unknown'}",
             f"/{r['alias']}" if r.get("alias") else "", str(r.get("platform") or "unknown")]
            for r in newest]
    lines = [f"Slash commands, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "command", "typed as", "platform"], body)
    return "\n".join(lines)
