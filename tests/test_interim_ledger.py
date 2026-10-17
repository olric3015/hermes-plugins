"""interim-ledger: what one mid-turn message becomes, how it is stored and how /interim-log reads."""

import json
import time

import pytest

NOW = 1_800_000_000.0
SESSION = "20261017_120000_abcdef"
TURN = SESSION + ":turn-0001"
TEXT = "SECRET-PLAN: let me read the config first."


def _payload(**over):
    base = {"text": TEXT, "already_streamed": True, "turn_id": TURN, "iteration": 2,
            "session_id": SESSION, "model": "gpt-x", "provider": "openrouter", "surface": "telegram"}
    base.update(over)
    return base


def test_row_keeps_who_where_and_length_never_the_text(imledger):
    row = imledger.build_row(_payload(), now=NOW)
    assert row == {"ts": NOW, "session": imledger.tag(SESSION), "turn": imledger.tag(TURN),
                   "surface": "telegram", "model": "gpt-x", "provider": "openrouter", "iteration": 2,
                   "chars": len(TEXT), "streamed": True}
    assert "SECRET" not in json.dumps(row) and SESSION not in json.dumps(row)
    assert len(row["session"]) == len(row["turn"]) == 12
    assert imledger.tag("") == "" and imledger.tag(None) == ""


def test_long_turn_ids_that_share_a_prefix_stay_apart(imledger):
    # Hermes's turn ids can start with a long session id; only the end tells two turns apart.
    first = imledger.build_row(_payload(turn_id="s" * 100 + ":turn-1"), now=NOW)
    second = imledger.build_row(_payload(turn_id="s" * 100 + ":turn-2"), now=NOW)
    assert first["turn"] != second["turn"]


def test_row_tolerates_a_sparse_or_odd_payload(imledger):
    row = imledger.build_row({}, now=NOW)
    assert row == {"ts": NOW, "session": "", "turn": "", "surface": "unknown", "model": "unknown",
                   "provider": "unknown", "iteration": 0, "chars": 0, "streamed": False}
    odd = imledger.build_row(_payload(text=["not", "text"], iteration="x", model="m" * 500,
                                      surface="s" * 500, already_streamed=None), now=NOW)
    assert (odd["chars"], odd["iteration"], odd["streamed"]) == (0, 0, False)
    assert len(odd["model"]) == 80 and len(odd["surface"]) == 40
    assert imledger.build_row(_payload(iteration=-3), now=NOW)["iteration"] == 0


def test_storage_round_trip_bom_bound_and_clear(imledger, tmp_path, monkeypatch):
    path = tmp_path / imledger.FILENAME
    assert imledger.read(path) == [] and imledger.clear(path) == 0
    for model in ("a", "b", "c"):
        imledger.append(path, imledger.build_row(_payload(model=model), now=NOW))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["model"] for r in imledger.read(path)] == ["a", "b", "c"]
    assert b"\r\n" not in path.read_bytes() and b"SECRET" not in path.read_bytes()

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert [r["model"] for r in imledger.read(bom)] == ["a", "b", "c"]

    monkeypatch.setattr(imledger, "MAX_BYTES", 400)
    monkeypatch.setattr(imledger, "KEEP_LINES", 2)
    imledger.append(path, imledger.build_row(_payload(model="d"), now=NOW))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["model"] for r in imledger.read(path)] == ["d"]
    assert not list(tmp_path.glob(".interim-*"))
    assert imledger.clear(path) == 1 and not path.exists()

    # A file another tool saved with a BOM is still trimmed to rows that parse.
    monkeypatch.setattr(imledger, "KEEP_LINES", 5)
    bom.write_bytes(b"\xef\xbb\xbf" + (json.dumps({"model": "old"}) + "\n").encode("utf-8"))
    imledger.append(bom, imledger.build_row(_payload(model="d"), now=NOW))
    assert [r["model"] for r in imledger.read(bom)] == ["old", "d"]


def _row(model="gpt-x", session="s1", turn="t1", chars=40, ts=NOW, provider="openrouter",
         surface="telegram", iteration=1, streamed=False):
    return {"ts": ts, "session": session, "turn": turn, "surface": surface, "model": model,
            "provider": provider, "iteration": iteration, "chars": chars, "streamed": streamed}


def test_select_windows(imledger):
    rows = [_row(ts=NOW - 2 * 86400, session="old"), _row(session="s1"), _row("claude", session="s2"),
            _row("gemini", session="")]
    assert len(imledger.select(rows, "all", NOW)) == 4
    assert len(imledger.select(rows, "day", NOW)) == 3
    assert len(imledger.select(rows, "day", NOW, span=3 * 86400)) == 4
    assert [r["model"] for r in imledger.select(rows, "session", NOW)] == ["claude"]
    assert imledger.select([_row(session="")], "session", NOW) == []


def test_summary_counts_turns_and_puts_the_most_messages_first(imledger):
    rows = [_row("gpt-x", "a", "t1", 30, NOW - 7200), _row("gpt-x", "a", "t1", 50, NOW - 7100),
            _row("gpt-x", "a", "t2", 90, NOW - 600),
            # Same turn tag in another session is another turn.
            _row("gpt-x", "b", "t1", 10, NOW - 300),
            _row("claude", "c", "t9", 200, NOW - 40),
            # No turn id: a turn of its own each.
            _row("local", "d", "", 5, NOW - 3 * 3600), _row("local", "d", "", 7, NOW - 3 * 3600)]
    out = imledger.summarize(rows, NOW, title="last 24 hours")
    lines = out.splitlines()
    assert lines[0] == "Mid-turn messages, last 24 hours:"
    assert lines[2].split() == ["model", "messages", "turns", "per", "turn", "median", "chars", "last"]
    assert lines[3].split() == ["gpt-x", "4", "3", "1.3", "40", "5m", "ago"]
    assert lines[4].split() == ["local", "2", "2", "1.0", "6", "3h", "ago"]
    assert lines[5].split() == ["claude", "1", "1", "1.0", "200", "40s", "ago"]
    assert lines[6].split() == ["total", "7", "6", "1.2", "30", "40s", "ago"]
    assert lines[-1] == "Counted even where display.interim_assistant_messages hides them."


def test_summary_other_groupings(imledger):
    rows = [_row(provider="openrouter", surface="telegram"), _row(provider="nous", surface="cli"),
            _row(provider="nous", surface="cli", turn="t2")]
    by_provider = imledger.summarize(rows, NOW, title="all recorded", by="provider").splitlines()
    assert by_provider[2].split()[0] == "provider"
    assert [line.split()[:3] for line in by_provider[3:5]] == [["nous", "2", "2"], ["openrouter", "1", "1"]]
    by_surface = imledger.summarize(rows, NOW, title="all recorded", by="surface").splitlines()
    assert [line.split()[0] for line in by_surface[3:6]] == ["cli", "telegram", "total"]


def test_summary_single_group_has_no_total_and_empty_says_so(imledger):
    lines = imledger.summarize([_row()], NOW, title="all recorded").splitlines()
    assert lines[3].split()[0] == "gpt-x" and not any(line.startswith("total") for line in lines)
    assert imledger.summarize([], NOW, title="all recorded") == "Mid-turn messages, all recorded: none recorded."


def test_days_newest_first_unknown_last(imledger):
    rows = [_row(ts=NOW - 86400), _row(ts=NOW), _row(ts=NOW, turn="t2"), {"model": "x", "chars": 3}]
    out = imledger.summarize(rows, NOW, title="all recorded", by="day").splitlines()
    names = [line.split()[0] for line in out[3:-2]]
    assert names == [imledger.local_day(NOW), imledger.local_day(NOW - 86400), "unknown", "total"]


def test_recent_lists_newest_first(imledger):
    rows = [_row("gpt-x", ts=NOW - 7200, iteration=1, chars=30),
            _row("claude", ts=NOW - 30, surface="cli", iteration=3, chars=120, streamed=True),
            _row("local", ts=NOW - 5, surface="discord", iteration=2, chars=8)]
    out = imledger.recent(rows, NOW, title="all recorded", limit=2).splitlines()
    assert out[0] == "Mid-turn messages, all recorded, newest 2 of 3:"
    assert out[2].split() == ["when", "model", "surface", "step", "chars", "streamed"]
    assert out[3].split() == ["5s", "ago", "local", "discord", "2", "8", "no"]
    assert out[4].split() == ["30s", "ago", "claude", "cli", "3", "120", "yes"]
    assert imledger.recent([], NOW, title="x") == "Mid-turn messages, x: none recorded."


@pytest.mark.parametrize("word, count", [("1", 1), ("25", 25), ("100", 100), ("0", None), ("101", None),
                                         ("9" * 5000, None), ("-5", None), ("٣", None), ("7d", None)])
def test_parse_count(imledger, word, count):
    assert imledger.parse_count(word) == count


@pytest.mark.parametrize("word, seconds", [("6h", 6 * 3600.0), ("7d", 7 * 86400.0), ("0d", None),
                                           ("12345h", None), ("7w", None), ("d", None), ("٣d", None)])
def test_parse_span(imledger, word, seconds):
    assert imledger.parse_span(word) == seconds


@pytest.fixture
def ledger_file(interim_plugin, tmp_path, monkeypatch):
    path = tmp_path / "interim.jsonl"
    monkeypatch.setattr(interim_plugin, "_ledger_path", lambda: path)
    return path


def test_hook_records_a_message(interim_plugin, imledger, ledger_file):
    assert interim_plugin._on_interim(**_payload(), telemetry_schema_version=1) is None
    [row] = imledger.read(ledger_file)
    assert (row["model"], row["surface"], row["chars"]) == ("gpt-x", "telegram", len(TEXT))
    assert b"SECRET" not in ledger_file.read_bytes()


def test_hook_never_raises(interim_plugin, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(interim_plugin, "_ledger_path", boom)
    interim_plugin._on_interim(**_payload())
    interim_plugin._on_interim(session_id=object())


def test_command(interim_plugin, imledger, ledger_file):
    now = time.time()
    # Oldest first, as the hook writes them.
    for model, session, turn, ts in (("gemini", "c", "t1", now - 3 * 86400), ("gpt-x", "a", "t1", now - 60),
                                     ("claude", "b", "t1", now - 30)):
        imledger.append(ledger_file, _row(model, session, turn, ts=ts))
    out = interim_plugin._handle_interim_log("")
    assert out.startswith("Mid-turn messages, last 24 hours:") and "gemini" not in out
    assert "gemini" in interim_plugin._handle_interim_log("7d")
    assert "last 7 days" in interim_plugin._handle_interim_log("7d")
    assert interim_plugin._handle_interim_log("session").splitlines()[3].split()[0] == "claude"
    # session wins over all, and both win over a time span.
    assert interim_plugin._handle_interim_log("all session").splitlines()[0] == "Mid-turn messages, latest session:"
    session_week = interim_plugin._handle_interim_log("session 7d").splitlines()
    assert session_week[0] == "Mid-turn messages, latest session:" and "gpt-x" not in "\n".join(session_week)
    assert interim_plugin._handle_interim_log("all 1h").splitlines()[0] == "Mid-turn messages, all recorded:"
    assert interim_plugin._handle_interim_log("all days").splitlines()[2].split()[0] == "day"
    assert interim_plugin._handle_interim_log("all providers").splitlines()[2].split()[0] == "provider"
    assert interim_plugin._handle_interim_log("all surfaces").splitlines()[2].split()[0] == "surface"
    assert interim_plugin._handle_interim_log("all models").splitlines()[2].split()[0] == "model"
    assert interim_plugin._handle_interim_log("all recent 2").splitlines()[0] == (
        "Mid-turn messages, all recorded, newest 2 of 3:")
    assert interim_plugin._handle_interim_log("all recent").splitlines()[0] == (
        "Mid-turn messages, all recorded, newest 3 of 3:")
    assert interim_plugin._handle_interim_log("recent 0").startswith("Unknown option: 0")
    assert interim_plugin._handle_interim_log("all 5").startswith("Unknown option: 5")
    assert interim_plugin._handle_interim_log("bogus").startswith("Unknown option: bogus")
    assert interim_plugin._handle_interim_log("help").startswith("/interim-log:")
    assert interim_plugin._handle_interim_log("clear") == "interim-ledger: deleted 3 recorded message(s)."
    assert interim_plugin._handle_interim_log("all") == "Mid-turn messages, all recorded: none recorded."


def test_register_wires_the_hook_and_the_command(interim_plugin):
    calls = []

    class Ctx:
        def register_hook(self, name, cb):
            calls.append(("hook", name, cb))

        def register_command(self, name, handler, **kw):
            calls.append(("command", name, handler))

    interim_plugin.register(Ctx())
    assert calls == [("hook", "on_interim_message", interim_plugin._on_interim),
                     ("command", "interim-log", interim_plugin._handle_interim_log)]


def test_help_lines_up(interim_plugin):
    options = [line for line in interim_plugin._HELP.splitlines() if line.startswith("  /")]
    assert len(options) == 8 and len({len(line) - len(line[2:].split("  ", 1)[1].lstrip()) for line in options}) == 1
