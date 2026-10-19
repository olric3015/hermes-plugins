"""End-to-end check of reaction-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/reaction_ledger_e2e.py

A child process loads the plugin through Hermes's own plugin discovery, builds Hermes's real
Telegram adapter and wires it to the gateway the way the gateway does at start-up
(``set_platform_event_handler`` with the runner's own platform-event handler). It then feeds the
adapter's own update handler (``_on_platform_update``) Telegram reaction updates, so every step
Hermes takes is real: the reaction is normalized by the adapter, the reacting user is checked
against ``TELEGRAM_ALLOWED_USERS`` by the gateway, and only then is ``gateway_platform_event``
fired. The updates are stand-ins with the attributes python-telegram-bot gives a
``message_reaction`` update, so the test needs no bot token and no network.

The parent then checks the recorded rows and the `/reaction-log` report.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace as NS

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("REACTION_E2E_HOME") or tempfile.mkdtemp(prefix="reaction-ledger-e2e-"))
ALLOWED, STRANGER = 900111, 900222
CHAT_A, CHAT_B = 700333444, 700555666
# (user, chat, message, emojis, custom emoji ids) in the order the reactions happen.
UPDATES = [
    (ALLOWED, CHAT_A, 5, ["👍"], []),
    (ALLOWED, CHAT_A, 6, ["❤️"], []),
    (ALLOWED, CHAT_A, 5, ["👎"], []),               # changed their mind on message 5
    (ALLOWED, CHAT_B, 7, ["🔥"], ["5368324170671202286"]),
    (ALLOWED, CHAT_A, 6, [], []),                   # took the reaction on message 6 back
    (STRANGER, CHAT_A, 8, ["👍"], []),              # not on the allowlist: never reaches plugins
]
EXPECTED = [(["👍"], 0), (["❤️"], 0), (["👎"], 0), (["🔥"], 1), ([], 0)]


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_", "TELEGRAM_", "GATEWAY_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", REACTION_E2E_HOME=str(HOME), TELEGRAM_ALLOWED_USERS=str(ALLOWED))
    return env


def _update(user, chat, message, emojis, custom):
    reactions = [NS(type="emoji", emoji=e, custom_emoji_id=None) for e in emojis]
    reactions += [NS(type="custom_emoji", emoji=None, custom_emoji_id=c) for c in custom]
    reaction = NS(chat=NS(id=chat, type="private", is_forum=False), message_id=message,
                  old_reaction=(), new_reaction=tuple(reactions), actor_chat=None,
                  user=NS(id=user, username=f"user{user}", full_name="Test User", is_bot=False))
    return NS(update_id=message, message_reaction=reaction, edited_message=None, effective_user=reaction.user)


def child() -> int:
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.run import GatewayRunner
    from hermes_cli.plugins import discover_plugins
    from plugins.platforms.telegram.adapter import TelegramAdapter

    discover_plugins(force=True)
    config = PlatformConfig(enabled=True, token="***")
    adapter = TelegramAdapter(config)
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: config})
    runner.adapters = {Platform.TELEGRAM: adapter}
    adapter.set_platform_event_handler(runner._primary_platform_event_handler())

    async def feed():
        for update in UPDATES:
            await adapter._on_platform_update(_update(*update), None)

    asyncio.run(feed())
    print("fed", len(UPDATES), "updates")
    return 0


def main() -> int:
    shutil.copytree(REPO / "reaction-ledger", HOME / "plugins" / "reaction-ledger")
    (HOME / "config.yaml").write_text("plugins:\n  enabled: [reaction-ledger]\n", encoding="utf-8")
    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    print(run.stdout.strip())
    assert run.returncode == 0, run.stderr[-4000:]

    data = HOME / "plugin-data" / "reaction-ledger" / "reactions.jsonl"
    assert data.exists(), "no reaction was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        print("  ", row)
    for secret in (ALLOWED, STRANGER, CHAT_A, CHAT_B, "5368324170671202286", "Test User"):
        assert str(secret) not in raw, f"{secret} reached the ledger"
    assert [(r["emojis"], r["custom"]) for r in rows] == EXPECTED, "wrong rows (or the stranger's reaction got in)"
    assert all(r["platform"] == "telegram" for r in rows)
    assert rows[0]["message"] == rows[2]["message"] != rows[1]["message"] == rows[4]["message"]
    assert rows[0]["chat"] == rows[1]["chat"] != rows[3]["chat"]

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    reaction_log = get_plugin_command_handler("reaction-log")
    assert reaction_log is not None, "/reaction-log was not registered"
    report = reaction_log("all")
    print(report)
    lines = report.splitlines()
    assert lines[0] == "Reactions, all recorded:"
    assert lines[2].split() == ["emoji", "name", "messages", "chats", "last"]
    assert lines[3].split()[:5] == ["👎", "thumbs", "down", "sign", "1"]
    assert lines[4].split()[:3] == ["🔥", "fire", "1"]
    assert lines[5].split()[:4] == ["custom", "custom", "emoji", "1"]
    assert lines[6].split()[:3] == ["all", "2", "2"]
    assert lines[-2:] == ["Each message counts once, with the reactions it has now.",
                          "Reactions were removed again from 1 message(s)."]
    assert reaction_log("").splitlines()[3:7] == lines[3:7]
    assert reaction_log("7d").splitlines()[0] == "Reactions, last 7 days:"
    days = reaction_log("all days").splitlines()
    print("\n".join(days))
    expected_days = {}
    for row in rows[2:4]:  # the rows that still hold reactions: message 5 (👎) and message 7
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        expected_days[day] = expected_days.get(day, 0) + 1
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)]
    listing = reaction_log("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Reaction changes, all recorded, newest 5 of 5:"
    assert [line.split()[2] for line in listing[3:]] == ["(removed)", "🔥", "👎", "❤️", "👍"]
    two = reaction_log("all recent 2").splitlines()
    assert two[0] == "Reaction changes, all recorded, newest 2 of 5:" and len(two) == 5
    assert reaction_log("recent 0").startswith("Unknown option: 0")
    assert reaction_log("clear") == "reaction-ledger: deleted 5 recorded reaction change(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
