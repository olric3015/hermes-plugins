"""End-to-end check of command-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/command_ledger_e2e.py

A one-shot `hermes chat -q` turn never dispatches a slash command, and the interactive CLI and
the gateway need a terminal or a chat platform. So a fresh child process does what they do:
loads the plugin through Hermes's own plugin discovery, then

- ``cli``: sends commands through the CLI's own ``process_command`` (handlers that would open a
  screen or exit are replaced; ``pre_command`` fires before any handler runs): ``/help``,
  ``/exit`` (an alias of ``/quit``), ``/help`` again, and the plugin's own ``/command-log``,
  which the CLI does not report to ``pre_command``;
- ``gateway``: sends Telegram-shaped messages through the gateway's own command resolution
  (``_hm_resolve_command``): ``/q`` (an alias of ``/queue``), ``/usage``, the plugin's
  ``/command-log``, which the gateway does report and the plugin skips, and plain text.

The parent then checks the recorded rows and the `/command-log` report. Nothing leaves the
machine and no model is called.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("COMMAND_E2E_HOME") or tempfile.mkdtemp(prefix="command-ledger-e2e-"))
LEDGER = HOME / "plugin-data" / "command-ledger" / "commands.jsonl"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", COMMAND_E2E_HOME=str(HOME))
    return env


def _cli() -> None:
    import cli as cli_mod

    inst = object.__new__(cli_mod.HermesCLI)
    inst.session_id = "e2e-cli-session"
    inst._pending_resume_sessions = None
    inst.show_help = lambda *a, **k: print("[help shown]")
    assert inst.process_command("/help") is True
    assert inst.process_command("/exit") is False
    assert inst.process_command("/help Some ARGS-NOT-STORED") is True
    try:
        inst.process_command("/command-log")
    except Exception as exc:  # a bare CLI object may not print; only the hook matters here
        print("cli /command-log:", type(exc).__name__)


async def _gateway() -> None:
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource, build_session_key

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")})

    async def _no_hooks(*_a, **_k):
        return []

    runner.hooks = SimpleNamespace(emit_collect=_no_hooks)
    runner._check_slash_access = lambda _source, _command: None
    source = SessionSource(platform=Platform.TELEGRAM, user_id="u-e2e", chat_id="c-e2e",
                           user_name="tester", chat_type="dm")
    key = build_session_key(source)
    for text in ("/q later ARGS-NOT-STORED", "/usage", "/command-log 7d", "just chatting"):
        event = MessageEvent(text=text, message_type=MessageType.TEXT, source=source, message_id="m1")
        result = await runner._hm_resolve_command(event, source, key)
        print("gateway", repr(text), "->", result[1:])


def child() -> int:
    from hermes_cli.plugins import discover_plugins, get_plugin_commands

    discover_plugins(force=True)
    assert "command-log" in (get_plugin_commands() or {}), sorted(get_plugin_commands() or {})
    _cli()
    asyncio.run(_gateway())
    handler = get_plugin_commands()["command-log"]["handler"]
    for args in ("", "all", "all platforms", "all days", "session", "all recent", "7d"):
        (HOME / f"report-{args.replace(' ', '_') or 'default'}.txt").write_text(handler(args), encoding="utf-8")
    return 0


def main() -> int:
    (HOME / "plugins").mkdir(parents=True, exist_ok=True)
    target = HOME / "plugins" / "command-ledger"
    if not target.exists():
        import shutil

        shutil.copytree(REPO / "command-ledger", target, ignore=shutil.ignore_patterns("__pycache__"))
    (HOME / "config.yaml").write_text("plugins:\n  enabled: [command-ledger]\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, __file__, "--child"], env=_env(), capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    print(proc.stdout[-3000:])
    if proc.returncode:
        print(proc.stderr[-6000:])
        raise SystemExit(f"child failed with {proc.returncode}")

    rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8-sig").splitlines()]
    seen = [(r["surface"], r["platform"], r["command"], r["alias"]) for r in rows]
    print("rows:", seen)
    assert seen == [("cli", "cli", "help", ""), ("cli", "cli", "quit", "exit"), ("cli", "cli", "help", ""),
                    ("gateway", "telegram", "queue", "q"), ("gateway", "telegram", "usage", "")], seen
    raw = LEDGER.read_text(encoding="utf-8-sig")
    assert "ARGS-NOT-STORED" not in raw and "u-e2e" not in raw and "c-e2e" not in raw and "e2e-cli" not in raw
    sessions = {r["platform"]: r["session"] for r in rows}
    assert len(sessions["cli"]) == len(sessions["telegram"]) == 12 and sessions["cli"] != sessions["telegram"]

    report = {p.stem: p.read_text(encoding="utf-8-sig") for p in HOME.glob("report-*.txt")}
    for name, text in sorted(report.items()):
        print(f"--- /command-log {name}\n{text}")
    lines = report["report-default"].splitlines()
    assert lines[0] == "Slash commands, last 24 hours:"
    assert lines[3].split()[:4] == ["/help", "2", "40%", "1"], lines
    assert [line.split()[0] for line in lines[4:8]] == ["/queue", "/quit", "/usage", "total"], lines
    assert lines[7].split()[:4] == ["total", "5", "100%", "2"], lines
    platforms = report["report-all_platforms"].splitlines()
    assert [line.split()[:2] for line in platforms[3:5]] == [["cli", "3"], ["telegram", "2"]], platforms
    days = report["report-all_days"].splitlines()
    expected_days = {}
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        expected_days[day] = expected_days.get(day, 0) + 1
    assert days[2].split()[0] == "day", days
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)], days
    session = report["report-session"].splitlines()
    assert session[0] == "Slash commands, latest session:" and session[-3].split()[:2] == ["total", "2"], session
    recent = report["report-all_recent"].splitlines()
    assert recent[0] == "Slash commands, all recorded, newest 5 of 5:"
    assert recent[3].split()[2:] == ["/usage", "telegram"] and recent[4].split()[2:] == ["/queue", "/q", "telegram"]
    assert report["report-7d"].splitlines()[0] == "Slash commands, last 7 days:"
    print("command-ledger e2e: OK")
    return 0


if __name__ == "__main__":
    sys.exit(child() if "--child" in sys.argv else main())
