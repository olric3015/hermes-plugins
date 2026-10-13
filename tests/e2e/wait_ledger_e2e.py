"""End-to-end check of wait-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/wait_ledger_e2e.py

A one-shot `hermes chat -q` turn never stops for a person (nobody could answer), and an
interactive prompt needs a terminal. So a child process does what an interactive CLI turn does:
loads the plugin through Hermes's own plugin discovery, binds the session id the way the tool
loop does, and goes through Hermes's own three human-wait points with the CLI's prompt replaced
by a callback that answers late on purpose:

- the dangerous-command guard for a recursive delete of a directory that does not exist (the
  guard only decides; nothing is executed): denied, then approved once;
- the clarify tool: answered, then timed out, then a prompt that fails;
- the sudo password prompt: a password given, then an empty answer (nothing runs sudo).

The parent then checks the recorded rows and the `/waits` report. Nothing leaves 127.0.0.1.
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
HOME = Path(os.environ.get("WAIT_E2E_HOME") or tempfile.mkdtemp(prefix="wait-ledger-e2e-"))
# Never created, and the guard only decides: nothing is deleted.
TARGETS = ("missing-secret-40b9", "missing-secret-7c1e")
# (kind, outcome, how late the person answers)
PLAN = [("approval", "deny", 0.5), ("approval", "once", 0.3),
        ("clarify", "submitted", 0.4), ("clarify", "timed_out", 0.2), ("clarify", "error", 0.1),
        ("sudo", "provided", 0.3), ("sudo", "skipped", 0.2)]
RESULTS = ["answered", "answered", "answered", "unanswered", "error", "answered", "skipped"]

from tests.fakes.fake_llm_provider import FakeLLMServer, write_hermes_home  # noqa: E402


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", WAIT_E2E_HOME=str(HOME))
    return env


def child() -> int:
    """One interactive CLI session's worth of human waits, in this (child) process."""
    os.environ["HERMES_INTERACTIVE"] = "1"
    from hermes_cli.plugins import discover_plugins
    from tools.approval import check_all_command_guards
    from tools.approval_context import set_current_observability_context
    from tools.clarify_tool import clarify_tool
    from tools.terminal_tool import set_sudo_password_callback
    from tools.terminal_tool_sudo import _prompt_for_sudo_password

    discover_plugins(force=True)
    set_current_observability_context(turn_id="turn-1", tool_call_id="call-1", session_id="e2e-waits")
    approvals = iter(TARGETS)
    for kind, outcome, delay in PLAN:
        if kind == "approval":
            def prompt(command, description, **_kwargs):
                time.sleep(delay)
                return outcome

            # A bare relative name: an absolute /tmp path would also trip "delete in root path".
            result = check_all_command_guards(f"rm -r {next(approvals)}", "local", approval_callback=prompt)
            assert result.get("approved") is (outcome == "once"), result
        elif kind == "clarify":
            def ask(questions):
                time.sleep(delay)
                if outcome == "error":
                    raise RuntimeError("the prompt went away")
                answers = {questions[0]["qid"]: "SECRET-ANSWER"} if outcome == "submitted" else {}
                return {"answers": answers, "outcome": outcome}

            reply = json.loads(clarify_tool([{"question": "Which SECRET-QUESTION file?"}], callback=ask))
            assert reply.get("outcome", "error") == outcome or "error" in reply, reply
        else:
            def password():
                time.sleep(delay)
                return "SECRET-PASSWORD" if outcome == "provided" else ""

            set_sudo_password_callback(password)
            got = _prompt_for_sudo_password(5, command="sudo cat /etc/SECRET-FILE")
            assert got == ("SECRET-PASSWORD" if outcome == "provided" else ""), got
        print(kind, outcome, "done")
    return 0


def main() -> int:
    shutil.copytree(REPO / "wait-ledger", HOME / "plugins" / "wait-ledger")
    with FakeLLMServer([]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config=(
            "approvals:\n  mode: manual\nplugins:\n  enabled: [wait-ledger]\n"))
        run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=300)
    print(run.stdout.strip())
    assert run.returncode == 0, run.stderr[-4000:]

    data = HOME / "plugin-data" / "wait-ledger" / "waits.jsonl"
    assert data.exists(), "no wait was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        print("  ", row)
    assert "SECRET" not in raw and "missing-" not in raw, "prompt or answer text reached the ledger"
    assert [(r["kind"], r["outcome"]) for r in rows] == [(k, o) for k, o, _ in PLAN]
    assert [r["result"] for r in rows] == RESULTS
    assert {r["session_id"] for r in rows} == {"e2e-waits"} and {r["platform"] for r in rows} == {"cli"}
    for row, (_, _, delay) in zip(rows, PLAN):
        # The person answered late on purpose: the recorded wait covers that delay (less the
        # clock's resolution: Windows timers tick every ~16 ms).
        assert delay - 0.05 <= row["wait_s"] < delay + 30, row

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    waits = get_plugin_command_handler("waits")
    assert waits is not None, "/waits was not registered"
    report = waits("all")
    print(report)
    lines = report.splitlines()
    assert lines[0] == "Waits, all recorded:"
    assert lines[2].split() == ["kind", "waits", "answered", "unanswered", "total", "wait", "median", "longest"]
    by_kind = {line.split()[0]: line.split()[1:4] for line in lines[3:7]}
    assert by_kind == {"approval": ["2", "2", "0"], "clarify": ["3", "1", "1"], "sudo": ["2", "1", "0"],
                       "total": ["7", "4", "1"]}
    assert lines[6].startswith("total") and lines[-2].startswith("Unanswered:")
    assert waits("session").splitlines()[0] == "Waits, latest session:"
    week = waits("7d").splitlines()
    assert week[0] == "Waits, last 7 days:" and week[3:] == lines[3:]
    platforms = waits("all platforms").splitlines()
    assert platforms[3].split()[:3] == ["cli", "7", "4"]
    days = waits("all days").splitlines()
    print("\n".join(days))
    expected_days = {}
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        expected_days[day] = expected_days.get(day, 0) + 1
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)]
    listing = waits("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Waits, all recorded, newest 7 of 7:"
    assert [line.split()[-2] for line in listing[3:]] == [o for _, o, _ in reversed(PLAN)]
    two = waits("all recent 2").splitlines()
    assert two[0] == "Waits, all recorded, newest 2 of 7:" and len(two) == 5
    assert waits("recent 0").startswith("Unknown option: 0")
    assert waits("clear") == "wait-ledger: deleted 7 recorded wait(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
