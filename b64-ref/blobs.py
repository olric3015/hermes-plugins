"""Decoding for ``@b64:``: base64, base64url, hex, percent-encoding and data: URIs, made readable.

Standard library only (``base64``, ``binascii``, ``json``, ``urllib.parse``, ``zlib``): nothing here
imports Hermes, so it runs unchanged under the admission probe and in unit tests. Nothing decoded is
run, opened or written anywhere; gzip and zlib data is inflated in memory, at most 1 MiB.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import unicodedata
import urllib.parse
import zlib
from typing import List, Optional, Tuple

MAX_INPUT_CHARS = 65_536   # a value longer than this is refused, not decoded
MAX_INFLATED = 1_048_576   # gzip/zlib output is cut at this many bytes
MAX_TEXT_CHARS = 3_000     # decoded text shown; the rest is counted
MAX_TEXT_LINES = 60
DUMP_BYTES = 128           # bytes of a binary result shown as a hex dump
_SHORT = 40                # characters of text in a suggestion

_FORCED = re.compile(r"^(base64url|base64|hex|url):", re.IGNORECASE)
_DATA_URI = re.compile(r"^data:([^,]*),(.*)$", re.IGNORECASE | re.DOTALL)
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2})+$")
_B64_STD = re.compile(r"^[A-Za-z0-9+/]*$")
_B64_URL = re.compile(r"^[A-Za-z0-9_-]*$")
_B64_ANY = re.compile(r"^[A-Za-z0-9+/_-]+$")
_JWT = re.compile(r"^eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
_WHITESPACE = re.compile(r"\s+")


class Decoded:
    """One decoded value: how it was read, what it held, and anything worth a note."""

    def __init__(self, label: str, chars: int, data: bytes, notes: List[str]):
        self.label = label    # "base64 (standard, padded)", "hex", "percent-encoding", ...
        self.chars = chars    # characters read, separators and whitespace not counted
        self.data = data
        self.notes = notes


# --- reading the value -------------------------------------------------------------------------

def _hex(text: str, notes: List[str]) -> Decoded:
    body = text[2:] if text[:2].lower() == "0x" else text
    body = body.replace("\\x", "")
    body = _WHITESPACE.sub("", body.replace(":", ""))
    if not _HEX.match(body):
        if re.fullmatch(r"[0-9A-Fa-f]+", body):
            raise ValueError(f"hex needs two digits per byte; this has {len(body)} digits")
        bad = next(ch for ch in body if ch not in "0123456789abcdefABCDEF")
        raise ValueError(f"{bad!r} is not a hex digit")
    return Decoded("hex", len(body), bytes.fromhex(body), notes)


def _base64(text: str, notes: List[str], forced: Optional[str] = None) -> Decoded:
    body = _WHITESPACE.sub("", text)
    stripped = body.rstrip("=")
    padding = len(body) - len(stripped)
    if not stripped:
        raise ValueError("nothing to decode")
    has_std = bool(re.search(r"[+/]", stripped))
    has_url = bool(re.search(r"[-_]", stripped))
    if has_std and has_url:
        raise ValueError("this mixes the standard base64 alphabet ('+', '/') with the URL-safe one ('-', '_')")
    url = has_url or (forced == "base64url" and not has_std)
    pattern = _B64_URL if url else _B64_STD
    if "=" in stripped:
        raise ValueError("'=' may only appear at the end of base64, as padding")
    if not pattern.match(stripped):
        bad = next(ch for ch in stripped if not pattern.match(ch))
        raise ValueError(f"{bad!r} is not a base64 character")
    if padding > 2:
        raise ValueError(f"base64 ends in at most two '='; this ends in {padding}")
    if len(stripped) % 4 == 1:
        raise ValueError(f"base64 cannot be {len(stripped):,} characters long (one character is left over); "
                         "it was probably cut short")
    if padding and len(body) % 4:
        notes.append(f"the padding is off: {padding} '=' for {len(stripped):,} characters (decoded anyway)")
    try:
        data = base64.b64decode(stripped + "=" * (-len(stripped) % 4),
                                altchars=b"-_" if url else None, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"not valid base64: {exc}")
    again = base64.b64encode(data, altchars=b"-_" if url else None).decode("ascii").rstrip("=")
    if again != stripped:
        notes.append("the last character has bits set that decoding drops: the text was probably cut or edited")
    label = "base64url" if url else "base64"
    if len(stripped) % 4 and not padding:
        label += ", unpadded"
    return Decoded(label, len(stripped), data, notes)


def _percent(text: str, notes: List[str]) -> Decoded:
    if "+" in text:
        notes.append("'+' is kept as '+'; in a form-encoded query string it stands for a space")
    for match in re.finditer(r"%(?![0-9A-Fa-f]{2})", text):
        notes.append(f"a '%' at position {match.start() + 1} is not followed by two hex digits and is kept as is")
        break
    return Decoded("percent-encoding", len(text), urllib.parse.unquote_to_bytes(text), notes)


def _data_uri(match: re.Match, notes: List[str]) -> Decoded:
    meta, payload = match.group(1), match.group(2)
    params = [p.strip() for p in meta.split(";")]
    is_base64 = params[-1].lower() == "base64"
    mime = params[0] or "text/plain"
    if is_base64:
        inner = _base64(urllib.parse.unquote(payload), notes)
        kind = "base64"
    else:
        inner = _percent(payload, notes)
        kind = "percent-encoded"
    return Decoded(f"data: URI ({mime}, {kind})", inner.chars, inner.data, inner.notes)


def clean(target: str) -> str:
    """The value of a reference: surrounding space and one pair of quotes removed."""
    text = target.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    return text


def read(target: str) -> Decoded:
    """Decode a value, telling the encoding by its shape. Raises ValueError with the reason."""
    text = clean(target)
    if not text:
        raise ValueError("nothing to decode: write @b64:<base64, base64url, hex or percent-encoded text>")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"the value is {len(text):,} characters long; b64-ref reads at most {MAX_INPUT_CHARS:,}")
    notes: List[str] = []
    forced = _FORCED.match(text)
    if forced:
        kind, body = forced.group(1).lower(), text[forced.end():]
        if kind == "hex":
            return _hex(body, notes)
        if kind == "url":
            return _percent(body, notes)
        return _base64(body, notes, forced=kind)
    data_uri = _DATA_URI.match(text)
    if data_uri:
        return _data_uri(data_uri, notes)
    if _PERCENT.search(text):
        return _percent(text, notes)
    compact = _WHITESPACE.sub("", text.replace(":", ""))
    if text[:2].lower() == "0x" or "\\x" in text or (len(compact) >= 2 and _HEX.match(compact)):
        decoded = _hex(text, notes)
        if not text.lower().startswith(("0x", "\\x")) and ":" not in text and len(compact) % 4 != 1:
            notes.append("read as hex; it is also valid base64, which @b64:base64:<value> reads instead")
        return decoded
    body = _WHITESPACE.sub("", text)
    if re.fullmatch(r"[A-Za-z0-9+/_=-]+", body):
        return _base64(text, notes)
    bad = next((ch for ch in body if not re.match(r"[A-Za-z0-9+/_=-]", ch)), None)
    where = f": {bad!r} belongs to none of them" if bad else ""
    raise ValueError(f"this is not base64, base64url, hex or percent-encoding{where}")


# --- describing what it decoded to -------------------------------------------------------------

def _png(data: bytes) -> str:
    if len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return f"PNG image, {width} x {height} pixels"
    return "PNG image"


def _gif(data: bytes) -> str:
    if len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return f"GIF image, {width} x {height} pixels"
    return "GIF image"


def _pdf(data: bytes) -> str:
    version = re.match(rb"%PDF-(\d\.\d)", data)
    return f"PDF document, version {version.group(1).decode()}" if version else "PDF document"


# Signatures that text never starts with, checked before the text test.
_STRONG = (
    (b"\x89PNG\r\n\x1a\n", _png),
    (b"GIF87a", _gif),
    (b"GIF89a", _gif),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"%PDF-", _pdf),
    (b"PK\x03\x04", "ZIP archive (also .docx, .xlsx, .jar, .apk)"),
    (b"PK\x05\x06", "ZIP archive, empty"),
    (b"\x1f\x8b", "gzip data"),
    (b"BZh", "bzip2 data"),
    (b"\xfd7zXZ\x00", "xz data"),
    (b"(\xb5/\xfd", "zstd data"),
    (b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    (b"\x7fELF", "ELF executable or library"),
    (b"\xca\xfe\xba\xbe", "Java class file or macOS universal binary"),
    (b"\xcf\xfa\xed\xfe", "macOS executable (Mach-O, 64-bit)"),
    (b"\xce\xfa\xed\xfe", "macOS executable (Mach-O, 32-bit)"),
    (b"\x00asm", "WebAssembly module"),
    (b"SQLite format 3\x00", "SQLite database"),
    (b"OggS", "Ogg audio or video"),
    (b"ID3", "MP3 audio"),
)


def _riff(data: bytes) -> Optional[str]:
    if data[:4] == b"RIFF" and len(data) >= 12:
        return {b"WEBP": "WebP image", b"WAVE": "WAV audio", b"AVI ": "AVI video"}.get(data[8:12], "RIFF data")
    if data[4:8] == b"ftyp":
        return "MP4 / QuickTime media"
    return None


def kind_of(data: bytes) -> Optional[str]:
    """A known file format by its first bytes, or None."""
    for magic, what in _STRONG:
        if data.startswith(magic):
            return what(data) if callable(what) else what
    return _riff(data)


def _weak_kind(data: bytes) -> Optional[str]:
    """Formats whose signature is short enough for text to start with it, checked after the text test."""
    if len(data) >= 2 and data[0] == 0x78 and (data[0] * 256 + data[1]) % 31 == 0:
        return "zlib data"
    if data[:2] == b"MZ":
        return "Windows executable (PE)"
    if len(data) >= 2 and data[0] == 0x30 and data[1] in (0x81, 0x82, 0x83):
        return "DER (ASN.1): a certificate, key or signature in binary form"
    return None


def _text(data: bytes) -> Optional[Tuple[str, Optional[str]]]:
    """The bytes as text, with the encoding when it is not plain UTF-8; None for binary."""
    for bom, codec in ((b"\xef\xbb\xbf", "UTF-8 with a byte order mark"),
                       (b"\xff\xfe", "UTF-16 (little-endian)"), (b"\xfe\xff", "UTF-16 (big-endian)")):
        if data.startswith(bom):
            try:
                text = data[len(bom):].decode({"UTF-8 with a byte order mark": "utf-8",
                                               "UTF-16 (little-endian)": "utf-16-le",
                                               "UTF-16 (big-endian)": "utf-16-be"}[codec])
            except UnicodeDecodeError:
                return None
            return text, codec
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None
    controls = sum(1 for ch in text if unicodedata.category(ch) == "Cc" and ch not in "\t\n\r")
    if controls > max(1, len(text) // 20):
        return None
    return text, None


def _escape(line: str) -> Tuple[str, int]:
    out, hidden = [], 0
    for ch in line:
        if (unicodedata.category(ch) in ("Cc", "Cf") and ch != "\t") or ch == "\r":
            out.append(f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")
            hidden += 1
        else:
            out.append(ch)
    return "".join(out), hidden


def _text_lines(text: str) -> Tuple[List[str], int, int]:
    """Indented display lines, the count of characters left out, and how many were escaped."""
    text = text.replace("\r\n", "\n")
    shown, rest = text[:MAX_TEXT_CHARS], len(text) - min(len(text), MAX_TEXT_CHARS)
    lines = shown.split("\n")
    if len(lines) > 1 and not lines[-1] and not rest:
        lines.pop()  # a final line break, not an empty line
    if len(lines) > MAX_TEXT_LINES:
        rest += len("\n".join(lines[MAX_TEXT_LINES:])) + 1
        lines = lines[:MAX_TEXT_LINES]
    out, hidden = [], 0
    for line in lines:
        escaped, count = _escape(line)
        hidden += count
        out.append(f"  {escaped}")
    return out, rest, hidden


def _json_shape(value) -> str:
    if isinstance(value, dict):
        return f"JSON object, {len(value)} key{'' if len(value) == 1 else 's'}"
    return f"JSON array, {len(value)} item{'' if len(value) == 1 else 's'}"


def _another_layer(text: str) -> Optional[str]:
    """What the text decodes to when it is itself base64 of something readable, else None."""
    body = text.strip()
    if len(body) < 8 or not _B64_ANY.match(body.rstrip("=")) or "\n" in body:
        return None
    try:
        inner = _base64(body, []).data
    except ValueError:
        return None
    known = kind_of(inner)
    if known:
        return known
    as_text = _text(inner)
    if as_text and len(as_text[0].strip()) >= 4 and as_text[0].isprintable():
        return "text"
    return None


def _inflate(data: bytes, wbits: int) -> Tuple[bytes, bool]:
    engine = zlib.decompressobj(wbits)
    out = engine.decompress(data, MAX_INFLATED)
    return out, bool(engine.unconsumed_tail)


def _dump(data: bytes) -> List[str]:
    lines = []
    for offset in range(0, min(len(data), DUMP_BYTES), 16):
        chunk = data[offset:offset + 16]
        hexes = " ".join(f"{b:02x}" for b in chunk)
        ascii_ = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in chunk)
        lines.append(f"  {offset:08x}  {hexes:<47}  {ascii_}")
    if len(data) > DUMP_BYTES:
        lines.append(f"  ... {len(data) - DUMP_BYTES:,} more bytes")
    return lines


def _content(data: bytes, notes: List[str], depth: int = 0) -> List[str]:
    if not data:
        return ["Content: empty (0 bytes)"]
    known = kind_of(data)
    if known in ("gzip data",) or (known is None and _text(data) is None and _weak_kind(data) == "zlib data"):
        what = known or "zlib data"
        try:
            inflated, cut = _inflate(data, 31 if what == "gzip data" else 15)
        except zlib.error as exc:
            notes.append(f"{what} that does not inflate: {exc}")
            return [f"Content: {what}, {len(data):,} bytes", "First bytes (hex)", *_dump(data)]
        if cut:
            notes.append(f"inflated output is cut at {MAX_INFLATED:,} bytes")
        head = f"Content: {what}, inflates to {len(inflated):,} bytes"
        if depth:
            return [head]
        inner = _content(inflated, notes, depth + 1)
        return [head, *[line.replace("Content: ", "Inflated: ", 1) for line in inner]]
    if known:
        return [f"Content: {known}, {len(data):,} bytes", "First bytes (hex)", *_dump(data)]
    as_text = _text(data)
    if as_text is None:
        weak = _weak_kind(data)
        head = f"Content: {weak}, {len(data):,} bytes" if weak else f"Content: binary, {len(data):,} bytes (no known format)"
        return [head, "First bytes (hex)", *_dump(data)]
    text, encoding = as_text
    if encoding:
        notes.append(f"the text is {encoding}")
    stripped = text.strip()
    try:
        parsed = json.loads(stripped) if stripped[:1] in "{[" else None
    except ValueError:
        parsed = None
    count = max(1, len(text.splitlines()))
    if isinstance(parsed, (dict, list)):
        head = f"Content: {_json_shape(parsed)}"
        body = json.dumps(parsed, indent=2, ensure_ascii=False)
    else:
        head = f"Content: text, {len(text):,} characters, {count:,} line{'' if count == 1 else 's'}"
        body = text
    lines, rest, hidden = _text_lines(body)
    out = [head, "Text" if parsed is None else "JSON", *lines]
    if rest:
        out.append(f"  ... {rest:,} more characters")
    if hidden:
        notes.append(f"{hidden} control or invisible character{'' if hidden == 1 else 's'} shown as \\x.. / \\u....")
    if _JWT.match(stripped):
        notes.append("the text is a JWT: @jwt:<token> decodes its header and claims")
    elif depth == 0:
        layer = _another_layer(stripped)
        if layer:
            notes.append(f"the text is base64 again ({layer} inside): @b64:<text> decodes the next layer")
        elif _PERCENT.search(stripped) and urllib.parse.unquote(stripped) != stripped:
            notes.append("the text is still percent-encoded (encoded twice): @b64:<text> decodes the next layer")
    return out


def describe(decoded: Decoded) -> str:
    notes = list(decoded.notes)
    lines = [f"{decoded.label}: {decoded.chars:,} characters -> {len(decoded.data):,} bytes"]
    lines += _content(decoded.data, notes)
    if notes:
        lines.append("Notes")
        lines += [f"  - {note}" for note in notes]
    return "\n".join(lines)


def expand(target: str) -> str:
    """What ``@b64:<target>`` puts into the message."""
    return describe(read(target))


def _short(data: bytes) -> str:
    known = kind_of(data)
    if known:
        return known
    as_text = _text(data)
    if as_text is None:
        return _weak_kind(data) or "binary"
    text = as_text[0].strip()
    if text[:1] in "{[":
        try:
            return _json_shape(json.loads(text))
        except ValueError:
            pass
    shown, _ = _escape(text[:_SHORT].replace("\r\n", " ").replace("\n", " "))
    return f'text "{shown}{"..." if len(text) > _SHORT else ""}"'


def preview(query: str) -> List[Tuple[str, str, str]]:
    """Suggestions for the TUI and desktop app: (inserted text, display, meta)."""
    text = clean(query)
    try:
        decoded = read(text)
    except ValueError:
        return []
    if re.search(r"[\s()\[\]{}<>\"'`]", text):
        inserted = f'"{text}"' if '"' not in text else f"'{text}'"
    else:
        inserted = text
    display = f"{decoded.label} -> {len(decoded.data):,} bytes, {_short(decoded.data)}"
    return [(inserted, display, "decoded")]
