"""error-ledger: what one failed provider attempt becomes, how it is stored and how /errors reads."""

import json
import time

import pytest

NOW = 1_800_000_000.0


def _payload(**over):
    base = {"task_id": "t", "turn_id": "turn-1", "api_request_id": "req-1", "session_id": "s1",
            "platform": "cli", "model": "m-large", "provider": "custom",
            "base_url": "https://user:SECRET-URL-PASS@example.invalid/v1", "api_mode": "chat_completions",
            "api_call_count": 1, "api_duration": 1.2349, "started_at": NOW - 1.2349, "ended_at": NOW,
            "status_code": 429, "retry_count": 1, "max_retries": 3, "retryable": True,
            "reason": "rate_limit",
            "error": {"type": "RateLimitError", "message": "SECRET-ERROR-TEXT slow down"},
            "request": {"method": "POST", "body": {"messages": [{"role": "user", "content": "SECRET-PROMPT"}]}}}
    base.update(over)
    return base


def test_row_keeps_the_classification_and_none_of_the_text(errledger):
    row = errledger.build_row(_payload())
    assert row == {"ts": NOW, "session_id": "s1", "platform": "cli", "provider": "custom",
                   "model": "m-large", "api_mode": "chat_completions", "status": 429,
                   "reason": "rate_limit", "error_type": "RateLimitError", "retryable": True,
                   "retry_count": 1, "duration_s": 1.235}
    assert "SECRET" not in json.dumps(row)


@pytest.mark.parametrize("value, status", [
    (429, 429), ("503", 503), (529.0, 529), (None, None), ("rate_limit_exceeded", None),
    (True, None), (0, None), (99, None), (600, None), (-1, None)])
def test_status_is_an_http_status_or_nothing(errledger, value, status):
    assert errledger.build_row(_payload(status_code=value))["status"] == status


def test_row_tolerates_a_sparse_or_odd_payload(errledger):
    row = errledger.build_row({})
    assert row["provider"] == row["model"] == row["reason"] == "unknown"
    assert row["status"] is None and row["retryable"] is None and row["error_type"] == ""
    assert row["retry_count"] == 0 and row["duration_s"] == 0.0 and row["ts"] == 0.0
    odd = errledger.build_row(_payload(error="boom", retryable="yes", retry_count=-3, api_duration=-5,
                                       reason="r" * 500, model="m" * 500))
    assert odd["error_type"] == "" and odd["retryable"] is None and odd["retry_count"] == 0
    assert odd["duration_s"] == 0.0 and len(odd["reason"]) == 60 and len(odd["model"]) == 200


def test_storage_round_trip_bound_and_clear(errledger, tmp_path, monkeypatch):
    path = tmp_path / errledger.FILENAME
    assert errledger.read(path) == [] and errledger.clear(path) == 0
    for i in range(3):
        errledger.append(path, errledger.build_row(_payload(retry_count=i)))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["retry_count"] for r in errledger.read(path)] == [0, 1, 2]
    assert b"\r\n" not in path.read_bytes()

    monkeypatch.setattr(errledger, "MAX_BYTES", 400)
    monkeypatch.setattr(errledger, "KEEP_LINES", 2)
    errledger.append(path, errledger.build_row(_payload(retry_count=7)))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["retry_count"] for r in errledger.read(path)] == [7]
    assert not list(tmp_path.glob(".errors-*"))
    assert errledger.clear(path) == 1 and not path.exists()


def _row(model, reason, status, retryable=True, provider="p1", session="s1", ts=NOW):
    return {"ts": ts, "session_id": session, "provider": provider, "model": model, "reason": reason,
            "status": status, "retryable": retryable}


def test_select_windows(errledger):
    rows = [_row("a", "timeout", None, ts=NOW - 2 * 86400, session="old"),
            _row("a", "rate_limit", 429, session="s1"), _row("b", "overloaded", 529, session="s2"),
            _row("b", "overloaded", 529, session="")]
    assert len(errledger.select(rows, "all", NOW)) == 4
    assert [r["session_id"] for r in errledger.select(rows, "day", NOW)] == ["s1", "s2", ""]
    assert [r["model"] for r in errledger.select(rows, "session", NOW)] == ["b"]
    assert errledger.select([_row("a", "x", None, session="")], "session", NOW) == []


def test_summary_by_model_reason_and_provider(errledger):
    rows = [_row("m-large", "rate_limit", 429), _row("m-large", "rate_limit", 429),
            _row("m-large", "overloaded", 529), _row("m-large", "context_overflow", 400, retryable=False),
            _row("m-small", "timeout", None, provider="p2"), _row("m-small", "server_error", 500, provider="p2")]
    lines = errledger.summarize(rows, title="last 24 hours").splitlines()
    assert lines[0] == "Provider errors, last 24 hours:"
    assert lines[2].split() == ["model", "errors", "retryable", "top", "reason", "top", "status"]
    assert lines[3].split() == ["m-large", "4", "3", "rate_limit", "(2)", "429", "(2)"]
    # A tie goes to the name that sorts first, so the report is stable.
    assert lines[4].split() == ["m-small", "2", "2", "server_error", "(1)", "500", "(1)"]
    assert lines[5].split() == ["total", "6", "5", "rate_limit", "(2)", "429", "(2)"]
    assert lines[-1].startswith("One row per failed attempt")

    by_reason = errledger.summarize(rows, title="x", by="reason").splitlines()
    assert by_reason[2].split()[:1] == ["reason"] and "top model" in by_reason[2]
    assert by_reason[3].split() == ["rate_limit", "2", "2", "m-large", "(2)", "429", "(2)"]
    by_provider = errledger.summarize(rows, title="x", by="provider").splitlines()
    assert by_provider[3].split()[:3] == ["p1", "4", "3"] and by_provider[4].split()[:3] == ["p2", "2", "2"]


def test_summary_edges(errledger):
    assert errledger.summarize([], title="all recorded") == "Provider errors, all recorded: none recorded."
    one = errledger.summarize([_row("m", "timeout", None, retryable=None)], title="x").splitlines()
    assert one[3].split() == ["m", "1", "0", "timeout", "(1)", "-"]
    assert not any(line.startswith("total") for line in one)


class _Ctx:
    def __init__(self):
        self.hooks, self.commands = {}, {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


@pytest.fixture
def wired(errors_plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(errors_plugin, "_ledger_path", lambda: tmp_path / errors_plugin.ledger.FILENAME)
    ctx = _Ctx()
    errors_plugin.register(ctx)
    return ctx, tmp_path / errors_plugin.ledger.FILENAME


def test_plugin_surface_and_round_trip(wired):
    ctx, path = wired
    assert sorted(ctx.hooks) == ["api_request_error"] and sorted(ctx.commands) == ["errors"]
    hook, errors = ctx.hooks["api_request_error"], ctx.commands["errors"]
    assert errors("") == "Provider errors, last 24 hours: none recorded."
    hook(**_payload(ended_at=time.time()))
    hook(**_payload(ended_at=time.time(), session_id="s2", model="m-small", provider="other",
                    status_code=529, reason="overloaded"))
    assert "SECRET" not in path.read_text(encoding="utf-8-sig")
    assert errors("").splitlines()[3].split()[:3] == ["m-large", "1", "1"]
    session = errors("session").splitlines()
    assert session[0] == "Provider errors, latest session:" and session[3].split()[:2] == ["m-small", "1"]
    assert errors("all reasons").splitlines()[3].split()[:2] == ["overloaded", "1"]
    assert errors("all providers").splitlines()[3].split()[:2] == ["custom", "1"]
    assert errors("all models").splitlines()[2].split()[0] == "model"


def test_command_help_unknown_and_clear(wired):
    ctx, path = wired
    hook, errors = ctx.hooks["api_request_error"], ctx.commands["errors"]
    assert errors("help").startswith("/errors:")
    assert errors("bogus").startswith("Unknown option: bogus")
    hook(**_payload(ended_at=time.time()))
    assert errors("clear") == "error-ledger: deleted 1 recorded error(s)." and not path.exists()
    assert errors("all") == "Provider errors, all recorded: none recorded."


def test_hook_never_raises(errors_plugin, monkeypatch):
    def boom():
        raise OSError("read-only home")

    monkeypatch.setattr(errors_plugin, "_ledger_path", boom)
    errors_plugin._on_api_request_error(**_payload())


@pytest.mark.parametrize("word, seconds", [
    ("7d", 7 * 86400.0), ("1d", 86400.0), ("12h", 43200.0), ("9999h", 9999 * 3600.0),
    ("0d", None), ("00h", None), ("d", None), ("7", None), ("7w", None), ("-1d", None),
    ("1.5h", None), ("12345d", None), ("\u0667d", None), ("", None), ("day", None)])
def test_span_words(errledger, word, seconds):
    assert errledger.parse_span(word) == seconds


def test_span_titles(errledger):
    assert [errledger.span_title(w) for w in ("1h", "6h", "1d", "30d")] == [
        "last 1 hour", "last 6 hours", "last 1 day", "last 30 days"]


def test_select_span(errledger):
    rows = [_row("a", "timeout", None, ts=NOW - 5 * 86400), _row("b", "timeout", None, ts=NOW - 2 * 86400),
            _row("c", "timeout", None, ts=NOW - 60)]
    assert [r["model"] for r in errledger.select(rows, "day", NOW, 3 * 86400.0)] == ["b", "c"]
    assert [r["model"] for r in errledger.select(rows, "day", NOW, 3600.0)] == ["c"]
    # The span never narrows the other two windows.
    assert len(errledger.select(rows, "all", NOW, 60.0)) == 3
    assert len(errledger.select(rows, "session", NOW, 1.0)) == 3


def test_recent_lists_newest_first(errledger):
    rows = [_row("m-old", "timeout", None, retryable=None, ts=NOW - 3 * 86400 - 5),
            _row("m-mid", "overloaded", 529, provider="p2", ts=NOW - 2 * 3600 - 1),
            _row("m-new", "rate_limit", 429, ts=NOW - 125),
            _row("m-now", "context_overflow", 400, retryable=False, ts=NOW - 7)]
    lines = errledger.recent(rows, NOW, title="all recorded").splitlines()
    assert lines[0] == "Provider errors, all recorded, newest 4 of 4:"
    assert lines[2].split() == ["when", "model", "provider", "reason", "status", "retryable"]
    assert lines[3].split() == ["7s", "ago", "m-now", "p1", "context_overflow", "400", "no"]
    assert lines[4].split() == ["2m", "ago", "m-new", "p1", "rate_limit", "429", "yes"]
    assert lines[5].split() == ["2h", "ago", "m-mid", "p2", "overloaded", "529", "yes"]
    assert lines[6].split() == ["3d", "ago", "m-old", "p1", "timeout", "-", "-"]
    limited = errledger.recent(rows, NOW, title="x", limit=2).splitlines()
    assert limited[0] == "Provider errors, x, newest 2 of 4:" and len(limited) == 5
    assert [line.split()[2] for line in limited[3:]] == ["m-now", "m-new"]
    assert errledger.recent([], NOW, title="last 7 days") == "Provider errors, last 7 days: none recorded."
    # A clock that moved backwards, or a row without a time, is never a negative age.
    assert errledger.recent([_row("m", "x", None, ts=NOW + 50)], NOW, title="x").splitlines()[3].startswith("0s ago")


def test_default_recent_limit_is_ten(errledger):
    rows = [_row(f"m{i}", "timeout", None, ts=NOW - 100 + i) for i in range(14)]
    lines = errledger.recent(rows, NOW, title="x").splitlines()
    assert lines[0] == "Provider errors, x, newest 10 of 14:"
    assert [line.split()[2] for line in lines[3:]] == [f"m{i}" for i in range(13, 3, -1)]


def test_command_time_window_and_recent(wired):
    ctx, path = wired
    hook, errors = ctx.hooks["api_request_error"], ctx.commands["errors"]
    now = time.time()
    hook(**_payload(ended_at=now - 5 * 86400, model="m-week", session_id="old"))
    hook(**_payload(ended_at=now - 30, model="m-today", status_code=529, reason="overloaded"))
    assert [line.split()[0] for line in errors("").splitlines()[3:4]] == ["m-today"]
    week = errors("7d").splitlines()
    assert week[0] == "Provider errors, last 7 days:"
    assert sorted(line.split()[0] for line in week[3:5]) == ["m-today", "m-week"]
    assert errors("3d").splitlines()[0] == "Provider errors, last 3 days:"
    assert "m-week" not in errors("3d") and "m-week" not in errors("1h")
    assert errors("7d providers").splitlines()[3].split()[:2] == ["custom", "2"]
    listing = errors("7d recent").splitlines()
    assert listing[0] == "Provider errors, last 7 days, newest 2 of 2:"
    assert listing[3].split()[2:] == ["m-today", "custom", "overloaded", "529", "yes"]
    assert listing[4].split()[:3] == ["5d", "ago", "m-week"]
    assert errors("recent").splitlines()[0] == "Provider errors, last 24 hours, newest 1 of 1:"
    # session and all keep their meaning when a span is typed next to them.
    assert errors("session 1h").splitlines()[0] == "Provider errors, latest session:"
    assert errors("all 1h recent").splitlines()[0] == "Provider errors, all recorded, newest 2 of 2:"
    assert errors("0d").startswith("Unknown option: 0d") and errors("7w").startswith("Unknown option: 7w")
    assert "/errors 7d" in errors("help") and "/errors recent" in errors("help")


@pytest.mark.parametrize("seconds, shown", [
    (0, "0s ago"), (59.9, "59s ago"), (60, "1m ago"), (3599, "59m ago"), (3600, "1h ago"),
    (86399, "23h ago"), (86400, "1d ago"), (40 * 86400, "40d ago")])
def test_recent_ages_change_unit_at_the_boundary(errledger, seconds, shown):
    line = errledger.recent([_row("m", "timeout", None, ts=NOW - seconds)], NOW, title="x").splitlines()[3]
    assert line.startswith(shown + " ")


def _local(year, month, day, hour=12, minute=0):
    from datetime import datetime

    return datetime(year, month, day, hour, minute).timestamp()


def test_summary_by_day_is_newest_day_first(errledger):
    rows = [_row("m-a", "rate_limit", 429, ts=_local(2027, 1, 8, 9)),
            _row("m-a", "rate_limit", 429, ts=_local(2027, 1, 10, 0, 1)),
            _row("m-b", "overloaded", 529, retryable=False, ts=_local(2027, 1, 10, 23, 59)),
            _row("m-a", "overloaded", 529, ts=_local(2027, 1, 10, 13)),
            _row("m-a", "timeout", None, ts=_local(2027, 1, 9, 23, 59)),
            _row("m-a", "timeout", None, ts=_local(2027, 1, 9, 0, 0))]
    lines = errledger.summarize(rows, title="last 7 days", by="day").splitlines()
    assert lines[2].split() == ["day", "errors", "retryable", "top", "reason", "top", "status"]
    # Days, not counts, set the order: the smallest day can sit above a busier one.
    assert lines[3].split() == ["2027-01-10", "3", "2", "overloaded", "(2)", "529", "(2)"]
    assert lines[4].split() == ["2027-01-09", "2", "2", "timeout", "(2)", "-"]
    assert lines[5].split() == ["2027-01-08", "1", "1", "rate_limit", "(1)", "429", "(1)"]
    assert lines[6].split()[:2] == ["total", "6"]


def test_day_of_a_row_without_a_time_goes_last(errledger):
    assert errledger.local_day(_local(2027, 3, 4, 0, 0)) == "2027-03-04"
    assert errledger.local_day(None) == errledger.local_day("soon") == errledger.local_day(1e300) == "unknown"
    rows = [{"model": "m", "reason": "timeout"}, _row("m", "timeout", None, ts=_local(2027, 1, 2)),
            _row("m", "timeout", None, ts=_local(2027, 1, 1))]
    lines = errledger.summarize(rows, title="x", by="day").splitlines()
    assert [line.split()[0] for line in lines[3:7]] == ["2027-01-02", "2027-01-01", "unknown", "total"]


def test_command_days(wired):
    ctx, _path = wired
    hook, errors = ctx.hooks["api_request_error"], ctx.commands["errors"]
    now = time.time()
    hook(**_payload(ended_at=now - 3 * 86400, model="m-old"))
    # Both at the same moment, so a run at midnight cannot split them over two days.
    hook(**_payload(ended_at=now - 10, model="m-new"))
    hook(**_payload(ended_at=now - 10, model="m-new"))
    week = errors("7d days").splitlines()
    assert week[0] == "Provider errors, last 7 days:" and week[2].split()[0] == "day"
    assert [line.split()[:2] for line in week[3:6]] == [
        [time.strftime("%Y-%m-%d", time.localtime(now - 10)), "2"],
        [time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400)), "1"], ["total", "3"]]
    assert [line.split()[1] for line in errors("days").splitlines()[3:4]] == ["2"]
    assert errors("session days").splitlines()[0] == "Provider errors, latest session:"
    assert "/errors days" in errors("help")
    # recent still lists failures, not days.
    assert errors("all days recent").splitlines()[2].split()[0] == "when"


BOM = "﻿".encode("utf-8")


def test_a_ledger_saved_with_a_bom_still_reads_and_trims(errledger, tmp_path, monkeypatch):
    # Windows tools (PowerShell's Set-Content, some editors) add a BOM to files they touch.
    path = tmp_path / errledger.FILENAME
    row = _row("m-bom", "timeout", None)
    path.write_bytes(BOM + (json.dumps(row) + "\n").encode("utf-8"))
    assert [r["model"] for r in errledger.read(path)] == ["m-bom"]
    monkeypatch.setattr(errledger, "MAX_BYTES", 10)
    monkeypatch.setattr(errledger, "KEEP_LINES", 2)
    errledger.append(path, _row("m-next", "timeout", None))
    assert [r["model"] for r in errledger.read(path)] == ["m-bom", "m-next"]
    assert not path.read_bytes().startswith(BOM), "the trim rewrites the file without the BOM"
