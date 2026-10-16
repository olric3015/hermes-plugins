"""End-to-end check of stop-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/stop_ledger_e2e.py

A one-shot `hermes chat -q` turn cannot be stopped from outside, so a child process does what
Hermes does when you stop a running turn: it loads the plugin through Hermes's own plugin
discovery and calls Hermes's own interrupt code with a stand-in for the running agent:

- the gateway's ``_interrupt_and_clear_session`` the way ``/stop`` and ``/new`` call it while a
  turn is running on Telegram, and once with no turn running (the hook must stay silent);
- the TUI/desktop ``session.interrupt`` path (``_interrupt_session_turn``) for a running turn,
  and for an idle one (silent too).

The parent then checks the recorded rows and the `/stop-log` report. No network is used.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("STOP_E2E_HOME") or tempfile.mkdtemp(prefix="stop-ledger-e2e-"))
CHAT = "SECRET-CHAT-ID"
# (platform, cause, detail) of the stops that reach the ledger, in order.
EXPECTED = [("telegram", "stop", "stop_command"), ("telegram", "new", "new_command"),
            ("tui", "stop", "session_interrupt")]


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", STOP_E2E_HOME=str(HOME))
    return env


def _runner():
    """A GatewayRunner with just the state ``_interrupt_and_clear_session`` reads."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")})
    runner.adapters = {Platform.TELEGRAM: SimpleNamespace(send=AsyncMock())}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._invalidate_session_run_generation = lambda *a, **kw: None
    runner._release_running_agent_state = lambda *a, **kw: None
    return runner


def child() -> int:
    from gateway.config import Platform
    from gateway.run import _INTERRUPT_REASON_RESET, _INTERRUPT_REASON_STOP
    from gateway.session import SessionSource, build_session_key
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    source = SessionSource(platform=Platform.TELEGRAM, user_id="SECRET-USER", chat_id=CHAT,
                           user_name="tester", chat_type="dm")
    key = build_session_key(source)
    for reason, detail in ((_INTERRUPT_REASON_STOP, "stop_command"), (_INTERRUPT_REASON_RESET, "new_command")):
        runner = _runner()
        runner._running_agents[key] = MagicMock()
        asyncio.run(runner._interrupt_and_clear_session(
            key, source, interrupt_reason=reason, invalidation_reason=detail))
        print("gateway", detail, "done")
    # /stop with no running turn: nothing to stop, nothing recorded.
    asyncio.run(_runner()._interrupt_and_clear_session(
        key, source, interrupt_reason=_INTERRUPT_REASON_STOP, invalidation_reason="stop_command"))

    for running in (True, False):
        session = {"history_lock": threading.Lock(), "running": running, "queued_prompt": None,
                   "session_key": f"agent:main:tui:dm:{CHAT}", "agent": MagicMock(), "_run_thread": None}
        with patch.object(server, "_clear_pending"):
            server._interrupt_session_turn("e2e-tui", session)
        print("tui running" if running else "tui idle", "done")
    return 0


def main() -> int:
    shutil.copytree(REPO / "stop-ledger", HOME / "plugins" / "stop-ledger")
    (HOME / "config.yaml").write_text("plugins:\n  enabled: [stop-ledger]\n", encoding="utf-8")
    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    print(run.stdout.strip())
    assert run.returncode == 0, run.stderr[-4000:]

    data = HOME / "plugin-data" / "stop-ledger" / "stops.jsonl"
    assert data.exists(), "no stop was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        print("  ", row)
    assert "SECRET" not in raw, "the session key reached the ledger"
    assert [(r["platform"], r["cause"], r["detail"]) for r in rows] == EXPECTED
    assert rows[0]["session"] == rows[1]["session"] != rows[2]["session"]
    assert all(len(r["session"]) == 12 for r in rows)

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    stop_log = get_plugin_command_handler("stop-log")
    assert stop_log is not None, "/stop-log was not registered"
    report = stop_log("all")
    print(report)
    lines = report.splitlines()
    assert lines[0] == "Stopped turns, all recorded:"
    assert lines[2].split() == ["platform", "stops", "by", "/stop", "by", "/new", "sessions", "last"]
    assert lines[3].split()[:5] == ["telegram", "2", "1", "1", "1"]
    assert lines[4].split()[:5] == ["tui", "1", "1", "0", "1"]
    assert lines[5].split()[:5] == ["total", "3", "2", "1", "2"]
    assert lines[-1].startswith("Only turns that were still running count")
    week = stop_log("7d").splitlines()
    assert week[0] == "Stopped turns, last 7 days:" and [line.split()[:5] for line in week[3:6]] == \
        [line.split()[:5] for line in lines[3:6]]
    session = stop_log("session").splitlines()
    assert session[0] == "Stopped turns, latest session:" and session[3].split()[:2] == ["tui", "1"]
    days = stop_log("all days").splitlines()
    print("\n".join(days))
    expected_days = {}
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        expected_days[day] = expected_days.get(day, 0) + 1
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)]
    listing = stop_log("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Stopped turns, all recorded, newest 3 of 3:"
    assert [line.split()[2:5] for line in listing[3:]] == [list(e) for e in reversed(EXPECTED)]
    two = stop_log("all recent 2").splitlines()
    assert two[0] == "Stopped turns, all recorded, newest 2 of 3:" and len(two) == 5
    assert stop_log("recent 0").startswith("Unknown option: 0")
    assert stop_log("clear") == "stop-ledger: deleted 3 recorded stop(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
