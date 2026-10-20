"""Timestamp conversions for ``@ts:``: Unix numbers and written dates turned into readable times.

Standard library only (``datetime``, ``email.utils``): nothing here imports Hermes, so it runs
unchanged under the admission probe and in unit tests. Every function that depends on the current
time takes ``now`` (Unix seconds) so tests can pin it.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

MAX_VALUES = 10           # timestamps converted in one reference

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DAY = 86_400

# Unit chosen for a bare number by its size: seconds reach past the year 5000 before they need
# 12 digits, so 12-14 digits are milliseconds, 15-17 microseconds and anything larger nanoseconds.
_UNITS = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}
_UNIT_NAMES = {"s": "seconds", "ms": "milliseconds", "us": "microseconds", "ns": "nanoseconds"}
_NUMBER = re.compile(r"^([+-]?\d{1,20}(?:\.\d{1,9})?)(s|ms|us|ns)?$")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class Instant:
    """One parsed timestamp: the moment itself plus how it was written."""

    def __init__(self, raw: str, moment: datetime, kind: str, note: str = "", written: Optional[datetime] = None):
        self.raw = raw
        self.moment = moment          # aware, UTC
        self.kind = kind              # what the value was read as
        self.note = note              # anything the reader should know about that reading
        self.written = written        # the time in the value's own offset, when it had one


def _guess_unit(digits: str) -> str:
    n = len(digits.lstrip("0")) or 1
    if n <= 11:
        return "s"
    if n <= 14:
        return "ms"
    if n <= 17:
        return "us"
    return "ns"


def _from_number(raw: str, number: str, unit: Optional[str]) -> Instant:
    whole = number.lstrip("+-").split(".")[0]
    guessed = unit is None
    unit = unit or _guess_unit(whole)
    try:
        value = Decimal(number)
    except InvalidOperation:  # the pattern only lets digits through, so this is defensive
        raise ValueError(f"'{raw}' is not a number")
    micros = value * 1_000_000 / _UNITS[unit]
    whole_micros = int(micros.to_integral_value(rounding="ROUND_FLOOR"))
    try:
        moment = EPOCH + timedelta(microseconds=whole_micros)
    except OverflowError:
        raise ValueError(f"'{raw}' read as Unix {_UNIT_NAMES[unit]} is outside the years 1-9999")
    if guessed:
        digits = len(whole.lstrip("0")) or 1
        note = f"read as {_UNIT_NAMES[unit]} from its {digits} digits; add s, ms, us or ns to choose"
    else:
        note = f"Unix {_UNIT_NAMES[unit]}, as written"
    if micros != whole_micros:
        note += "; time below one microsecond is dropped"
    return Instant(raw, moment, f"Unix {_UNIT_NAMES[unit]}", note)


def _from_text(raw: str) -> Instant:
    text = raw.strip()
    iso = text[:-1] + "+00:00" if text[-1:] in "Zz" else text  # Python 3.10 has no "Z"
    try:
        parsed = datetime.fromisoformat(iso)
        kind = "ISO 8601"
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            raise ValueError(f"'{raw}' is not a Unix timestamp, an ISO 8601 date or an RFC 2822 date")
        kind = "RFC 2822"
    if parsed.tzinfo is None:
        return Instant(raw, parsed.replace(tzinfo=timezone.utc), kind,
                       "no UTC offset in the value, so it is read as UTC")
    return Instant(raw, parsed.astimezone(timezone.utc), kind, written=parsed)


def parse(raw: str, *, now: Optional[float] = None) -> Instant:
    """One timestamp: ``now``, a Unix number (optionally ending in s/ms/us/ns), an ISO 8601 date
    or an RFC 2822 date (``Mon, 21 Oct 2024 00:00:00 GMT``)."""
    value = raw.strip()
    if not value:
        raise ValueError("empty timestamp")
    if value.lower() == "now":
        current = time.time() if now is None else now
        micros = int(Decimal(repr(current)) * 1_000_000)
        return Instant(raw, EPOCH + timedelta(microseconds=micros), "the current time")
    number = _NUMBER.match(value.replace("_", ""))
    if number:
        return _from_number(value, number.group(1), number.group(2))
    return _from_text(value)


def split(target: str) -> List[str]:
    """The values of one reference: separated by commas, or by spaces when no value has one."""
    target = target.strip()
    if "," in target:
        parts = [p.strip() for p in target.split(",")]
        # "Mon, 21 Oct 2024 ..." is one RFC 2822 date, not two values.
        if re.match(r"^[A-Za-z]{3}$", parts[0]) and len(parts) == 2:
            return [target]
    else:
        parts = target.split() if all(_NUMBER.match(p) or p.lower() == "now" for p in target.split()) else [target]
    return [p for p in parts if p]


def _clock(dt: datetime) -> str:
    stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
    if dt.microsecond:
        stamp += f".{dt.microsecond:06d}".rstrip("0")
    return stamp


def _offset(dt: datetime) -> str:
    raw = dt.strftime("%z") or "+0000"
    return f"{raw[:3]}:{raw[3:5]}"


def _local(moment: datetime) -> Optional[datetime]:
    """The moment in this computer's time zone, or None where the system cannot say (Windows
    refuses dates before 1970)."""
    try:
        return moment.astimezone()
    except (OSError, OverflowError, ValueError):
        return None


def _iso(moment: datetime) -> str:
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if moment.microsecond:
        stamp += f".{moment.microsecond:06d}".rstrip("0")
    return stamp + "Z"


def span(seconds: float) -> str:
    """A duration in words, two units at most: '3 days 4 hours', '2 minutes 5 seconds'."""
    seconds = abs(seconds)
    if seconds < 1:
        return f"{seconds:.3f} seconds" if seconds else "0 seconds"
    whole = int(seconds)
    if whole >= 365 * _DAY:
        years = whole / (365.2425 * _DAY)
        return f"about {years:.1f} years ({whole // _DAY:,} days)"
    parts = []
    for size, label in ((_DAY, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        count, whole = divmod(whole, size)
        if count or parts:
            parts.append(f"{count} {label}{'' if count == 1 else 's'}")
        if len(parts) == 2:
            break
    if parts and parts[-1].startswith("0 "):
        parts.pop()
    return " ".join(parts)


def relative(moment: datetime, now: float) -> str:
    delta = (moment - EPOCH).total_seconds() - now
    if abs(delta) < 1:
        return "now"
    return f"in {span(delta)}" if delta > 0 else f"{span(delta)} ago"


def _scaled(micros: int, per_unit: int, places: int) -> str:
    """``micros`` in a coarser unit, exactly: integer arithmetic on the magnitude, sign in front."""
    whole, rest = divmod(abs(micros), per_unit)
    text = f"{whole}" if not rest else f"{whole}.{rest:0{places}d}".rstrip("0")
    return f"-{text}" if micros < 0 else text


def _unix(moment: datetime) -> str:
    micros = (moment - EPOCH) // timedelta(microseconds=1)
    return f"{_scaled(micros, 1_000_000, 6)} s, {_scaled(micros, 1000, 3)} ms, {micros} us"


def describe(instant: Instant, now: float) -> str:
    moment = instant.moment
    rows = []
    if instant.written is not None and _offset(instant.written) != "+00:00":
        rows.append(("as written", f"{_clock(instant.written)} {_offset(instant.written)} "
                                   f"{_WEEKDAYS[instant.written.weekday()]}"))
    rows.append(("UTC", f"{_clock(moment)} {_WEEKDAYS[moment.weekday()]}"))
    local = _local(moment)
    if local is None:
        rows.append(("local", "not available for this date on this system"))
    else:
        rows.append(("local", f"{_clock(local)} {_offset(local)} {_WEEKDAYS[local.weekday()]}"))
    rows.append(("ISO 8601", _iso(moment)))
    rows.append(("relative", relative(moment, now)))
    rows.append(("Unix", _unix(moment)))
    year, week, _ = moment.isocalendar()
    rows.append(("calendar", f"day {moment.timetuple().tm_yday} of {moment.year}, ISO week {year}-W{week:02d}"))
    width = max(len(label) for label, _ in rows)
    lines = [f"Timestamp {instant.raw} ({instant.kind})"]
    if instant.note:
        lines.append(f"  note: {instant.note}")
    lines += [f"  {label.ljust(width)}  {value}" for label, value in rows]
    return "\n".join(lines)


def expand(target: str, *, now: Optional[float] = None) -> str:
    """What ``@ts:<target>`` puts into the message."""
    now = time.time() if now is None else now
    values = split(target)
    if not values:
        raise ValueError("no timestamp given: write @ts:1729468800, @ts:2024-10-21T09:00:00+03:00 or @ts:now")
    if len(values) > MAX_VALUES:
        raise ValueError(f"too many timestamps: at most {MAX_VALUES} in one @ts: reference")
    instants = [parse(v, now=now) for v in values]
    blocks = [describe(i, now) for i in instants]
    if len(instants) > 1:
        lines = ["Differences:"]
        for n, (a, b) in enumerate(zip(instants, instants[1:]), start=1):
            gap = (b.moment - a.moment).total_seconds()
            sign = "+" if gap >= 0 else "-"
            exact = f"{abs(gap):,.6f}".rstrip("0").rstrip(".")
            lines.append(f"  {n} -> {n + 1}: {sign}{span(gap)} ({sign}{exact} s)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def preview(query: str, *, now: Optional[float] = None) -> List[Tuple[str, str, str]]:
    """Suggestions for the TUI and desktop app: (inserted text, display, meta)."""
    now = time.time() if now is None else now
    query = query.strip()
    if not query or "now".startswith(query.lower()):
        current = int(now)
        return [("now", f"now = {_clock(EPOCH + timedelta(seconds=current))} UTC", "current time"),
                (str(current), f"{current} = {_clock(EPOCH + timedelta(seconds=current))} UTC", "Unix seconds")]
    try:
        instant = parse(query, now=now)
    except ValueError:
        return []
    text = query if " " not in query else f'"{query}"'
    return [(text, f"{query} = {_clock(instant.moment)} UTC", instant.kind)]
