"""Explain a cron expression the way Hermes's scheduler reads it.

The reading comes from croniter, the library Hermes's own ``/cron`` scheduler uses: the fields are
expanded with ``croniter.expand`` and the next runs come from croniter walking the wall clock of
Hermes's timezone setting, exactly like ``cron/jobs.py::compute_next_run``. This module only puts
that reading into words and adds notes for the cases people get wrong.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, tzinfo
from typing import List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

MAX_LENGTH = 200
NEXT_RUNS = 5
_DAY = 86400

FIELD_NAMES = ("minute", "hour", "day of month", "month", "day of week", "second", "year")
_MINUTE, _HOUR, _DOM, _MONTH, _DOW, _SECOND, _YEAR = range(7)

MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", "l": "last"}

# cron/jobs.py::parse_schedule only hands a schedule to croniter when its first five fields match
# this; anything else is read as an interval, a timestamp or a duration, or rejected.
_HERMES_FIELD = re.compile(r"^[A-Za-z\d\*\-,/]+$")

EXAMPLES = (
    "0 9 * * 1-5",
    "*/15 * * * *",
    "30 18 1 * *",
)


def _croniter():
    try:
        from croniter import croniter
    except ImportError:  # Hermes installs croniter for its own scheduler; a bare Python may not have it
        raise ValueError("croniter is not installed; Hermes installs it for its scheduler, "
                         "so run this inside Hermes") from None
    return croniter


def _join(items: Sequence[str], word: str = "and") -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {word} {items[-1]}"


def _runs(values: Sequence[int]) -> List[Tuple[int, int]]:
    """Sorted integers grouped into (first, last) runs of consecutive values."""
    runs: List[Tuple[int, int]] = []
    for value in sorted(values):
        if runs and value == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], value)
        else:
            runs.append((value, value))
    return runs


def _numbers(values: Sequence[int]) -> str:
    """'1-5, 10 and 15': runs of three or more as ranges."""
    parts = []
    for first, last in _runs(values):
        if last - first >= 2:
            parts.append(f"{first}-{last}")
        else:
            parts.extend(str(v) for v in range(first, last + 1))
    return _join(parts)


def _named(values: Sequence[int], names: Sequence[str], offset: int = 0) -> str:
    """'Monday through Friday', 'January and July'."""
    parts = []
    for first, last in _runs(values):
        if last - first >= 2:
            parts.append(f"{names[first - offset]} through {names[last - offset]}")
        else:
            parts.extend(names[v - offset] for v in range(first, last + 1))
    return _join(parts)


def _step(values: Sequence[int], low: int, high: int) -> Optional[int]:
    """The step when ``values`` is every n-th value from its first one to the end of the field."""
    if len(values) < 2:
        return None
    step = values[1] - values[0]
    if list(values) == list(range(values[0], high + 1, step)) and values[0] - step < low:
        return step
    return None


def _clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


class Reading:
    """One cron expression as croniter expands it."""

    def __init__(self, raw: str, expr: str, fields: List[str], expanded: list, nth: dict):
        self.raw = raw            # what was written
        self.expr = expr          # what croniter reads (a macro becomes its five fields)
        self.fields = fields      # the written fields
        self.expanded = expanded  # croniter.expand(): one list per field, ["*"] for "any"
        self.nth = nth            # croniter.expand(): weekday -> {1..5, "l"} for 5#3 / L5

    def any(self, index: int) -> bool:
        return index >= len(self.expanded) or self.expanded[index] == ["*"]

    def values(self, index: int) -> list:
        return [] if self.any(index) else list(self.expanded[index])

    @property
    def either_day(self) -> bool:
        """croniter's day rule: with both day fields restricted, a day matching either one runs."""
        return not self.any(_DOM) and not self.any(_DOW)


def parse(raw: str) -> Reading:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    if not text:
        raise ValueError('no cron expression given: write @crontab:"0 9 * * 1-5"')
    if len(text) > MAX_LENGTH:
        raise ValueError(f"that is {len(text)} characters; a cron expression is far shorter "
                         f"(at most {MAX_LENGTH} here)")
    lowered = text.lower()
    if lowered.startswith("@"):
        if lowered == "@reboot":
            raise ValueError("@reboot means 'when the system starts'; it is not a schedule "
                             "croniter or Hermes can run")
        if lowered not in MACROS:
            raise ValueError(f"unknown shortcut {text}; the known ones are {_join(sorted(MACROS), 'and')}")
        expr = MACROS[lowered]
    else:
        expr = " ".join(text.split())
    fields = expr.split(" ")
    if len(fields) not in (5, 6, 7):
        raise ValueError(
            f"a cron expression has 5 fields (minute hour day-of-month month day-of-week), "
            f"optionally a 6th (second) and 7th (year); this has {len(fields)}"
            + ('. Values with spaces go in quotes: @crontab:"0 9 * * 1-5"' if len(fields) < 5 else ""))
    croniter = _croniter()
    try:
        expanded, nth = croniter.expand(expr)
    except Exception as exc:  # croniter raises its own error types; the message is what matters
        raise ValueError(f"croniter cannot read {expr!r}: {exc}") from None
    nth = dict(nth or {})
    if any(not isinstance(day, int) for day in nth):
        raise ValueError(f"{fields[_DOW]} (an nth weekday on any day) is not something crontab-ref explains")
    return Reading(text, expr, fields, expanded, nth)


def hermes_verdict(reading: Reading) -> Tuple[bool, str]:
    """Whether Hermes's ``/cron`` would take this text as a cron expression, and why not."""
    if reading.raw.startswith("@"):
        return False, (f"Hermes's /cron does not take {reading.raw}; "
                       f"write the five fields instead: {reading.expr}")
    bad = [f for f in reading.fields[:5] if not _HERMES_FIELD.match(f)]
    if bad:
        return False, (f"Hermes reads a schedule as cron only when its first five fields use digits, "
                       f"letters and * , - /; {_join(bad)} has other characters, "
                       f"so /cron would not treat this as a cron expression")
    # parse() already had croniter read the expression, which is the rest of Hermes's check.
    return True, "accepted as a cron expression"


def _hours_phrase(hours: Sequence[int]) -> str:
    parts = []
    for first, last in _runs(hours):
        if first == last:
            parts.append(f"{first:02d}:00-{first:02d}:59")
        else:
            parts.append(f"{first:02d}:00-{last:02d}:59")
    return _join(parts)


def _time_phrase(reading: Reading) -> str:
    minutes, hours = reading.values(_MINUTE), reading.values(_HOUR)
    any_minute, any_hour = reading.any(_MINUTE), reading.any(_HOUR)
    if any_minute and any_hour:
        phrase = "Every minute"
    elif any_minute:
        phrase = f"Every minute during {_hours_phrase(hours)}"
    elif any_hour:
        step = _step(minutes, 0, 59)
        if len(minutes) == 1:
            phrase = f"At minute {minutes[0]} of every hour"
        elif step and minutes[0] == 0:
            phrase = f"Every {step} minutes"
        elif step:
            phrase = f"Every {step} minutes from minute {minutes[0]} of every hour"
        else:
            phrase = f"At minutes {_numbers(minutes)} of every hour"
    elif len(minutes) * len(hours) <= 8:
        phrase = "At " + _join([_clock(h, m) for h in hours for m in minutes])
    else:
        step = _step(minutes, 0, 59)
        if step and minutes[0] == 0:
            each = f"Every {step} minutes"
        elif len(minutes) == 1:
            each = f"At minute {minutes[0]}"
        else:
            each = f"At minutes {_numbers(minutes)}"
        phrase = (f"{each} during {_hours_phrase(hours)} "
                  f"(first {_clock(hours[0], minutes[0])}, last {_clock(hours[-1], minutes[-1])})")
    if len(reading.expanded) > _SECOND and not reading.any(_SECOND):
        seconds = reading.values(_SECOND)
        if seconds != [0]:
            phrase += f", at second {_numbers(seconds)}" if len(seconds) == 1 else f", at seconds {_numbers(seconds)}"
    elif len(reading.expanded) > _SECOND:
        phrase += ", every second"
    return phrase


def _dom_phrase(reading: Reading) -> str:
    days = reading.values(_DOM)
    last = "l" in days
    numbers = [d for d in days if d != "l"]
    parts = []
    if numbers:
        parts.append(f"day {numbers[0]}" if len(numbers) == 1 else f"days {_numbers(numbers)}")
    if last:
        parts.append("the last day")
    return f"{_join(parts)} of the month"


def _dow_phrase(reading: Reading) -> str:
    days = reading.values(_DOW)
    if reading.nth:
        parts = []
        for day in sorted(reading.nth):
            nths = sorted(reading.nth[day], key=lambda n: 6 if n == "l" else n)
            parts.append(f"the {_join([_ORDINALS[n] for n in nths])} {WEEKDAYS[day]}")
        return f"{_join(parts)} of the month"
    return _named(days, WEEKDAYS)


def _day_phrase(reading: Reading) -> str:
    any_dom, any_dow = reading.any(_DOM), reading.any(_DOW)
    if any_dom and any_dow:
        day = ""
    elif any_dow:
        day = f"on {_dom_phrase(reading)}"
    elif any_dom:
        day = f"on {_dow_phrase(reading)}"
    else:
        day = f"on {_dom_phrase(reading)} or on {_dow_phrase(reading)}"
    if not reading.any(_MONTH):
        day += (" " if day else "") + f"in {_named(reading.values(_MONTH), MONTHS, offset=1)}"
    if len(reading.expanded) > _YEAR and not reading.any(_YEAR):
        years = reading.values(_YEAR)
        day += (" " if day else "") + (f"in the year {years[0]}" if len(years) == 1
                                       else f"in the years {_numbers(years)}")
    return day


def summary(reading: Reading) -> str:
    time_part, day_part = _time_phrase(reading), _day_phrase(reading)
    if (reading.any(_DOM) and reading.any(_DOW) and time_part.startswith("At ")
            and not reading.any(_HOUR)):
        day_part = "every day" + (f" {day_part}" if day_part else "")
    return f"{time_part}{' ' + day_part if day_part else ''}."


def _field_value(reading: Reading, index: int) -> str:
    """What one field means, next to how it was written."""
    if reading.any(index):
        return "any"
    values = reading.values(index)
    if index == _DOW:
        if reading.nth:
            return _dow_phrase(reading)
        return _named(values, WEEKDAYS)
    if index == _MONTH:
        return _named(values, MONTHS, offset=1)
    if index == _DOM:
        numbers = _numbers([v for v in values if v != "l"])
        return _join([p for p in (numbers, "last day" if "l" in values else "") if p])
    return _numbers(values)


def _has_number(field: str, number: int) -> bool:
    return any(token == str(number) for token in re.split(r"[,\-/]", field))


def notes(reading: Reading) -> List[str]:
    out = []
    minutes, hours, days = reading.values(_MINUTE), reading.values(_HOUR), reading.values(_DOM)
    if reading.either_day:
        if [d for d in days if d != "l"] == list(range(1, 32)):
            out.append("day of month covers every day, so day of week changes nothing: this runs every day")
        else:
            out.append("day of month and day of week are both set, so a day that matches EITHER one runs "
                       "(croniter's rule, as in standard cron), not only days that match both")
    if reading.any(_MINUTE) and not reading.any(_HOUR):
        out.append(f"the minute field is *, so this runs every minute of those hours "
                   f"({60 * len(hours)} times a day); for once at {_clock(hours[0], 0)} "
                   f"write 0 {reading.fields[_HOUR]} ...")
    step = _step(minutes, 0, 59)
    if step and minutes[0] == 0 and 60 % step:
        out.append(f"*/{step} in the minute field starts again every hour: after :{minutes[-1]:02d} "
                   f"the next run is :00, {60 - minutes[-1]} minutes later, not {step}")
    step = _step(hours, 0, 23)
    if step and hours[0] == 0 and 24 % step:
        out.append(f"*/{step} in the hour field starts again every day: after {_clock(hours[-1], 0)} "
                   f"the next run is 00:00, {24 - hours[-1]} hours later, not {step}")
    numbers = [d for d in days if d != "l"]
    if numbers and min(numbers) >= 29 and not reading.either_day:
        out.append(f"day {_numbers(numbers)} is missing from some months, and those months are skipped "
                   f"(no run on their last day instead); L means the last day of every month")
    if _has_number(reading.fields[_DOW], 7):
        out.append("7 in day of week means Sunday, the same as 0")
    if len(reading.fields) >= 6:
        out.append("croniter reads the 6th field as seconds"
                   + (" and the 7th as the year" if len(reading.fields) == 7 else ""))
    return out


def _span(seconds: float) -> str:
    whole = int(abs(seconds))
    if whole < 60:
        return "less than a minute"
    parts = []
    for size, label in ((_DAY, "day"), (3600, "hour"), (60, "minute")):
        count, whole = divmod(whole, size)
        if count or parts:
            parts.append(f"{count} {label}{'' if count == 1 else 's'}")
        if len(parts) == 2:
            break
    if parts and parts[-1].startswith("0 "):
        parts.pop()
    return " ".join(parts)


def _offset(moment: datetime) -> str:
    total = int(moment.utcoffset().total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    return f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


def configured_zone_name() -> str:
    """Hermes's timezone setting, resolved the way hermes_time does it: ``HERMES_TIMEZONE``
    (ignored under the multiplexed gateway, where it holds only the default profile's value),
    then ``timezone`` in config.yaml. Empty when nothing is set or outside Hermes."""
    try:
        from agent.secret_scope import is_multiplex_active
        from hermes_cli.config import load_config_readonly
    except ImportError:
        return ""
    if not is_multiplex_active():
        name = os.getenv("HERMES_TIMEZONE", "").strip()
        if name:
            return name
    value = (load_config_readonly() or {}).get("timezone", "")
    return value.strip() if isinstance(value, str) else ""


def hermes_now() -> Tuple[datetime, tzinfo, str]:
    """Now, the zone cron/jobs.py schedules in (the configured one, else this computer's), and a label."""
    name = configured_zone_name()
    if name:
        try:
            zone = ZoneInfo(name)
        except Exception:  # Hermes logs the same mistake and falls back to local time
            local = datetime.now().astimezone()
            return local, local.tzinfo, (f"UTC{_offset(local)} (this computer's time; the timezone "
                                         f"setting {name!r} is not a time zone Python knows)")
        return datetime.now(zone), zone, f"{name} (Hermes's timezone setting)"
    local = datetime.now().astimezone()
    return local, local.tzinfo, f"UTC{_offset(local)} (this computer's time; no timezone is set in Hermes)"


def _next_after(croniter, expr: str, base: datetime, zone: tzinfo) -> datetime:
    """The next run after ``base``, worked out as cron/jobs.py::compute_next_run does it: croniter
    walks the zone's wall clock, the zone is attached to the result, and of a repeated autumn hour
    the earliest instant strictly after the base wins."""
    iterator = croniter(expr, base.astimezone(zone).replace(tzinfo=None))
    base_ts = base.timestamp()
    wall = iterator.get_next(datetime)
    for _ in range(2):
        for fold in (0, 1):
            candidate = wall.replace(tzinfo=zone, fold=fold)
            if candidate.timestamp() > base_ts:
                return candidate
        wall = iterator.get_next(datetime)
    return wall.replace(tzinfo=zone)


def next_runs(reading: Reading, now: datetime, zone: Optional[tzinfo] = None,
              count: int = NEXT_RUNS) -> List[datetime]:
    croniter = _croniter()
    zone = zone or now.tzinfo
    runs: List[datetime] = []
    base = now
    for _ in range(count):
        try:
            base = _next_after(croniter, reading.expr, base, zone)
        except Exception:  # CroniterBadDateError: no date in croniter's search window matches
            break
        runs.append(base)
    return runs


def describe(reading: Reading, now: datetime, zone: tzinfo, label: str) -> str:
    lines = [f'Cron expression "{reading.raw}", as Hermes\'s scheduler (croniter) reads it',
             f"  {summary(reading)}"]
    if reading.expr != reading.raw:
        lines.append(f"  same as {reading.expr}")
    width = max(len(FIELD_NAMES[i]) for i in range(len(reading.fields)))
    fwidth = max(len(f) for f in reading.fields)
    for index, field in enumerate(reading.expr.split(" ")):
        lines.append(f"  {FIELD_NAMES[index].ljust(width)}  {field.ljust(fwidth)}  {_field_value(reading, index)}")
    accepted, why = hermes_verdict(reading)
    lines.append(f"Hermes /cron: {why}")
    runs = next_runs(reading, now, zone)
    if not runs:
        lines.append("Next runs: none. No date matches this expression, so it never runs")
    else:
        count = "Next run" if len(runs) == 1 else f"Next {len(runs)} runs"
        after = "; no run after that" if len(runs) < NEXT_RUNS else ""
        lines.append(f"{count}, in {label}{after}")
        seconds = len(reading.expanded) > _SECOND
        for run in runs:
            clock = run.strftime("%a %Y-%m-%d %H:%M:%S" if seconds else "%a %Y-%m-%d %H:%M")
            lines.append(f"  {clock} {_offset(run)}  in {_span((run - now).total_seconds())}")
    extra = notes(reading)
    if extra:
        lines.append("Notes")
        lines += [f"  - {note}" for note in extra]
    return "\n".join(lines)


def expand(target: str, *, now: Optional[datetime] = None, zone: Optional[tzinfo] = None,
           label: Optional[str] = None) -> str:
    """What ``@crontab:<target>`` puts into the message. ``now``/``zone``/``label`` are for tests;
    by default they come from Hermes's clock and timezone setting."""
    reading = parse(target)
    if now is None:
        now, zone, label = hermes_now()
    zone = zone or now.tzinfo
    return describe(reading, now, zone, label or f"UTC{_offset(now)}")


def preview(query: str) -> List[Tuple[str, str, str]]:
    """Suggestions for the TUI and desktop app: (inserted text, display, meta)."""
    text = query.strip().strip("\"'`").strip()
    if not text:
        out = []
        for example in EXAMPLES:
            out.append((f'"{example}"', f"{example} = {summary(parse(example))}", "example"))
        return out
    try:
        reading = parse(text)
    except ValueError:
        return []
    inserted = f'"{reading.raw}"' if " " in reading.raw else reading.raw
    return [(inserted, f"{reading.raw} = {summary(reading)}", "cron expression")]
