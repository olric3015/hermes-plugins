"""End-to-end check of chmod-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/chmod_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider, then reads the request Hermes sent to the model: the `@chmod:`
references must have been expanded in place. A child process then asks the TUI gateway's own
`complete.path` handler for `@chmod:` suggestions. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("CHMOD_E2E_HOME") or tempfile.mkdtemp(prefix="chmod-ref-e2e-"))
ANSWER = "ANSWER-c4m all done"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", CHMOD_E2E_HOME=str(HOME))
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
    """Ask the TUI gateway for @chmod: suggestions (its import moves stdout, so write a file)."""
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    out = {}
    for word in ("@chmod:750", "@chmod:", '@chmod:"644 u+x"', "@chmod:79"):
        out[word] = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
    (HOME / "complete.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    prompts = (
        "Is this safe on a script? @chmod:4755 and what is @chmod:-rw-r--r--.",
        'git says the mode changed: @chmod:"100644 100755" and I ran @chmod:u+x,go-w',
        'New files here get what? @chmod:"umask 027" @chmod:788',
    )
    with FakeLLMServer([Text(ANSWER) for _ in prompts]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [chmod-ref]\n")
        shutil.copytree(REPO / "chmod-ref", HOME / "plugins" / "chmod-ref")
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

    # An octal mode with setuid, and ls -l text whose sentence "." stays out of the value.
    assert "Mode 4755 (rwsr-xr-x)" in first
    assert "  special   setuid" in first
    assert "  - setuid: the program runs with its owner's rights" in first
    assert "Mode 644 (rw-r--r--), a regular file" in first
    assert "  ls -l     -rw-r--r--\n" in first
    assert "plugin expansion error" not in first
    # Two quoted st_modes compared (git's old mode / new mode), and a change on its own.
    assert "Mode 644 -> 755" in second
    assert "  adds      execute for owner, group and others" in second
    assert "  - this is the change git shows as old mode / new mode when a file's execute bit flips" in second
    assert "chmod u+x,go-w" in second
    assert "  go-w      remove write from the group and others" in second
    assert "  on 644    rwxr--r--  (744)" in second
    assert "plugin expansion error" not in second
    # A umask, and a value that is not octal.
    assert "umask 027" in third
    assert "  files     rw-r-----  (640)  new files (programs ask for 666)" in third
    assert "  dirs      rwxr-x---  (750)  new directories (mkdir asks for 777)" in third
    assert "@chmod:788: plugin expansion error: '788' is not octal: 8 is not an octal digit (0-7)" in third

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    replies = json.loads((HOME / "complete.json").read_text(encoding="utf-8-sig"))
    for word, reply in replies.items():
        items = reply["result"]["items"]
        print(word, "->", [(i["text"], i.get("display"), i.get("meta")) for i in items])
    preview = replies["@chmod:750"]["result"]["items"]
    assert [(i["text"], i["display"]) for i in preview] == [("@chmod:750", "750 = rwxr-x---")], preview
    examples = replies["@chmod:"]["result"]["items"]
    assert [i["text"] for i in examples] == ["@chmod:755", '@chmod:"644 u+x"', '@chmod:"umask 022"'], examples
    change = replies['@chmod:"644 u+x"']["result"]["items"]
    assert [i["display"] for i in change] == ["644 u+x -> 744 (rwxr--r--)"], change
    assert replies["@chmod:79"]["result"]["items"] == []
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
