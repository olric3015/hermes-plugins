"""wait-ledger: what one human wait becomes, how it is stored and how /waits reads."""

import json
import time

import pytest

NOW = 1_800_000_000.0


def _payload(**over):
    base = {"kind": "approval", "request_id": "r1", "session_id": "s1",
            "session_key": "agent:main:telegram:dm:SECRET-CHAT-ID", "platform": "cli",
            "prompt": "rm -rf SECRET-COMMAND-PATH", "outcome": "once"}
    base.update(over)
    return base


def test_row_keeps_how_the_wait_ended_and_none_of_the_text(wtledger):
    row = wtledger.build_row(_payload(), now=NOW, wait=2.71828)
    assert row == {"ts": NOW, "session_id": "s1", "platform": "cli", "kind": "approval",
                   "outcome": "once", "result": "answered", "wait_s": 2.718}
    assert "SECRET" not in json.dumps(row)


@pytest.mark.parametrize("outcome, result", [
    # approval choices
    ("once", "answered"), ("session", "answered"), ("always", "answered"), ("deny", "answered"),
    ("timeout", "unanswered"), ("cancelled", "unanswered"), ("notify_failed", "unanswered"),
    ("transport_timeout", "unanswered"), ("transport_error", "unanswered"),
    # clarify replies
    ("submitted", "answered"), ("timed_out", "unanswered"), ("undelivered", "unanswered"),
    # sudo prompts
    ("provided", "answered"), ("skipped", "skipped"),
    ("error", "error"), ("ONCE", "other"), ("", "other")])
def test_result_of_every_documented_outcome(wtledger, outcome, result):
    assert wtledger.result(outcome) == result


def test_row_tolerates_a_sparse_or_odd_payload(wtledger):
    row = wtledger.build_row({}, now=NOW, wait=None)
    assert (row["kind"], row["platform"], row["outcome"], row["result"]) == ("unknown", "unknown", "unknown", "other")
    assert row["session_id"] == "" and row["wait_s"] is None
    odd = wtledger.build_row(_payload(kind="k" * 500, platform="p" * 500, outcome="o" * 500), now=NOW, wait=-4)
    assert len(odd["kind"]) == len(odd["platform"]) == len(odd["outcome"]) == 40 and odd["wait_s"] == 0.0


def test_storage_round_trip_bom_bound_and_clear(wtledger, tmp_path, monkeypatch):
    path = tmp_path / wtledger.FILENAME
    assert wtledger.read(path) == [] and wtledger.clear(path) == 0
    for outcome in ("once", "submitted", "timeout"):
        wtledger.append(path, wtledger.build_row(_payload(outcome=outcome), now=NOW, wait=1.0))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["outcome"] for r in wtledger.read(path)] == ["once", "submitted", "timeout"]
    assert b"\r\n" not in path.read_bytes()

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert [r["outcome"] for r in wtledger.read(bom)] == ["once", "submitted", "timeout"]

    monkeypatch.setattr(wtledger, "MAX_BYTES", 400)
    monkeypatch.setattr(wtledger, "KEEP_LINES", 2)
    wtledger.append(path, wtledger.build_row(_payload(outcome="provided"), now=NOW, wait=None))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["outcome"] for r in wtledger.read(path)] == ["provided"]
    assert not list(tmp_path.glob(".waits-*"))
    assert wtledger.clear(path) == 1 and not path.exists()

    # A file another tool saved with a BOM is still trimmed to rows that parse.
    monkeypatch.setattr(wtledger, "KEEP_LINES", 5)
    bom.write_bytes(b"\xef\xbb\xbf" + (json.dumps(_payload()) + "\n").encode("utf-8"))
    wtledger.append(bom, wtledger.build_row(_payload(outcome="provided"), now=NOW, wait=None))
    assert [r["outcome"] for r in wtledger.read(bom)] == ["once", "provided"]


def _row(kind, outcome, wait=1.0, platform="cli", session="s1", ts=NOW):
    result = {"once": "answered", "deny": "answered", "submitted": "answered", "provided": "answered",
              "skipped": "skipped", "timeout": "unanswered", "timed_out": "unanswered"}.get(outcome, "other")
    return {"ts": ts, "session_id": session, "platform": platform, "kind": kind,
            "outcome": outcome, "result": result, "wait_s": wait}


def test_select_windows(wtledger):
    rows = [_row("approval", "once", ts=NOW - 2 * 86400, session="old"), _row("approval", "deny", session="s1"),
            _row("clarify", "submitted", session="s2"), _row("sudo", "provided", session="")]
    assert len(wtledger.select(rows, "all", NOW)) == 4
    assert len(wtledger.select(rows, "day", NOW)) == 3
    assert len(wtledger.select(rows, "day", NOW, span=3 * 86400)) == 4
    assert [r["kind"] for r in wtledger.select(rows, "session", NOW)] == ["clarify"]
    assert wtledger.select([_row("a", "once", session="")], "session", NOW) == []


def test_summary_puts_the_longest_total_wait_first(wtledger):
    rows = [_row("approval", "once", 2.0), _row("approval", "deny", 4.0), _row("approval", "timeout", 60.0),
            _row("clarify", "submitted", 125.0), _row("sudo", "skipped", None)]
    out = wtledger.summarize(rows, title="last 24 hours")
    lines = out.splitlines()
    assert lines[0] == "Waits, last 24 hours:"
    assert lines[2].split() == ["kind", "waits", "answered", "unanswered", "total", "wait", "median", "longest"]
    assert lines[3].split() == ["clarify", "1", "1", "0", "2m05s", "2m05s", "2m05s"]
    assert lines[4].split() == ["approval", "3", "2", "1", "1m06s", "4.0s", "1m00s"]
    assert lines[5].split() == ["sudo", "1", "0", "0", "-", "-", "-"]
    assert lines[6].split() == ["total", "5", "3", "1", "3m11s", "32s", "2m05s"]
    assert lines[-2:] == ["Unanswered: timed out, withdrawn or never delivered. An empty answer or a failed prompt",
                          "counts in waits only."]


def test_summary_single_group_has_no_total_and_empty_says_so(wtledger):
    out = wtledger.summarize([_row("sudo", "provided", 3.0)], title="all recorded", by="platform")
    lines = out.splitlines()
    assert lines[2].split()[0] == "platform" and lines[3].split()[0] == "cli"
    assert not any(line.startswith("total") for line in lines)
    assert wtledger.summarize([], title="all recorded") == "Waits, all recorded: none recorded."


def test_days_newest_first_unknown_last(wtledger):
    rows = [_row("approval", "once", ts=NOW - 86400), _row("approval", "once", ts=NOW),
            _row("approval", "once", ts=NOW), {"kind": "approval", "result": "answered", "wait_s": 1.0}]
    out = wtledger.summarize(rows, title="all recorded", by="day").splitlines()
    names = [line.split()[0] for line in out[3:-3]]
    assert names == [wtledger.local_day(NOW), wtledger.local_day(NOW - 86400), "unknown", "total"]


def test_recent_lists_newest_first(wtledger):
    rows = [_row("approval", "once", 2.0, ts=NOW - 7200), _row("clarify", "timed_out", 300.0, ts=NOW - 30),
            _row("sudo", "skipped", None, platform="telegram", ts=NOW - 5)]
    out = wtledger.recent(rows, NOW, title="all recorded", limit=2).splitlines()
    assert out[0] == "Waits, all recorded, newest 2 of 3:"
    assert out[2].split() == ["when", "kind", "platform", "ended", "wait"]
    assert out[3].split() == ["5s", "ago", "sudo", "telegram", "skipped", "-"]
    assert out[4].split() == ["30s", "ago", "clarify", "cli", "timed_out", "5m00s"]
    assert wtledger.recent([], NOW, title="x") == "Waits, x: none recorded."


@pytest.mark.parametrize("word, count", [("1", 1), ("25", 25), ("100", 100), ("0", None), ("101", None),
                                         ("9" * 5000, None), ("-5", None), ("٣", None), ("7d", None)])
def test_parse_count(wtledger, word, count):
    assert wtledger.parse_count(word) == count


@pytest.mark.parametrize("word, seconds", [("6h", 6 * 3600.0), ("7d", 7 * 86400.0), ("0d", None),
                                           ("12345h", None), ("7w", None), ("d", None), ("٣d", None)])
def test_parse_span(wtledger, word, seconds):
    assert wtledger.parse_span(word) == seconds


@pytest.fixture
def ledger_file(waits_plugin, tmp_path, monkeypatch):
    path = tmp_path / "waits.jsonl"
    monkeypatch.setattr(waits_plugin, "_ledger_path", lambda: path)
    waits_plugin._open.clear()
    return path


def test_request_then_resolved_records_the_wait(waits_plugin, wtledger, ledger_file, monkeypatch):
    clock = iter([100.0, 103.5])
    monkeypatch.setattr(waits_plugin.time, "monotonic", lambda: next(clock))
    waits_plugin._on_request(**_payload(request_id="abc", outcome=None))
    waits_plugin._on_resolved(**_payload(request_id="abc", kind="clarify", outcome="submitted"))
    [row] = wtledger.read(ledger_file)
    assert (row["kind"], row["result"], row["wait_s"]) == ("clarify", "answered", 3.5)
    assert waits_plugin._open == {}


def test_resolved_without_its_request_has_no_wait(waits_plugin, wtledger, ledger_file):
    waits_plugin._on_request(**_payload(request_id="other"))
    waits_plugin._on_resolved(**_payload(request_id="abc"))
    [row] = wtledger.read(ledger_file)
    assert row["wait_s"] is None and "other" in waits_plugin._open


def test_open_prompts_are_bounded(waits_plugin, ledger_file, monkeypatch):
    monkeypatch.setattr(waits_plugin, "MAX_OPEN", 3)
    for i in range(5):
        waits_plugin._on_request(**_payload(request_id=f"r{i}"))
    assert list(waits_plugin._open) == ["r2", "r3", "r4"]
    waits_plugin._on_request(**_payload(request_id=""))
    assert list(waits_plugin._open) == ["r2", "r3", "r4"]


def test_hooks_never_raise(waits_plugin, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(waits_plugin, "_ledger_path", boom)
    waits_plugin._on_resolved(**_payload())
    waits_plugin._on_request(request_id=object())


def test_command(waits_plugin, wtledger, ledger_file):
    now = time.time()
    for kind, outcome, wait, ts in (("approval", "once", 2.0, now - 60), ("clarify", "timed_out", 30.0, now - 30),
                                    ("sudo", "provided", 4.0, now - 3 * 86400)):
        wtledger.append(ledger_file, _row(kind, outcome, wait, ts=ts))
    out = waits_plugin._handle_waits("")
    assert out.startswith("Waits, last 24 hours:") and "sudo" not in out
    assert "sudo" in waits_plugin._handle_waits("7d") and "last 7 days" in waits_plugin._handle_waits("7d")
    assert "platform" in waits_plugin._handle_waits("all platforms").splitlines()[2]
    assert waits_plugin._handle_waits("all recent 2").splitlines()[0] == "Waits, all recorded, newest 2 of 3:"
    assert waits_plugin._handle_waits("all recent").splitlines()[0] == "Waits, all recorded, newest 3 of 3:"
    assert waits_plugin._handle_waits("recent 0").startswith("Unknown option: 0")
    assert waits_plugin._handle_waits("all 5").startswith("Unknown option: 5")
    assert waits_plugin._handle_waits("bogus").startswith("Unknown option: bogus")
    assert waits_plugin._handle_waits("help").startswith("/waits:")
    assert waits_plugin._handle_waits("clear") == "wait-ledger: deleted 3 recorded wait(s)."
    assert waits_plugin._handle_waits("all") == "Waits, all recorded: none recorded."


def test_register_wires_both_hooks_and_the_command(waits_plugin):
    calls = []

    class Ctx:
        def register_hook(self, name, cb):
            calls.append(("hook", name, cb))

        def register_command(self, name, handler, **kw):
            calls.append(("command", name, handler))

    waits_plugin.register(Ctx())
    assert calls == [("hook", "on_human_input_request", waits_plugin._on_request),
                     ("hook", "on_human_input_resolved", waits_plugin._on_resolved),
                     ("command", "waits", waits_plugin._handle_waits)]
