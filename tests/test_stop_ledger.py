"""stop-ledger: what one stopped turn becomes, how it is stored and how /stop-log reads."""

import json
import time

import pytest

NOW = 1_800_000_000.0
KEY = "agent:main:telegram:dm:SECRET-CHAT-ID"


def _payload(**over):
    base = {"session_key": KEY, "platform": "telegram", "reason": "Stop requested",
            "invalidation_reason": "stop_command"}
    base.update(over)
    return base


def test_row_keeps_where_and_why_and_only_a_hash_of_the_session(stledger):
    row = stledger.build_row(_payload(), now=NOW)
    assert row == {"ts": NOW, "session": stledger.session_tag(KEY), "platform": "telegram",
                   "cause": "stop", "detail": "stop_command"}
    assert len(row["session"]) == 12 and "SECRET" not in json.dumps(row)
    assert stledger.session_tag("") == "" and stledger.session_tag(None) == ""


@pytest.mark.parametrize("reason, detail, cause", [
    # gateway /stop while a turn runs (busy path and the normal /stop handler)
    ("Stop requested", "stop_command", "stop"), ("Stop requested", "stop_command_handler", "stop"),
    # gateway /new or /reset while a turn runs
    ("Session reset requested", "new_command", "new"), ("Session reset requested", "reset_command", "new"),
    # TUI and desktop Stop
    ("user_stop", "session_interrupt", "stop"),
    ("", "", "other"), ("something else", "unknown", "other")])
def test_cause_of_every_reason_hermes_sends(stledger, reason, detail, cause):
    assert stledger.cause(reason, detail) == cause


def test_row_tolerates_a_sparse_or_odd_payload(stledger):
    row = stledger.build_row({}, now=NOW)
    assert row == {"ts": NOW, "session": "", "platform": "unknown", "cause": "other", "detail": "unknown"}
    odd = stledger.build_row(_payload(platform="p" * 500, invalidation_reason="d" * 500), now=NOW)
    assert len(odd["platform"]) == len(odd["detail"]) == 40


def test_storage_round_trip_bom_bound_and_clear(stledger, tmp_path, monkeypatch):
    path = tmp_path / stledger.FILENAME
    assert stledger.read(path) == [] and stledger.clear(path) == 0
    for platform in ("telegram", "discord", "tui"):
        stledger.append(path, stledger.build_row(_payload(platform=platform), now=NOW))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["platform"] for r in stledger.read(path)] == ["telegram", "discord", "tui"]
    assert b"\r\n" not in path.read_bytes()

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert [r["platform"] for r in stledger.read(bom)] == ["telegram", "discord", "tui"]

    monkeypatch.setattr(stledger, "MAX_BYTES", 400)
    monkeypatch.setattr(stledger, "KEEP_LINES", 2)
    stledger.append(path, stledger.build_row(_payload(platform="slack"), now=NOW))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["platform"] for r in stledger.read(path)] == ["slack"]
    assert not list(tmp_path.glob(".stops-*"))
    assert stledger.clear(path) == 1 and not path.exists()

    # A file another tool saved with a BOM is still trimmed to rows that parse.
    monkeypatch.setattr(stledger, "KEEP_LINES", 5)
    bom.write_bytes(b"\xef\xbb\xbf" + (json.dumps(_payload()) + "\n").encode("utf-8"))
    stledger.append(bom, stledger.build_row(_payload(platform="slack"), now=NOW))
    assert [r["platform"] for r in stledger.read(bom)] == ["telegram", "slack"]


def _row(platform="telegram", cause="stop", session="s1", ts=NOW, detail=None):
    return {"ts": ts, "session": session, "platform": platform, "cause": cause,
            "detail": detail or {"stop": "stop_command", "new": "new_command"}.get(cause, "unknown")}


def test_select_windows(stledger):
    rows = [_row(ts=NOW - 2 * 86400, session="old"), _row(session="s1"), _row("tui", session="s2"),
            _row("discord", session="")]
    assert len(stledger.select(rows, "all", NOW)) == 4
    assert len(stledger.select(rows, "day", NOW)) == 3
    assert len(stledger.select(rows, "day", NOW, span=3 * 86400)) == 4
    assert [r["platform"] for r in stledger.select(rows, "session", NOW)] == ["tui"]
    assert stledger.select([_row(session="")], "session", NOW) == []


def test_summary_puts_the_most_stops_first(stledger):
    rows = [_row("telegram", "stop", "a", NOW - 7200), _row("telegram", "new", "a", NOW - 600),
            _row("telegram", "stop", "b", NOW - 300), _row("tui", "stop", "c", NOW - 40),
            _row("discord", "other", "", NOW - 3 * 3600)]
    out = stledger.summarize(rows, NOW, title="last 24 hours")
    lines = out.splitlines()
    assert lines[0] == "Stopped turns, last 24 hours:"
    assert lines[2].split() == ["platform", "stops", "by", "/stop", "by", "/new", "sessions", "last"]
    assert lines[3].split() == ["telegram", "3", "2", "1", "2", "5m", "ago"]
    assert lines[4].split() == ["discord", "1", "0", "0", "0", "3h", "ago"]
    assert lines[5].split() == ["tui", "1", "1", "0", "1", "40s", "ago"]
    assert lines[6].split() == ["total", "5", "3", "1", "3", "40s", "ago"]
    assert lines[-1] == "Only turns that were still running count: a /stop or /new between turns is not a stop."


def test_summary_single_group_has_no_total_and_empty_says_so(stledger):
    lines = stledger.summarize([_row()], NOW, title="all recorded").splitlines()
    assert lines[3].split()[0] == "telegram" and not any(line.startswith("total") for line in lines)
    assert stledger.summarize([], NOW, title="all recorded") == "Stopped turns, all recorded: none recorded."


def test_days_newest_first_unknown_last(stledger):
    rows = [_row(ts=NOW - 86400), _row(ts=NOW), _row(ts=NOW), {"platform": "cli", "cause": "stop"}]
    out = stledger.summarize(rows, NOW, title="all recorded", by="day").splitlines()
    names = [line.split()[0] for line in out[3:-2]]
    assert names == [stledger.local_day(NOW), stledger.local_day(NOW - 86400), "unknown", "total"]


def test_recent_lists_newest_first(stledger):
    rows = [_row("telegram", "stop", "aaaaaaaaaaaa", NOW - 7200), _row("tui", "stop", "bbbbbbbbbbbb", NOW - 30),
            _row("slack", "new", "", NOW - 5)]
    out = stledger.recent(rows, NOW, title="all recorded", limit=2).splitlines()
    assert out[0] == "Stopped turns, all recorded, newest 2 of 3:"
    assert out[2].split() == ["when", "platform", "cause", "detail", "session"]
    assert out[3].split() == ["5s", "ago", "slack", "new", "new_command", "-"]
    assert out[4].split() == ["30s", "ago", "tui", "stop", "stop_command", "bbbbbbbbbbbb"]
    assert stledger.recent([], NOW, title="x") == "Stopped turns, x: none recorded."


@pytest.mark.parametrize("word, count", [("1", 1), ("25", 25), ("100", 100), ("0", None), ("101", None),
                                         ("9" * 5000, None), ("-5", None), ("٣", None), ("7d", None)])
def test_parse_count(stledger, word, count):
    assert stledger.parse_count(word) == count


@pytest.mark.parametrize("word, seconds", [("6h", 6 * 3600.0), ("7d", 7 * 86400.0), ("0d", None),
                                           ("12345h", None), ("7w", None), ("d", None), ("٣d", None)])
def test_parse_span(stledger, word, seconds):
    assert stledger.parse_span(word) == seconds


@pytest.fixture
def ledger_file(stops_plugin, tmp_path, monkeypatch):
    path = tmp_path / "stops.jsonl"
    monkeypatch.setattr(stops_plugin, "_ledger_path", lambda: path)
    return path


def test_hook_records_a_stop(stops_plugin, stledger, ledger_file):
    assert stops_plugin._on_stopped(**_payload(invalidation_reason="new_command")) is None
    [row] = stledger.read(ledger_file)
    assert (row["platform"], row["cause"], row["detail"]) == ("telegram", "new", "new_command")


def test_hook_never_raises(stops_plugin, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(stops_plugin, "_ledger_path", boom)
    stops_plugin._on_stopped(**_payload())
    stops_plugin._on_stopped(session_key=object())


def test_command(stops_plugin, stledger, ledger_file):
    now = time.time()
    # Oldest first, as the hook writes them.
    for platform, cause, session, ts in (("discord", "stop", "c", now - 3 * 86400),
                                         ("telegram", "stop", "a", now - 60), ("tui", "new", "b", now - 30)):
        stledger.append(ledger_file, _row(platform, cause, session, ts))
    out = stops_plugin._handle_stop_log("")
    assert out.startswith("Stopped turns, last 24 hours:") and "discord" not in out
    assert "discord" in stops_plugin._handle_stop_log("7d") and "last 7 days" in stops_plugin._handle_stop_log("7d")
    assert stops_plugin._handle_stop_log("session").splitlines()[3].split()[0] == "tui"
    assert stops_plugin._handle_stop_log("all days").splitlines()[2].split()[0] == "day"
    assert stops_plugin._handle_stop_log("all platforms").splitlines()[2].split()[0] == "platform"
    assert stops_plugin._handle_stop_log("all recent 2").splitlines()[0] == "Stopped turns, all recorded, newest 2 of 3:"
    assert stops_plugin._handle_stop_log("all recent").splitlines()[0] == "Stopped turns, all recorded, newest 3 of 3:"
    assert stops_plugin._handle_stop_log("recent 0").startswith("Unknown option: 0")
    assert stops_plugin._handle_stop_log("all 5").startswith("Unknown option: 5")
    assert stops_plugin._handle_stop_log("bogus").startswith("Unknown option: bogus")
    assert stops_plugin._handle_stop_log("help").startswith("/stop-log:")
    assert stops_plugin._handle_stop_log("clear") == "stop-ledger: deleted 3 recorded stop(s)."
    assert stops_plugin._handle_stop_log("all") == "Stopped turns, all recorded: none recorded."


def test_register_wires_the_hook_and_the_command(stops_plugin):
    calls = []

    class Ctx:
        def register_hook(self, name, cb):
            calls.append(("hook", name, cb))

        def register_command(self, name, handler, **kw):
            calls.append(("command", name, handler))

    stops_plugin.register(Ctx())
    assert calls == [("hook", "agent_loop_stopped", stops_plugin._on_stopped),
                     ("command", "stop-log", stops_plugin._handle_stop_log)]


def test_help_lines_up(stops_plugin):
    options = [line for line in stops_plugin._HELP.splitlines() if line.startswith("  /")]
    assert len(options) == 8 and len({len(line) - len(line[2:].split("  ", 1)[1].lstrip()) for line in options}) == 1
