"""edit-ledger: what one turn that changed files becomes, how it is stored and how /edit-log reads."""

import json
import re
import time

import pytest

NOW = 1_800_000_000.0
SESSION = "20261017_120000_abcdef"
PATHS = ["/home/SECRET-USER/project/app.py", "/home/SECRET-USER/project/README.md",
         "C:\\Users\\SECRET-USER\\project\\ui\\View.TSX"]


def _payload(**over):
    base = {"session_id": SESSION, "platform": "telegram", "model": "gpt-x", "coding": True, "attempt": 0,
            "final_response": "SECRET-ANSWER done", "changed_paths": PATHS}
    base.update(over)
    return base


def test_row_keeps_counts_and_types_never_paths(edledger):
    row = edledger.build_row(_payload(), now=NOW)
    assert row == {"ts": NOW, "session": edledger.tag(SESSION), "platform": "telegram", "model": "gpt-x",
                   "attempt": 0, "files": 3, "types": {".md": 1, ".py": 1, ".tsx": 1}}
    dumped = json.dumps(row)
    assert "SECRET" not in dumped and "app" not in dumped and SESSION[:8] not in dumped
    assert re.fullmatch(r"[0-9a-f]{12}", row["session"])
    assert edledger.tag("") == "" and edledger.tag(None) == ""


@pytest.mark.parametrize("path, kind", [
    ("src/app.py", ".py"), ("C:\\x\\Y.JSON", ".json"), ("a/b.tar.gz", ".gz"), ("Makefile", "(none)"),
    (".env", "(none)"), ("dir.d/file", "(none)"), ("C:\\dir.d\\file", "(none)"), ("weird.ext with space", "(other)"),
    ("x." + "a" * 30, "(other)"), ("a/b.c++", ".c++"), ("", "(none)"), (None, "(none)")])
def test_file_type(edledger, path, kind):
    assert edledger.file_type(path) == kind


def test_types_are_capped(edledger, monkeypatch):
    monkeypatch.setattr(edledger, "MAX_TYPES", 2)
    paths = ["a.py", "b.py", "c.py", "d.md", "e.md", "f.js", "g.css"]
    assert edledger.count_types(paths) == {".py": 3, ".md": 2, "(other)": 2}


def test_row_tolerates_a_sparse_or_odd_payload(edledger):
    row = edledger.build_row({}, now=NOW)
    assert row == {"ts": NOW, "session": "", "platform": "unknown", "model": "unknown", "attempt": 0,
                   "files": 0, "types": {}}
    odd = edledger.build_row(_payload(changed_paths="a.py", attempt="x", model="m" * 500,
                                      platform="p" * 500), now=NOW)
    assert (odd["files"], odd["types"], odd["attempt"]) == (0, {}, 0)
    assert len(odd["model"]) == 80 and len(odd["platform"]) == 40
    assert edledger.build_row(_payload(changed_paths=["a.py", "", None], attempt=-2), now=NOW)["files"] == 1
    assert edledger.build_row(_payload(attempt=-2), now=NOW)["attempt"] == 0


def test_storage_round_trip_bom_bound_and_clear(edledger, tmp_path, monkeypatch):
    path = tmp_path / edledger.FILENAME
    assert edledger.read(path) == [] and edledger.clear(path) == 0
    for model in ("a", "b", "c"):
        edledger.append(path, edledger.build_row(_payload(model=model), now=NOW))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["model"] for r in edledger.read(path)] == ["a", "b", "c"]
    assert b"\r\n" not in path.read_bytes() and b"SECRET" not in path.read_bytes()

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert [r["model"] for r in edledger.read(bom)] == ["a", "b", "c"]

    monkeypatch.setattr(edledger, "MAX_BYTES", 400)
    monkeypatch.setattr(edledger, "KEEP_LINES", 2)
    edledger.append(path, edledger.build_row(_payload(model="d"), now=NOW))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["model"] for r in edledger.read(path)] == ["d"]
    assert not list(tmp_path.glob(".edits-*"))
    assert edledger.clear(path) == 1 and not path.exists()

    # A file another tool saved with a BOM is still trimmed to rows that parse.
    monkeypatch.setattr(edledger, "KEEP_LINES", 5)
    bom.write_bytes(b"\xef\xbb\xbf" + (json.dumps({"model": "old"}) + "\n").encode("utf-8"))
    edledger.append(bom, edledger.build_row(_payload(model="d"), now=NOW))
    assert [r["model"] for r in edledger.read(bom)] == ["old", "d"]


def _row(model="gpt-x", session="s1", files=1, types=None, ts=NOW, attempt=0, platform="telegram"):
    return {"ts": ts, "session": session, "platform": platform, "model": model, "attempt": attempt,
            "files": files, "types": types if types is not None else {".py": files}}


def test_turns_fold_a_nudged_turn_and_keep_sessions_apart(edledger):
    rows = [_row(session="a", files=1, ts=NOW - 50),
            _row(session="b", files=3, types={".md": 3}, ts=NOW - 40),
            # attempt 1 for session a: the same turn, with its full file list so far.
            _row(session="a", files=2, types={".py": 1, ".ts": 1}, ts=NOW - 30, attempt=1),
            _row(session="a", files=1, ts=NOW - 20),          # a new turn in session a
            _row(session="", files=4, ts=NOW - 10, attempt=1),  # no session: a turn of its own
            _row(session="", files=6, ts=NOW - 8, attempt=1),   # ... each one
            _row(session="c", files=5, ts=NOW - 5, attempt=2)]  # nothing earlier to fold into
    folded = edledger.turns(rows)
    assert edledger.turns([{"files": 1}])[0]["ts"] is None
    assert [(t["session"], t["files"], t["ts"]) for t in folded] == [
        ("a", 2, NOW - 30), ("b", 3, NOW - 40), ("a", 1, NOW - 20), ("", 4, NOW - 10), ("", 6, NOW - 8),
        ("c", 5, NOW - 5)]
    assert folded[0]["types"] == {".py": 1, ".ts": 1}
    assert edledger.turns([{"types": "bad", "files": "x"}])[0]["types"] == {}
    assert edledger.turns([{"types": {".py": 0, ".md": 2, ".js": "x"}}])[0]["types"] == {".md": 2}


def test_select_windows(edledger):
    rows = [_row(ts=NOW - 2 * 86400, session="old"), _row(session="s1"), _row("claude", session="s2"),
            _row("gemini", session="")]
    assert len(edledger.select(rows, "all", NOW)) == 4
    assert len(edledger.select(rows, "day", NOW)) == 3
    assert len(edledger.select(rows, "day", NOW, span=3 * 86400)) == 4
    assert [r["model"] for r in edledger.select(rows, "session", NOW)] == ["claude"]
    assert edledger.select([_row(session="")], "session", NOW) == []


def test_summary_puts_the_most_turns_first(edledger):
    rows = [_row("gpt-x", "a", 1, {".py": 1}, NOW - 7200), _row("gpt-x", "b", 4, {".md": 3, ".py": 1}, NOW - 600),
            _row("gpt-x", "c", 2, {".md": 2}, NOW - 300),
            _row("claude", "d", 9, {".tsx": 9}, NOW - 40),
            _row("local", "e", 3, {".py": 3}, NOW - 3 * 3600), _row("local", "f", 1, {".py": 1}, NOW - 2 * 3600)]
    lines = edledger.summarize(rows, NOW, title="last 24 hours").splitlines()
    assert lines[0] == "Turns that changed files, last 24 hours:"
    assert lines[2].split() == ["model", "turns", "files", "median", "top", "type", "last"]
    assert lines[3].split() == ["gpt-x", "3", "7", "2", ".md", "5m", "ago"]
    assert lines[4].split() == ["local", "2", "4", "2", ".py", "2h", "ago"]
    assert lines[5].split() == ["claude", "1", "9", "9", ".tsx", "40s", "ago"]
    # Six turns of 1, 4, 2, 9, 3, 1 files: the median falls between 2 and 3.
    assert lines[6].split() == ["total", "6", "20", "2.5", ".tsx", "40s", "ago"]
    assert lines[-1] == "Counts files changed with write_file and patch, not by terminal commands."


def test_summary_ties_and_missing_types(edledger):
    # Equal counts: the alphabetically first type wins; a turn with no types shows "-".
    lines = edledger.summarize([_row(types={".ts": 1, ".js": 1}, files=2)], NOW, title="x").splitlines()
    assert lines[3].split()[4] == ".js" and not any(line.startswith("total") for line in lines)
    assert edledger.summarize([_row(types={}, files=0)], NOW, title="x").splitlines()[3].split()[4] == "-"
    assert edledger.summarize([], NOW, title="all recorded") == "Turns that changed files, all recorded: none recorded."


def test_summary_by_type(edledger):
    rows = [_row(session="a", files=3, types={".py": 2, ".md": 1}, ts=NOW - 600),
            _row(session="b", files=3, types={".py": 3}, ts=NOW - 60),
            _row(session="c", files=2, types={".md": 2}, ts=NOW - 30),
            _row(session="d", files=3, types={".js": 3}, ts=NOW - 20)]
    lines = edledger.summarize(rows, NOW, title="all recorded", by="type").splitlines()
    assert lines[0] == "Turns that changed files, all recorded, by file type:"
    assert lines[2].split() == ["type", "files", "turns", "last"]
    # Most files first; a tie goes alphabetically.
    assert lines[3].split() == [".py", "5", "2", "1m", "ago"]
    assert lines[4].split() == [".js", "3", "1", "20s", "ago"]
    assert lines[5].split() == [".md", "3", "2", "30s", "ago"]


def test_summary_by_platform(edledger):
    rows = [_row(platform="cli", session="a"), _row(platform="telegram", session="b"),
            _row(platform="cli", session="c", files=3, types={".py": 3})]
    lines = edledger.summarize(rows, NOW, title="all recorded", by="platform").splitlines()
    assert [line.split()[:3] for line in lines[3:6]] == [["cli", "2", "4"], ["telegram", "1", "1"], ["total", "3", "5"]]


def test_days_newest_first_unknown_last(edledger):
    rows = [_row(ts=NOW - 86400, session="a"), _row(ts=NOW, session="b"), _row(ts=NOW, session="c"),
            {"model": "x", "files": 1, "types": {".py": 1}}]
    out = edledger.summarize(rows, NOW, title="all recorded", by="day").splitlines()
    names = [line.split()[0] for line in out[3:-2]]
    assert names == [edledger.local_day(NOW), edledger.local_day(NOW - 86400), "unknown", "total"]


def test_recent_lists_turns_newest_first(edledger):
    rows = [_row("gpt-x", "a", 1, {".py": 1}, NOW - 7200),
            _row("claude", "b", 6, {".py": 3, ".md": 1, ".ts": 1, ".css": 1}, NOW - 30, platform="cli"),
            _row("claude", "b", 7, {".py": 4, ".md": 1, ".ts": 1, ".css": 1}, NOW - 20, attempt=1, platform="cli"),
            _row("local", "c", 0, {}, NOW - 5, platform="discord")]
    out = edledger.recent(rows, NOW, title="all recorded", limit=2).splitlines()
    assert out[0] == "Turns that changed files, all recorded, newest 2 of 3:"
    assert out[2].split() == ["when", "model", "platform", "files", "types"]
    assert out[3].split() == ["5s", "ago", "local", "discord", "0", "-"]
    assert out[4].split() == ["20s", "ago", "claude", "cli", "7", ".py", "4,", ".css", "1,", ".md", "1", "..."]
    assert edledger.recent([], NOW, title="x") == "Turns that changed files, x: none recorded."


@pytest.mark.parametrize("word, count", [("1", 1), ("25", 25), ("100", 100), ("0", None), ("101", None),
                                         ("9" * 5000, None), ("-5", None), ("٣", None), ("7d", None)])
def test_parse_count(edledger, word, count):
    assert edledger.parse_count(word) == count


@pytest.mark.parametrize("word, seconds", [("6h", 6 * 3600.0), ("7d", 7 * 86400.0), ("0d", None),
                                           ("12345h", None), ("7w", None), ("d", None), ("٣d", None)])
def test_parse_span(edledger, word, seconds):
    assert edledger.parse_span(word) == seconds


@pytest.fixture
def ledger_file(edits_plugin, tmp_path, monkeypatch):
    path = tmp_path / "edits.jsonl"
    monkeypatch.setattr(edits_plugin, "_ledger_path", lambda: path)
    return path


def test_hook_records_a_turn_and_never_keeps_the_agent_going(edits_plugin, edledger, ledger_file):
    assert edits_plugin._on_pre_verify(**_payload(), telemetry_schema_version=1) is None
    [row] = edledger.read(ledger_file)
    assert (row["model"], row["files"], row["types"]) == ("gpt-x", 3, {".md": 1, ".py": 1, ".tsx": 1})
    assert b"SECRET" not in ledger_file.read_bytes()


def test_hook_never_raises(edits_plugin, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(edits_plugin, "_ledger_path", boom)
    assert edits_plugin._on_pre_verify(**_payload()) is None
    assert edits_plugin._on_pre_verify(session_id=object(), changed_paths=[object()]) is None


def test_command(edits_plugin, edledger, ledger_file):
    now = time.time()
    # Oldest first, as the hook writes them.
    for model, session, ts in (("gemini", "c", now - 3 * 86400), ("gpt-x", "a", now - 60), ("claude", "b", now - 30)):
        edledger.append(ledger_file, _row(model, session, ts=ts))
    out = edits_plugin._handle_edit_log("")
    assert out.startswith("Turns that changed files, last 24 hours:") and "gemini" not in out
    assert "gemini" in edits_plugin._handle_edit_log("7d") and "last 7 days" in edits_plugin._handle_edit_log("7d")
    assert edits_plugin._handle_edit_log("session").splitlines()[3].split()[0] == "claude"
    # session wins over all, and both win over a time span.
    assert edits_plugin._handle_edit_log("all session").splitlines()[0] == "Turns that changed files, latest session:"
    session_week = edits_plugin._handle_edit_log("session 7d").splitlines()
    assert session_week[0] == "Turns that changed files, latest session:" and "gpt-x" not in "\n".join(session_week)
    assert edits_plugin._handle_edit_log("all 1h").splitlines()[0] == "Turns that changed files, all recorded:"
    assert edits_plugin._handle_edit_log("all days").splitlines()[2].split()[0] == "day"
    assert edits_plugin._handle_edit_log("all platforms").splitlines()[2].split()[0] == "platform"
    assert edits_plugin._handle_edit_log("all models").splitlines()[2].split()[0] == "model"
    assert edits_plugin._handle_edit_log("all types").splitlines()[2].split()[0] == "type"
    assert edits_plugin._handle_edit_log("all recent 2").splitlines()[0] == (
        "Turns that changed files, all recorded, newest 2 of 3:")
    assert edits_plugin._handle_edit_log("all recent").splitlines()[0] == (
        "Turns that changed files, all recorded, newest 3 of 3:")
    assert edits_plugin._handle_edit_log("recent 0").startswith("Unknown option: 0")
    assert edits_plugin._handle_edit_log("all 5").startswith("Unknown option: 5")
    assert edits_plugin._handle_edit_log("bogus").startswith("Unknown option: bogus")
    assert edits_plugin._handle_edit_log("help").startswith("/edit-log:")
    assert edits_plugin._handle_edit_log("clear") == "edit-ledger: deleted 3 recorded row(s)."
    assert edits_plugin._handle_edit_log("all") == "Turns that changed files, all recorded: none recorded."


def test_register_wires_the_hook_and_the_command(edits_plugin):
    calls = []

    class Ctx:
        def register_hook(self, name, cb):
            calls.append(("hook", name, cb))

        def register_command(self, name, handler, **kw):
            calls.append(("command", name, handler))

    edits_plugin.register(Ctx())
    assert calls == [("hook", "pre_verify", edits_plugin._on_pre_verify),
                     ("command", "edit-log", edits_plugin._handle_edit_log)]


def test_help_lines_up(edits_plugin):
    options = [line for line in edits_plugin._HELP.splitlines() if line.startswith("  /")]
    assert len(options) == 8 and len({len(line) - len(line[2:].split("  ", 1)[1].lstrip()) for line in options}) == 1
