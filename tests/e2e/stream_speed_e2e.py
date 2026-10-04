"""End-to-end check of stream-speed against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/stream_speed_e2e.py

Installs the plugin into a throwaway HERMES_HOME and runs real one-shot `hermes chat` turns
against a loopback provider that streams slowly on purpose, then checks the recorded rows and the
`/speed` report. Nothing leaves 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(tempfile.mkdtemp(prefix="stream-speed-e2e-"))

from tests.fakes.fake_llm_provider import FakeLLMServer, Text, write_hermes_home  # noqa: E402

ANSWER = "PRIVATE-ANSWER-4c1e " + "streamed words " * 12
FIRST_TEXT_DELAY = 0.15  # per chunk; the first content chunk waits this long


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    return env


def _hermes(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hermes_cli.main", *args], env=_env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)


def main() -> int:
    script = [Text(ANSWER, chunk_chars=16, delay_per_chunk=FIRST_TEXT_DELAY) for _ in range(2)]
    with FakeLLMServer(script) as srv:
        write_hermes_home(HOME, srv.base_url, extra_config="plugins:\n  enabled: [stream-speed]\n")
        shutil.copytree(REPO / "stream-speed", HOME / "plugins" / "stream-speed")
        for prompt in ("first question", "second question"):
            run = _hermes("chat", "-q", prompt, "--oneshot")
            assert run.returncode == 0, f"hermes chat failed:\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}"
            assert "PRIVATE-ANSWER-4c1e" in run.stdout, "the scripted answer was not printed"
        main_turns = len(srv.main_requests())

    data = HOME / "plugin-data" / "stream-speed" / "streams.jsonl"
    assert data.exists(), "no stream was recorded"
    raw = data.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    print(f"hermes made {main_turns} main request(s); {len(rows)} stream row(s) recorded")
    for row in rows:
        print("  ", row)
    assert len(rows) == main_turns == 2, "one row per streamed response"
    assert "PRIVATE-ANSWER" not in raw, "streamed text reached the record"
    chunks = -(-len(ANSWER) // 16)
    for row in rows:
        assert row["model"] == "fake-model" and row["finished"] and not row["failed"]
        assert row["chars"] == len(ANSWER.strip()) or row["chars"] == len(ANSWER)
        # The provider sleeps before every chunk: the first text cannot arrive sooner than one
        # delay, and the rest of the answer takes about one delay per remaining chunk.
        assert row["ttft_s"] is not None and row["ttft_s"] >= FIRST_TEXT_DELAY * 0.5
        assert row["duration_s"] - row["ttft_s"] >= FIRST_TEXT_DELAY * (chunks - 1) * 0.5

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    speed = get_plugin_command_handler("speed")
    assert speed is not None, "/speed was not registered"
    report = speed("all")
    print(report)
    assert report.splitlines()[3].split()[:3] == ["fake-model", "2", "0"]
    assert speed("clear") == "stream-speed: deleted 2 recorded stream(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
