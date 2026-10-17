"""End-to-end check of edit-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/edit_ledger_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider whose model writes files with write_file before it answers, then
checks the recorded rows and the `/edit-log` report. A second, test-only plugin keeps the second
turn going once (a `pre_verify` "continue"), so the run also covers a turn the hook sees twice.
Nothing leaves 127.0.0.1; files are written only inside a temporary workspace.
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
HOME = Path(tempfile.mkdtemp(prefix="edit-ledger-e2e-"))
WORK = Path(tempfile.mkdtemp(prefix="edit-ledger-work-"))

from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall, write_hermes_home  # noqa: E402

ANSWER = "PRIVATE-ANSWER-5b1e all done"
NUDGE_PLUGIN = '''\
def _keep_going(attempt=0, changed_paths=(), **kwargs):
    if attempt == 0 and any(p.endswith("b.py") for p in changed_paths):
        return {"action": "continue", "message": "Also add c.py, then finish."}
    return None


def register(ctx):
    ctx.register_hook("pre_verify", _keep_going)
'''


def _write(name: str, body: str) -> tuple:
    return ("write_file", {"path": str(WORK / name), "content": body})


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    return env


def _hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], env=_env(), cwd=str(WORK),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)


def main() -> int:
    first, notes, second, third = (_write("a.py", "print('a')\n"), _write("NOTES.md", "# notes\n"),
                                   _write("b.py", "print('b')\n"), _write("c.py", "print('c')\n"))
    # Turn 1: two files, then the answer. Turn 2: one file, the answer, a nudge, one more file, the answer.
    script = [ToolCall(*first, parallel=[notes]), Text(ANSWER),
              ToolCall(*second), Text(ANSWER), ToolCall(*third), Text(ANSWER)]
    with FakeLLMServer(script) as srv:
        # Tool Search (newer Hermes) would hide write_file behind its bridge tools; turn it off so the
        # scripted calls are plain tool calls on every version. Older versions ignore the key.
        write_hermes_home(HOME, srv.base_url, extra_config=(
            "plugins:\n  enabled: [edit-ledger, nudge-once]\ntools:\n  tool_search:\n    enabled: \"off\"\n"))
        shutil.copytree(REPO / "edit-ledger", HOME / "plugins" / "edit-ledger")
        nudge = HOME / "plugins" / "nudge-once"
        nudge.mkdir(parents=True)
        (nudge / "plugin.yaml").write_text("name: nudge-once\nversion: 0.0.1\nprovides_hooks:\n  - pre_verify\n",
                                           encoding="utf-8", newline="\n")
        (nudge / "__init__.py").write_text(NUDGE_PLUGIN, encoding="utf-8", newline="\n")
        for prompt in ("first task", "second task"):
            run = _hermes("chat", "-q", prompt, "--oneshot", "-t", "file")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert "PRIVATE-ANSWER-5b1e" in run.stdout, "the scripted answer was not printed"
        print(f"hermes made {len(srv.main_requests())} main request(s)")
    for name in ("a.py", "NOTES.md", "b.py", "c.py"):
        assert (WORK / name).exists(), f"{name} was not written"

    data = HOME / "plugin-data" / "edit-ledger" / "edits.jsonl"
    assert data.exists(), "no turn was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        print("  ", row)
    assert [(r["attempt"], r["files"], r["types"]) for r in rows] == [
        (0, 2, {".md": 1, ".py": 1}), (0, 1, {".py": 1}), (1, 2, {".py": 2})], "unexpected rows"
    assert str(WORK) not in raw and "a.py" not in raw and "NOTES" not in raw and "PRIVATE" not in raw
    assert all(r["model"] == "fake-model" and len(r["session"]) == 12 for r in rows)
    assert rows[0]["session"] != rows[1]["session"] == rows[2]["session"]

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    log = get_plugin_command_handler("edit-log")
    assert log is not None, "/edit-log was not registered"
    report = log("all")
    print(report)
    # Two turns, not three: the nudged turn's second call replaces its first.
    assert report.splitlines()[3].split()[:5] == ["fake-model", "2", "4", "2", ".py"]
    week = log("7d").splitlines()
    assert week[0] == "Turns that changed files, last 7 days:" and week[3:] == report.splitlines()[3:]
    session = log("session").splitlines()
    print("\n".join(session))
    assert session[0] == "Turns that changed files, latest session:"
    assert session[3].split()[:5] == ["fake-model", "1", "2", "2", ".py"]
    types = log("all types").splitlines()
    print("\n".join(types))
    assert [line.split()[:3] for line in types[3:5]] == [[".py", "3", "2"], [".md", "1", "1"]]
    platforms = log("all platforms").splitlines()
    print("\n".join(platforms))
    assert platforms[2].split()[0] == "platform" and platforms[3].split()[1:3] == ["2", "4"]
    days = log("all days").splitlines()
    expected_days = {}
    for row in rows:
        if row["attempt"] == 0:
            day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
            expected_days[day] = expected_days.get(day, 0) + 1
    assert days[2].split()[0] == "day"
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)]
    listing = log("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Turns that changed files, all recorded, newest 2 of 2:"
    assert listing[2].split() == ["when", "model", "platform", "files", "types"]
    assert listing[3].split()[2:] == ["fake-model", rows[2]["platform"], "2", ".py", "2"]
    assert listing[4].split()[2:] == ["fake-model", rows[0]["platform"], "2", ".md", "1,", ".py", "1"]
    one = log("all recent 1").splitlines()
    assert one[0] == "Turns that changed files, all recorded, newest 1 of 2:" and len(one) == 4
    assert log("recent 0").startswith("Unknown option: 0")
    assert log("clear") == "edit-ledger: deleted 3 recorded row(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(code)
