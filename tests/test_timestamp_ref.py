"""timestamp-ref: what an @ts: reference expands to, and the suggestions it offers."""

import asyncio
import sys
import types
from datetime import timedelta, timezone

import pytest

NOW = 1761036000.0  # 2025-10-21 08:40:00 UTC
ISTANBUL = timezone(timedelta(hours=3))


@pytest.fixture(autouse=True)
def fixed_local_zone(stamps, monkeypatch):
    """Local time is this computer's zone; pin it to +03:00 so the tests read the same anywhere."""
    def local(moment):
        if moment.year < 1970:
            return None  # what Windows answers for dates before 1970
        return moment.astimezone(ISTANBUL)
    monkeypatch.setattr(stamps, "_local", local)


def _rows(block):
    """{label: value} for the indented rows of one timestamp block."""
    rows = {}
    for line in block.splitlines()[1:]:
        label, _, value = line.strip().partition("  ")
        rows[label.rstrip(":")] = value.strip()
    return rows


def test_unix_seconds_block(stamps):
    out = stamps.expand("1729468800", now=NOW)
    lines = out.splitlines()
    assert lines[0] == "Timestamp 1729468800 (Unix seconds)"
    assert lines[1] == "  note: read as seconds from its 10 digits; add s, ms, us or ns to choose"
    assert lines[2:] == [
        "  UTC       2024-10-21 00:00:00 Monday",
        "  local     2024-10-21 03:00:00 +03:00 Monday",
        "  ISO 8601  2024-10-21T00:00:00Z",
        "  relative  about 1.0 years (365 days) ago",
        "  Unix      1729468800 s, 1729468800000 ms, 1729468800000000 us",
        "  calendar  day 295 of 2024, ISO week 2024-W43",
    ]


@pytest.mark.parametrize("value, unit, utc", [
    ("1729468800", "seconds", "2024-10-21 00:00:00 Monday"),
    ("1729468800123", "milliseconds", "2024-10-21 00:00:00.123 Monday"),
    ("1729468800123456", "microseconds", "2024-10-21 00:00:00.123456 Monday"),
    ("1729468800123456789", "nanoseconds", "2024-10-21 00:00:00.123456 Monday"),
    ("99999999999", "seconds", "5138-11-16 09:46:39 Wednesday"),
    ("100000000000", "milliseconds", "1973-03-03 09:46:40 Saturday"),
    ("99999999999999", "milliseconds", "5138-11-16 09:46:39.999 Wednesday"),
    ("100000000000000", "microseconds", "1973-03-03 09:46:40 Saturday"),
    ("99999999999999999", "microseconds", "5138-11-16 09:46:39.999999 Wednesday"),
    ("100000000000000000", "nanoseconds", "1973-03-03 09:46:40 Saturday"),
    ("0001729468800", "seconds", "2024-10-21 00:00:00 Monday"),
])
def test_bare_numbers_pick_their_unit_by_length(stamps, value, unit, utc):
    out = stamps.expand(value, now=NOW)
    assert out.splitlines()[0] == f"Timestamp {value} (Unix {unit})"
    assert _rows(out)["UTC"] == utc


@pytest.mark.parametrize("value, unit", [
    ("1729468800s", "seconds"), ("1729468800000ms", "milliseconds"),
    ("1729468800000000us", "microseconds"), ("1729468800000000000ns", "nanoseconds"),
])
def test_a_suffix_chooses_the_unit(stamps, value, unit):
    out = stamps.expand(value, now=NOW)
    assert out.splitlines()[:2] == [f"Timestamp {value} (Unix {unit})", f"  note: Unix {unit}, as written"]
    assert _rows(out)["UTC"] == "2024-10-21 00:00:00 Monday"


def test_a_suffix_overrides_the_length_guess(stamps):
    assert _rows(stamps.expand("1729468800ms", now=NOW))["UTC"] == "1970-01-21 00:24:28.8 Wednesday"


def test_iso_row_trims_trailing_zeros_of_the_fraction(stamps):
    assert _rows(stamps.expand("1729468800123", now=NOW))["ISO 8601"] == "2024-10-21T00:00:00.123Z"
    assert _rows(stamps.expand("1729468800.5", now=NOW))["ISO 8601"] == "2024-10-21T00:00:00.5Z"


def test_nanoseconds_below_a_microsecond_are_reported_as_dropped(stamps):
    out = stamps.expand("1729468800123456789", now=NOW)
    assert out.splitlines()[1].endswith("; time below one microsecond is dropped")
    assert "dropped" not in stamps.expand("1729468800123456000", now=NOW)


def test_unix_row_is_exact_for_fractions_and_huge_values(stamps):
    assert _rows(stamps.expand("1729468800.5", now=NOW))["Unix"] == \
        "1729468800.5 s, 1729468800500 ms, 1729468800500000 us"
    assert _rows(stamps.expand("99999999999999999999", now=NOW))["Unix"] == \
        "99999999999.999999 s, 99999999999999.999 ms, 99999999999999999 us"


def test_negative_values_before_1970(stamps):
    out = stamps.expand("-86400.5", now=NOW)
    rows = _rows(out)
    assert rows["UTC"] == "1969-12-30 23:59:59.5 Tuesday"
    assert rows["Unix"] == "-86400.5 s, -86400500 ms, -86400500000 us"
    assert rows["local"] == "not available for this date on this system"
    # 1970 starts on a Thursday, so its ISO week 1 begins on Monday 1969-12-29.
    assert rows["calendar"] == "day 364 of 1969, ISO week 1970-W01"
    assert _rows(stamps.expand("-1500us", now=NOW))["Unix"] == "-0.0015 s, -1.5 ms, -1500 us"


def test_local_time_falls_back_when_the_system_refuses(stamps, monkeypatch):
    monkeypatch.undo()  # the real _local, not the fixture's stand-in
    assert stamps._local(stamps.EPOCH + timedelta(days=20_000)).tzinfo is not None

    class Moment:
        def astimezone(self, tz=None):
            raise OSError(22, "Invalid argument")
    assert stamps._local(Moment()) is None


def test_iso_with_an_offset_shows_the_written_time(stamps):
    out = stamps.expand("2026-10-21T09:00:00+03:00", now=NOW)
    assert out.splitlines()[:3] == [
        "Timestamp 2026-10-21T09:00:00+03:00 (ISO 8601)",
        "  as written  2026-10-21 09:00:00 +03:00 Wednesday",
        "  UTC         2026-10-21 06:00:00 Wednesday",
    ]
    assert _rows(out)["Unix"] == "1792562400 s, 1792562400000 ms, 1792562400000000 us"
    assert _rows(out)["relative"] == "in 364 days 21 hours"


@pytest.mark.parametrize("value", ["2024-10-21T00:00:00Z", "2024-10-21T00:00:00z", "2024-10-21T00:00:00+00:00",
                                   "2024-10-21 03:00:00+03:00", "Mon, 21 Oct 2024 00:00:00 GMT",
                                   "Mon, 21 Oct 2024 03:00:00 +0300"])
def test_written_dates_land_on_the_same_moment(stamps, value):
    out = stamps.expand(value, now=NOW)
    assert _rows(out)["UTC"] == "2024-10-21 00:00:00 Monday", out
    assert "note:" not in out


def test_utc_values_do_not_repeat_the_written_time(stamps):
    assert "as written" not in stamps.expand("2024-10-21T00:00:00Z", now=NOW)
    assert "as written" in stamps.expand("Mon, 21 Oct 2024 03:00:00 +0300", now=NOW)


def test_values_without_an_offset_are_read_as_utc_and_say_so(stamps):
    for value in ("2026-10-21", "2026-10-21T09:00", "2026-10-21 09:00:00"):
        out = stamps.expand(value, now=NOW)
        assert out.splitlines()[1] == "  note: no UTC offset in the value, so it is read as UTC", value
    assert _rows(stamps.expand("2026-10-21", now=NOW))["UTC"] == "2026-10-21 00:00:00 Wednesday"


def test_now_uses_the_current_time(stamps):
    out = stamps.expand("now", now=NOW)
    assert out.splitlines()[0] == "Timestamp now (the current time)"
    assert _rows(out)["UTC"] == "2025-10-21 08:40:00 Tuesday"
    assert _rows(out)["relative"] == "now"
    assert _rows(stamps.expand("NOW", now=NOW + 0.25))["UTC"] == "2025-10-21 08:40:00.25 Tuesday"


@pytest.mark.parametrize("seconds, words", [
    (0, "0 seconds"), (0.25, "0.250 seconds"), (1, "1 second"), (59, "59 seconds"), (60, "1 minute"),
    (125, "2 minutes 5 seconds"), (3600, "1 hour"), (3725, "1 hour 2 minutes"), (86400, "1 day"),
    (90000, "1 day 1 hour"), (2 * 86400 + 59, "2 days"), (364 * 86400, "364 days"),
    (365 * 86400, "about 1.0 years (365 days)"), (-125, "2 minutes 5 seconds"),
])
def test_span(stamps, seconds, words):
    assert stamps.span(seconds) == words


def test_relative_direction(stamps):
    assert _rows(stamps.expand(str(int(NOW) - 125), now=NOW))["relative"] == "2 minutes 5 seconds ago"
    assert _rows(stamps.expand(str(int(NOW) + 3600), now=NOW))["relative"] == "in 1 hour"
    assert _rows(stamps.expand(str(NOW + 0.5), now=NOW))["relative"] == "now"


def test_several_values_get_the_gaps_between_them(stamps):
    out = stamps.expand("1729468800, 1729555200,1729468799.5", now=NOW)
    blocks = out.split("\n\n")
    assert len(blocks) == 4
    assert blocks[-1].splitlines() == [
        "Differences:",
        "  1 -> 2: +1 day (+86,400 s)",
        "  2 -> 3: -1 day (-86,400.5 s)",
    ]
    assert stamps.expand("1700000000 1700003600.25", now=NOW).endswith("  1 -> 2: +1 hour (+3,600.25 s)")
    assert "Differences" not in stamps.expand("1729468800", now=NOW)


def test_split(stamps):
    assert stamps.split("1 2 now") == ["1", "2", "now"]
    assert stamps.split("1, 2,,3 ") == ["1", "2", "3"]
    assert stamps.split("2026-10-21 09:00:00") == ["2026-10-21 09:00:00"]
    assert stamps.split("Mon, 21 Oct 2024 00:00:00 GMT") == ["Mon, 21 Oct 2024 00:00:00 GMT"]


def test_underscores_in_numbers_are_ignored(stamps):
    assert _rows(stamps.expand("1_729_468_800", now=NOW))["UTC"] == "2024-10-21 00:00:00 Monday"


@pytest.mark.parametrize("value, message", [
    ("", "no timestamp given"),
    ("yesterday", "not a Unix timestamp, an ISO 8601 date or an RFC 2822 date"),
    ("2026-13-01", "not a Unix timestamp"),
    ("12ks", "not a Unix timestamp"),
    ("999999999999s", "outside the years 1-9999"),
    ("-99999999999", "outside the years 1-9999"),
])
def test_bad_values_raise_with_a_clear_message(stamps, value, message):
    with pytest.raises(ValueError, match=message):
        stamps.expand(value, now=NOW)


def test_too_many_values(stamps):
    with pytest.raises(ValueError, match="at most 10"):
        stamps.expand(" ".join(["1"] * 11), now=NOW)
    assert stamps.expand(" ".join(["1"] * 10), now=NOW).count("Timestamp 1 ") == 10


def test_preview(stamps):
    assert stamps.preview("", now=NOW) == [
        ("now", "now = 2025-10-21 08:40:00 UTC", "current time"),
        ("1761036000", "1761036000 = 2025-10-21 08:40:00 UTC", "Unix seconds"),
    ]
    assert stamps.preview("no", now=NOW)[0][0] == "now"
    assert stamps.preview("1729468800123", now=NOW) == [
        ("1729468800123", "1729468800123 = 2024-10-21 00:00:00.123 UTC", "Unix milliseconds")]
    assert stamps.preview("2026-10-21 09:00+03:00", now=NOW) == [
        ('"2026-10-21 09:00+03:00"', "2026-10-21 09:00+03:00 = 2026-10-21 06:00:00 UTC", "ISO 8601")]
    assert stamps.preview("tomorrow", now=NOW) == []


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


def test_provider_expands_and_suggests(ts_plugin, hermes_api):
    provider = ts_plugin.make_provider()
    assert isinstance(provider, hermes_api.ContextReferenceProvider)
    assert provider.prefix == "ts" and provider.description
    out = asyncio.run(provider.expand("1729468800"))
    assert "  UTC       2024-10-21 00:00:00 Monday" in out
    items = asyncio.run(provider.autocomplete("1729468800", limit=5))
    assert [(i.text, i.display, i.meta) for i in items] == [
        ("1729468800", "1729468800 = 2024-10-21 00:00:00 UTC", "Unix seconds")]
    assert len(asyncio.run(provider.autocomplete("", limit=1))) == 1


def test_register_registers_one_context_reference(ts_plugin, hermes_api):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    ts_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "ts"
