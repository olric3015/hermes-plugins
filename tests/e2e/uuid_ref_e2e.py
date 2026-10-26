"""End-to-end check of uuid-ref against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/uuid_ref_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider, then reads the request Hermes sent to the model: the `@uuid:`
references must have been expanded in place. A child process then asks the TUI gateway's own
`complete.path` handler for `@uuid:` suggestions. Nothing leaves 127.0.0.1.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("UUID_E2E_HOME") or tempfile.mkdtemp(prefix="uuid-ref-e2e-"))
ANSWER = "ANSWER-u7d all done"

V7 = "017f22e2-79b0-7cc3-98c4-dc0c0c07398f"  # RFC 9562 Appendix A
V1 = "c232ab00-9414-11ec-b3c8-9f6bdeced846"
V6 = "1ec9414c-232a-6b00-b3c8-9f6bdeced846"
ULID = "01ARYZ6S41TSV4RRFFQ69G5FAV"


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", UUID_E2E_HOME=str(HOME))
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


def _fresh_v7() -> tuple:
    """A v7 UUID made now, and the creation time it carries as uuid-ref prints it."""
    if hasattr(uuid, "uuid7"):  # Python 3.14: the standard library makes it and reads its time
        made = uuid.uuid7()
        ms = made.time
    else:
        ms = int(time.time() * 1000)
        bits = (ms << 80) | (0x7 << 76) | (random.getrandbits(12) << 64) | (0b10 << 62) | random.getrandbits(62)
        made = uuid.UUID(int=bits)
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)
    stamp = moment.strftime("%Y-%m-%d %H:%M:%S") + (f".{ms % 1000:03d}".rstrip("0") if ms % 1000 else "")
    return str(made), stamp + " UTC"


def child() -> int:
    """Ask the TUI gateway for @uuid: suggestions (its import moves stdout, so write a file)."""
    from hermes_cli.plugins import discover_plugins
    from tui_gateway import server

    discover_plugins(force=True)
    out = {}
    for word in (f"@uuid:{V7.upper()}", "@uuid:", "@uuid:9191"):
        out[word] = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path", "params": {"word": word}})
    (HOME / "complete.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


def main() -> int:
    from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home

    fresh, fresh_stamp = _fresh_v7()
    prompts = (
        f"When were these rows created? @uuid:{V7} and @uuid:{fresh}",
        "Is this a random one? @uuid:{919108F7-52D1-4320-9BAC-F847DB4148A8}. And @uuid:urn:uuid:6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        f'Which came first? @uuid:"{V1} {V6} {ULID}" @uuid:not-a-uuid',
    )
    with FakeLLMServer([Text(ANSWER) for _ in prompts]) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [uuid-ref]\n")
        shutil.copytree(REPO / "uuid-ref", HOME / "plugins" / "uuid-ref")
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

    # The RFC 9562 v7 vector, and a v7 made a moment ago (by the standard library on Python 3.14).
    assert f"UUID {V7}" in first
    assert "  version  7: Unix time in milliseconds + random bits" in first
    assert "  created  2022-02-22 19:22:22 UTC, about " in first
    assert f"UUID {fresh}" in first
    assert f"  created  {fresh_stamp}, " in first
    assert "plugin expansion error" not in first
    # Braces and upper case are read, and the sentence's "." stays out; a URN-form namespace UUID.
    assert "UUID 919108f7-52d1-4320-9bac-f847db4148a8" in second
    assert "  written  in braces, in upper case" in second
    assert "  version  4: random" in second
    assert "this is the DNS namespace UUID from RFC 9562" in second
    assert "  node       00:c0:4f:d4:30:c8: looks like a network card (MAC) address" in second
    assert "plugin expansion error" not in second
    # Three identifiers in one quoted reference, compared by time, and a value that is not a UUID.
    assert f"1. UUID {V1}" in third and f"2. UUID {V6}" in third and f"3. ULID {ULID}" in third
    assert "  2 carries the same time as 1" in third
    assert "  3 was made about 5.6 years (2,032 days) before 2" in third
    assert ("@uuid:not-a-uuid: plugin expansion error: 'not-a-uuid' is not a UUID: "
            "'i', 'n', 'o', 't', 'u' are not hex digits") in third

    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 0, run.stderr[-4000:]
    replies = json.loads((HOME / "complete.json").read_text(encoding="utf-8-sig"))
    for word, reply in replies.items():
        items = reply["result"]["items"]
        print(word[:24], "->", [(i["text"][:48], i.get("display"), i.get("meta")) for i in items])
    preview = replies[f"@uuid:{V7.upper()}"]["result"]["items"]
    assert [i["text"] for i in preview] == [f"@uuid:{V7}"], preview
    assert preview[0]["display"].startswith("v7, created 2022-02-22 19:22:22 UTC, about "), preview
    assert replies["@uuid:9191"]["result"]["items"] == []
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
