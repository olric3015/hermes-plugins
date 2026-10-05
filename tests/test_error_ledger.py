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
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
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
    assert "SECRET" not in path.read_text(encoding="utf-8")
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
