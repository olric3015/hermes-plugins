"""Stream timing: pair the three streaming hooks into one row per response, store and summarise.

Standard library only. Hermes delivers each streaming hook on its own queue and worker thread,
so start, first delta and end can be observed in any order and a few milliseconds after the
token they describe; everything here is keyed by stream and tolerant of that. A row holds
identifiers, timings and a character count, never the streamed text.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

FILENAME = "streams.jsonl"
MAX_BYTES = 2_000_000
KEEP_LINES = 5000
DAY_SECONDS = 86400.0
MAX_OPEN = 256
OPEN_TTL_SECONDS = 3600.0
# How long the end event waits for a first-delta event still queued on the other worker.
DELTA_GRACE_SECONDS = 0.25

Key = Tuple[str, str, int]
_file_lock = threading.Lock()


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def stream_key(payload: Dict[str, Any]) -> Key:
    try:
        iteration = int(payload.get("iteration") or 0)
    except (TypeError, ValueError):
        iteration = 0
    return (_text(payload.get("session_id")), _text(payload.get("turn_id")), iteration)


class Tracker:
    """In-memory state of streams that have started but not ended. Thread-safe and bounded."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._open: Dict[Key, Dict[str, float]] = {}

    def _entry(self, key: Key, now: float) -> Dict[str, float]:
        entry = self._open.get(key)
        if entry is None:
            for stale in [k for k, e in self._open.items() if now - e["seen"] > OPEN_TTL_SECONDS]:
                del self._open[stale]
            while len(self._open) >= MAX_OPEN:
                del self._open[next(iter(self._open))]
            entry = self._open[key] = {"seen": now}
        return entry

    def start(self, payload: Dict[str, Any]) -> None:
        now = self._clock()
        with self._lock:
            self._entry(stream_key(payload), now)["start"] = now

    def delta(self, payload: Dict[str, Any]) -> None:
        if payload.get("kind") not in (None, "text") or not payload.get("delta"):
            return
        now = self._clock()
        with self._lock:
            self._entry(stream_key(payload), now).setdefault("first", now)

    def end(self, payload: Dict[str, Any], wall_time: float) -> Optional[Dict[str, Any]]:
        """Close the stream and return its row, or None when its start was never observed."""
        key = stream_key(payload)
        now = self._clock()
        chars = len(payload.get("final_text") or "")
        deadline = now + DELTA_GRACE_SECONDS
        while True:
            with self._lock:
                entry = self._open.get(key) or {}
                if "first" in entry or not chars or self._clock() >= deadline:
                    self._open.pop(key, None)
                    break
            self._sleep(0.01)
        start, first = entry.get("start"), entry.get("first")
        if start is None:
            return None
        # A delta delivered before its own start event cannot be timed: report it as unknown.
        ttft = None if first is None or first < start else round(first - start, 3)
        return {
            "ts": float(wall_time),
            "session_id": key[0],
            "iteration": key[2],
            "provider": _text(payload.get("provider")),
            "model": _text(payload.get("model")) or "unknown",
            "surface": _text(payload.get("surface")),
            "ttft_s": ttft,
            "duration_s": round(max(0.0, now - start), 3),
            "chars": chars,
            "finished": bool(payload.get("finished")),
            # A flag only: the error text can carry provider data.
            "failed": bool(payload.get("error")),
        }

    def open_count(self) -> int:
        with self._lock:
            return len(self._open)


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()[-KEEP_LINES:]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".streams-", suffix=".tmp")
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
    with _file_lock:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
        if path.stat().st_size > MAX_BYTES:
            _trim(path)


def read(path: Path) -> List[Dict[str, Any]]:
    """Every parseable row, oldest first. A torn or foreign line is skipped, not fatal."""
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
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
    with _file_lock:
        count = len(read(path))
        path.unlink(missing_ok=True)
    return count


def select(rows: List[Dict[str, Any]], window: str, now: float) -> List[Dict[str, Any]]:
    if window == "all":
        return list(rows)
    return [r for r in rows if float(r.get("ts") or 0.0) >= now - DAY_SECONDS]


def _percentile(values: List[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def _seconds(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.2f}s"


def _rate(row: Dict[str, Any]) -> Optional[float]:
    """Characters per second while text was arriving (first delta to end)."""
    ttft, chars = row.get("ttft_s"), int(row.get("chars") or 0)
    if ttft is None or not chars:
        return None
    writing = float(row.get("duration_s") or 0.0) - float(ttft)
    return chars / writing if writing >= 0.05 else None


def _table(header: List[str], body: List[List[str]]) -> List[str]:
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    out = []
    for row in [header] + body:
        cells = [row[0].ljust(widths[0])] + [c.rjust(widths[i]) for i, c in enumerate(row) if i]
        out.append("  ".join(cells).rstrip())
    return out


def summarize(rows: List[Dict[str, Any]], *, title: str) -> str:
    """Per-model stream count, failures, median and p90 time to first text, median chars/s."""
    if not rows:
        return f"Streaming speed, {title}: no streams recorded."
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("model") or "unknown"), []).append(row)
    body = []
    for model, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        firsts = [float(r["ttft_s"]) for r in items if r.get("ttft_s") is not None]
        rates = [rate for rate in (_rate(r) for r in items) if rate is not None]
        body.append([model, str(len(items)), str(sum(1 for r in items if r.get("failed") or not r.get("finished"))),
                     _seconds(median(firsts) if firsts else None),
                     _seconds(_percentile(firsts, 0.9) if firsts else None),
                     f"{median(rates):.0f}" if rates else "-"])
    lines = [f"Streaming speed, {title}:", ""]
    lines += _table(["model", "streams", "failed", "first text", "p90", "chars/s"], body)
    silent = sum(1 for r in rows if r.get("ttft_s") is None)
    if silent:
        lines += ["", f"{silent} stream(s) carried no text (tool calls only, or failed before any)."]
    return "\n".join(lines)
