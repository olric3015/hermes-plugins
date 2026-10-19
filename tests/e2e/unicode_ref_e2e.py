"""End-to-end check of unicode-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/unicode_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider, then reads the request Hermes sent to the model: the `@unicode:`
reference must have been expanded in place, with the hidden characters of the quoted text
spelled out. A child process then asks the TUI gateway's own `complete.path` handler for
`@unicode:` suggestions. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("UNICODE_E2E_HOME") or tempfile.mkdtemp(prefix="unicode-ref-e2e-"))
ANSWER = "ANSWER-u41c all done"
# A zero-width space inside one word, a Cyrillic "р" in a Latin one, and a no-break space.
TEXT = "pa​ypal рaypal ok"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", UNICODE_E2E_HOME=str(HOME))
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
    """Ask the TUI gateway for @unicode: suggestions (its import moves stdout, so write a file)."""
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    out = {}
    for word in ("@unicode:zero width", "@unicode:U+202E"):
        reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
        out[word] = reply
    (HOME / "complete.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    with FakeLLMServer([Text(ANSWER), Text(ANSWER)]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [unicode-ref]\n")
        shutil.copytree(REPO / "unicode-ref", HOME / "plugins" / "unicode-ref")
        # No -Q: quiet mode sends the query to the agent without expanding @ references.
        for prompt in (f'What is odd here? @unicode:"{TEXT}"', "And these? @unicode:U+202E,U+00A0."):
            run = _hermes("chat", "-q", prompt, "--oneshot")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert ANSWER in run.stdout, "the scripted answer was not printed"
        first, second = (_user_text(body) for body in srv.main_requests()[:2])
    print(first)
    print(second)

    # The quoted text reached the plugin untouched and its breakdown reached the model.
    assert "Unicode breakdown of the quoted text: 17 character(s), 21 UTF-8 byte(s)." in first
    assert " 3        U+200B  ZERO WIDTH SPACE" in first
    assert "- 1 invisible character(s): U+200B ZERO WIDTH SPACE at 3." in first
    assert "- Spaces other than the ordinary one: U+00A0 NO-BREAK SPACE at 15." in first
    assert "mixes CYRILLIC and LATIN letters: р U+0440 CYRILLIC SMALL LETTER ER at 9." in first
    assert "plugin expansion error" not in first and "unsupported reference type" not in first
    # Code point lookups; the trailing "." is the sentence's, not part of the reference.
    assert "U+202E  RIGHT-TO-LEFT OVERRIDE" in second and "changes text direction" in second
    assert "U+00A0  NO-BREAK SPACE" in second and "decomposes to: <noBreak> 0020" in second

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    replies = json.loads((HOME / "complete.json").read_text(encoding="utf-8"))
    for word, reply in replies.items():
        items = reply["result"]["items"]
        print(word, "->", [(i["text"], i.get("display"), i.get("meta")) for i in items][:5])
        texts = [i["text"] for i in items]
        expected = "@unicode:U+200B" if "zero" in word else "@unicode:U+202E"
        assert expected in texts, f"{word}: {texts}"
    zero = [i["text"] for i in replies["@unicode:zero width"]["result"]["items"]]
    assert all("ZERO WIDTH" in i["display"] for i in replies["@unicode:zero width"]["result"]["items"]), zero
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
