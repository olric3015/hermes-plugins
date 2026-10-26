"""UUID and ULID decoding for ``@uuid:``: what an identifier says about itself. Nothing is looked up.

Standard library only (``re``, ``datetime``): nothing here imports Hermes, so it runs unchanged under
the admission probe and in unit tests. Every function that depends on the current time takes ``now``
(Unix seconds) so tests can pin it.
"""

from __future__ import annotations

import re
import time
from fractions import Fraction
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

MAX_VALUES = 10          # identifiers in one reference
MAX_TARGET_CHARS = 2_000  # a longer reference value is refused

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
GREGORIAN_OFFSET = 0x01B21DD213814000  # 100-nanosecond intervals from 1582-10-15 to 1970-01-01
MAX_INT = (1 << 128) - 1
_DAY = 86_400
_SKEW = 60  # seconds of clock difference that are not worth a note

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
_HYPHENATED = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}$")
_SPLIT = re.compile(r"[\s,]+")

# The namespace UUIDs RFC 9562 publishes for version 3 and 5 names (Appendix C).
_NAMESPACES = {
    0x6BA7B8109DAD11D180B400C04FD430C8: "DNS",
    0x6BA7B8119DAD11D180B400C04FD430C8: "URL",
    0x6BA7B8129DAD11D180B400C04FD430C8: "ISO OID",
    0x6BA7B8149DAD11D180B400C04FD430C8: "X.500 DN",
}
_VERSIONS = {
    1: "time-based: a 60-bit clock (100 ns steps since 1582) + clock sequence + node",
    2: "DCE Security: a v1 layout with a local user, group or org id in place of part of the clock",
    3: "name-based: MD5 of a namespace and a name",
    4: "random",
    5: "name-based: SHA-1 of a namespace and a name",
    6: "time-based, reordered: the v1 clock with its most significant bits first",
    7: "Unix time in milliseconds + random bits",
    8: "custom: laid out however its maker chose",
}
_DOMAINS = {0: "person (POSIX uid)", 1: "group (POSIX gid)", 2: "organization"}


class Item:
    """One identifier read from the reference."""

    def __init__(self, kind: str, value: int, written: str, form: List[str]):
        self.kind = kind      # "UUID" or "ULID"
        self.value = value    # the 128 bits as an integer
        self.written = written
        self.form = form      # how it was written, when that is not the canonical form

    @property
    def canonical(self) -> str:
        if self.kind == "ULID":
            return to_ulid(self.value)
        text = f"{self.value:032x}"
        return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def to_ulid(value: int) -> str:
    """128 bits as 26 Crockford base32 characters, the ULID text form."""
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def _read_ulid(text: str) -> int:
    upper = text.upper()
    if upper[0] > "7":
        raise ValueError(f"{text!r} is 26 characters like a ULID, but a ULID starts with 0-7; "
                         f"{upper[0]!r} would make it longer than 128 bits")
    value = 0
    for char in upper:
        value = value * 32 + _CROCKFORD.index(char)
    return value


def read(text: str) -> Item:
    """One UUID (any common spelling) or ULID. Raises ValueError with the reason."""
    written = text
    form = []
    if text[:9].lower() == "urn:uuid:":
        text = text[9:]
        form.append("as a URN (urn:uuid:)")
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
        form.append("in braces")
    elif text.startswith("{") or text.endswith("}"):
        raise ValueError(f"{written!r} has an unmatched brace")
    if len(text) == 26 and _ULID.match(text):
        if form:
            raise ValueError(f"{written!r} wraps a ULID; braces and urn:uuid: belong to UUIDs, write a ULID bare")
        return Item("ULID", _read_ulid(text), written, ["in lower case"] if text != text.upper() else [])
    if "-" in text:
        if not _HYPHENATED.match(text):
            digits = text.replace("-", "")
            if _HEX32.match(digits):
                raise ValueError(f"{written!r} has 32 hex digits but its hyphens are not in the 8-4-4-4-12 places")
            raise ValueError(_why_not(written, digits))
        digits = text.replace("-", "")
    else:
        digits = text
        if _HEX32.match(digits):
            form.append("without hyphens")
    if not _HEX32.match(digits):
        raise ValueError(_why_not(written, digits))
    if digits != digits.lower():
        form.append("in upper case")
    return Item("UUID", int(digits, 16), written, form)


def _why_not(written: str, digits: str) -> str:
    if len(digits) == 26 and set(digits.upper()) <= set(_CROCKFORD + "ILOU") and set(digits.upper()) & set("ILOU"):
        left_out = ", ".join(repr(c) for c in sorted({c for c in digits if c.upper() in "ILOU"}))
        return (f"{written!r} is 26 characters like a ULID, but ULIDs leave out I, L, O and U "
                f"(Crockford base32); this has {left_out}")
    stray = sorted({c for c in digits if c not in "0123456789abcdefABCDEF"})
    if stray:
        shown = ", ".join(repr(c) for c in stray[:5])
        return f"{written!r} is not a UUID: {shown} {'is not a hex digit' if len(stray) == 1 else 'are not hex digits'}"
    return f"a UUID has 32 hex digits (36 characters with hyphens); {written!r} has {len(digits)}"


def parse(target: str) -> List[Item]:
    """Every identifier in a reference value: separated by commas or (in quotes) spaces."""
    target = target.strip()
    if not target:
        raise ValueError("no UUID given: write @uuid:<uuid>, or @uuid:<ulid>")
    if len(target) > MAX_TARGET_CHARS:
        raise ValueError(f"the reference is {len(target):,} characters long; uuid-ref reads at most {MAX_TARGET_CHARS:,}")
    values = [v for v in _SPLIT.split(target) if v]
    if len(values) > MAX_VALUES:
        raise ValueError(f"{len(values)} identifiers given; uuid-ref reads at most {MAX_VALUES} per reference")
    return [read(v) for v in values]


def variant(value: int) -> str:
    """The variant field: "ncs", "rfc", "microsoft" or "future"."""
    top = (value >> 61) & 0b111
    if top < 0b100:
        return "ncs"
    if top < 0b110:
        return "rfc"
    return "microsoft" if top == 0b110 else "future"


def version(value: int) -> int:
    return (value >> 76) & 0xF


def gregorian_ticks(value: int) -> Optional[int]:
    """The 60-bit clock of a v1 or v6 UUID (100 ns steps since 1582-10-15), else None."""
    ver = version(value)
    if ver in (1, 2):
        return (((value >> 64) & 0x0FFF) << 48) | (((value >> 80) & 0xFFFF) << 32) | (value >> 96)
    if ver == 6:
        return ((value >> 80) << 12) | ((value >> 64) & 0x0FFF)
    return None


def unix_ms(value: int) -> int:
    """The 48-bit millisecond clock at the front of a v7 UUID or a ULID."""
    return value >> 80


def clock_seq(value: int) -> int:
    return (value >> 48) & 0x3FFF


def node(value: int) -> int:
    return value & 0xFFFFFFFFFFFF


def _clock(seconds: int, fraction: str) -> Optional[str]:
    try:
        moment = EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        return None  # past 9999-12-31, which datetime cannot hold
    fraction = fraction.rstrip("0")
    return moment.strftime("%Y-%m-%d %H:%M:%S") + (f".{fraction}" if fraction else "") + " UTC"


def span(seconds: float) -> str:
    """A duration in words, two units at most: '3 days 4 hours', '2 minutes 5 seconds'."""
    seconds = abs(seconds)
    if seconds < 1:
        return "0 seconds"
    whole = int(seconds)
    if whole >= 365 * _DAY:
        return f"about {whole / (365.2425 * _DAY):.1f} years ({whole // _DAY:,} days)"
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


def _relative(seconds: float, now: float) -> str:
    delta = seconds - now
    if abs(delta) < 1:
        return "now"
    return f"in {span(delta)}" if delta > 0 else f"{span(delta)} ago"


def created(item: Item) -> Optional[Fraction]:
    """When a time-based identifier says it was made, in Unix seconds; None for the others."""
    if item.kind == "ULID":
        return Fraction(unix_ms(item.value), 1000)
    if variant(item.value) != "rfc":
        return None
    ver = version(item.value)
    if ver == 7:
        return Fraction(unix_ms(item.value), 1000)
    if ver in (1, 6):
        return Fraction(gregorian_ticks(item.value) - GREGORIAN_OFFSET, 10_000_000)
    return None


def _time_rows(item: Item, now: float) -> Tuple[List[Tuple[str, str]], List[str]]:
    rows, notes = [], []
    if item.kind == "ULID" or version(item.value) == 7:
        ms = unix_ms(item.value)
        seconds = ms / 1000
        shown = _clock(ms // 1000, f"{ms % 1000:03d}")
        if shown is None:
            rows.append(("created", f"{ms} ms since 1970, after the year 9999"))
        else:
            rows.append(("created", f"{shown}, {_relative(seconds, now)}"))
        rows.append(("clock", f"{ms} ms since 1970-01-01 (48 bits)"))
    else:
        ticks = gregorian_ticks(item.value)
        since = ticks - GREGORIAN_OFFSET
        seconds = since / 10_000_000
        whole, rest = divmod(since, 10_000_000)
        rows.append(("created", f"{_clock(whole, f'{rest:07d}')}, {_relative(seconds, now)}"))
        rows.append(("clock", f"{ticks} x 100 ns since 1582-10-15 (60 bits)"))
    if seconds > now + _SKEW:
        notes.append("the time is in the future: the maker's clock was off, or these bits were not made from a clock")
    elif seconds < 0:
        notes.append("the time is before 1970: the bits were probably not made from a clock")
    return rows, notes


def _node_rows(value: int) -> Tuple[List[Tuple[str, str]], List[str]]:
    raw = node(value)
    mac = ":".join(f"{(raw >> (8 * i)) & 0xFF:02x}" for i in range(5, -1, -1))
    rows = [("clock seq", f"{clock_seq(value)} (14 bits; changes when the clock goes back or the node changes)")]
    if (raw >> 40) & 1:
        rows.append(("node", f"{mac}: random (the multicast bit is set), not a network card address"))
        return rows, []
    rows.append(("node", f"{mac}: looks like a network card (MAC) address"))
    return rows, ["the node is a MAC address: this UUID tells which machine made it, and when"]


def _uuid_rows(item: Item, now: float) -> Tuple[List[Tuple[str, str]], List[str]]:
    value = item.value
    if value == 0:
        return [("kind", "Nil UUID: all 128 bits zero, the placeholder for 'no UUID' (RFC 9562 5.9)")], []
    if value == MAX_INT:
        return [("kind", "Max UUID: all 128 bits one, the 'largest UUID' sentinel (RFC 9562 5.10)")], []
    ver, var = version(value), variant(value)
    rows: List[Tuple[str, str]] = []
    notes: List[str] = []
    if var != "rfc":
        names = {"ncs": "NCS (reserved: Apollo Network Computing System IDs from the 1980s)",
                 "microsoft": "Microsoft (reserved: old COM/DCOM GUIDs)",
                 "future": "reserved for future definition"}
        rows.append(("variant", names[var]))
        rows.append(("version", f"digit {ver:x}, which has no defined meaning outside the RFC 9562 variant"))
        notes.append("not an RFC 9562 UUID: nothing more can be read from its bits")
        return rows, notes
    if ver not in _VERSIONS:
        rows.append(("version", f"{ver}: not defined (RFC 9562 defines versions 1 to 8)"))
        rows.append(("variant", "RFC 9562 (the standard layout)"))
        return rows, notes
    rows.append(("version", f"{ver}: {_VERSIONS[ver]}"))
    rows.append(("variant", "RFC 9562 (the standard layout)"))
    if ver in (1, 6, 7):
        time_rows, time_notes = _time_rows(item, now)
        rows += time_rows
        notes += time_notes
    if ver in (1, 6):
        node_rows, node_notes = _node_rows(value)
        rows += node_rows
        notes += node_notes
    if ver == 2:
        domain = (value >> 48) & 0xFF
        ticks = gregorian_ticks(value) - GREGORIAN_OFFSET
        whole = ticks // 10_000_000
        rows.append(("local id", f"{value >> 96} in domain {domain} ({_DOMAINS.get(domain, 'not a standard domain')})"))
        rows.append(("created", f"about {_clock(whole, '')} (the low 32 clock bits hold the id, so only to "
                                f"within about 7 minutes)"))
        node_rows, node_notes = _node_rows(value)
        rows += node_rows[1:]
        notes += node_notes
    if ver == 4:
        rows.append(("random", "122 bits"))
    if ver == 7:
        rows.append(("random", "74 bits (or a counter in the first 12, as some makers do)"))
    if ver in (3, 5):
        notes.append("the same namespace and name always give this UUID; the name cannot be read back from the hash")
    if ver == 1:
        notes.append("v1 UUIDs do not sort by time as text (the low clock bits come first); v6 and v7 do")
    if ver in (6, 7):
        notes.append("sorts by creation time, as text and as bytes")
    if value in _NAMESPACES:
        notes.append(f"this is the {_NAMESPACES[value]} namespace UUID from RFC 9562, used to make v3 and v5 UUIDs "
                     f"from {_NAMESPACES[value]} names")
    return rows, notes


def _ulid_rows(item: Item, now: float) -> Tuple[List[Tuple[str, str]], List[str]]:
    rows, notes = _time_rows(item, now)
    rows.append(("random", "80 bits"))
    text = f"{item.value:032x}"
    rows.append(("as UUID", f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]} "
                            f"(the same 128 bits, as a UUID column stores them)"))
    notes.append("sorts by creation time, as text and as bytes")
    notes.append("stored as a UUID its version and variant digits are random bits, not a real UUID version")
    return rows, notes


def describe(item: Item, now: float) -> List[str]:
    rows, notes = _ulid_rows(item, now) if item.kind == "ULID" else _uuid_rows(item, now)
    lines = [f"{item.kind} {item.canonical}"]
    if item.form:
        rows.insert(0, ("written", ", ".join(item.form)))
    width = max(len(label) for label, _ in rows)
    lines += [f"  {label.ljust(width)}  {shown}" for label, shown in rows]
    if notes:
        lines.append("Notes")
        lines += [f"  - {note}" for note in notes]
    return lines


def expand(target: str, *, now: Optional[float] = None) -> str:
    """What ``@uuid:<target>`` puts into the message."""
    now = time.time() if now is None else now
    items = parse(target)
    if len(items) == 1:
        return "\n".join(describe(items[0], now))
    blocks = []
    for number, item in enumerate(items, start=1):
        lines = describe(item, now)
        blocks.append("\n".join([f"{number}. {lines[0]}"] + lines[1:]))
    out = "\n\n".join(blocks)
    timed = [(number, created(item)) for number, item in enumerate(items, start=1)]
    timed = [(number, seconds) for number, seconds in timed if seconds is not None]
    if len(timed) >= 2:
        lines = ["", "Differences in creation time"]
        for (a, first), (b, second) in zip(timed, timed[1:]):
            delta = second - first
            if delta == 0:
                lines.append(f"  {b} carries the same time as {a}")
                continue
            gap = abs(delta)
            if gap < 1:
                gap = f"{float(round(gap * 1000, 4)):g} ms"
            elif gap < 60:
                gap = f"{float(round(gap, 4)):g} seconds"
            else:
                gap = span(float(gap))
            lines.append(f"  {b} was made {gap} {'after' if delta > 0 else 'before'} {a}")
        out += "\n" + "\n".join(lines)
    return out


def preview(query: str, *, now: Optional[float] = None) -> List[Tuple[str, str, str]]:
    """Suggestions for the TUI and desktop app: (inserted text, display, meta)."""
    now = time.time() if now is None else now
    try:
        items = parse(query)
    except ValueError:
        return []
    if len(items) != 1:
        return []
    item = items[0]
    seconds = created(item)
    if item.kind == "ULID":
        bits = ["ULID"]
    elif item.value in (0, MAX_INT):
        bits = ["Nil UUID" if item.value == 0 else "Max UUID"]
    elif variant(item.value) != "rfc":
        bits = [f"{variant(item.value)} variant"]
    else:
        bits = [f"v{version(item.value)}"]
        if version(item.value) == 4:
            bits.append("random")
        elif version(item.value) in (3, 5):
            bits.append("name-based")
    if seconds is not None:
        shown = _clock(int(seconds // 1), "")
        if shown is not None:
            bits.append(f"created {shown}, {_relative(seconds, now)}")
    return [(item.canonical, ", ".join(bits), item.kind)]
