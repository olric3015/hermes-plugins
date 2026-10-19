"""Character-by-character breakdowns of text, and Unicode name lookups, for ``@unicode:``.

Standard library only (``unicodedata``): nothing here imports Hermes, so it runs unchanged under
the admission probe and in unit tests.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

MAX_LISTED = 200          # characters shown one per line; the notes still cover the whole text
MAX_TEXT = 10_000         # characters analysed at all
MAX_CODE_POINTS = 20      # U+XXXX values looked up in one reference

_CODE_POINT = re.compile(r"(?:U\+|u\+)([0-9A-Fa-f]{1,6})")
_CODE_POINT_LIST = re.compile(r"^\s*(?:(?:U\+|u\+)[0-9A-Fa-f]{1,6}[\s,]*)+$")

_CATEGORIES = {
    "Lu": "uppercase letter", "Ll": "lowercase letter", "Lt": "titlecase letter", "Lm": "modifier letter",
    "Lo": "letter", "Mn": "combining mark", "Mc": "spacing mark", "Me": "enclosing mark",
    "Nd": "digit", "Nl": "letter number", "No": "number", "Pc": "connector punctuation",
    "Pd": "dash", "Ps": "opening punctuation", "Pe": "closing punctuation", "Pi": "opening quote",
    "Pf": "closing quote", "Po": "punctuation", "Sm": "math symbol", "Sc": "currency symbol",
    "Sk": "modifier symbol", "So": "symbol", "Zs": "space", "Zl": "line separator",
    "Zp": "paragraph separator", "Cc": "control", "Cf": "format", "Cs": "surrogate",
    "Co": "private use", "Cn": "unassigned",
}
# unicodedata has no names for control characters.
_CONTROL_NAMES = {
    0x00: "NULL", 0x07: "BELL", 0x08: "BACKSPACE", 0x09: "CHARACTER TABULATION", 0x0A: "LINE FEED",
    0x0B: "LINE TABULATION", 0x0C: "FORM FEED", 0x0D: "CARRIAGE RETURN", 0x1B: "ESCAPE", 0x7F: "DELETE",
    0x85: "NEXT LINE",
}
# Letters and symbols that draw nothing although their category says they are visible.
_BLANK_LOOKING = {0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0}
_BIDI_CONTROLS = {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
_TAGS = range(0xE0000, 0xE0080)
# Scripts whose letters pass for Latin ones.
_LOOKALIKE_SCRIPTS = {"LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE", "COPTIC", "FULLWIDTH", "MATHEMATICAL"}


def name(cp: int) -> str:
    """Unicode's name for a code point, or a bracketed stand-in for the ones that have none."""
    found = unicodedata.name(chr(cp), "")
    if found:
        return found
    category = unicodedata.category(chr(cp))
    if category == "Cc":
        return _CONTROL_NAMES.get(cp, "<control>")
    return {"Cs": "<surrogate>", "Co": "<private use>"}.get(category, "<unassigned>")


def is_variation_selector(cp: int) -> bool:
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def is_invisible(cp: int) -> bool:
    """True for characters that draw nothing: format and control characters, line and paragraph
    separators, variation selectors, and the few letters that are blank by design."""
    category = unicodedata.category(chr(cp))
    return (category in {"Cf", "Cc", "Zl", "Zp"} or is_variation_selector(cp) or cp in _BLANK_LOOKING
            or cp == 0x034F or 0x180B <= cp <= 0x180F)


def script(cp: int) -> str:
    """The first word of a letter's Unicode name (LATIN, CYRILLIC, ...), "" for anything else."""
    if not unicodedata.category(chr(cp)).startswith("L"):
        return ""
    return name(cp).split(" ", 1)[0]


def _hex(cp: int) -> str:
    return f"U+{cp:04X}"


def _utf8(cp: int) -> str:
    return " ".join(f"{b:02X}" for b in chr(cp).encode("utf-8", "surrogatepass"))


def _shown(cp: int) -> str:
    """The character itself when it draws something on its own, otherwise nothing."""
    category = unicodedata.category(chr(cp))
    if is_invisible(cp) or category[0] in {"Z", "M", "C"}:
        return ""
    return chr(cp)


def _at(positions: List[int]) -> str:
    shown = ", ".join(str(p) for p in positions[:10])
    return shown + (f" and {len(positions) - 10} more" if len(positions) > 10 else "")


def _words(text: str) -> List[Tuple[int, str]]:
    """(1-based start, word) for each run of letters and combining marks."""
    return [(m.start() + 1, m.group()) for m in re.finditer(r"[^\W\d_]+", text)]


def notes(text: str) -> List[str]:
    """The findings worth a reader's attention, one sentence each."""
    groups: Dict[int, List[int]] = {}
    for i, ch in enumerate(text, 1):
        groups.setdefault(ord(ch), []).append(i)
    found: List[str] = []

    invisible = [(cp, at) for cp, at in groups.items() if is_invisible(cp) and cp not in _TAGS]
    if invisible:
        total = sum(len(at) for _, at in invisible)
        parts = [f"{_hex(cp)} {name(cp)} at {_at(at)}" for cp, at in sorted(invisible, key=lambda g: g[1][0])]
        found.append(f"{total} invisible character(s): " + "; ".join(parts) + ".")
    bidi = sorted(cp for cp in groups if cp in _BIDI_CONTROLS)
    if bidi:
        found.append("Text direction controls (" + ", ".join(_hex(cp) for cp in bidi) + ") change the order "
                     "the text is shown in, so what you see may not be the order of the characters.")
    if 0x200D in groups or 0xFE0F in groups:
        found.append("U+200D and U+FE0F are also how emoji are joined and styled; next to emoji they are expected.")
    tags = [ch for ch in text if ord(ch) in _TAGS]
    if tags:
        hidden = "".join(chr(ord(ch) - 0xE0000) for ch in tags if 0xE0020 <= ord(ch) <= 0xE007E)
        found.append(f"{len(tags)} Unicode tag character(s), which can carry hidden ASCII text"
                     + (f"; they spell {hidden!r}." if hidden else "."))
    spaces = [(cp, at) for cp, at in groups.items() if unicodedata.category(chr(cp)) == "Zs" and cp != 0x20]
    if spaces:
        found.append("Spaces other than the ordinary one: "
                     + "; ".join(f"{_hex(cp)} {name(cp)} at {_at(at)}" for cp, at in sorted(spaces, key=lambda g: g[1][0]))
                     + ".")
    for start, word in _words(text):
        scripts = {script(ord(ch)) for ch in word} - {""}
        if len(scripts) > 1 and "LATIN" in scripts and scripts <= _LOOKALIKE_SCRIPTS:
            odd = [(start + i, ch) for i, ch in enumerate(word) if script(ord(ch)) not in {"", "LATIN"}]
            listed = ", ".join(f"{ch} {_hex(ord(ch))} {name(ord(ch))} at {pos}" for pos, ch in odd[:5])
            found.append(f"The word {word!r} mixes {' and '.join(sorted(scripts))} letters: {listed}"
                         + (" and more" if len(odd) > 5 else "") + ".")
    for cp in sorted(groups):
        category = unicodedata.category(chr(cp))
        if category in {"Co", "Cn", "Cs"}:
            found.append(f"{_hex(cp)} is {_CATEGORIES[category]} at {_at(groups[cp])}: no font shows it the same way.")
    return found


def describe_text(text: str) -> str:
    """A table of every character of ``text`` (the first MAX_LISTED) with its code point, name and
    category, followed by notes on invisible characters, direction controls, unusual spaces and
    words that mix lookalike scripts."""
    if not text:
        raise ValueError("nothing to describe: put the text in quotes, for example @unicode:\"some text\"")
    cut = len(text) > MAX_TEXT
    text = text[:MAX_TEXT]
    size = len(text.encode("utf-8", "surrogatepass"))
    head = (f"Unicode breakdown of the quoted text: {len(text)} character(s), {size} UTF-8 byte(s)"
            + (f" (only the first {MAX_TEXT} characters were read)" if cut else "") + ".")
    rows = []
    for i, ch in enumerate(text[:MAX_LISTED], 1):
        cp = ord(ch)
        flag = "invisible" if is_invisible(cp) else ""
        rows.append([str(i), _shown(cp), _hex(cp), name(cp), unicodedata.category(ch), flag])
    widths = [max(len(r[c]) for r in rows + [["#", "char", "code", "name", "cat", ""]]) for c in range(6)]
    lines = [head, ""]
    for row in [["#", "char", "code", "name", "cat", ""]] + rows:
        cells = [row[0].rjust(widths[0])] + [row[c].ljust(widths[c]) for c in range(1, 6)]
        lines.append("  ".join(cells).rstrip())
    if len(text) > MAX_LISTED:
        lines.append(f"... {len(text) - MAX_LISTED} more character(s) not listed; the notes cover them.")
    found = notes(text)
    lines += ["", "Notes:"] + [f"- {n}" for n in found] if found else ["", "Nothing hidden: no invisible characters, "
                                                                        "direction controls, unusual spaces or "
                                                                        "lookalike letters."]
    return "\n".join(lines)


def describe_code_point(cp: int) -> str:
    if not 0 <= cp <= 0x10FFFF:
        return f"U+{cp:X} is not a Unicode code point (the last one is U+10FFFF)."
    category = unicodedata.category(chr(cp))
    lines = [f"{_hex(cp)}  {name(cp)}",
             f"  category {category} ({_CATEGORIES.get(category, category)})"
             + (", invisible" if is_invisible(cp) else "")
             + (", changes text direction" if cp in _BIDI_CONTROLS else ""),
             f"  UTF-8 {_utf8(cp)}"]
    if _shown(cp):
        lines.append(f"  looks like: {chr(cp)}")
    decomposition = unicodedata.decomposition(chr(cp))
    if decomposition:
        lines.append(f"  decomposes to: {decomposition}")
    return "\n".join(lines)


def code_points(target: str) -> Optional[List[int]]:
    """The code points of a target made only of ``U+XXXX`` values, None for any other target."""
    if not _CODE_POINT_LIST.match(target):
        return None
    return [int(h, 16) for h in _CODE_POINT.findall(target)]


def expand(target: str) -> str:
    """What ``@unicode:<target>`` puts into the message: code point details for ``U+XXXX``
    values, otherwise a breakdown of the text itself."""
    points = code_points(target)
    if points is None:
        return describe_text(target)
    if len(points) > MAX_CODE_POINTS:
        raise ValueError(f"too many code points: at most {MAX_CODE_POINTS} in one @unicode: reference")
    return "\n\n".join(describe_code_point(cp) for cp in points)


_index: List[Tuple[int, str]] = []


def _names() -> List[Tuple[int, str]]:
    """Every named code point, built once on first use (about a second)."""
    if not _index:
        for cp in range(0x110000):
            found = unicodedata.name(chr(cp), "")
            if found:
                _index.append((cp, found))
    return _index


def search(query: str, limit: int = 10) -> List[Tuple[int, str]]:
    """Named characters for a ``U+XXXX`` value or for words of their name, best matches first."""
    query = query.strip()
    exact = _CODE_POINT.fullmatch(query)
    if exact:
        cp = int(exact.group(1), 16)
        return [(cp, name(cp))] if cp <= 0x10FFFF else []
    words = query.upper().replace("_", " ").replace("-", " ").split()
    if not words or len("".join(words)) < 2:
        return []
    phrase = " ".join(words)
    hits = []
    for cp, n in _names():
        if not all(w in n for w in words):  # cheap substring pass first
            continue
        parts = n.replace("-", " ").split()
        # Each query word starts a word of the name: "zero width" finds ZERO WIDTH SPACE, not FULLWIDTH.
        if all(any(part.startswith(w) for part in parts) for w in words):
            exact = all(w in parts for w in words)
            hits.append(((n != phrase, not n.startswith(phrase), not exact, len(n), cp), cp, n))
    hits.sort()
    return [(cp, n) for _, cp, n in hits[:max(0, limit)]]
