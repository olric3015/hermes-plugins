"""crontab-ref: what an @crontab: reference expands to, and the suggestions it offers."""

import asyncio
import calendar
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("croniter")
from croniter import croniter  # noqa: E402

TZ = timezone(timedelta(hours=3))
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=TZ)  # a Saturday
ZONE = "Europe/Istanbul (Hermes's timezone setting)"


def test_weekdays_at_nine(crons):
    assert crons.expand('"0 9 * * 1-5"', now=NOW, label=ZONE).splitlines() == [
        'Cron expression "0 9 * * 1-5", as Hermes\'s scheduler (croniter) reads it',
        "  At 09:00 on Monday through Friday.",
        "  minute        0    0",
        "  hour          9    9",
        "  day of month  *    any",
        "  month         *    any",
        "  day of week   1-5  Monday through Friday",
        "Hermes /cron: accepted as a cron expression",
        "Next 5 runs, in Europe/Istanbul (Hermes's timezone setting)",
        "  Mon 2026-10-26 09:00 +03:00  in 1 day 21 hours",
        "  Tue 2026-10-27 09:00 +03:00  in 2 days 21 hours",
        "  Wed 2026-10-28 09:00 +03:00  in 3 days 21 hours",
        "  Thu 2026-10-29 09:00 +03:00  in 4 days 21 hours",
        "  Fri 2026-10-30 09:00 +03:00  in 5 days 21 hours",
    ]


@pytest.mark.parametrize("expr, words", [
    ("* * * * *", "Every minute."),
    ("*/15 * * * *", "Every 15 minutes."),
    ("0,30 * * * *", "Every 30 minutes."),
    ("5/15 * * * *", "Every 15 minutes from minute 5 of every hour."),
    ("30 * * * *", "At minute 30 of every hour."),
    ("10,20 * * * *", "At minutes 10 and 20 of every hour."),
    # Evenly spaced, but not from the start of the hour: no ":00" run, so not "every 20 minutes".
    ("20,40 * * * *", "At minutes 20 and 40 of every hour."),
    ("* 9 * * *", "Every minute during 09:00-09:59."),
    ("* 9-11,14 * * *", "Every minute during 09:00-11:59 and 14:00-14:59."),
    ("0 9 * * *", "At 09:00 every day."),
    ("0,30 9,17 * * *", "At 09:00, 09:30, 17:00 and 17:30 every day."),
    ("*/15 9-17 * * 1-5",
     "Every 15 minutes during 09:00-17:59 (first 09:00, last 17:45) on Monday through Friday."),
    ("15 9-17 * * *", "At minute 15 during 09:00-17:59 (first 09:15, last 17:15) every day."),
    ("10,40 8-20 * * *",
     "At minutes 10 and 40 during 08:00-20:59 (first 08:10, last 20:40) every day."),
    ("0 9 * * MON-FRI", "At 09:00 on Monday through Friday."),
    ("0 9 * * 1,3,5", "At 09:00 on Monday, Wednesday and Friday."),
    ("0 10 * * 0,6", "At 10:00 on Sunday and Saturday."),
    ("0 9 * * 7", "At 09:00 on Sunday."),
    ("0 9 1 * *", "At 09:00 on day 1 of the month."),
    ("0 9 1,15 * *", "At 09:00 on days 1 and 15 of the month."),
    ("0 9 1,2 * *", "At 09:00 on days 1 and 2 of the month."),
    ("0 9 1-10 * *", "At 09:00 on days 1-10 of the month."),
    ("0 9 L * *", "At 09:00 on the last day of the month."),
    ("0 9 1 * 1", "At 09:00 on day 1 of the month or on Monday."),
    ("0 9 * * 5#3", "At 09:00 on the third Friday of the month."),
    ("0 9 * * L5", "At 09:00 on the last Friday of the month."),
    ("0 0 * jan,jul *", "At 00:00 every day in January and July."),
    ("0 0 1 3-5 *", "At 00:00 on day 1 of the month in March through May."),
    ("@daily", "At 00:00 every day."),
    ("@hourly", "At minute 0 of every hour."),
    ("@weekly", "At 00:00 on Sunday."),
    ("* * * * * 30", "Every minute, at second 30."),
    ("* * * * * *", "Every minute, every second."),
    ("0 0 1 1 * 0 2027", "At 00:00 on day 1 of the month in January in the year 2027."),
])
def test_summary(crons, expr, words):
    assert crons.summary(crons.parse(expr)) == words


def _notes(crons, expr):
    return crons.notes(crons.parse(expr))


def test_both_day_fields_mean_either(crons):
    assert _notes(crons, "0 9 1 * 1") == [
        "day of month and day of week are both set, so a day that matches EITHER one runs "
        "(croniter's rule, as in standard cron), not only days that match both"]
    # croniter counts "*/2" as set too, unlike the Vixie cron bug some sites describe.
    assert "EITHER" in _notes(crons, "0 9 */2 * 1")[0]
    assert _notes(crons, "0 9 1-31 * 1") == [
        "day of month covers every day, so day of week changes nothing: this runs every day"]


def test_star_minute_with_a_set_hour(crons):
    assert _notes(crons, "* 9 * * *") == [
        "the minute field is *, so this runs every minute of those hours (60 times a day); "
        "for once at 09:00 write 0 9 ..."]
    assert "120 times a day" in _notes(crons, "* 9,17 * * *")[0]


def test_uneven_steps_restart(crons):
    assert _notes(crons, "*/7 * * * *") == [
        "*/7 in the minute field starts again every hour: after :56 the next run is :00, "
        "4 minutes later, not 7"]
    assert _notes(crons, "0 */5 * * *") == [
        "*/5 in the hour field starts again every day: after 20:00 the next run is 00:00, "
        "4 hours later, not 5"]
    assert _notes(crons, "*/15 * * * *") == []
    assert _notes(crons, "0 */6 * * *") == []


def test_short_month_days(crons):
    assert _notes(crons, "0 9 31 * *") == [
        "day 31 is missing from some months, and those months are skipped (no run on their "
        "last day instead); L means the last day of every month"]
    assert "day 29-31 is missing" in _notes(crons, "0 9 29-31 * *")[0]
    assert _notes(crons, "0 9 28 * *") == []
    # With day of week set too, the either-rule note is the one that matters.
    assert len(_notes(crons, "0 9 31 * 1")) == 1


def test_seven_is_sunday_and_extra_fields(crons):
    assert _notes(crons, "0 9 * * 7") == ["7 in day of week means Sunday, the same as 0"]
    assert _notes(crons, "0 9 * * 1-7") == ["7 in day of week means Sunday, the same as 0"]
    assert _notes(crons, "* * * * * 30") == ["croniter reads the 6th field as seconds"]
    assert _notes(crons, "0 0 1 1 * 0 2027") == [
        "croniter reads the 6th field as seconds and the 7th as the year"]


@pytest.mark.parametrize("expr, accepted, words", [
    ("0 9 * * 1-5", True, "accepted as a cron expression"),
    ("0 9 * * MON-FRI", True, "accepted as a cron expression"),
    ("0 9 L * *", True, "accepted as a cron expression"),
    ("* * * * * 30", True, "accepted as a cron expression"),
    ("@daily", False, "Hermes's /cron does not take @daily; write the five fields instead: 0 0 * * *"),
    ("0 9 * * 5#3", False, "5#3 has other characters, so /cron would not treat this as a cron expression"),
    ("0 9 ? * 1", False, "? has other characters"),
])
def test_hermes_verdict(crons, expr, accepted, words):
    ok, why = crons.hermes_verdict(crons.parse(expr))
    assert ok is accepted
    assert words in why


def test_a_date_that_never_comes(crons):
    out = crons.expand("0 9 31 2 *", now=NOW, label=ZONE)
    assert "  At 09:00 on day 31 of the month in February." in out
    assert "Hermes /cron: accepted as a cron expression" in out
    assert "Next runs: none. No date matches this expression, so it never runs" in out


def test_a_single_remaining_run(crons):
    out = crons.expand("0 0 1 1 * 0 2027", now=NOW, label=ZONE)
    assert "Next run, in Europe/Istanbul (Hermes's timezone setting); no run after that" in out
    assert "  Fri 2027-01-01 00:00:00 +03:00  in 68 days 12 hours" in out


def test_macro_shows_its_fields(crons):
    lines = crons.expand("@weekly", now=NOW, label=ZONE).splitlines()
    assert lines[:3] == ['Cron expression "@weekly", as Hermes\'s scheduler (croniter) reads it',
                         "  At 00:00 on Sunday.",
                         "  same as 0 0 * * 0"]
    assert "  day of week   0  Sunday" in lines


def test_field_rows_use_names(crons):
    out = crons.expand("0 9 L jan-mar 5#3", now=NOW, label=ZONE)
    assert "  day of month  L        last day" in out
    assert "  month         jan-mar  January through March" in out
    assert "  day of week   5#3      the third Friday of the month" in out


def test_runs_come_from_croniter_in_the_given_zone(crons):
    expected = croniter("*/20 9-10 * * *", NOW.replace(tzinfo=None))
    runs = crons.next_runs(crons.parse("*/20 9-10 * * *"), NOW)
    assert runs == [expected.get_next(datetime).replace(tzinfo=TZ) for _ in range(5)]
    assert all(r.utcoffset() == timedelta(hours=3) for r in runs)


def _zone(name):
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        return zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError:  # Windows without the tzdata package
        pytest.skip(f"no time zone data for {name}")


def test_wall_clock_holds_across_daylight_saving(crons):
    """Like cron/jobs.py::compute_next_run: 09:00 stays 09:00 local when the offset changes."""
    toronto = _zone("America/Toronto")
    now = datetime(2026, 10, 30, 12, 0, tzinfo=toronto)  # Toronto falls back on 2026-11-01
    runs = crons.next_runs(crons.parse("0 9 * * *"), now, toronto)
    assert [(r.day, r.hour, crons._offset(r)) for r in runs] == [
        (31, 9, "-04:00"), (1, 9, "-05:00"), (2, 9, "-05:00"), (3, 9, "-05:00"), (4, 9, "-05:00")]


def test_repeated_autumn_hour_moves_forward(crons):
    """Inside the second 01:xx of a fall-back night, the next 01:30 is the later one or the next day's."""
    toronto = _zone("America/Toronto")
    second_pass = datetime(2026, 11, 1, 1, 10, tzinfo=toronto, fold=1)  # 01:10 EST, after 01:10 EDT
    runs = crons.next_runs(crons.parse("30 1 * * *"), second_pass, toronto)
    assert runs[0].timestamp() > second_pass.timestamp()
    assert (runs[0].day, runs[0].hour, runs[0].minute, crons._offset(runs[0])) == (1, 1, 30, "-05:00")
    assert all(b.timestamp() > a.timestamp() for a, b in zip(runs, runs[1:]))


def test_quotes_and_spaces(crons):
    for written in ('"0 9 * * 1-5"', "'0 9 * * 1-5'", "`0 9 * * 1-5`", "  0  9 * *   1-5 "):
        reading = crons.parse(written)
        assert reading.raw.split() == ["0", "9", "*", "*", "1-5"]
        assert reading.expr == "0 9 * * 1-5"


@pytest.mark.parametrize("value, message", [
    ("", "no cron expression given"),
    ('""', "no cron expression given"),
    ("0 9 * *", "this has 4. Values with spaces go in quotes"),
    ("*/5", "this has 1."),
    ("0 0 1 1 * 0 2027 9", "this has 8"),
    ("61 * * * *", "croniter cannot read '61 * * * *'"),
    ("0 9 * * fri-xyz", "croniter cannot read"),
    ("@reboot", "it is not a schedule"),
    ("@sometimes", "unknown shortcut @sometimes"),
])
def test_bad_values_raise_with_a_clear_message(crons, value, message):
    with pytest.raises(ValueError, match=message.replace("*", r"\*").replace(".", r"\.")):
        crons.parse(value)


def test_too_long_values_are_refused(crons):
    with pytest.raises(ValueError, match="a cron expression is far shorter"):
        crons.parse("0 " * 150)


def test_missing_croniter_is_explained(crons, monkeypatch):
    monkeypatch.setitem(sys.modules, "croniter", None)
    with pytest.raises(ValueError, match="croniter is not installed"):
        crons.parse("0 9 * * *")


@pytest.mark.parametrize("seconds, words", [
    (30, "less than a minute"),
    (60, "1 minute"),
    (3600, "1 hour"),
    (3660, "1 hour 1 minute"),
    (86400 + 7200, "1 day 2 hours"),
    (2 * 86400 + 60, "2 days"),
])
def test_span(crons, seconds, words):
    assert crons._span(seconds) == words


@pytest.fixture
def hermes_settings(monkeypatch):
    """Stand-ins for the two public Hermes modules the timezone lookup reads."""
    state = {"multiplex": False, "config": {}}
    scope = types.ModuleType("agent.secret_scope")
    scope.is_multiplex_active = lambda: state["multiplex"]
    config = types.ModuleType("hermes_cli.config")
    config.load_config_readonly = lambda: state["config"]
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    return state


def test_zone_setting_is_resolved_like_hermes_time(crons, hermes_settings, monkeypatch):
    assert crons.configured_zone_name() == ""
    hermes_settings["config"] = {"timezone": "  Asia/Kolkata "}
    assert crons.configured_zone_name() == "Asia/Kolkata"
    monkeypatch.setenv("HERMES_TIMEZONE", "Europe/Istanbul")
    assert crons.configured_zone_name() == "Europe/Istanbul"
    # Under the multiplexed gateway the variable belongs to the default profile only.
    hermes_settings["multiplex"] = True
    assert crons.configured_zone_name() == "Asia/Kolkata"
    hermes_settings["config"] = {"timezone": 5}
    assert crons.configured_zone_name() == ""


def test_zone_lookup_outside_hermes(crons, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.config", None)
    assert crons.configured_zone_name() == ""


def test_hermes_now_uses_the_configured_zone(crons, hermes_settings):
    kolkata = _zone("Asia/Kolkata")
    hermes_settings["config"] = {"timezone": "Asia/Kolkata"}
    moment, zone, label = crons.hermes_now()
    assert zone == kolkata and moment.utcoffset() == timedelta(hours=5, minutes=30)
    assert label == "Asia/Kolkata (Hermes's timezone setting)"
    out = crons.expand("0 9 * * *")
    assert "Next 5 runs, in Asia/Kolkata (Hermes's timezone setting)" in out
    assert out.count(" 09:00 +05:30  in ") == 5


def test_hermes_now_without_a_usable_zone(crons, hermes_settings):
    moment, zone, label = crons.hermes_now()
    assert zone is moment.tzinfo and label.endswith("(this computer's time; no timezone is set in Hermes)")
    hermes_settings["config"] = {"timezone": "Mars/Olympus_Mons"}
    moment, zone, label = crons.hermes_now()
    assert zone is moment.tzinfo
    assert label.endswith("(this computer's time; the timezone setting 'Mars/Olympus_Mons' is not "
                          "a time zone Python knows)")


def test_preview(crons):
    examples = crons.preview("")
    assert [text for text, _, _ in examples] == ['"0 9 * * 1-5"', '"*/15 * * * *"', '"30 18 1 * *"']
    assert examples[0][1] == "0 9 * * 1-5 = At 09:00 on Monday through Friday."
    assert crons.preview('"0 9 * * 1-5') == [
        ('"0 9 * * 1-5"', "0 9 * * 1-5 = At 09:00 on Monday through Friday.", "cron expression")]
    assert crons.preview("@daily") == [("@daily", "@daily = At 00:00 every day.", "cron expression")]
    assert crons.preview('"0 9 *') == []
    assert crons.preview("nonsense") == []


def _nth_weekday_matches(day: date, nths: set) -> bool:
    if "l" in nths and day.day + 7 > calendar.monthrange(day.year, day.month)[1]:
        return True
    return (day.day - 1) // 7 + 1 in nths


def _day_runs(crons, reading, day: date) -> bool:
    """Does the day rule the summary states (and nothing else) let ``day`` run?"""
    dom = reading.values(2)
    dow = reading.values(4)
    month_ok = reading.any(3) or day.month in reading.values(3)
    last = calendar.monthrange(day.year, day.month)[1]
    dom_ok = day.day in dom or ("l" in dom and day.day == last)
    weekday = (day.weekday() + 1) % 7  # cron numbering: 0 = Sunday
    if reading.nth:
        dow_ok = weekday in reading.nth and _nth_weekday_matches(day, reading.nth[weekday])
    else:
        dow_ok = weekday in dow
    if reading.any(2) and reading.any(4):
        days_ok = True
    elif reading.any(4):
        days_ok = dom_ok
    elif reading.any(2):
        days_ok = dow_ok
    else:
        days_ok = dom_ok or dow_ok
    return month_ok and days_ok


@pytest.mark.parametrize("days", [
    "* * *", "* * 1-5", "* * 0,6", "1 * *", "1,15 * *", "L * *", "31 * *", "29 2 *",
    "1 * 1", "*/2 * 1", "1-7 * 5", "13 * 5", "L * 0", "1-31 * 1", "* jan,jul *",
    "* * 5#3", "* * L5", "* * 1#1,1#3", "* * 7", "15 3-5 1-5",
])
def test_day_rule_matches_croniter_for_two_years(crons, days):
    """The day rule the summary describes picks exactly the days croniter runs on."""
    expr = f"0 9 {days}"
    reading = crons.parse(expr)
    start = datetime(2026, 1, 1, tzinfo=TZ)
    stop = start + timedelta(days=731)
    iterator = croniter(expr, start)
    fired = set()
    while True:
        run = iterator.get_next(datetime)
        if run >= stop:
            break
        fired.add(run.date())
    stated = {(start + timedelta(days=n)).date() for n in range(731)
              if _day_runs(crons, reading, (start + timedelta(days=n)).date())}
    assert fired == stated


def test_time_of_day_matches_croniter(crons):
    """Every run croniter makes in a day is one of the times the summary lists, and all of them."""
    for expr in ("0,30 9,17 * * *", "*/7 * * * *", "0 */5 * * *", "5/15 9-11 * * *"):
        reading = crons.parse(expr)
        minutes = range(60) if reading.any(0) else reading.values(0)
        hours = range(24) if reading.any(1) else reading.values(1)
        stated = {(h, m) for h in hours for m in minutes}
        start = datetime(2026, 10, 24, tzinfo=TZ)
        iterator, fired = croniter(expr, start - timedelta(seconds=1)), set()
        while True:
            run = iterator.get_next(datetime)
            if run.date() != start.date():
                break
            fired.add((run.hour, run.minute))
        assert fired == stated, expr


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


def test_provider_expands_and_suggests(crontab_plugin, hermes_api, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.config", None)
    provider = crontab_plugin.make_provider()
    assert isinstance(provider, hermes_api.ContextReferenceProvider)
    assert provider.prefix == "crontab" and provider.description
    out = asyncio.run(provider.expand('"0 9 * * 1-5"'))
    assert "  At 09:00 on Monday through Friday." in out
    items = asyncio.run(provider.autocomplete("", limit=2))
    assert [(i.text, i.meta) for i in items] == [('"0 9 * * 1-5"', "example"), ('"*/15 * * * *"', "example")]
    assert asyncio.run(provider.autocomplete("", limit=0)) == []
    with pytest.raises(ValueError, match="no cron expression given"):
        asyncio.run(provider.expand(""))


def test_register_registers_one_context_reference(crontab_plugin, hermes_api):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    crontab_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "crontab"
