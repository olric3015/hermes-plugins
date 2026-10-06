"""End-to-end check of error-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/error_ledger_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider that fails on purpose (a 429, then a 500, then a normal answer), then
checks the recorded rows and the `/errors` report. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(tempfile.mkdtemp(prefix="error-ledger-e2e-"))

from tests.fakes.fake_llm_provider import Error, FakeLLMServer, Text, write_hermes_home  # noqa: E402

ANSWER = "PRIVATE-ANSWER-b7d2 recovered"
RATE_LIMIT = "PRIVATE-PROVIDER-ERROR-29aa slow down"
SERVER_ERROR = "PRIVATE-PROVIDER-ERROR-63fe exploded"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    return env


def _hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], env=_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def main() -> int:
    # Turn 1 meets a 429 and fails: the turn loop sees it on every Hermes version, so it must be
    # recorded. Turn 2 meets a 500 and recovers on the next request. Hermes 0.21.5 reports that
    # failure through api_request_error; newer Hermes reconnects inside its streaming layer and
    # reports nothing. Both are accepted, a row for a request that succeeded is not.
    script = [Error(429, RATE_LIMIT), Error(500, SERVER_ERROR)] + [Text(ANSWER)] * 4
    with FakeLLMServer(script) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [error-ledger]\n")
        shutil.copytree(REPO / "error-ledger", HOME / "plugins" / "error-ledger")
        answered = 0
        for prompt in ("first question", "second question"):
            run = _hermes("chat", "-q", f"PRIVATE-PROMPT-51c0 {prompt}", "--oneshot")
            answered += "PRIVATE-ANSWER-b7d2" in run.stdout
        requests = len(srv.main_requests())
    failed_attempts = requests - answered  # an answered turn ends on exactly one good request

    data = HOME / "plugin-data" / "error-ledger" / "errors.jsonl"
    assert data.exists(), "no provider error was recorded"
    raw = data.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    print(f"provider saw {requests} main request(s), {answered} turn(s) answered; "
          f"{len(rows)} error row(s) recorded")
    for row in rows:
        print("  ", row)
    assert "PRIVATE" not in raw, "prompt, answer or provider error text reached the ledger"
    assert answered == 1 and failed_attempts == 2, "the script is one failed turn and one recovered turn"
    assert 1 <= len(rows) <= failed_attempts, "at most one row per failed attempt, none for a success"
    assert all(row["model"] == "fake-model" and row["session_id"] for row in rows)
    assert (rows[0]["status"], rows[0]["reason"], rows[0]["retryable"]) == (429, "rate_limit", True)
    for row in rows[1:]:
        assert (row["status"], row["reason"]) == (500, "server_error")
        assert row["session_id"] != rows[0]["session_id"], "each one-shot turn is its own session"
    last_session = sum(1 for row in rows if row["session_id"] == rows[-1]["session_id"])
    last_reason, first_reason = rows[-1]["reason"], rows[0]["reason"]

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    errors = get_plugin_command_handler("errors")
    assert errors is not None, "/errors was not registered"
    report = errors("all")
    print(report)
    assert report.splitlines()[3].split()[:2] == ["fake-model", str(len(rows))]
    by_reason = errors("all reasons").splitlines()
    assert {row["reason"] for row in rows} <= {line.split()[0] for line in by_reason[3:] if line}
    session = errors("session")
    assert session.splitlines()[3].split()[:2] == ["fake-model", str(last_session)]
    assert last_reason in session and (first_reason == last_reason or first_reason not in session)
    week = errors("7d").splitlines()
    assert week[0] == "Provider errors, last 7 days:" and week[3:] == report.splitlines()[3:]
    listing = errors("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == f"Provider errors, all recorded, newest {len(rows)} of {len(rows)}:"
    newest = rows[-1]
    assert listing[3].split()[2:] == ["fake-model", newest["provider"], newest["reason"], str(newest["status"]),
                                      {True: "yes", False: "no"}.get(newest["retryable"], "-")]
    assert listing[3].split()[1] == "ago" and len(listing) == 3 + len(rows)
    assert errors("clear") == f"error-ledger: deleted {len(rows)} recorded error(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
