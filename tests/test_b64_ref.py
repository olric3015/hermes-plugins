"""b64-ref: what an @b64: reference expands to, and the suggestions it offers."""

import asyncio
import base64
import binascii
import gzip
import json
import random
import re
import struct
import sys
import types
import urllib.parse
import zlib

import pytest


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _section(out, title):
    """The indented lines under one heading ("Text", "JSON", "First bytes (hex)", "Notes")."""
    lines, inside = [], False
    for line in out.splitlines():
        if not line.startswith(" "):
            inside = line.startswith(title)
            continue
        if inside:
            lines.append(line)
    return lines


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 64, 32)
       + b"\x08\x06\x00\x00\x00" + b"\x00" * 8)


# --- round trips against the standard library ---------------------------------------------------

def _samples():
    rng = random.Random(20261025)
    yield b""[:0] + b"\x00"
    for size in (1, 2, 3, 4, 5, 15, 16, 17, 64, 255, 1000):
        yield bytes(rng.randrange(256) for _ in range(size))


@pytest.mark.parametrize("data", list(_samples()))
def test_round_trips_match_the_standard_library(blobs, data):
    std = base64.b64encode(data).decode()
    url = base64.urlsafe_b64encode(data).decode()
    assert blobs.read(std).data == data
    assert blobs.read("base64:" + std.rstrip("=")).data == data
    assert blobs.read("base64url:" + url.rstrip("=")).data == data
    assert blobs.read("hex:" + binascii.hexlify(data).decode()).data == data
    assert blobs.read("0x" + data.hex().upper()).data == data
    assert blobs.read("url:" + urllib.parse.quote_from_bytes(data, safe="")).data == data


def test_url_safe_alphabet_is_recognised_without_a_prefix(blobs):
    data = bytes([0xfb, 0xff, 0xbf, 0xfe])
    url = base64.urlsafe_b64encode(data).decode().rstrip("=")
    assert "-" in url or "_" in url
    decoded = blobs.read(url)
    assert decoded.data == data and decoded.label == "base64url, unpadded"


# --- what the expansion says ----------------------------------------------------------------------

def test_plain_text(blobs):
    assert blobs.expand("SGVsbG8sIHdvcmxkIQ==").splitlines() == [
        "base64: 18 characters -> 13 bytes",
        "Content: text, 13 characters, 1 line",
        "Text",
        "  Hello, world!",
    ]


def test_unpadded_value_says_so(blobs):
    assert blobs.expand("SGVsbG8sIHdvcmxkIQ").splitlines()[0] == "base64, unpadded: 18 characters -> 13 bytes"


def test_json_is_pretty_printed(blobs):
    value = _b64(json.dumps({"user": "ana", "roles": ["admin"]}).encode())
    out = blobs.expand(value)
    assert out.splitlines()[1] == "Content: JSON object, 2 keys"
    assert _section(out, "JSON") == ["  {", '    "user": "ana",', '    "roles": [', '      "admin"', "    ]", "  }"]


def test_json_array_shape(blobs):
    assert "Content: JSON array, 1 item" in blobs.expand(_b64(b'["x"]'))


def test_png_is_named_with_its_size_and_dumped(blobs):
    out = blobs.expand(_b64(PNG))
    assert out.splitlines()[1] == f"Content: PNG image, 64 x 32 pixels, {len(PNG)} bytes"
    assert _section(out, "First bytes")[0] == (
        "  00000000  89 50 4e 47 0d 0a 1a 0a 00 00 00 0d 49 48 44 52  .PNG........IHDR")


def test_gzip_is_inflated_and_described(blobs):
    out = blobs.expand(_b64(gzip.compress(b'{"event": "deploy"}')))
    lines = out.splitlines()
    assert lines[1] == "Content: gzip data, inflates to 19 bytes"
    assert lines[2] == "Inflated: JSON object, 1 key"
    assert '    "event": "deploy"' in lines


def test_zlib_is_inflated(blobs):
    out = blobs.expand(_b64(zlib.compress(b"hello zlib")))
    assert "Content: zlib data, inflates to 10 bytes" in out
    assert _section(out, "Text") == ["  hello zlib"]


def test_broken_gzip_is_reported_not_raised(blobs):
    out = blobs.expand(_b64(b"\x1f\x8b\x08\x00garbage-not-deflate"))
    assert out.splitlines()[1].startswith("Content: gzip data, ")
    assert any("gzip data that does not inflate" in line for line in _section(out, "Notes"))


def test_inflating_is_bounded(blobs, monkeypatch):
    monkeypatch.setattr(blobs, "MAX_INFLATED", 1000)
    out = blobs.expand(_b64(gzip.compress(b"a" * 50_000)))
    assert "Content: gzip data, inflates to 1,000 bytes" in out
    assert "  - inflated output is cut at 1,000 bytes" in out


def test_binary_without_a_known_format_is_dumped(blobs):
    out = blobs.expand(_b64(bytes(range(200))))
    assert out.splitlines()[1] == "Content: binary, 200 bytes (no known format)"
    dump = _section(out, "First bytes")
    assert len(dump) == 9 and dump[-1] == "  ... 72 more bytes"
    assert dump[2] == "  00000020  20 21 22 23 24 25 26 27 28 29 2a 2b 2c 2d 2e 2f   !\"#$%&'()*+,-./"


@pytest.mark.parametrize("data, expected", [
    (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", "PDF document, version 1.7"),
    (b"PK\x03\x04\x14\x00", "ZIP archive (also .docx, .xlsx, .jar, .apk)"),
    (b"\x7fELF\x02\x01", "ELF executable or library"),
    (b"GIF89a\x10\x00\x20\x00", "GIF image, 16 x 32 pixels"),
    (b"\xff\xd8\xff\xe0", "JPEG image"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "WebP image"),
    (b"SQLite format 3\x00\x10\x00", "SQLite database"),
    (b"\x00asm\x01\x00\x00\x00", "WebAssembly module"),
    (b"MZ\x90\x00\x03\x00", "Windows executable (PE)"),
    (b"\x30\x82\x01\x0a\x02\x82", "DER (ASN.1): a certificate, key or signature in binary form"),
])
def test_known_formats(blobs, data, expected):
    assert blobs.expand(_b64(data)).splitlines()[1] == f"Content: {expected}, {len(data)} bytes"


def test_text_starting_like_zlib_stays_text(blobs):
    # "x^" is a valid zlib header, but readable text is shown as text.
    out = blobs.expand(_b64(b"x^2 + y^2"))
    assert _section(out, "Text") == ["  x^2 + y^2"]


def test_empty_result(blobs):
    assert blobs.expand("data:,").splitlines() == [
        "data: URI (text/plain, percent-encoded): 0 characters -> 0 bytes", "Content: empty (0 bytes)"]


def test_hex_forms(blobs):
    for value in ("48656c6c6f", "0x48656c6c6f", "48:65:6c:6c:6f", "48 65 6c 6c 6f", "\\x48\\x65\\x6c\\x6c\\x6f"):
        assert blobs.read(value).data == b"Hello", value


def test_ambiguous_hex_says_how_to_read_it_as_base64(blobs):
    out = blobs.expand("48656c6c6f")
    assert out.splitlines()[0] == "hex: 10 characters -> 5 bytes"
    assert _section(out, "Notes") == ["  - read as hex; it is also valid base64, which @b64:base64:<value> reads instead"]
    assert blobs.read("base64:48656c6c6f").label.startswith("base64")
    assert "Notes" not in blobs.expand("0x48656c6c6f")


def test_unpadded_base64_that_is_also_hex_reads_as_hex(blobs):
    # One zero byte is "AA" unpadded: hex 0xAA too. Hex wins; the note says how to get base64.
    assert blobs.read("AA").data == b"\xaa"
    assert "@b64:base64:<value>" in blobs.expand("AA")
    assert blobs.read("base64:AA").data == b"\x00"
    assert blobs.read("AA==").data == b"\x00"


def test_percent_encoding(blobs):
    out = blobs.expand("caf%C3%A9%20au%20lait")
    assert out.splitlines()[:2] == ["percent-encoding: 21 characters -> 13 bytes",
                                    "Content: text, 12 characters, 1 line"]
    assert _section(out, "Text") == ["  café au lait"]


def test_percent_encoding_notes(blobs):
    notes = _section(blobs.expand("a+b%20c%zz"), "Notes")
    assert "  - '+' is kept as '+'; in a form-encoded query string it stands for a space" in notes
    assert "  - a '%' at position 8 is not followed by two hex digits and is kept as is" in notes


def test_double_percent_encoding_is_noted(blobs):
    notes = _section(blobs.expand("a%2520b"), "Notes")
    assert notes == ["  - the text is still percent-encoded (encoded twice): @b64:<text> decodes the next layer"]


def test_data_uri(blobs):
    out = blobs.expand("data:text/plain;charset=utf-8;base64,SGk=")
    assert out.splitlines()[0] == "data: URI (text/plain, base64): 3 characters -> 2 bytes"
    assert blobs.read("data:,a%20b").label == "data: URI (text/plain, percent-encoded)"
    assert blobs.read("data:,a%20b").data == b"a b"
    assert blobs.read("data:image/png;base64," + _b64(PNG)).data == PNG


def test_second_layer_of_base64_is_noted(blobs):
    notes = _section(blobs.expand(_b64(_b64(b"second layer text").encode())), "Notes")
    assert notes == ["  - the text is base64 again (text inside): @b64:<text> decodes the next layer"]


def test_a_word_is_not_called_another_layer(blobs):
    assert "Notes" not in blobs.expand(_b64(b"usernameusername"))
    # "ICBhYiAg" is base64 of "  ab  ": too little to call it a second layer.
    assert "Notes" not in blobs.expand(_b64(b"ICBhYiAg"))


def test_a_nul_byte_makes_it_binary(blobs):
    assert blobs.expand(_b64(b"ab\x00cd")).splitlines()[1] == "Content: binary, 5 bytes (no known format)"


def test_nested_compression_is_inflated_once(blobs):
    out = blobs.expand(_b64(gzip.compress(gzip.compress(b"inner"))))
    lines = out.splitlines()
    assert lines[1].startswith("Content: gzip data, inflates to ")
    assert lines[2].startswith("Inflated: gzip data, inflates to ")
    assert len(lines) == 3


def test_forced_base64url_is_labelled_as_such(blobs):
    assert blobs.read("base64url:SGk").label == "base64url, unpadded"
    assert blobs.read("base64:SGk").label == "base64, unpadded"


def test_jwt_inside_is_pointed_to_jwt_ref(blobs):
    jwt = b"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln"
    assert _section(blobs.expand(_b64(jwt)), "Notes") == [
        "  - the text is a JWT: @jwt:<token> decodes its header and claims"]


def test_dropped_bits_are_noted(blobs):
    # "SGVsbG9=" decodes to "Hello", but its last character carries bits "SGVsbG8=" does not.
    notes = _section(blobs.expand("SGVsbG9="), "Notes")
    assert notes == ["  - the last character has bits set that decoding drops: the text was probably cut or edited"]
    assert "Notes" not in blobs.expand("SGVsbG8=")


def test_wrong_padding_is_noted(blobs):
    notes = _section(blobs.expand("SGVsbG8h="), "Notes")
    assert notes == ["  - the padding is off: 1 '=' for 8 characters (decoded anyway)"]


def test_control_characters_are_escaped(blobs):
    out = blobs.expand(_b64(b"line one\r\nline two\x07\r\n"))
    assert out.splitlines()[1] == "Content: text, 21 characters, 2 lines"
    assert _section(out, "Text") == ["  line one", "  line two\\x07"]
    assert _section(out, "Notes") == ["  - 1 control or invisible character shown as \\x.. / \\u...."]


def test_invisible_format_characters_are_escaped(blobs):
    out = blobs.expand(_b64("pay\u200bpal".encode()))
    assert _section(out, "Text") == ["  pay\\u200bpal"]


def test_byte_order_marks(blobs):
    assert "  - the text is UTF-8 with a byte order mark" in blobs.expand(_b64(b"\xef\xbb\xbfhi"))
    out = blobs.expand(_b64("\ufeffhi".encode("utf-16-le")))
    assert _section(out, "Text") == ["  hi"]
    assert "  - the text is UTF-16 (little-endian)" in out


def test_long_text_is_cut(blobs, monkeypatch):
    monkeypatch.setattr(blobs, "MAX_TEXT_CHARS", 10)
    out = blobs.expand(_b64(b"abcdefghijklmnopqrstuvwxyz"))
    assert _section(out, "Text") == ["  abcdefghij", "  ... 16 more characters"]


def test_many_lines_are_cut(blobs, monkeypatch):
    monkeypatch.setattr(blobs, "MAX_TEXT_LINES", 3)
    out = blobs.expand(_b64(b"1\n2\n3\n4\n5"))
    assert _section(out, "Text") == ["  1", "  2", "  3", "  ... 4 more characters"]


def test_quotes_and_wrapped_lines(blobs):
    assert blobs.read('"SGVs bG8s IHdv cmxk IQ=="').data == b"Hello, world!"
    assert blobs.read("`SGVsbG8=`").data == b"Hello"


# --- errors -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, message", [
    ("", "nothing to decode: write @b64:<base64, base64url, hex or percent-encoded text>"),
    ("SGVsbG9+-", "this mixes the standard base64 alphabet ('+', '/') with the URL-safe one ('-', '_')"),
    ("SGVsbG8h===", "base64 ends in at most two '='; this ends in 3"),
    ("SGVs=bG8", "'=' may only appear at the end of base64, as padding"),
    ("SGVsbG", None),
    ("SGVsb", "base64 cannot be 5 characters long (one character is left over); it was probably cut short"),
    ('"hello, world!"', "this is not base64, base64url, hex or percent-encoding: ',' belongs to none of them"),
    ("hex:4865x", "'x' is not a hex digit"),
    ("hex:486", "hex needs two digits per byte; this has 3 digits"),
    ("base64:ab$c", "'$' is not a base64 character"),
    ("base64:====", "nothing to decode"),
])
def test_errors_say_what_is_wrong(blobs, value, message):
    if message is None:
        blobs.read(value)  # 6 characters: valid unpadded base64
        return
    with pytest.raises(ValueError, match=re.escape(message)):
        blobs.read(value)


def test_too_long_is_refused(blobs, monkeypatch):
    monkeypatch.setattr(blobs, "MAX_INPUT_CHARS", 8)
    with pytest.raises(ValueError, match=re.escape("the value is 12 characters long; b64-ref reads at most 8")):
        blobs.read("SGVsbG8gYWdh")


# --- suggestions ------------------------------------------------------------------------------------

def test_preview(blobs):
    assert blobs.preview("SGVsbG8sIHdvcmxkIQ==") == [
        ("SGVsbG8sIHdvcmxkIQ==", 'base64 -> 13 bytes, text "Hello, world!"', "decoded")]
    assert blobs.preview(_b64(PNG))[0][1] == f"base64 -> {len(PNG)} bytes, PNG image, 64 x 32 pixels"
    assert blobs.preview(_b64(b'{"a": 1}'))[0][1] == "base64 -> 8 bytes, JSON object, 1 key"
    assert blobs.preview(_b64(bytes(range(1, 30))))[0][1] == "base64 -> 29 bytes, binary"


def test_preview_cuts_long_text(blobs):
    text = blobs.preview(_b64(b"x" * 60))[0][1]
    assert text == 'base64 -> 60 bytes, text "' + "x" * 40 + '..."'


def test_preview_quotes_a_value_with_spaces(blobs):
    assert blobs.preview("48 65 6c")[0][0] == '"48 65 6c"'


def test_preview_skips_what_does_not_decode(blobs):
    assert blobs.preview("") == []
    assert blobs.preview("not base64!") == []


# --- the provider, through Hermes's own base class --------------------------------------------------

@pytest.fixture
def hermes_stub(monkeypatch):
    class ContextCompletionItem:
        def __init__(self, text, display="", meta=""):
            self.text, self.display, self.meta = text, display, meta

    class ContextReferenceProvider:
        prefix = ""
        description = ""

    module = types.ModuleType("agent.context_references")
    module.ContextCompletionItem = ContextCompletionItem
    module.ContextReferenceProvider = ContextReferenceProvider
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.context_references", module)
    return module


def test_provider_expands_and_completes(b64_plugin, hermes_stub):
    provider = b64_plugin.make_provider()
    assert provider.prefix == "b64"
    assert isinstance(provider, hermes_stub.ContextReferenceProvider)
    out = asyncio.run(provider.expand("SGk="))
    assert out.splitlines()[-1] == "  Hi"
    items = asyncio.run(provider.autocomplete("SGk="))
    assert [(i.text, i.display, i.meta) for i in items] == [("SGk=", 'base64 -> 2 bytes, text "Hi"', "decoded")]
    with pytest.raises(ValueError):
        asyncio.run(provider.expand("hex:zz"))


def test_register_adds_one_provider(b64_plugin, hermes_stub):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    b64_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "b64"
