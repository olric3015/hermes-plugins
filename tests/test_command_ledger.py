"""command-ledger: what one slash command becomes, how it is stored and how /command-log reads."""

import hashlib
import json
import time

import pytest

NOW = 1_800_000_000.0
KEY = "agent:main:telegram:dm:SECRET-CHAT-ID"


def _payload(**over):
    base = {"surface": "gateway", "command": "model", "alias_used": "model",
            "args_raw": "SECRET-ARGS openai/gpt-x --api-key sk-SECRET", "session_key": KEY,
            "platform": "telegram"}
    base.update(over)
    return base


def test_row_keeps_the_command_and_none_of_the_text(cmdledger):
    row = cmdledger.build_row(_payload(), now=NOW)
    assert row == {"ts": NOW, "session": hashlib.sha256(KEY.encode()).hexdigest()[:12],
                   "surface": "gateway", "platform": "telegram", "command": "model", "alias": ""}
    assert "SECRET" not in json.dumps(row)


def test_alias_is_kept_only_when_it_differs(cmdledger):
    assert cmdledger.build_row(_payload(command="quit", alias_used="exit"), now=NOW)["alias"] == "exit"
    assert cmdledger.build_row(_payload(command="quit", alias_used="/EXIT"), now=NOW)["alias"] == "exit"
    assert cmdledger.build_row(_payload(command="quit", alias_used="Quit"), now=NOW)["alias"] == ""
    assert cmdledger.build_row(_payload(command="quit", alias_used=None), now=NOW)["alias"] == ""


def test_session_tag_is_stable_short_and_empty_without_a_key(cmdledger):
    assert cmdledger.session_tag(KEY) == cmdledger.session_tag(KEY) != cmdledger.session_tag(KEY + "x")
    assert len(cmdledger.session_tag(KEY)) == 12
    assert cmdledger.session_tag(None) == cmdledger.session_tag("") == ""


def test_row_tolerates_a_sparse_or_odd_payload(cmdledger):
    row = cmdledger.build_row({}, now=NOW)
    assert (row["surface"], row["platform"], row["command"]) == ("unknown", "unknown", "unknown")
    assert row["session"] == row["alias"] == ""
    odd = cmdledger.build_row(_payload(command="/" + "c" * 500, surface="s" * 500, platform="p" * 500,
                                       alias_used="a" * 500), now=NOW)
    assert odd["command"] == "c" * 60 and len(odd["surface"]) == 20 and len(odd["platform"]) == 40
    assert odd["alias"] == "a" * 60


def test_storage_round_trip_bound_and_clear(cmdledger, tmp_path, monkeypatch):
    path = tmp_path / cmdledger.FILENAME
    assert cmdledger.read(path) == [] and cmdledger.clear(path) == 0
    for command in ("model", "new", "usage"):
        cmdledger.append(path, cmdledger.build_row(_payload(command=command), now=NOW))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["command"] for r in cmdledger.read(path)] == ["model", "new", "usage"]
    assert b"\r\n" not in path.read_bytes()

    monkeypatch.setattr(cmdledger, "MAX_BYTES", 300)
    monkeypatch.setattr(cmdledger, "KEEP_LINES", 2)
    cmdledger.append(path, cmdledger.build_row(_payload(command="help"), now=NOW))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["command"] for r in cmdledger.read(path)] == ["help"]
    assert not list(tmp_path.glob(".commands-*"))
    assert cmdledger.clear(path) == 1 and not path.exists()


def _row(command, session="s1", platform="cli", ts=NOW, alias="", surface=None):
    return {"ts": ts, "session": session, "surface": surface or ("cli" if platform == "cli" else "gateway"),
            "platform": platform, "command": command, "alias": alias}


def test_select_windows(cmdledger):
    rows = [_row("a", ts=NOW - 2 * 86400, session="old"), _row("a", session="s1"),
            _row("b", session="s2"), _row("b", session="")]
    assert len(cmdledger.select(rows, "all", NOW)) == 4
    assert [r["session"] for r in cmdledger.select(rows, "day", NOW)] == ["s1", "s2", ""]
    assert [r["command"] for r in cmdledger.select(rows, "session", NOW)] == ["b"]
    assert cmdledger.select([_row("a", session="")], "session", NOW) == []


def test_summary_by_command_and_platform(cmdledger):
    rows = [_row("model", session="s1", ts=NOW - 3600), _row("model", session="s1", ts=NOW - 50),
            _row("model", session="s2", platform="telegram", ts=NOW - 7200),
            _row("new", session="s2", platform="telegram", ts=NOW - 600),
            _row("usage", session="", ts=NOW - 2 * 86400)]
    lines = cmdledger.summarize(rows, title="last 24 hours", now=NOW).splitlines()
    assert lines[0] == "Slash commands, last 24 hours:"
    assert lines[2].split() == ["command", "uses", "share", "sessions", "last", "used"]
    assert lines[3].split() == ["/model", "3", "60%", "2", "50s", "ago"]
    assert lines[4].split() == ["/new", "1", "20%", "1", "10m", "ago"]
    # A row without a session counts as a use, not as a session.
    assert lines[5].split() == ["/usage", "1", "20%", "0", "2d", "ago"]
    assert lines[6].split() == ["total", "5", "100%", "2", "50s", "ago"]
    assert lines[-1] == "The CLI counts Hermes's built-in commands; messaging platforms add plugin commands."

    by_platform = cmdledger.summarize(rows, title="x", now=NOW, by="platform").splitlines()
    assert by_platform[2].split()[0] == "platform"
    assert [line.split()[:3] for line in by_platform[3:6]] == [
        ["cli", "3", "60%"], ["telegram", "2", "40%"], ["total", "5", "100%"]]
    by_surface = cmdledger.summarize(rows, title="x", now=NOW, by="surface").splitlines()
    assert [line.split()[:2] for line in by_surface[3:5]] == [["cli", "3"], ["gateway", "2"]]


def test_summary_puts_the_most_used_first_and_ties_by_name(cmdledger):
    rows = [_row("zz")] + [_row("mm")] * 3 + [_row("aa")]
    lines = cmdledger.summarize(rows, title="x", now=NOW).splitlines()
    assert [line.split()[0] for line in lines[3:7]] == ["/mm", "/aa", "/zz", "total"]


def test_summary_edges(cmdledger):
    assert cmdledger.summarize([], title="all recorded", now=NOW) == "Slash commands, all recorded: none recorded."
    one = cmdledger.summarize([_row("help", ts=NOW + 30)], title="x", now=NOW).splitlines()
    # A clock that moved backwards is never a negative age.
    assert one[3].split() == ["/help", "1", "100%", "1", "0s", "ago"]
    assert not any(line.startswith("total") for line in one)
    # A row from a foreign writer: no command, no time.
    odd = cmdledger.summarize([{"ts": None}], title="x", now=NOW).splitlines()
    assert odd[3].split()[:4] == ["/unknown", "1", "100%", "0"]


@pytest.mark.parametrize("word, seconds", [
    ("7d", 7 * 86400.0), ("1d", 86400.0), ("12h", 43200.0), ("9999h", 9999 * 3600.0),
    ("0d", None), ("00h", None), ("d", None), ("7", None), ("7w", None), ("-1d", None),
    ("1.5h", None), ("12345d", None), ("٧d", None), ("", None), ("day", None)])
def test_span_words(cmdledger, word, seconds):
    assert cmdledger.parse_span(word) == seconds


def test_span_titles(cmdledger):
    assert [cmdledger.span_title(w) for w in ("1h", "6h", "1d", "30d")] == [
        "last 1 hour", "last 6 hours", "last 1 day", "last 30 days"]


def test_recent_lists_newest_first(cmdledger):
    rows = [_row("usage", ts=NOW - 3 * 86400 - 5),
            _row("quit", alias="exit", ts=NOW - 2 * 3600 - 1),
            _row("new", platform="telegram", ts=NOW - 125),
            _row("model", ts=NOW - 7)]
    lines = cmdledger.recent(rows, NOW, title="all recorded").splitlines()
    assert lines[0] == "Slash commands, all recorded, newest 4 of 4:"
    assert lines[2].split() == ["when", "command", "typed", "as", "platform"]
    assert lines[3].split() == ["7s", "ago", "/model", "cli"]
    assert lines[4].split() == ["2m", "ago", "/new", "telegram"]
    assert lines[5].split() == ["2h", "ago", "/quit", "/exit", "cli"]
    assert lines[6].split() == ["3d", "ago", "/usage", "cli"]
    limited = cmdledger.recent(rows, NOW, title="x", limit=2).splitlines()
    assert limited[0] == "Slash commands, x, newest 2 of 4:" and len(limited) == 5
    assert cmdledger.recent([], NOW, title="last 7 days") == "Slash commands, last 7 days: none recorded."


def test_default_recent_limit_is_ten(cmdledger):
    rows = [_row(f"c{i}", ts=NOW - 100 + i) for i in range(14)]
    lines = cmdledger.recent(rows, NOW, title="x").splitlines()
    assert lines[0] == "Slash commands, x, newest 10 of 14:"
    assert [line.split()[2] for line in lines[3:]] == [f"/c{i}" for i in range(13, 3, -1)]


@pytest.mark.parametrize("seconds, shown", [
    (0, "0s ago"), (59.9, "59s ago"), (60, "1m ago"), (3599, "59m ago"), (3600, "1h ago"),
    (86399, "23h ago"), (86400, "1d ago"), (40 * 86400, "40d ago")])
def test_ages_change_unit_at_the_boundary(cmdledger, seconds, shown):
    line = cmdledger.recent([_row("p", ts=NOW - seconds)], NOW, title="x").splitlines()[3]
    assert line.startswith(shown + " ")


class _Ctx:
    def __init__(self):
        self.hooks, self.commands = {}, {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


@pytest.fixture
def wired(commands_plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(commands_plugin, "_ledger_path", lambda: tmp_path / commands_plugin.ledger.FILENAME)
    ctx = _Ctx()
    commands_plugin.register(ctx)
    return ctx, tmp_path / commands_plugin.ledger.FILENAME


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()]


def test_plugin_surface_and_round_trip(wired):
    ctx, path = wired
    assert sorted(ctx.hooks) == ["pre_command"]
    assert sorted(ctx.commands) == ["command-log"]
    hook, log = ctx.hooks["pre_command"], ctx.commands["command-log"]
    assert log("") == "Slash commands, last 24 hours: none recorded."
    hook(**_payload())
    hook(**_payload(command="new", alias_used="reset"))
    hook(**_payload(surface="cli", platform="cli", session_key="cli-session-2", command="model", args_raw=""))
    assert "SECRET" not in path.read_text(encoding="utf-8-sig")
    assert [(r["command"], r["alias"], r["platform"]) for r in _rows(path)] == [
        ("model", "", "telegram"), ("new", "reset", "telegram"), ("model", "", "cli")]
    lines = log("").splitlines()
    assert lines[3].split()[:4] == ["/model", "2", "67%", "2"]
    assert lines[4].split()[:4] == ["/new", "1", "33%", "1"]
    session = log("session").splitlines()
    assert session[0] == "Slash commands, latest session:" and session[3].split()[:4] == ["/model", "1", "100%", "1"]
    assert log("all platforms").splitlines()[3].split()[:2] == ["telegram", "2"]
    assert log("all surfaces").splitlines()[3].split()[:2] == ["gateway", "2"]
    assert log("all commands").splitlines()[2].split()[0] == "command"


def test_reading_the_ledger_is_not_counted(wired):
    ctx, path = wired
    hook = ctx.hooks["pre_command"]
    hook(**_payload(command="command-log", alias_used="command-log", args_raw="7d"))
    hook(**_payload(command="/command-log"))
    assert not path.exists()
    hook(**_payload(command="command-logs"))
    assert [r["command"] for r in _rows(path)] == ["command-logs"]


def test_command_help_unknown_windows_and_clear(wired):
    ctx, path = wired
    hook, log = ctx.hooks["pre_command"], ctx.commands["command-log"]
    assert log("help").startswith("/command-log:")
    assert log("bogus").startswith("Unknown option: bogus")
    assert log("0d").startswith("Unknown option: 0d") and log("7w").startswith("Unknown option: 7w")
    assert "/command-log 7d" in log("help") and "/command-log recent" in log("help")
    hook(**_payload())
    assert log("clear") == "command-ledger: deleted 1 recorded command(s)." and not path.exists()
    assert log("all") == "Slash commands, all recorded: none recorded."


def test_command_time_window_and_recent(wired, cmdledger):
    ctx, path = wired
    log = ctx.commands["command-log"]
    now = time.time()
    for row in (_row("weekly", session="old", ts=now - 5 * 86400),
                _row("today", platform="discord", ts=now - 30)):
        cmdledger.append(path, row)
    assert [line.split()[0] for line in log("").splitlines()[3:4]] == ["/today"]
    week = log("7d").splitlines()
    assert week[0] == "Slash commands, last 7 days:"
    assert sorted(line.split()[0] for line in week[3:5]) == ["/today", "/weekly"]
    assert log("3d").splitlines()[0] == "Slash commands, last 3 days:" and "/weekly" not in log("3d")
    assert "/weekly" not in log("1h")
    assert log("7d platforms").splitlines()[3].split()[:2] in (["cli", "1"], ["discord", "1"])
    listing = log("7d recent").splitlines()
    assert listing[0] == "Slash commands, last 7 days, newest 2 of 2:"
    assert listing[3].split()[2:] == ["/today", "discord"]
    assert listing[4].split()[:3] == ["5d", "ago", "/weekly"]
    assert log("recent").splitlines()[0] == "Slash commands, last 24 hours, newest 1 of 1:"
    # session and all keep their meaning when a span is typed next to them.
    assert log("session 1h").splitlines()[0] == "Slash commands, latest session:"
    assert log("all 1h recent").splitlines()[0] == "Slash commands, all recorded, newest 2 of 2:"


def test_hook_never_raises(commands_plugin, monkeypatch):
    def boom():
        raise OSError("read-only home")

    monkeypatch.setattr(commands_plugin, "_ledger_path", boom)
    commands_plugin._on_pre_command(**_payload())

    class Unprintable:
        def __str__(self):
            raise ValueError("no text")

    commands_plugin._on_pre_command(command=Unprintable())
    commands_plugin._on_pre_command(**_payload(surface=Unprintable()))


def test_documented_bounds(cmdledger):
    # The README and the catalog entry promise these numbers.
    assert (cmdledger.MAX_BYTES, cmdledger.KEEP_LINES, cmdledger.RECENT_ROWS) == (2_000_000, 5000, 10)


def test_day_window_includes_its_exact_edge(cmdledger):
    rows = [_row("edge", ts=NOW - 86400), _row("out", ts=NOW - 86400 - 0.5)]
    assert [r["command"] for r in cmdledger.select(rows, "day", NOW)] == ["edge"]
    assert [r["command"] for r in cmdledger.select(rows, "day", NOW, 3600.0)] == []


def test_session_wins_over_all(wired):
    ctx, _path = wired
    log = ctx.commands["command-log"]
    ctx.hooks["pre_command"](**_payload())
    assert log("all session").splitlines()[0] == "Slash commands, latest session:"
    assert log("session all").splitlines()[0] == "Slash commands, latest session:"


def _local(year, month, day, hour=12, minute=0):
    from datetime import datetime

    return datetime(year, month, day, hour, minute).timestamp()


def test_summary_by_day_is_newest_day_first(cmdledger):
    rows = [_row("help", ts=_local(2027, 1, 8, 9)),
            _row("model", session="s2", ts=_local(2027, 1, 10, 0, 1)),
            _row("model", session="s3", ts=_local(2027, 1, 10, 23, 59)),
            _row("usage", session="s2", ts=_local(2027, 1, 10, 13)),
            _row("help", ts=_local(2027, 1, 9, 23, 59)),
            _row("new", ts=_local(2027, 1, 9, 0, 0))]
    now = _local(2027, 1, 11, 0, 59)
    lines = cmdledger.summarize(rows, title="last 7 days", now=now, by="day").splitlines()
    assert lines[2].split() == ["day", "uses", "share", "sessions", "last", "used"]
    # Days, not counts, set the order: the smallest day can sit above a busier one.
    assert lines[3].split() == ["2027-01-10", "3", "50%", "2", "1h", "ago"]
    assert lines[4].split() == ["2027-01-09", "2", "33%", "1", "1d", "ago"]
    assert lines[5].split() == ["2027-01-08", "1", "17%", "1", "2d", "ago"]
    assert lines[6].split()[:4] == ["total", "6", "100%", "3"]


def test_day_of_a_row_without_a_time_goes_last(cmdledger):
    assert cmdledger.local_day(_local(2027, 3, 4, 0, 0)) == "2027-03-04"
    assert cmdledger.local_day(None) == cmdledger.local_day("soon") == cmdledger.local_day(1e300) == "unknown"
    rows = [{"command": "help"}, _row("help", ts=_local(2027, 1, 2)), _row("help", ts=_local(2027, 1, 1))]
    lines = cmdledger.summarize(rows, title="x", now=_local(2027, 1, 3), by="day").splitlines()
    assert [line.split()[0] for line in lines[3:7]] == ["2027-01-02", "2027-01-01", "unknown", "total"]


def test_command_days(wired, cmdledger):
    ctx, path = wired
    log = ctx.commands["command-log"]
    now = time.time()
    cmdledger.append(path, _row("help", session="old", ts=now - 3 * 86400))
    # Both at the same moment, so a run at midnight cannot split them over two days.
    cmdledger.append(path, _row("model", ts=now - 10))
    cmdledger.append(path, _row("usage", ts=now - 10))
    week = log("7d days").splitlines()
    assert week[0] == "Slash commands, last 7 days:" and week[2].split()[0] == "day"
    assert [line.split()[:2] for line in week[3:6]] == [
        [time.strftime("%Y-%m-%d", time.localtime(now - 10)), "2"],
        [time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400)), "1"], ["total", "3"]]
    assert [line.split()[1] for line in log("days").splitlines()[3:4]] == ["2"]
    assert log("session days").splitlines()[0] == "Slash commands, latest session:"
    assert "/command-log days" in log("help")
    # recent still lists commands, not days.
    assert log("all days recent").splitlines()[2].split()[0] == "when"
