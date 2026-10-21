"""End-to-end check of jwt-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/jwt_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider, then reads the request Hermes sent to the model: the `@jwt:`
references must have been expanded in place. A child process then asks the TUI gateway's own
`complete.path` handler for `@jwt:` suggestions. Nothing leaves 127.0.0.1.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("JWT_E2E_HOME") or tempfile.mkdtemp(prefix="jwt-ref-e2e-"))
ANSWER = "ANSWER-j8w all done"

# The example token from jwt.io: HS256, sub/name/iat, no exp.
JWT_IO = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
          ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
          ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


def _part(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).rstrip(b"=").decode("ascii")


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", JWT_E2E_HOME=str(HOME))
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
    """Ask the TUI gateway for @jwt: suggestions (its import moves stdout, so write a file)."""
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    out = {}
    for word in (f"@jwt:{JWT_IO}", "@jwt:"):
        out[word] = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
    (HOME / "complete.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    now = int(time.time())
    expired = f"{_part({'alg': 'RS256', 'typ': 'JWT', 'kid': 'k-2026'})}.{_part({'iss': 'https://auth.example.com', 'sub': 'user-4821', 'iat': now - 7500, 'exp': now - 3900})}.c2lnbmF0dXJl"
    unsigned = f"{_part({'alg': 'none'})}.{_part({'sub': 'admin'})}."
    prompts = (
        f"Who is this token for? @jwt:{JWT_IO}",
        f'Why is the API refusing this? @jwt:"Bearer {expired}".',
        f"And these two? @jwt:{unsigned} @jwt:not-a-token",
    )
    with FakeLLMServer([Text(ANSWER) for _ in prompts]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [jwt-ref]\n")
        shutil.copytree(REPO / "jwt-ref", HOME / "plugins" / "jwt-ref")
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

    # The jwt.io token, decoded exactly.
    assert "JWT, signed (JWS), 3 parts; the signature is NOT verified" in first
    assert '  alg (algorithm)  "HS256" = HMAC with SHA-256' in first
    assert '  sub (subject)    "1234567890"' in first
    assert "  iat (issued at)  1516239022 = 2018-01-18 01:30:22 UTC" in first
    assert "  no exp claim: the token does not expire on its own" in first
    assert "plugin expansion error" not in first
    # A quoted Bearer header value; the sentence's "." is not part of it.
    assert '  kid (key id)     "k-2026"' in second
    assert "  EXPIRED 1 hour 5 minutes ago" in second
    assert "  lifetime 1 hour (exp - iat)" in second
    assert "plugin expansion error" not in second
    # Two references in one message: an unsigned token (Hermes drops its final "." as punctuation,
    # and the plugin still reads it), and a value that is not a token.
    assert "JWT, signed (JWS), 2 parts; the signature is NOT verified" in third
    assert "  - only 2 parts: the signature part is missing" in third
    assert 'alg is "none": the token is unsigned' in third
    assert '  sub (subject)  "admin"' in third
    assert ("@jwt:not-a-token: plugin expansion error: a JWT has 3 parts (signed) or 5 parts "
            "(encrypted) separated by dots; this has 1") in third

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    replies = json.loads((HOME / "complete.json").read_text(encoding="utf-8-sig"))
    for word, reply in replies.items():
        items = reply["result"]["items"]
        print(word[:24], "->", [(i["text"][:24], i.get("display"), i.get("meta")) for i in items])
    preview = replies[f"@jwt:{JWT_IO}"]["result"]["items"]
    assert [i["text"] for i in preview] == [f"@jwt:{JWT_IO}"], preview
    assert preview[0]["display"] == 'alg HS256, sub "1234567890"', preview
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
