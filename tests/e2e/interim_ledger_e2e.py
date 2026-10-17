"""End-to-end check of interim-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/interim_ledger_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider whose model writes a message alongside each tool call before it
answers, then checks the recorded rows and the `/interim-log` report. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(tempfile.mkdtemp(prefix="interim-ledger-e2e-"))

from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall, write_hermes_home  # noqa: E402

FIRST = "PRIVATE-INTERIM-7d2a: let me look at the task list first."
SECOND = "PRIVATE-INTERIM-7d2a: one more look before I answer."
ANSWER = "PRIVATE-ANSWER-7d2a all done"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    return env


def _hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], env=_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def main() -> int:
    # First turn: one message with a tool call, then the answer. Second turn: two, then the answer.
    script = [ToolCall("todo_list", {}, text=FIRST), Text(ANSWER),
              ToolCall("todo_list", {}, text=FIRST), ToolCall("todo_list", {}, text=SECOND), Text(ANSWER)]
    with FakeLLMServer(script) as srv:
        # Tool Search (newer Hermes) would hide todo_list behind its bridge tools; turn it off so the
        # scripted call is a plain tool call on every version. Older versions ignore the key.
        write_hermes_home(HOME, srv.base_url, extra_config=(
            "plugins:\n  enabled: [interim-ledger]\ntools:\n  tool_search:\n    enabled: \"off\"\n"))
        shutil.copytree(REPO / "interim-ledger", HOME / "plugins" / "interim-ledger")
        for prompt in ("first question", "second question"):
            run = _hermes("chat", "-q", prompt, "--oneshot", "-t", "todo")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert "PRIVATE-ANSWER-7d2a" in run.stdout, "the scripted answer was not printed"
        print(f"hermes made {len(srv.main_requests())} main request(s)")

    data = HOME / "plugin-data" / "interim-ledger" / "interim.jsonl"
    # The hook runs on Hermes's stream-hook worker thread: give a late write a moment.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (not data.exists() or len(data.read_text(encoding="utf-8-sig").splitlines()) < 3):
        time.sleep(0.05)
    assert data.exists(), "no mid-turn message was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        print("  ", row)
    assert len(rows) == 3, "one row per mid-turn message"
    assert "PRIVATE" not in raw, "message text reached the ledger"
    assert [r["chars"] for r in rows] == [len(FIRST), len(FIRST), len(SECOND)]
    assert all(r["model"] == "fake-model" and len(r["session"]) == len(r["turn"]) == 12 and r["surface"] == "cli"
               for r in rows)
    assert rows[0]["session"] != rows[1]["session"] == rows[2]["session"]
    assert rows[0]["turn"] != rows[1]["turn"] == rows[2]["turn"] and rows[1]["iteration"] < rows[2]["iteration"]

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    log = get_plugin_command_handler("interim-log")
    assert log is not None, "/interim-log was not registered"
    report = log("all")
    print(report)
    assert report.splitlines()[3].split()[:4] == ["fake-model", "3", "2", "1.5"]
    week = log("7d").splitlines()
    assert week[0] == "Mid-turn messages, last 7 days:" and week[3:] == report.splitlines()[3:]
    session = log("session").splitlines()
    print("\n".join(session))
    assert session[0] == "Mid-turn messages, latest session:" and session[3].split()[:4] == ["fake-model", "2", "1", "2.0"]
    by_provider = log("all providers").splitlines()
    print("\n".join(by_provider))
    assert by_provider[2].split()[0] == "provider"
    assert {line.split()[0] for line in by_provider[3:-2]} == {r["provider"] for r in rows}
    surfaces = log("all surfaces").splitlines()
    assert surfaces[2].split()[0] == "surface" and surfaces[3].split()[:2] == ["cli", "3"]
    days = log("all days").splitlines()
    print("\n".join(days))
    expected_days = {}
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        expected_days[day] = expected_days.get(day, 0) + 1
    assert days[2].split()[0] == "day"
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)]
    listing = log("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Mid-turn messages, all recorded, newest 3 of 3:"
    assert listing[2].split() == ["when", "model", "surface", "step", "chars", "streamed"]
    for line, row in zip(listing[3:6], rows[::-1]):
        assert line.split()[2:6] == ["fake-model", "cli", str(row["iteration"]), str(row["chars"])]
    two = log("all recent 2").splitlines()
    assert two[0] == "Mid-turn messages, all recorded, newest 2 of 3:" and len(two) == 5
    assert log("recent 0").startswith("Unknown option: 0")
    assert log("clear") == "interim-ledger: deleted 3 recorded message(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
