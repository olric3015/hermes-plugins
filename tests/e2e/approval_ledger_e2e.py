"""End-to-end check of approval-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/approval_ledger_e2e.py

A one-shot `hermes chat -q` turn never reaches an approval prompt (nobody could answer it, so
Hermes blocks or auto-approves before asking), and an interactive prompt needs a terminal. So
each phase runs in a fresh child process that does what an interactive CLI turn does: loads the
plugin through Hermes's own plugin discovery, binds the session id the way the tool loop does,
and calls Hermes's own dangerous-command guard for a recursive delete of a directory that does
not exist (the guard only decides; nothing is executed).

- ``manual``: the classic CLI prompt; the approval callback (the CLI's prompt) answers late on
  purpose, ``deny`` after 0.6 s and ``once`` after 0.3 s.
- ``smart``: ``approvals.mode: smart``; the verdict comes from a loopback provider acting as the
  auxiliary reviewer, which denies the first command and approves the second. After a smart
  deny Hermes asks the person whether to override it; the same late callback declines.
  So the smart phase records three decisions: smart_deny, the declined override, smart_approve.

The parent then checks the recorded rows and the `/approval-log` report. Nothing leaves
127.0.0.1.
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
HOME = Path(os.environ.get("APPROVAL_E2E_HOME") or tempfile.mkdtemp(prefix="approval-ledger-e2e-"))
# Never created, and the guard only decides: nothing is deleted.
DENY_TARGET, ALLOW_TARGET = HOME / "missing-deny-40b9", HOME / "missing-allow-7c1e"
DELAYS = {"deny": 0.6, "once": 0.3}

from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home  # noqa: E402


def _reviewer(record: dict) -> Text:
    """The smart-approval call gets a verdict by target; any other side call a plain text."""
    body = json.dumps(record["body"])
    if "APPROVE, DENY, or ESCALATE" not in body:
        return Text("Fake summary of the earlier conversation.")
    return Text("APPROVE" if ALLOW_TARGET.name in body else "DENY")


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", APPROVAL_E2E_HOME=str(HOME))
    return env


def child(phase: str) -> int:
    """One interactive CLI session's worth of approvals, in this (child) process."""
    os.environ["HERMES_INTERACTIVE"] = "1"
    from hermes_cli.plugins import discover_plugins
    from tools.approval import check_all_command_guards
    from tools.approval_context import set_current_observability_context

    discover_plugins(force=True)
    set_current_observability_context(turn_id=f"turn-{phase}", tool_call_id="call-1", session_id=f"e2e-{phase}")
    for target, answer in ((DENY_TARGET, "deny"), (ALLOW_TARGET, "once")):
        def prompt(command, description, **_kwargs):
            time.sleep(DELAYS[answer])
            return answer

        # A bare relative name: an absolute /tmp path would also trip "delete in root path".
        result = check_all_command_guards(f"rm -r {target.name}", "local", approval_callback=prompt)
        print(phase, target.name, json.dumps(result)[:300])
        assert result.get("approved") is (answer == "once"), result
    return 0


def _phase(phase: str, srv: FakeLLMServer) -> None:
    write_hermes_home(HOME, srv.base_url, extra_config=(
        f"approvals:\n  mode: {phase}\nplugins:\n  enabled: [approval-ledger]\n"))
    run = subprocess.run([sys.executable, __file__, phase], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    print(run.stdout.strip())
    assert run.returncode == 0, run.stderr[-4000:]


def main() -> int:
    shutil.copytree(REPO / "approval-ledger", HOME / "plugins" / "approval-ledger")
    with FakeLLMServer([], aux=_reviewer) as srv:
        _phase("manual", srv)
        _phase("smart", srv)
        reviews = [r for r in srv.aux_requests() if "APPROVE, DENY, or ESCALATE" in json.dumps(r)]

    data = HOME / "plugin-data" / "approval-ledger" / "approvals.jsonl"
    assert data.exists(), "no approval decision was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    print(f"{len(reviews)} smart review(s) sent; {len(rows)} decision row(s) recorded")
    for row in rows:
        print("  ", row)
    assert "missing-" not in raw and "rm -r" not in raw, "command text reached the ledger"
    assert len(reviews) == 2 and len(rows) == 5
    assert [r["choice"] for r in rows] == ["deny", "once", "smart_deny", "deny", "smart_approve"]
    assert [r["outcome"] for r in rows] == ["denied", "approved", "denied", "denied", "approved"]
    assert [r["session_id"] for r in rows] == ["e2e-manual"] * 2 + ["e2e-smart"] * 3
    assert {r["pattern"] for r in rows} == {"recursive delete"}
    people = [r for r in rows if r["surface"] == "cli"]
    assert len(people) == 3 and all(r["decided_by"] == "" for r in people)
    for row in people:
        # The prompt answered late on purpose: the recorded wait covers that delay (less the
        # clock's resolution: Windows timers tick every ~16 ms).
        assert DELAYS[row["choice"]] - 0.05 <= row["wait_s"] < DELAYS[row["choice"]] + 30, row
    for row in (rows[2], rows[4]):
        assert (row["surface"], row["decided_by"]) == ("smart", "aux_llm")
        assert isinstance(row["wait_s"], float), "the smart request and verdict pair up"

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    log = get_plugin_command_handler("approval-log")
    assert log is not None, "/approval-log was not registered"
    report = log("all")
    print(report)
    lines = report.splitlines()
    assert lines[2].split() == ["pattern", "asked", "approved", "denied", "unanswered", "median", "wait"]
    assert lines[3].startswith("recursive delete") and lines[3].split()[2:6] == ["5", "2", "3", "0"]
    median = sorted(r["wait_s"] for r in people)[1]  # people's answers only
    assert lines[3].split()[6] == f"{median:.1f}s"
    assert lines[-1].startswith("Smart-mode verdicts count")
    surfaces = log("all surfaces").splitlines()
    assert [line.split()[:2] for line in surfaces[3:5]] == [["cli", "3"], ["smart", "2"]]
    assert surfaces[5].split()[:5] == ["total", "5", "2", "3", "0"]
    session = log("session").splitlines()
    assert session[0] == "Approvals, latest session:" and session[3].split()[2:6] == ["3", "1", "2", "0"]
    assert session[3].split()[6] == f"{rows[3]['wait_s']:.1f}s", "only the override was a person's answer"
    week = log("7d").splitlines()
    assert week[0] == "Approvals, last 7 days:" and week[3:] == lines[3:]
    listing = log("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Approvals, all recorded, newest 5 of 5:"
    assert [line.split()[-2] for line in listing[3:]] == ["smart_approve", "deny", "smart_deny", "once", "deny"]
    assert listing[3].split()[1] == "ago" and len(listing) == 8
    assert log("clear") == "approval-ledger: deleted 5 recorded decision(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child(sys.argv[1]))
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
