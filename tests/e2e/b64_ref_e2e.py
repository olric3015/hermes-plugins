"""End-to-end check of b64-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/b64_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider, then reads the request Hermes sent to the model: the `@b64:`
references must have been expanded in place. A child process then asks the TUI gateway's own
`complete.path` handler for `@b64:` suggestions. Nothing leaves 127.0.0.1.
"""

import base64
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("B64_E2E_HOME") or tempfile.mkdtemp(prefix="b64-ref-e2e-"))
ANSWER = "ANSWER-b64 all done"

DSN = b"postgres://app:hunter2@db.internal:5432/orders"
SECRET = base64.b64encode(DSN).decode("ascii")
EVENT = base64.b64encode(gzip.compress(json.dumps({"event": "deploy", "ok": False}).encode(), mtime=0)).decode("ascii")


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", B64_E2E_HOME=str(HOME))
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
    """Ask the TUI gateway for @b64: suggestions (its import moves stdout, so write a file)."""
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    out = {}
    for word in (f"@b64:{SECRET}", "@b64:"):
        out[word] = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
    (HOME / "complete.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    prompts = (
        f"Which database is this secret for? @b64:{SECRET}",
        f"What happened here? @b64:{EVENT}.",
        "And these two? @b64:caf%C3%A9%20au%20lait @b64:hex:4865x",
    )
    with FakeLLMServer([Text(ANSWER) for _ in prompts]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [b64-ref]\n")
        shutil.copytree(REPO / "b64-ref", HOME / "plugins" / "b64-ref")
        # No -Q: quiet mode sends the query to the agent without expanding @ references.
        for prompt in prompts:
            run = _hermes("chat", "-q", prompt, "--oneshot")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert ANSWER in run.stdout, "the scripted answer was not printed"
        first, second, third = (_user_text(body) for body in srv.main_requests()[:3])
    for text in (first, second, third):
        print(text)
        print("-" * 40)
        assert "unsupported reference type" not in text

    # Standard base64 with "+", "/" or "=" reaches the plugin whole, and decodes to its text.
    assert f"base64: {len(SECRET.rstrip('='))} characters -> {len(DSN)} bytes" in first
    assert "  postgres://app:hunter2@db.internal:5432/orders" in first
    assert "plugin expansion error" not in first
    # gzip inside base64, with the sentence's "." dropped by Hermes before the plugin sees it.
    assert "Content: gzip data, inflates to 32 bytes" in second
    assert "Inflated: JSON object, 2 keys" in second
    assert '    "event": "deploy",' in second
    assert "plugin expansion error" not in second
    # Two references in one message: percent-encoding, and a value that is not hex.
    assert "percent-encoding: 21 characters -> 13 bytes" in third
    assert "  café au lait" in third
    assert "@b64:hex:4865x: plugin expansion error: 'x' is not a hex digit" in third

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    replies = json.loads((HOME / "complete.json").read_text(encoding="utf-8-sig"))
    for word, reply in replies.items():
        items = reply["result"]["items"]
        print(word[:24], "->", [(i["text"][:24], i.get("display"), i.get("meta")) for i in items])
    preview = replies[f"@b64:{SECRET}"]["result"]["items"]
    assert [i["text"] for i in preview] == [f"@b64:{SECRET}"], preview
    assert preview[0]["display"] == f'base64 -> {len(DSN)} bytes, text "{DSN[:40].decode()}..."', preview
    assert replies["@b64:"]["result"]["items"] == [], replies["@b64:"]
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
