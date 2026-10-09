"""approval-ledger: what one approval decision becomes, how it is stored and how /approval-log reads."""

import json
import threading
import time

import pytest

NOW = 1_800_000_000.0


def _payload(**over):
    base = {"command": "rm -rf SECRET-COMMAND-PATH", "description": "SECRET-DESCRIPTION recursive delete",
            "pattern_key": "recursive delete", "pattern_keys": ["recursive delete"],
            "session_key": "agent:main:telegram:dm:SECRET-CHAT-ID", "surface": "cli",
            "turn_id": "turn-1", "tool_call_id": "call-1", "session_id": "s1", "choice": "once"}
    base.update(over)
    return base


def test_row_keeps_the_decision_and_none_of_the_text(apledger):
    row = apledger.build_row(_payload(), now=NOW, wait=2.71828)
    assert row == {"ts": NOW, "session_id": "s1", "surface": "cli", "pattern": "recursive delete",
                   "choice": "once", "outcome": "approved", "decided_by": "", "coalesced": False,
                   "wait_s": 2.718}
    assert "SECRET" not in json.dumps(row)


@pytest.mark.parametrize("choice, outcome", [
    ("once", "approved"), ("session", "approved"), ("always", "approved"), ("smart_approve", "approved"),
    ("deny", "denied"), ("smart_deny", "denied"),
    ("timeout", "unanswered"), ("cancelled", "unanswered"), ("notify_failed", "unanswered"),
    ("transport_timeout", "unanswered"), ("transport_error", "unanswered"),
    ("smart_escalate", "other"), ("ONCE", "other"), ("", "other")])
def test_outcome_of_every_documented_choice(apledger, choice, outcome):
    assert apledger.outcome(choice) == outcome


def test_row_tolerates_a_sparse_or_odd_payload(apledger):
    row = apledger.build_row({}, now=NOW, wait=None)
    assert (row["surface"], row["pattern"], row["choice"], row["outcome"]) == ("unknown", "unknown", "unknown", "other")
    assert row["session_id"] == row["decided_by"] == "" and row["coalesced"] is False and row["wait_s"] is None
    odd = apledger.build_row(_payload(pattern_key="p" * 500, surface="s" * 500, choice="c" * 500,
                                      coalesced="yes", decided_by="aux_llm"), now=NOW, wait=-4)
    assert len(odd["pattern"]) == len(odd["surface"]) == 80 and len(odd["choice"]) == 40
    assert odd["coalesced"] is False and odd["wait_s"] == 0.0 and odd["decided_by"] == "aux_llm"
    assert apledger.build_row(_payload(coalesced=True), now=NOW, wait=None)["coalesced"] is True


def test_storage_round_trip_bound_and_clear(apledger, tmp_path, monkeypatch):
    path = tmp_path / apledger.FILENAME
    assert apledger.read(path) == [] and apledger.clear(path) == 0
    for choice in ("once", "deny", "timeout"):
        apledger.append(path, apledger.build_row(_payload(choice=choice), now=NOW, wait=1.0))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["choice"] for r in apledger.read(path)] == ["once", "deny", "timeout"]
    assert b"\r\n" not in path.read_bytes()

    monkeypatch.setattr(apledger, "MAX_BYTES", 400)
    monkeypatch.setattr(apledger, "KEEP_LINES", 2)
    apledger.append(path, apledger.build_row(_payload(choice="always"), now=NOW, wait=None))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["choice"] for r in apledger.read(path)] == ["always"]
    assert not list(tmp_path.glob(".approvals-*"))
    assert apledger.clear(path) == 1 and not path.exists()


def _row(pattern, choice, wait=1.0, surface="cli", session="s1", ts=NOW, decided_by=""):
    return {"ts": ts, "session_id": session, "surface": surface, "pattern": pattern, "choice": choice,
            "outcome": {"once": "approved", "session": "approved", "smart_approve": "approved",
                        "deny": "denied", "smart_deny": "denied", "timeout": "unanswered",
                        "cancelled": "unanswered"}.get(choice, "other"),
            "decided_by": decided_by, "coalesced": False, "wait_s": wait}


def test_select_windows(apledger):
    rows = [_row("a", "once", ts=NOW - 2 * 86400, session="old"), _row("a", "deny", session="s1"),
            _row("b", "once", session="s2"), _row("b", "deny", session="")]
    assert len(apledger.select(rows, "all", NOW)) == 4
    assert [r["session_id"] for r in apledger.select(rows, "day", NOW)] == ["s1", "s2", ""]
    assert [r["pattern"] for r in apledger.select(rows, "session", NOW)] == ["b"]
    assert apledger.select([_row("a", "once", session="")], "session", NOW) == []


def test_summary_by_pattern_and_surface(apledger):
    rows = [_row("recursive delete", "once", wait=4.0), _row("recursive delete", "deny", wait=12.0),
            _row("recursive delete", "timeout", wait=300.0), _row("recursive delete", "session", wait=2.0),
            _row("sudo", "smart_approve", wait=0.8, surface="smart", decided_by="aux_llm"),
            _row("sudo", "once", wait=75.0, surface="gateway")]
    lines = apledger.summarize(rows, title="last 24 hours").splitlines()
    assert lines[0] == "Approvals, last 24 hours:"
    assert lines[2].split() == ["pattern", "asked", "approved", "denied", "unanswered", "median", "wait"]
    # The timeout is not an answer time: the median is of 2.0, 4.0 and 12.0.
    assert lines[3].split() == ["recursive", "delete", "4", "2", "1", "1", "4.0s"]
    # The smart verdict is not a person's answer: the median is the one gateway answer.
    assert lines[4].split() == ["sudo", "2", "2", "0", "0", "1m15s"]
    assert lines[5].split() == ["total", "6", "4", "1", "1", "8.0s"]
    assert lines[-1].startswith("Smart-mode verdicts count as approved or denied")

    by_surface = apledger.summarize(rows, title="x", by="surface").splitlines()
    assert by_surface[2].split()[0] == "surface"
    assert [line.split()[:2] for line in by_surface[3:6]] == [["cli", "4"], ["gateway", "1"], ["smart", "1"]]
    assert by_surface[5].split()[-1] == "-", "a smart-only group has no person's answer"


def test_summary_puts_the_rule_that_asks_most_first(apledger):
    rows = [_row("a-rare", "once")] + [_row("z-common", "deny")] * 3
    lines = apledger.summarize(rows, title="x").splitlines()
    assert [line.split()[0] for line in lines[3:6]] == ["z-common", "a-rare", "total"]


def test_summary_edges(apledger):
    assert apledger.summarize([], title="all recorded") == "Approvals, all recorded: none recorded."
    one = apledger.summarize([_row("p", "cancelled", wait=None)], title="x").splitlines()
    assert one[3].split() == ["p", "1", "0", "0", "1", "-"]
    assert not any(line.startswith("total") for line in one)
    assert one[-1].startswith("Unanswered: timed out, withdrawn or never delivered")
    # A row from a foreign writer: a non-number wait is no answer time.
    odd = apledger.summarize([_row("p", "once", wait="soon"), _row("p", "once", wait=True)], title="x")
    assert odd.splitlines()[3].split() == ["p", "2", "2", "0", "0", "-"]


@pytest.mark.parametrize("waits, shown", [
    ([3.0], "3.0s"), ([1.0, 2.0], "1.5s"), ([1.0, 5.0, 100.0], "5.0s"), ([9.94], "9.9s"), ([10.0], "10s"),
    ([59.9], "59s"), ([60.0], "1m00s"), ([3599.0], "59m59s"), ([3600.0], "1h00m"), ([7260.0], "2h01m")])
def test_median_wait_and_its_units(apledger, waits, shown):
    rows = [_row("p", "once", wait=w) for w in waits]
    assert apledger.summarize(rows, title="x").splitlines()[3].split()[-1] == shown


@pytest.mark.parametrize("word, seconds", [
    ("7d", 7 * 86400.0), ("1d", 86400.0), ("12h", 43200.0), ("9999h", 9999 * 3600.0),
    ("0d", None), ("00h", None), ("d", None), ("7", None), ("7w", None), ("-1d", None),
    ("1.5h", None), ("12345d", None), ("٧d", None), ("", None), ("day", None)])
def test_span_words(apledger, word, seconds):
    assert apledger.parse_span(word) == seconds


def test_span_titles(apledger):
    assert [apledger.span_title(w) for w in ("1h", "6h", "1d", "30d")] == [
        "last 1 hour", "last 6 hours", "last 1 day", "last 30 days"]


def test_recent_lists_newest_first(apledger):
    rows = [_row("p-old", "timeout", wait=None, ts=NOW - 3 * 86400 - 5),
            _row("p-mid", "smart_deny", wait=0.4, surface="smart", decided_by="aux_llm", ts=NOW - 2 * 3600 - 1),
            _row("p-new", "deny", wait=14.0, surface="gateway", ts=NOW - 125),
            _row("p-now", "once", wait=3.25, ts=NOW - 7)]
    lines = apledger.recent(rows, NOW, title="all recorded").splitlines()
    assert lines[0] == "Approvals, all recorded, newest 4 of 4:"
    assert lines[2].split() == ["when", "pattern", "surface", "answer", "wait"]
    assert lines[3].split() == ["7s", "ago", "p-now", "cli", "once", "3.2s"]
    assert lines[4].split() == ["2m", "ago", "p-new", "gateway", "deny", "14s"]
    assert lines[5].split() == ["2h", "ago", "p-mid", "smart", "smart_deny", "0.4s"]
    assert lines[6].split() == ["3d", "ago", "p-old", "cli", "timeout", "-"]
    limited = apledger.recent(rows, NOW, title="x", limit=2).splitlines()
    assert limited[0] == "Approvals, x, newest 2 of 4:" and len(limited) == 5
    assert apledger.recent([], NOW, title="last 7 days") == "Approvals, last 7 days: none recorded."
    # A clock that moved backwards is never a negative age; a non-number wait shows as "-".
    odd = apledger.recent([_row("p", "once", wait=True, ts=NOW + 50)], NOW, title="x").splitlines()[3]
    assert odd.startswith("0s ago") and odd.split()[-1] == "-"


def test_default_recent_limit_is_ten(apledger):
    rows = [_row(f"p{i}", "once", ts=NOW - 100 + i) for i in range(14)]
    lines = apledger.recent(rows, NOW, title="x").splitlines()
    assert lines[0] == "Approvals, x, newest 10 of 14:"
    assert [line.split()[2] for line in lines[3:]] == [f"p{i}" for i in range(13, 3, -1)]


@pytest.mark.parametrize("seconds, shown", [
    (0, "0s ago"), (59.9, "59s ago"), (60, "1m ago"), (3599, "59m ago"), (3600, "1h ago"),
    (86399, "23h ago"), (86400, "1d ago"), (40 * 86400, "40d ago")])
def test_recent_ages_change_unit_at_the_boundary(apledger, seconds, shown):
    line = apledger.recent([_row("p", "once", ts=NOW - seconds)], NOW, title="x").splitlines()[3]
    assert line.startswith(shown + " ")


class _Ctx:
    def __init__(self):
        self.hooks, self.commands = {}, {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


@pytest.fixture
def wired(approvals_plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(approvals_plugin, "_ledger_path", lambda: tmp_path / approvals_plugin.ledger.FILENAME)
    approvals_plugin._open.prompt = None
    ctx = _Ctx()
    approvals_plugin.register(ctx)
    return ctx, tmp_path / approvals_plugin.ledger.FILENAME


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def clock(approvals_plugin, monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(approvals_plugin.time, "monotonic", fake)
    return fake


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()]


def test_plugin_surface_and_round_trip(wired, clock):
    ctx, path = wired
    assert sorted(ctx.hooks) == ["post_approval_response", "pre_approval_request"]
    assert sorted(ctx.commands) == ["approval-log"]
    pre, post, log = ctx.hooks["pre_approval_request"], ctx.hooks["post_approval_response"], ctx.commands["approval-log"]
    assert log("") == "Approvals, last 24 hours: none recorded."
    request = {k: v for k, v in _payload().items() if k != "choice"}
    pre(**request)
    clock.now += 7.5
    post(**request, choice="once")
    pre(**dict(request, session_id="s2", surface="gateway", pattern_key="sudo"))
    clock.now += 2.0
    post(**dict(request, session_id="s2", surface="gateway", pattern_key="sudo"), choice="deny")
    raw = path.read_text(encoding="utf-8-sig")
    assert "SECRET" not in raw
    assert [(r["choice"], r["wait_s"]) for r in _rows(path)] == [("once", 7.5), ("deny", 2.0)]
    assert log("").splitlines()[3].split() == ["recursive", "delete", "1", "1", "0", "0", "7.5s"]
    session = log("session").splitlines()
    assert session[0] == "Approvals, latest session:" and session[3].split() == ["sudo", "1", "0", "1", "0", "2.0s"]
    assert log("all surfaces").splitlines()[3].split()[:2] == ["cli", "1"]
    assert log("all patterns").splitlines()[2].split()[0] == "pattern"


def test_wait_needs_the_matching_request(wired, clock):
    ctx, path = wired
    pre, post = ctx.hooks["pre_approval_request"], ctx.hooks["post_approval_response"]
    # A decision with no request before it (notify_failed can be the first thing a plugin hears
    # after a restart) has no wait.
    post(**_payload(choice="notify_failed"))
    # A request for another prompt does not pair with this decision.
    pre(**_payload(pattern_key="sudo"))
    clock.now += 3.0
    post(**_payload(choice="deny"))
    # A smart verdict that escalates fires no decision; the human prompt after it replaces the
    # request, so the wait is the person's, not the reviewer's plus the person's.
    pre(**_payload(surface="smart"))
    clock.now += 1.0
    pre(**_payload())
    clock.now += 4.0
    post(**_payload(choice="once"))
    # The request is used once: a second decision on this thread has no wait.
    post(**_payload(choice="once"))
    # A request raised on one surface does not pair with a decision reported for another.
    pre(**_payload(surface="smart"))
    clock.now += 2.0
    post(**_payload(surface="cli", choice="deny"))
    assert [r["wait_s"] for r in _rows(path)] == [None, None, 4.0, None, None]


def test_session_key_tells_prompts_apart_but_is_never_stored(wired, clock):
    ctx, path = wired
    pre, post = ctx.hooks["pre_approval_request"], ctx.hooks["post_approval_response"]
    pre(**_payload(session_key="agent:main:cli:one"))
    clock.now += 1.0
    post(**_payload(session_key="agent:main:cli:two", choice="deny"))
    assert _rows(path)[0]["wait_s"] is None
    assert "agent:main" not in path.read_text(encoding="utf-8-sig")


def test_each_thread_pairs_its_own_prompt(wired, approvals_plugin):
    ctx, path = wired
    pre, post = ctx.hooks["pre_approval_request"], ctx.hooks["post_approval_response"]
    started, release = threading.Barrier(2), threading.Event()

    def waiter(pattern, choice):
        pre(**_payload(pattern_key=pattern))
        started.wait(5)
        release.wait(5)
        post(**_payload(pattern_key=pattern, choice=choice))

    workers = [threading.Thread(target=waiter, args=args) for args in (("p-a", "once"), ("p-b", "deny"))]
    for worker in workers:
        worker.start()
    time.sleep(0.05)
    release.set()
    for worker in workers:
        worker.join(5)
    rows = {r["pattern"]: r for r in _rows(path)}
    assert {r["choice"] for r in rows.values()} == {"once", "deny"}
    assert all(isinstance(r["wait_s"], float) for r in rows.values()), "each thread kept its own request"


def test_command_help_unknown_windows_and_clear(wired):
    ctx, path = wired
    post, log = ctx.hooks["post_approval_response"], ctx.commands["approval-log"]
    assert log("help").startswith("/approval-log:")
    assert log("bogus").startswith("Unknown option: bogus")
    assert log("0d").startswith("Unknown option: 0d") and log("7w").startswith("Unknown option: 7w")
    assert "/approval-log 7d" in log("help") and "/approval-log recent" in log("help")
    post(**_payload(choice="deny"))
    assert log("clear") == "approval-ledger: deleted 1 recorded decision(s)." and not path.exists()
    assert log("all") == "Approvals, all recorded: none recorded."


def test_command_time_window_and_recent(wired, apledger):
    ctx, path = wired
    log = ctx.commands["approval-log"]
    now = time.time()
    for row in (_row("p-week", "once", session="old", ts=now - 5 * 86400),
                _row("p-today", "deny", wait=6.0, surface="gateway", ts=now - 30)):
        apledger.append(path, row)
    assert [line.split()[0] for line in log("").splitlines()[3:4]] == ["p-today"]
    week = log("7d").splitlines()
    assert week[0] == "Approvals, last 7 days:"
    assert sorted(line.split()[0] for line in week[3:5]) == ["p-today", "p-week"]
    assert log("3d").splitlines()[0] == "Approvals, last 3 days:" and "p-week" not in log("3d")
    assert "p-week" not in log("1h")
    assert log("7d surfaces").splitlines()[3].split()[:2] in (["cli", "1"], ["gateway", "1"])
    listing = log("7d recent").splitlines()
    assert listing[0] == "Approvals, last 7 days, newest 2 of 2:"
    assert listing[3].split()[2:] == ["p-today", "gateway", "deny", "6.0s"]
    assert listing[4].split()[:3] == ["5d", "ago", "p-week"]
    assert log("recent").splitlines()[0] == "Approvals, last 24 hours, newest 1 of 1:"
    # session and all keep their meaning when a span is typed next to them.
    assert log("session 1h").splitlines()[0] == "Approvals, latest session:"
    assert log("all 1h recent").splitlines()[0] == "Approvals, all recorded, newest 2 of 2:"


def test_hooks_never_raise(approvals_plugin, monkeypatch):
    def boom():
        raise OSError("read-only home")

    monkeypatch.setattr(approvals_plugin, "_ledger_path", boom)
    approvals_plugin._on_pre_approval_request(**_payload())
    approvals_plugin._on_post_approval_response(**_payload())

    class Unprintable:
        def __str__(self):
            raise ValueError("no text")

    approvals_plugin._on_pre_approval_request(surface=Unprintable())
    approvals_plugin._on_post_approval_response(surface=Unprintable())


def _local(year, month, day, hour=12, minute=0):
    from datetime import datetime

    return datetime(year, month, day, hour, minute).timestamp()


def test_summary_by_day_is_newest_day_first(apledger):
    rows = [_row("sudo", "once", wait=3.0, ts=_local(2027, 1, 8, 9)),
            _row("sudo", "deny", wait=2.0, ts=_local(2027, 1, 10, 0, 1)),
            _row("sudo", "timeout", wait=300.0, ts=_local(2027, 1, 10, 23, 59)),
            _row("recursive delete", "once", wait=4.0, ts=_local(2027, 1, 10, 13)),
            _row("sudo", "smart_approve", wait=0.5, surface="smart", decided_by="aux_llm",
                 ts=_local(2027, 1, 9, 23, 59)),
            _row("sudo", "deny", wait=8.0, ts=_local(2027, 1, 9, 0, 0))]
    lines = apledger.summarize(rows, title="last 7 days", by="day").splitlines()
    assert lines[2].split() == ["day", "asked", "approved", "denied", "unanswered", "median", "wait"]
    # Days, not counts, set the order: the smallest day can sit above a busier one.
    assert lines[3].split() == ["2027-01-10", "3", "1", "1", "1", "3.0s"]
    assert lines[4].split() == ["2027-01-09", "2", "1", "1", "0", "8.0s"]
    assert lines[5].split() == ["2027-01-08", "1", "1", "0", "0", "3.0s"]
    assert lines[6].split() == ["total", "6", "3", "2", "1", "3.5s"]


def test_day_of_a_row_without_a_time_goes_last(apledger):
    assert apledger.local_day(_local(2027, 3, 4, 0, 0)) == "2027-03-04"
    assert apledger.local_day(None) == apledger.local_day("soon") == apledger.local_day(1e300) == "unknown"
    rows = [{"pattern": "p", "outcome": "approved"}, _row("p", "once", ts=_local(2027, 1, 2)),
            _row("p", "once", ts=_local(2027, 1, 1))]
    lines = apledger.summarize(rows, title="x", by="day").splitlines()
    assert [line.split()[0] for line in lines[3:7]] == ["2027-01-02", "2027-01-01", "unknown", "total"]


def test_command_days(wired, apledger):
    ctx, path = wired
    log = ctx.commands["approval-log"]
    now = time.time()
    apledger.append(path, _row("p-old", "once", session="old", ts=now - 3 * 86400))
    # Both at the same moment, so a run at midnight cannot split them over two days.
    apledger.append(path, _row("p-new", "deny", ts=now - 10))
    apledger.append(path, _row("p-new", "once", ts=now - 10))
    week = log("7d days").splitlines()
    assert week[0] == "Approvals, last 7 days:" and week[2].split()[0] == "day"
    assert [line.split()[:2] for line in week[3:6]] == [
        [time.strftime("%Y-%m-%d", time.localtime(now - 10)), "2"],
        [time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400)), "1"], ["total", "3"]]
    assert [line.split()[1] for line in log("days").splitlines()[3:4]] == ["2"]
    assert log("session days").splitlines()[0] == "Approvals, latest session:"
    assert "/approval-log days" in log("help")
    # recent still lists decisions, not days.
    assert log("all days recent").splitlines()[2].split()[0] == "when"
