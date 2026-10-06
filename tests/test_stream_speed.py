import json
import threading
import time

import pytest

NOW = 1_800_000_000.0


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _ev(**over):
    base = {"session_id": "s1", "turn_id": "t1", "iteration": 1, "model": "m-fast",
            "provider": "custom", "surface": "cli"}
    base.update(over)
    return base


@pytest.fixture
def tracked(timing):
    clock = _Clock()
    return timing.Tracker(clock=clock, sleep=clock.sleep), clock


def test_one_stream_becomes_one_row_without_its_text(timing, tracked):
    tracker, clock = tracked
    tracker.start(_ev())
    clock.now += 0.8
    tracker.delta(_ev(delta="SECRET-", kind="text"))
    clock.now += 0.5
    tracker.delta(_ev(delta="ANSWER", kind="text"))
    clock.now += 1.2
    row = tracker.end(_ev(final_text="SECRET-ANSWER", finished=True, error=None), NOW)
    assert (row["ttft_s"], row["duration_s"], row["chars"]) == (0.8, 2.5, 13)
    assert row["finished"] is True and row["failed"] is False and row["model"] == "m-fast"
    assert "SECRET" not in json.dumps(row) and tracker.open_count() == 0


def test_reasoning_and_empty_deltas_are_not_first_text(tracked):
    tracker, clock = tracked
    tracker.start(_ev())
    clock.now += 1.0
    tracker.delta(_ev(delta="thinking", kind="reasoning"))
    tracker.delta(_ev(delta="", kind="text"))
    clock.now += 1.0
    tracker.delta(_ev(delta="hi", kind="text"))
    assert tracker.end(_ev(final_text="hi", finished=True, error=None), NOW)["ttft_s"] == 2.0


def test_tool_call_only_stream_has_no_first_text_and_does_not_wait(tracked):
    tracker, clock = tracked
    tracker.start(_ev())
    clock.now += 0.4
    row = tracker.end(_ev(final_text="", finished=True, error=None), NOW)
    assert row["ttft_s"] is None and row["chars"] == 0 and row["duration_s"] == 0.4
    assert clock.now == 100.4


def test_end_waits_briefly_for_a_first_delta_still_in_flight(timing):
    tracker = timing.Tracker()
    tracker.start(_ev())
    threading.Timer(0.05, lambda: tracker.delta(_ev(delta="late", kind="text"))).start()
    row = tracker.end(_ev(final_text="late", finished=True, error=None), NOW)
    assert row["ttft_s"] is not None


def test_end_gives_up_after_the_grace_period(timing, tracked):
    tracker, clock = tracked
    tracker.start(_ev())
    row = tracker.end(_ev(final_text="text", finished=True, error=None), NOW)
    assert row["ttft_s"] is None
    assert clock.now - 100.0 <= timing.DELTA_GRACE_SECONDS + 0.02


def test_delta_seen_before_start_is_unknown_not_zero(tracked):
    tracker, clock = tracked
    tracker.delta(_ev(delta="x", kind="text"))
    clock.now += 0.1
    tracker.start(_ev())
    clock.now += 1.0
    row = tracker.end(_ev(final_text="x", finished=True, error=None), NOW)
    assert row["ttft_s"] is None and row["duration_s"] == 1.0


def test_end_without_start_records_nothing(tracked):
    tracker, _clock = tracked
    assert tracker.end(_ev(final_text="", finished=True, error=None), NOW) is None


def test_failed_stream_keeps_a_flag_not_the_error(tracked):
    tracker, clock = tracked
    tracker.start(_ev())
    clock.now += 3.0
    row = tracker.end(_ev(final_text="", finished=False, error="APIError: SECRET provider text"), NOW)
    assert row["failed"] is True and row["finished"] is False and "SECRET" not in json.dumps(row)


def test_streams_are_keyed_separately(tracked):
    tracker, clock = tracked
    tracker.start(_ev(session_id="a"))
    clock.now += 1.0
    tracker.start(_ev(session_id="b"))
    tracker.delta(_ev(session_id="b", delta="x", kind="text"))
    clock.now += 1.0
    tracker.delta(_ev(session_id="a", delta="y", kind="text"))
    assert tracker.end(_ev(session_id="a", final_text="y", finished=True, error=None), NOW)["ttft_s"] == 2.0
    assert tracker.end(_ev(session_id="b", final_text="x", finished=True, error=None), NOW)["ttft_s"] == 0.0


def test_open_streams_are_bounded(timing, tracked, monkeypatch):
    tracker, clock = tracked
    monkeypatch.setattr(timing, "MAX_OPEN", 10)
    for i in range(50):
        tracker.start(_ev(session_id=f"s{i}"))
    assert tracker.open_count() == 10
    clock.now += timing.OPEN_TTL_SECONDS + 1
    tracker.start(_ev(session_id="fresh"))
    assert tracker.open_count() == 1


def test_storage_round_trip_bound_and_clear(timing, tmp_path, monkeypatch):
    monkeypatch.setattr(timing, "MAX_BYTES", 2000)
    monkeypatch.setattr(timing, "KEEP_LINES", 5)
    path = tmp_path / timing.FILENAME
    for i in range(40):
        timing.append(path, {"ts": NOW, "model": f"m{i}", "ttft_s": 0.5, "duration_s": 1.0, "chars": 10})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"torn\n')
    rows = timing.read(path)
    assert rows[-1]["model"] == "m39" and len(rows) <= 5 + 2000 // 60
    assert not list(tmp_path.glob(".streams-*"))
    assert timing.clear(path) == len(rows) and not path.exists()
    assert timing.read(path) == []


def _row(model, ttft, duration, chars, ts=NOW, finished=True, failed=False):
    return {"ts": ts, "model": model, "ttft_s": ttft, "duration_s": duration, "chars": chars,
            "finished": finished, "failed": failed}


def test_summary_medians_per_model(timing):
    rows = [_row("m-fast", 0.4, 2.4, 400), _row("m-fast", 0.6, 2.6, 600), _row("m-fast", 2.0, 4.0, 200),
            _row("m-slow", 3.0, 13.0, 500), _row("m-slow", None, 9.0, 0, finished=False)]
    lines = timing.summarize(rows, title="last 24 hours").splitlines()
    assert lines[0] == "Streaming speed, last 24 hours:"
    assert lines[2].split() == ["model", "streams", "failed", "first", "text", "p90", "chars/s"]
    assert lines[3].split() == ["m-fast", "3", "0", "0.60s", "2.00s", "200"]
    assert lines[4].split() == ["m-slow", "2", "1", "3.00s", "3.00s", "50"]
    assert lines[-1] == "1 stream(s) carried no text (tool calls only, or failed before any)."
    errored = timing.summarize([_row("m", 1.0, 2.0, 10, failed=True)], title="x").splitlines()
    assert errored[3].split()[:3] == ["m", "1", "1"]
    assert timing.summarize([], title="all recorded") == "Streaming speed, all recorded: no streams recorded."


def test_select_window(timing):
    rows = [_row("old", 1.0, 2.0, 10, ts=NOW - 2 * timing.DAY_SECONDS), _row("new", 1.0, 2.0, 10, ts=NOW - 5)]
    assert [r["model"] for r in timing.select(rows, "day", NOW)] == ["new"]
    assert len(timing.select(rows, "all", NOW)) == 2


class _Ctx:
    def __init__(self):
        self.hooks, self.commands = {}, {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


def test_plugin_surface_and_round_trip(speed_plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(speed_plugin, "_data_path", lambda: tmp_path / speed_plugin.timing.FILENAME)
    ctx = _Ctx()
    speed_plugin.register(ctx)
    assert sorted(ctx.hooks) == ["on_stream_delta", "on_stream_end", "on_stream_start"]
    assert sorted(ctx.commands) == ["speed"]
    speed = ctx.commands["speed"]
    assert speed("") == "Streaming speed, last 24 hours: no streams recorded."
    ctx.hooks["on_stream_start"](**_ev())
    time.sleep(0.06)
    ctx.hooks["on_stream_delta"](**_ev(delta="hello", kind="text"))
    ctx.hooks["on_stream_end"](**_ev(final_text="hello", finished=True, error=None))
    report = speed("").splitlines()
    assert report[3].split()[:3] == ["m-fast", "1", "0"]
    assert speed("help").startswith("/speed:") and speed("bogus").startswith("Unknown option: bogus")
    assert speed("clear") == "stream-speed: deleted 1 recorded stream(s)."
    assert speed("all") == "Streaming speed, all recorded: no streams recorded."


def test_end_hook_never_raises(speed_plugin, monkeypatch):
    def boom():
        raise OSError("read-only home")

    monkeypatch.setattr(speed_plugin, "_data_path", boom)
    speed_plugin._on_stream_start(**_ev(session_id="x"))
    speed_plugin._on_stream_end(**_ev(session_id="x", final_text="", finished=True, error=None))


@pytest.mark.parametrize("word, seconds", [
    ("7d", 7 * 86400.0), ("1d", 86400.0), ("12h", 43200.0), ("9999h", 9999 * 3600.0),
    ("0d", None), ("00h", None), ("d", None), ("7", None), ("7w", None), ("-1d", None),
    ("1.5h", None), ("12345d", None), ("\u0667d", None), ("", None), ("day", None)])
def test_span_words(timing, word, seconds):
    assert timing.parse_span(word) == seconds


def test_span_titles(timing):
    assert [timing.span_title(w) for w in ("1h", "6h", "1d", "30d")] == [
        "last 1 hour", "last 6 hours", "last 1 day", "last 30 days"]


def _srow(model, session, provider, ts):
    return dict(_row(model, 1.0, 2.0, 10, ts=ts), session_id=session, provider=provider)


def test_select_span_and_session(timing):
    rows = [_srow("m-week", "s1", "p1", NOW - 5 * timing.DAY_SECONDS),
            _srow("m-days", "s2", "p1", NOW - 2 * timing.DAY_SECONDS),
            _srow("m-a", "s3", "p2", NOW - 90), _srow("m-b", "", "p2", NOW - 60),
            _srow("m-c", "s3", "p2", NOW - 30)]
    assert [r["model"] for r in timing.select(rows, "day", NOW, 3 * timing.DAY_SECONDS)] == ["m-days", "m-a", "m-b", "m-c"]
    assert [r["model"] for r in timing.select(rows, "day", NOW, 3600.0)] == ["m-a", "m-b", "m-c"]
    assert len(timing.select(rows, "all", NOW, 60.0)) == 5
    # The latest session is the newest row that names one; rows without a session never match.
    assert [r["model"] for r in timing.select(rows, "session", NOW, 1.0)] == ["m-a", "m-c"]
    assert timing.select([rows[3]], "session", NOW) == []
    assert timing.select([_row("legacy", 1.0, 2.0, 10)], "session", NOW) == []


def test_summary_by_provider(timing):
    rows = [dict(_row("m-fast", 0.4, 2.4, 400), provider="p1"), dict(_row("m-slow", 2.0, 4.0, 200), provider="p1"),
            dict(_row("m-fast", 1.0, 3.0, 100), provider="p2"), _row("m-legacy", 3.0, 5.0, 100)]
    lines = timing.summarize(rows, title="x", by="provider").splitlines()
    assert lines[2].split() == ["provider", "streams", "failed", "first", "text", "p90", "chars/s"]
    assert lines[3].split() == ["p1", "2", "0", "1.20s", "2.00s", "150"]
    assert lines[4].split()[:4] == ["p2", "1", "0", "1.00s"]
    # A row recorded without a provider is grouped, not dropped.
    assert lines[5].split()[:4] == ["unknown", "1", "0", "3.00s"]
    assert timing.summarize(rows, title="x").splitlines()[2].split()[0] == "model"


def test_command_windows_and_provider_grouping(speed_plugin, tmp_path, monkeypatch):
    path = tmp_path / speed_plugin.timing.FILENAME
    monkeypatch.setattr(speed_plugin, "_data_path", lambda: path)
    ctx = _Ctx()
    speed_plugin.register(ctx)
    speed, now = ctx.commands["speed"], time.time()
    for row in (_srow("m-week", "old", "p1", now - 5 * 86400), _srow("m-today", "new", "p2", now - 30)):
        speed_plugin.timing.append(path, row)
    assert [line.split()[0] for line in speed("").splitlines()[3:]] == ["m-today"]
    week = speed("7d").splitlines()
    assert week[0] == "Streaming speed, last 7 days:" and sorted(line.split()[0] for line in week[3:5]) == ["m-today", "m-week"]
    assert "m-week" not in speed("3d") and speed("12h").splitlines()[0] == "Streaming speed, last 12 hours:"
    session = speed("session").splitlines()
    assert session[0] == "Streaming speed, latest session:" and [line.split()[0] for line in session[3:]] == ["m-today"]
    providers = speed("7d providers").splitlines()
    assert providers[2].split()[0] == "provider" and sorted(line.split()[0] for line in providers[3:5]) == ["p1", "p2"]
    assert speed("all models").splitlines()[2].split()[0] == "model"
    assert speed("session 1h").splitlines()[0] == "Streaming speed, latest session:"
    assert len(speed("all 1h").splitlines()) == 5
    assert speed("0d").startswith("Unknown option: 0d") and speed("7w").startswith("Unknown option: 7w")
    assert "/speed 7d" in speed("help") and "/speed session" in speed("help") and "/speed providers" in speed("help")
