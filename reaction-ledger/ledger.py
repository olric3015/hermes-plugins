"""Append-only JSONL ledger of the emoji reactions left in your Telegram chats with Hermes, and
the summaries ``/reaction-log`` prints.

Standard library only: nothing here imports Hermes, so it runs unchanged under the admission
probe and in unit tests. A row holds when a message's reactions changed and the reactions it has
after the change. The chat and the message are kept only as short hashes, never their ids or
anything said in the chat.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "reactions.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
RECENT_ROWS = 10
MAX_RECENT_ROWS = 100
MAX_EMOJIS = 20
CUSTOM = "custom"
_SPAN_UNITS = {"h": 3600.0, "d": DAY_SECONDS}
# Joiners, variation selectors and skin tones shape an emoji but do not name it.
_SILENT = {0x200D, 0xFE0E, 0xFE0F, *range(0x1F3FB, 0x1F400)}

_lock = threading.Lock()


def tag(*parts: Any) -> str:
    """A short, stable stand-in for an id ("" when there is none).

    Telegram chat ids name a person or a group; the ledger only needs to tell chats and messages
    apart, so it keeps 12 hex digits of a SHA-256 instead."""
    if any(p is None or str(p).strip() == "" for p in parts):
        return ""
    return hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _emojis(value: Any) -> List[str]:
    """The distinct emoji of a reaction set, in order, at most ``MAX_EMOJIS``."""
    out: List[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip() and item.strip()[:16] not in out:
                out.append(item.strip()[:16])
    return out[:MAX_EMOJIS]


def build_row(platform: Any, event_type: Any, payload: Any, *, now: float) -> Optional[Dict[str, Any]]:
    """One ledger row from a ``gateway_platform_event`` call, or None for anything that is not a
    reaction change on an identifiable message."""
    if event_type != "reaction" or not isinstance(payload, dict):
        return None
    platform = str(platform or "").strip()[:40] or "unknown"
    chat, message = payload.get("chat_id"), payload.get("message_id")
    if not tag(chat) or not tag(message):
        return None
    custom = payload.get("custom_emoji_ids")
    return {
        "ts": float(now),
        "platform": platform,
        "chat": tag(platform, chat),
        "message": tag(platform, chat, message),
        # The reactions the message has after this change: an empty set means they were removed.
        "emojis": _emojis(payload.get("emojis")),
        "custom": len(custom) if isinstance(custom, (list, tuple)) else 0,
    }


def emoji_name(emoji: str) -> str:
    """Unicode's name for an emoji, lowercase ("" when Unicode has none)."""
    if emoji == CUSTOM:
        return "custom emoji"
    names = [unicodedata.name(ch, "").lower() for ch in emoji if ord(ch) not in _SILENT]
    return " + ".join(n for n in names if n)[:32]


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".reactions-", suffix=".tmp")
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


def select(rows: List[Dict[str, Any]], now: float, span: Optional[float] = DAY_SECONDS) -> List[Dict[str, Any]]:
    """The rows of the last ``span`` seconds (every row when ``span`` is None)."""
    if span is None:
        return list(rows)
    return [r for r in rows if float(r.get("ts") or 0.0) >= now - span]


def labels(row: Dict[str, Any]) -> List[str]:
    """The reactions a row records: its emoji, then ``custom`` when it holds custom emoji."""
    found = [str(e) for e in row.get("emojis") or [] if e]
    return found + ([CUSTOM] if row.get("custom") else [])


def latest(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The newest row of each message: the reactions that message has now."""
    newest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("message") or "")
        if key:
            newest.pop(key, None)
            newest[key] = row
    return list(newest.values())


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    for size, unit in ((DAY_SECONDS, "d"), (3600.0, "h"), (60.0, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return f"{int(seconds)}s ago"


def _table(header: List[str], body: List[List[str]], left: int = 1) -> List[str]:
    """Rows padded to their columns: the first ``left`` columns flush left, the rest flush right."""
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    out = []
    for row in [header] + body:
        cells = [c.ljust(widths[i]) if i < left else c.rjust(widths[i]) for i, c in enumerate(row)]
        out.append("  ".join(cells).rstrip())
    return out


def local_day(ts: Any) -> str:
    """The local calendar day of a row's timestamp, as YYYY-MM-DD ("unknown" when it has none)."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _last(items: List[Dict[str, Any]], now: float) -> str:
    return _age(now - max(float(r.get("ts") or 0.0) for r in items))


def summarize(rows: List[Dict[str, Any]], now: float, *, title: str, by: str = "emoji") -> str:
    """Each message once, with the reactions it has now: per emoji how many messages carry it,
    in how many chats, and how long ago it was last set.

    ``by="day"`` groups the messages by the local day their reactions last changed, newest day
    first, with the day's most used emoji."""
    current = latest(rows)
    reacted = [r for r in current if labels(r)]
    removed = len(current) - len(reacted)
    if not reacted:
        note = f" (reactions removed from {removed} message(s))" if removed else ""
        return f"Reactions, {title}: none recorded{note}."
    chats = lambda items: str(len({r.get("chat") for r in items if r.get("chat")}))  # noqa: E731
    if by == "day":
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in reacted:
            groups.setdefault(local_day(row.get("ts")), []).append(row)
        # Rows without a usable time go last.
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "unknown", kv[0]), reverse=True)
        body = [[day, str(len(items)), chats(items), _top(items), _last(items, now)] for day, items in ordered]
        table = _table(["day", "messages", "chats", "top", "last"], body)
    else:
        groups = {}
        for row in reacted:
            for label in labels(row):
                groups.setdefault(label, []).append(row)
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), *_rank(kv[0])))
        body = [[label, emoji_name(label), str(len(items)), chats(items), _last(items, now)]
                for label, items in ordered]
        body.append(["all", "", str(len(reacted)), chats(reacted), _last(reacted, now)])
        table = _table(["emoji", "name", "messages", "chats", "last"], body, left=2)
    lines = [f"Reactions, {title}:", ""] + table + [""]
    lines.append("Each message counts once, with the reactions it has now.")
    if removed:
        lines.append(f"Reactions were removed again from {removed} message(s).")
    return "\n".join(lines)


def _rank(label: str) -> tuple:
    """Tie order: real emoji before custom ones, then by the emoji itself."""
    return (label == CUSTOM, label)


def _top(items: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for row in items:
        for label in labels(row):
            counts[label] = counts.get(label, 0) + 1
    return min(counts, key=lambda label: (-counts[label], *_rank(label)))


def recent(rows: List[Dict[str, Any]], now: float, *, title: str, limit: int = RECENT_ROWS) -> str:
    """The newest ``limit`` reaction changes of ``rows``, newest first, one line each."""
    if not rows:
        return f"Reactions, {title}: none recorded."
    newest = rows[-limit:][::-1]
    body = [[_age(now - float(r.get("ts") or 0.0)), " ".join(labels(r)) or "(removed)",
             str(r.get("message") or "-"), str(r.get("chat") or "-")] for r in newest]
    lines = [f"Reaction changes, {title}, newest {len(newest)} of {len(rows)}:", ""]
    lines += _table(["when", "reactions", "message", "chat"], body, left=2)
    return "\n".join(lines)
