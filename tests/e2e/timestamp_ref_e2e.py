"""End-to-end check of timestamp-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/timestamp_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider, then reads the request Hermes sent to the model: the `@ts:`
references must have been expanded in place. A child process then asks the TUI gateway's own
`complete.path` handler for `@ts:` suggestions. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("TS_E2E_HOME") or tempfile.mkdtemp(prefix="timestamp-ref-e2e-"))
ANSWER = "ANSWER-t7s all done"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", TS_E2E_HOME=str(HOME))
    return env


def _hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], env=_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def _user_text(body: dict) -> str:
    """The newest user message of a chat-completions request, as plain text."""
    message = [m for m in body["messages"] if m.get("role") == "user"][-1]
    content = message["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content


def child() -> int:
    """Ask the TUI gateway for @ts: suggestions (its import moves stdout, so write a file)."""
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    out = {}
    for word in ("@ts:1729468800123", "@ts:"):
        reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
        out[word] = reply
    (HOME / "complete.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    prompts = (
        "When did this job start? @ts:1729468800123",
        'And this one? @ts:"Mon, 21 Oct 2024 03:00:00 +0300".',
        "How far apart are @ts:1729468800,1729555200.5 and is @ts:2024-10-21T00:00:00+03:00 right?",
    )
    with FakeLLMServer([Text(ANSWER) for _ in prompts]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [timestamp-ref]\n")
        shutil.copytree(REPO / "timestamp-ref", HOME / "plugins" / "timestamp-ref")
        # No -Q: quiet mode sends the query to the agent without expanding @ references.
        for prompt in prompts:
            run = _hermes("chat", "-q", prompt, "--oneshot")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert ANSWER in run.stdout, "the scripted answer was not printed"
        first, second, third = (_user_text(body) for body in srv.main_requests()[:3])
    for text in (first, second, third):
        print(text)
        print("-" * 40)
        assert "plugin expansion error" not in text and "unsupported reference type" not in text

    # A 13-digit number is read as milliseconds and converted exactly.
    assert "Timestamp 1729468800123 (Unix milliseconds)" in first
    assert "note: read as milliseconds from its 13 digits; add s, ms, us or ns to choose" in first
    assert "  UTC       2024-10-21 00:00:00.123 Monday" in first
    assert "  ISO 8601  2024-10-21T00:00:00.123Z" in first
    assert "  Unix      1729468800.123 s, 1729468800123 ms, 1729468800123000 us" in first
    assert " ago" in first.split("relative", 1)[1].splitlines()[0]
    # An RFC 2822 date in quotes keeps its offset; the sentence's "." is not part of it.
    assert "Timestamp Mon, 21 Oct 2024 03:00:00 +0300 (RFC 2822)" in second
    assert "  as written  2024-10-21 03:00:00 +03:00 Monday" in second
    assert "  UTC         2024-10-21 00:00:00 Monday" in second
    # Two references in one message, one of them with two values and their gap.
    assert "Differences:\n  1 -> 2: +1 day (+86,400.5 s)" in third
    assert "Timestamp 2024-10-21T00:00:00+03:00 (ISO 8601)" in third
    assert "  UTC         2024-10-20 21:00:00 Sunday" in third

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    replies = json.loads((HOME / "complete.json").read_text(encoding="utf-8-sig"))
    for word, reply in replies.items():
        items = reply["result"]["items"]
        print(word, "->", [(i["text"], i.get("display"), i.get("meta")) for i in items])
    preview = replies["@ts:1729468800123"]["result"]["items"]
    assert [i["text"] for i in preview] == ["@ts:1729468800123"], preview
    assert preview[0]["display"] == "1729468800123 = 2024-10-21 00:00:00.123 UTC", preview
    assert "@ts:now" in [i["text"] for i in replies["@ts:"]["result"]["items"]]
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
