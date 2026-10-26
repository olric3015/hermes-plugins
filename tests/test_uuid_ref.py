"""uuid-ref: what an @uuid: reference expands to, and the suggestions it offers."""

import asyncio
import random
import re
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc).timestamp()

# RFC 9562 Appendix A test vectors: v1, v6 and v7 all made at 2022-02-22 14:22:22 -05:00.
V1 = "C232AB00-9414-11EC-B3C8-9F6BDECED846"
V3 = "5df41881-3aed-3515-88a7-2f4a814cf09e"  # www.example.com in the DNS namespace
V4 = "919108f7-52d1-4320-9bac-f847db4148a8"
V5 = "2ed6657d-e927-568b-95e1-2665a8aea6a2"
V6 = "1EC9414C-232A-6B00-B3C8-9F6BDECED846"
V7 = "017F22E2-79B0-7CC3-98C4-DC0C0C07398F"
V8 = "2489E9AD-2EE2-8E00-8EC9-32D5F69181C0"
ULID = "01ARYZ6S41TSV4RRFFQ69G5FAV"  # the ULID spec's own example: 1469918176385 ms


def _section(out, title):
    """The indented lines under one heading ("Notes", "Differences ...")."""
    lines, inside = [], False
    for line in out.splitlines():
        if not line.startswith(" "):
            inside = line.startswith(title)
            continue
        if inside:
            lines.append(line)
    return lines


def _row(out, label):
    for line in out.splitlines():
        if line.startswith(f"  {label} "):
            return line[2 + len(label):].strip()
    return None


def test_v1_rfc_vector(uids):
    assert uids.expand(V1, now=NOW).splitlines() == [
        "UUID c232ab00-9414-11ec-b3c8-9f6bdeced846",
        "  written    in upper case",
        "  version    1: time-based: a 60-bit clock (100 ns steps since 1582) + clock sequence + node",
        "  variant    RFC 9562 (the standard layout)",
        "  created    2022-02-22 19:22:22 UTC, about 4.7 years (1,706 days) ago",
        "  clock      138648505420000000 x 100 ns since 1582-10-15 (60 bits)",
        "  clock seq  13256 (14 bits; changes when the clock goes back or the node changes)",
        "  node       9f:6b:de:ce:d8:46: random (the multicast bit is set), not a network card address",
        "Notes",
        "  - v1 UUIDs do not sort by time as text (the low clock bits come first); v6 and v7 do",
    ]


def test_v6_and_v7_rfc_vectors_carry_the_same_moment(uids):
    for value in (V6, V7):
        out = uids.expand(value, now=NOW)
        assert _row(out, "created") == "2022-02-22 19:22:22 UTC, about 4.7 years (1,706 days) ago"
        assert _section(out, "Notes") == ["  - sorts by creation time, as text and as bytes"]
    assert _row(uids.expand(V6, now=NOW), "clock") == "138648505420000000 x 100 ns since 1582-10-15 (60 bits)"
    assert _row(uids.expand(V7, now=NOW), "clock") == "1645557742000 ms since 1970-01-01 (48 bits)"
    assert _row(uids.expand(V7, now=NOW), "random") == "74 bits (or a counter in the first 12, as some makers do)"


def test_name_based_random_and_custom(uids):
    for value, ver, words in ((V3, 3, "MD5"), (V5, 5, "SHA-1")):
        out = uids.expand(value, now=NOW)
        assert _row(out, "version") == f"{ver}: name-based: {words} of a namespace and a name"
        assert _section(out, "Notes") == [
            "  - the same namespace and name always give this UUID; the name cannot be read back from the hash"]
        assert "created" not in out
    assert uids.expand(V4, now=NOW).splitlines() == [
        "UUID 919108f7-52d1-4320-9bac-f847db4148a8",
        "  version  4: random",
        "  variant  RFC 9562 (the standard layout)",
        "  random   122 bits",
    ]
    out = uids.expand(V8, now=NOW)
    assert _row(out, "version") == "8: custom: laid out however its maker chose"
    assert "Notes" not in out


def test_ulid_spec_example(uids):
    out = uids.expand(ULID, now=NOW)
    assert out.splitlines()[0] == f"ULID {ULID}"
    assert _row(out, "clock") == "1469918176385 ms since 1970-01-01 (48 bits)"
    assert _row(out, "created") == "2016-07-30 22:36:16.385 UTC, about 10.2 years (3,739 days) ago"
    assert _row(out, "random") == "80 bits"
    assert _row(out, "as UUID") == ("01563df3-6481-d676-4c61-efb99302bd5b "
                                    "(the same 128 bits, as a UUID column stores them)")
    assert _section(out, "Notes") == [
        "  - sorts by creation time, as text and as bytes",
        "  - stored as a UUID its version and variant digits are random bits, not a real UUID version",
    ]
    assert uids.to_ulid(int("01563df36481d6764c61efb99302bd5b", 16)) == ULID


def test_lower_case_ulid_is_the_same_ulid(uids):
    out = uids.expand(ULID.lower(), now=NOW)
    assert out.splitlines()[0] == f"ULID {ULID}"
    assert _row(out, "written") == "in lower case"


def test_mac_address_node_and_namespace(uids):
    out = uids.expand("6ba7b810-9dad-11d1-80b4-00c04fd430c8", now=NOW)
    assert _row(out, "node") == "00:c0:4f:d4:30:c8: looks like a network card (MAC) address"
    assert _row(out, "created") == "1998-02-04 22:13:53.1511824 UTC, about 28.7 years (10,490 days) ago"
    assert _section(out, "Notes") == [
        "  - the node is a MAC address: this UUID tells which machine made it, and when",
        "  - v1 UUIDs do not sort by time as text (the low clock bits come first); v6 and v7 do",
        "  - this is the DNS namespace UUID from RFC 9562, used to make v3 and v5 UUIDs from DNS names",
    ]
    for value, name in ((uuid.NAMESPACE_URL, "URL"), (uuid.NAMESPACE_OID, "ISO OID"), (uuid.NAMESPACE_X500, "X.500 DN")):
        assert f"this is the {name} namespace UUID" in uids.expand(str(value), now=NOW)


@pytest.mark.parametrize("first_octet, words", [
    ("01", "01:23:45:67:89:ab: random (the multicast bit is set), not a network card address"),
    ("02", "02:23:45:67:89:ab: looks like a network card (MAC) address"),
])
def test_only_the_multicast_bit_marks_a_random_node(uids, first_octet, words):
    for value in (f"c232ab00-9414-11ec-b3c8-{first_octet}23456789ab", f"1ec9414c-232a-6b00-b3c8-{first_octet}23456789ab"):
        assert _row(uids.expand(value, now=NOW), "node") == words


def _v1_at(ticks):
    """A v1 UUID whose clock reads ``ticks`` (100 ns steps since 1582-10-15)."""
    low, mid, high = ticks & 0xFFFFFFFF, (ticks >> 32) & 0xFFFF, (ticks >> 48) & 0x0FFF
    return f"{low:08x}-{mid:04x}-{0x1000 | high:04x}-b3c8-9f6bdeced846"


def test_a_time_just_before_1970_is_noted(uids):
    out = uids.expand(_v1_at(uids.GREGORIAN_OFFSET - 10_000_000), now=NOW)
    assert _row(out, "created").startswith("1969-12-31 23:59:59 UTC")
    assert "  - the time is before 1970: the bits were probably not made from a clock" in out.splitlines()
    assert "before 1970" not in uids.expand(_v1_at(uids.GREGORIAN_OFFSET), now=NOW)


def test_v2_local_id(uids):
    out = uids.expand("000003e8-cc4e-21f0-a400-325096b39f47", now=NOW)
    assert _row(out, "local id") == "1000 in domain 0 (person (POSIX uid))"
    assert _row(out, "created") == ("about 2025-11-28 11:33:02 UTC (the low 32 clock bits hold the id, "
                                    "so only to within about 7 minutes)")
    assert "clock seq" not in out
    assert _row(uids.expand("000003e8-cc4e-21f0-a105-325096b39f47", now=NOW), "local id") == (
        "1000 in domain 5 (not a standard domain)")


def test_nil_and_max(uids):
    assert uids.expand("00000000-0000-0000-0000-000000000000", now=NOW).splitlines() == [
        "UUID 00000000-0000-0000-0000-000000000000",
        "  kind  Nil UUID: all 128 bits zero, the placeholder for 'no UUID' (RFC 9562 5.9)",
    ]
    assert uids.expand("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF", now=NOW).splitlines() == [
        "UUID ffffffff-ffff-ffff-ffff-ffffffffffff",
        "  written  in upper case",
        "  kind     Max UUID: all 128 bits one, the 'largest UUID' sentinel (RFC 9562 5.10)",
    ]


@pytest.mark.parametrize("value, words", [
    ("00000000-0000-0000-C000-000000000046", "Microsoft (reserved: old COM/DCOM GUIDs)"),
    ("00000000-0000-1000-0000-000000000046", "NCS (reserved: Apollo Network Computing System IDs from the 1980s)"),
    ("00000000-0000-4000-e000-000000000046", "reserved for future definition"),
])
def test_other_variants_are_named_and_not_read_further(uids, value, words):
    out = uids.expand(value, now=NOW)
    assert _row(out, "variant") == words
    assert "has no defined meaning outside the RFC 9562 variant" in _row(out, "version")
    assert _section(out, "Notes") == ["  - not an RFC 9562 UUID: nothing more can be read from its bits"]


@pytest.mark.parametrize("digit", ["0", "9", "f"])
def test_undefined_versions(uids, digit):
    out = uids.expand(f"12345678-1234-{digit}234-8234-123456789abc", now=NOW)
    assert _row(out, "version") == f"{int(digit, 16)}: not defined (RFC 9562 defines versions 1 to 8)"


@pytest.mark.parametrize("value, form", [
    ("{919108f7-52d1-4320-9bac-f847db4148a8}", "in braces"),
    ("urn:uuid:919108f7-52d1-4320-9bac-f847db4148a8", "as a URN (urn:uuid:)"),
    ("URN:UUID:{919108F7-52D1-4320-9BAC-F847DB4148A8}", "as a URN (urn:uuid:), in braces, in upper case"),
    ("919108f752d143209bacf847db4148a8", "without hyphens"),
])
def test_spellings_decode_to_the_same_uuid(uids, value, form):
    out = uids.expand(value, now=NOW)
    assert out.splitlines()[0] == "UUID 919108f7-52d1-4320-9bac-f847db4148a8"
    assert _row(out, "written") == form


def test_future_and_pre_1970_times_are_noted(uids):
    future = (int(NOW) + 3600) * 1000
    v7 = f"{future:012x}" + "7000" + "8" + "0" * 15
    out = uids.expand(v7, now=NOW)
    assert _row(out, "created") == "2026-10-26 10:00:00 UTC, in 1 hour"
    assert _section(out, "Notes")[0] == ("  - the time is in the future: the maker's clock was off, "
                                         "or these bits were not made from a clock")
    near = f"{(int(NOW) + 30) * 1000:012x}" + "7000" + "8" + "0" * 15
    assert "in the future" not in uids.expand(near, now=NOW)
    old = uids.expand("00000000-0000-1000-8000-000000000000", now=NOW)
    assert _row(old, "created").startswith("1582-10-15 00:00:00 UTC")
    assert "  - the time is before 1970: the bits were probably not made from a clock" in old.splitlines()


def test_v7_past_the_year_9999(uids):
    out = uids.expand("ffffffff-ffff-7fff-bfff-ffffffffffff", now=NOW)
    assert _row(out, "created") == "281474976710655 ms since 1970, after the year 9999"


def test_several_identifiers_are_compared_by_time(uids):
    later = f"{1645557742000 + 2500:012x}" + "7cc398c4dc0c0c07398f"
    out = uids.expand(f"{V7},{V4} {later},{V1}", now=NOW)
    blocks = out.split("\n\n")
    assert [b.splitlines()[0] for b in blocks[:4]] == [
        "1. UUID 017f22e2-79b0-7cc3-98c4-dc0c0c07398f",
        "2. UUID 919108f7-52d1-4320-9bac-f847db4148a8",
        f"3. UUID {later[:8]}-{later[8:12]}-{later[12:16]}-{later[16:20]}-{later[20:]}",
        "4. UUID c232ab00-9414-11ec-b3c8-9f6bdeced846",
    ]
    assert _section(out, "Differences in creation time") == [
        "  3 was made 2.5 seconds after 1",
        "  4 was made 2.5 seconds before 3",
    ]


def test_sub_second_differences_are_exact(uids):
    a = "c232ab00-9414-11ec-b3c8-9f6bdeced846"
    b = "c232ab01-9414-11ec-b3c8-9f6bdeced846"  # one 100 ns tick later
    out = uids.expand(f"{a},{b},{b}", now=NOW)
    assert _section(out, "Differences in creation time") == [
        "  2 was made 0.0001 ms after 1",
        "  3 carries the same time as 2",
    ]


def test_two_identifiers_and_a_gap_under_a_second(uids):
    later = f"{1645557742000 + 700:012x}" + "7cc398c4dc0c0c07398f"
    out = uids.expand(f"{V7},{later}", now=NOW)
    assert _section(out, "Differences in creation time") == ["  2 was made 700 ms after 1"]
    assert _section(uids.expand(f"{V6},{V7}", now=NOW), "Differences in creation time") == [
        "  2 carries the same time as 1"]
    assert "Differences" not in uids.expand(f"{V7},{V4}", now=NOW)


@pytest.mark.parametrize("value, message", [
    ("", "no UUID given"),
    ("xyz", "'xyz' is not a UUID: 'x', 'y', 'z' are not hex digits"),
    ("919108f7-52d1-4320-9bac-f847db4148a", "a UUID has 32 hex digits (36 characters with hyphens); "
                                            "'919108f7-52d1-4320-9bac-f847db4148a' has 31"),
    ("919108f752d1-4320-9bac-f847db4148a8", "its hyphens are not in the 8-4-4-4-12 places"),
    ("919108f7-52d1-4320-9bac-f847db4148ag", "'g' is not a hex digit"),
    ("{919108f7-52d1-4320-9bac-f847db4148a8", "has an unmatched brace"),
    ("01ARYZ6S41TSV4RRFFQ69G5FAU", "ULIDs leave out I, L, O and U (Crockford base32); this has 'U'"),
    ("81ARYZ6S41TSV4RRFFQ69G5FAV", "a ULID starts with 0-7; '8' would make it longer than 128 bits"),
    ("{01ARYZ6S41TSV4RRFFQ69G5FAV}", "wraps a ULID; braces and urn:uuid: belong to UUIDs, write a ULID bare"),
    ("urn:uuid:01ARYZ6S41TSV4RRFFQ69G5FAV", "wraps a ULID"),
    ("{01ARYZ6S41TSV4RRFFQ69G5FAVX}", "is not a UUID: "),
    ("01ARYZ6S41-TSV4RRFFQ69G5FAV", "'01ARYZ6S41-TSV4RRFFQ69G5FAV' is not a UUID: 'G', 'Q', 'R', 'S', 'T' are not"),
])
def test_bad_values_raise_with_a_clear_message(uids, value, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        uids.expand(value, now=NOW)


def test_limits(uids):
    with pytest.raises(ValueError, match="11 identifiers given; uuid-ref reads at most 10"):
        uids.expand(",".join([V4] * 11), now=NOW)
    assert uids.expand(",".join([V4] * 10), now=NOW).count("UUID 919108f7") == 10
    with pytest.raises(ValueError, match="2,001 characters long"):
        uids.expand("a" * 2001, now=NOW)


def test_now_defaults_to_the_clock(uids, monkeypatch):
    monkeypatch.setattr(uids.time, "time", lambda: NOW)
    assert uids.expand(V7) == uids.expand(V7, now=NOW)
    assert uids.preview(V7) == uids.preview(V7, now=NOW)


def test_preview(uids):
    assert uids.preview(V7, now=NOW) == [(
        "017f22e2-79b0-7cc3-98c4-dc0c0c07398f",
        "v7, created 2022-02-22 19:22:22 UTC, about 4.7 years (1,706 days) ago", "UUID")]
    assert uids.preview(V4, now=NOW) == [(V4, "v4, random", "UUID")]
    assert uids.preview(V5, now=NOW) == [(V5, "v5, name-based", "UUID")]
    assert uids.preview(V8, now=NOW)[0][1] == "v8"
    assert uids.preview(ULID.lower(), now=NOW)[0][:2] == (
        ULID, "ULID, created 2016-07-30 22:36:16 UTC, about 10.2 years (3,739 days) ago")
    assert uids.preview("00000000-0000-0000-0000-000000000000", now=NOW)[0][1] == "Nil UUID"
    assert uids.preview("ffffffff-ffff-ffff-ffff-ffffffffffff", now=NOW)[0][1] == "Max UUID"
    assert uids.preview("00000000-0000-0000-C000-000000000046", now=NOW)[0][1] == "microsoft variant"
    assert uids.preview("ffffffff-ffff-7fff-bfff-ffffffffffff", now=NOW)[0][1] == "v7"
    for query in ("", "9191", f"{V4},{V4}", "not a uuid"):
        assert uids.preview(query, now=NOW) == []


# The standard library reads the same fields; every decoded field must agree with it.

def _random_uuid(rng, ver, var=0b10):
    value = rng.getrandbits(128)
    value = (value & ~(0xF << 76)) | (ver << 76)
    return (value & ~(0b11 << 62)) | (var << 62)


@pytest.mark.parametrize("ver", range(16))
def test_version_and_variant_agree_with_the_standard_library(uids, ver):
    rng = random.Random(ver)
    names = {uuid.RESERVED_NCS: "ncs", uuid.RFC_4122: "rfc", uuid.RESERVED_MICROSOFT: "microsoft",
             uuid.RESERVED_FUTURE: "future"}
    for _ in range(200):
        value = rng.getrandbits(128)
        value = (value & ~(0xF << 76)) | (ver << 76)
        stdlib = uuid.UUID(int=value)
        assert uids.variant(value) == names[stdlib.variant]
        if stdlib.variant == uuid.RFC_4122 and value not in (0, uids.MAX_INT):
            assert uids.version(value) == stdlib.version


def test_v1_fields_agree_with_the_standard_library(uids):
    rng = random.Random(1)
    for _ in range(500):
        value = _random_uuid(rng, 1)
        stdlib = uuid.UUID(int=value)
        assert uids.gregorian_ticks(value) == stdlib.time
        assert uids.clock_seq(value) == stdlib.clock_seq
        assert uids.node(value) == stdlib.node
    # A system libuuid makes uuid1() on Linux with its own clock (2 hours ahead on a CI runner);
    # giving node and clock_seq makes Python build it from time.time_ns() on every platform.
    made = uuid.uuid1(node=0x010203040506, clock_seq=1)
    assert uids.gregorian_ticks(made.int) == made.time
    assert uids.node(made.int) == 0x010203040506 and uids.clock_seq(made.int) == 1
    seconds = uids.created(uids.read(str(made)))
    assert abs(float(seconds) - __import__("time").time()) < 60


@pytest.mark.skipif(sys.version_info < (3, 14), reason="uuid reads v6 and v7 times from Python 3.14")
def test_v6_and_v7_times_agree_with_the_standard_library(uids):
    rng = random.Random(7)
    for _ in range(500):
        v6, v7 = _random_uuid(rng, 6), _random_uuid(rng, 7)
        assert uids.gregorian_ticks(v6) == uuid.UUID(int=v6).time
        assert uids.unix_ms(v7) == uuid.UUID(int=v7).time
    for made in (uuid.uuid6(), uuid.uuid7()):
        item = uids.read(str(made))
        assert abs(float(uids.created(item)) - __import__("time").time()) < 60
        assert uids.expand(str(made)).splitlines()[0] == f"UUID {made}"


def test_ulid_round_trip(uids):
    rng = random.Random(26)
    for _ in range(500):
        value = rng.getrandbits(128)
        text = uids.to_ulid(value)
        assert len(text) == 26 and text[0] in "01234567"
        assert uids.read(text).value == value
        assert uids.read(text.lower()).value == value


@pytest.fixture
def hermes_api(monkeypatch):
    """Stand-ins for the two classes the plugin takes from agent.context_references."""
    module = types.ModuleType("agent.context_references")

    class ContextCompletionItem:
        def __init__(self, text, display="", meta=""):
            self.text, self.display, self.meta = text, display or text, meta

    class ContextReferenceProvider:
        prefix = ""
        description = ""

    module.ContextCompletionItem = ContextCompletionItem
    module.ContextReferenceProvider = ContextReferenceProvider
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.context_references", module)
    return module


def test_provider_expands_and_suggests(uuid_plugin, hermes_api):
    provider = uuid_plugin.make_provider()
    assert isinstance(provider, hermes_api.ContextReferenceProvider)
    assert provider.prefix == "uuid" and provider.description
    out = asyncio.run(provider.expand(V4))
    assert "  version  4: random" in out
    items = asyncio.run(provider.autocomplete(V4, limit=5))
    assert [(i.text, i.meta) for i in items] == [(V4, "UUID")]
    assert asyncio.run(provider.autocomplete(V4, limit=0)) == []
    with pytest.raises(ValueError, match="no UUID given"):
        asyncio.run(provider.expand(""))


def test_register_registers_one_context_reference(uuid_plugin, hermes_api):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    uuid_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "uuid"
