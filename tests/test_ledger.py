import json
import threading

import pytest

NOW = 1_800_000_000.0


def _payload(**over):
    base = {
        "aux_task": "compression", "session_id": "s1", "provider": "custom", "model": "m-small",
        "response_model": "m-small-2026", "retry_count": 0, "streaming": False,
        "ended_at": NOW, "api_duration": 1.25, "error": None, "error_type": None,
        "usage": {"input_tokens": 900, "output_tokens": 120, "cache_read_tokens": 100,
                  "cache_write_tokens": 0, "reasoning_tokens": 7, "prompt_tokens": 1000,
                  "total_tokens": 1120},
        "request_messages": [{"role": "user", "content": "SECRET PROMPT"}],
        "response": {"assistant_message": {"content": "SECRET ANSWER"}},
        "system_prompt": "SECRET SYSTEM",
    }
    base.update(over)
    return base


def test_row_keeps_counts_and_drops_all_text(ledger):
    row = ledger.build_row(_payload(error="RateLimitError: SECRET PROVIDER TEXT",
                                    error_type="RateLimitError"))
    assert row["aux_task"] == "compression" and row["session_id"] == "s1"
    assert (row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (900, 120, 100)
    assert row["duration_s"] == 1.25 and row["error_type"] == "RateLimitError"
    assert "SECRET" not in json.dumps(row)


@pytest.mark.parametrize("usage", [None, "n/a", {"input_tokens": "12", "output_tokens": -5}])
def test_row_tolerates_missing_or_malformed_usage(ledger, usage):
    row = ledger.build_row(_payload(usage=usage, streaming=True))
    assert row["input_tokens"] == 0 and row["output_tokens"] == 0
    assert row["has_usage"] is isinstance(usage, dict)


def test_append_then_read_round_trips_and_skips_torn_lines(ledger, tmp_path):
    path = tmp_path / ledger.FILENAME
    ledger.append(path, ledger.build_row(_payload()))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"aux_task": "torn\n[1, 2]\n')
    ledger.append(path, ledger.build_row(_payload(aux_task="vision")))
    assert [r["aux_task"] for r in ledger.read(path)] == ["compression", "vision"]
    assert ledger.read(tmp_path / "missing.jsonl") == []


def test_ledger_is_bounded(ledger, tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "MAX_BYTES", 2000)
    monkeypatch.setattr(ledger, "KEEP_LINES", 5)
    path = tmp_path / ledger.FILENAME
    for i in range(40):
        ledger.append(path, ledger.build_row(_payload(session_id=f"s{i}")))
    rows = ledger.read(path)
    assert len(rows) <= 5 + 2000 // 100
    assert rows[-1]["session_id"] == "s39"
    assert not list(tmp_path.glob(".calls-*"))


def test_concurrent_appends_lose_nothing(ledger, tmp_path):
    path = tmp_path / ledger.FILENAME
    threads = [threading.Thread(target=lambda i=i: [ledger.append(path, ledger.build_row(
        _payload(session_id=f"t{i}"))) for _ in range(25)]) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ledger.read(path)) == 200


def test_select_windows(ledger):
    rows = [ledger.build_row(_payload(session_id="old", ended_at=NOW - 2 * ledger.DAY_SECONDS)),
            ledger.build_row(_payload(session_id="a", ended_at=NOW - 60)),
            ledger.build_row(_payload(session_id="", aux_task="cron", ended_at=NOW - 30)),
            ledger.build_row(_payload(session_id="b", ended_at=NOW - 10)),
            ledger.build_row(_payload(session_id="b", ended_at=NOW - 5))]
    assert len(ledger.select(rows, "all", NOW)) == 5
    assert [r["session_id"] for r in ledger.select(rows, "day", NOW)] == ["a", "", "b", "b"]
    assert [r["session_id"] for r in ledger.select(rows, "session", NOW)] == ["b", "b"]
    assert ledger.select([rows[2]], "session", NOW) == []


def test_summary_totals_by_task_and_by_model(ledger):
    rows = [ledger.build_row(_payload()),
            ledger.build_row(_payload(api_duration=70.0)),
            ledger.build_row(_payload(aux_task="title_generation", model="m-tiny",
                                      usage={"input_tokens": 50, "output_tokens": 8})),
            ledger.build_row(_payload(aux_task="vision", usage=None, api_duration=2.5,
                                      error_type="APITimeoutError"))]
    text = ledger.summarize(rows, title="last 24 hours")
    lines = text.splitlines()
    assert lines[0] == "Auxiliary LLM calls, last 24 hours:"
    assert lines[2].split() == ["task", "calls", "failed", "input", "output", "time"]
    assert lines[3].split() == ["compression", "2", "0", "2,000", "240", "1m11s"]
    assert lines[4].split() == ["title_generation", "1", "0", "50", "8", "1.2s"]
    assert lines[5].split() == ["vision", "1", "1", "0", "0", "2.5s"]
    assert lines[6].split() == ["total", "4", "1", "2,050", "248", "1m15s"]
    assert lines[-1] == "1 call(s) reported no token usage (streamed or failed)."
    by_model = ledger.summarize(rows, title="all recorded", by="model").splitlines()
    assert by_model[2].split()[0] == "model"
    assert by_model[3].split() == ["m-small", "3", "1", "2,000", "240", "1m14s"]


def test_empty_summary(ledger):
    assert ledger.summarize([], title="latest session") == "Auxiliary LLM calls, latest session: none recorded."


def test_clear_reports_and_removes(ledger, tmp_path):
    path = tmp_path / ledger.FILENAME
    for _ in range(3):
        ledger.append(path, ledger.build_row(_payload()))
    assert ledger.clear(path) == 3 and not path.exists()
    assert ledger.clear(path) == 0


@pytest.mark.parametrize("word, seconds", [
    ("7d", 7 * 86400.0), ("1d", 86400.0), ("12h", 43200.0), ("9999h", 9999 * 3600.0),
    ("0d", None), ("00h", None), ("d", None), ("7", None), ("7w", None), ("-1d", None),
    ("1.5h", None), ("12345d", None), ("\u0667d", None), ("", None), ("day", None)])
def test_span_words(ledger, word, seconds):
    assert ledger.parse_span(word) == seconds


def test_span_titles(ledger):
    assert [ledger.span_title(w) for w in ("1h", "6h", "1d", "30d")] == [
        "last 1 hour", "last 6 hours", "last 1 day", "last 30 days"]


def test_select_span(ledger):
    rows = [ledger.build_row(_payload(session_id="w", ended_at=NOW - 5 * ledger.DAY_SECONDS)),
            ledger.build_row(_payload(session_id="d", ended_at=NOW - 2 * ledger.DAY_SECONDS)),
            ledger.build_row(_payload(session_id="n", ended_at=NOW - 60))]
    assert [r["session_id"] for r in ledger.select(rows, "day", NOW, 3 * ledger.DAY_SECONDS)] == ["d", "n"]
    assert [r["session_id"] for r in ledger.select(rows, "day", NOW, 3600.0)] == ["n"]
    assert len(ledger.select(rows, "all", NOW, 60.0)) == 3
    assert [r["session_id"] for r in ledger.select(rows, "session", NOW, 1.0)] == ["n"]


def test_summary_by_provider(ledger):
    rows = [ledger.build_row(_payload(provider="openrouter")),
            ledger.build_row(_payload(provider="openrouter", aux_task="vision")),
            ledger.build_row(_payload(provider="", usage={"input_tokens": 5, "output_tokens": 1}))]
    lines = ledger.summarize(rows, title="x", by="provider").splitlines()
    assert lines[2].split()[0] == "provider"
    assert lines[3].split()[:3] == ["openrouter", "2", "0"]
    # A call that reported no provider is grouped, not dropped.
    assert lines[4].split()[:4] == ["unknown", "1", "0", "5"]
    assert lines[5].split()[:2] == ["total", "3"]
