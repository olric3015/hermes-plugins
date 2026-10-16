"""End-to-end check of voice-ledger against a real Hermes checkout (not collected by pytest).

    PYTHONPATH=<hermes checkout> <hermes venv python> tests/e2e/voice_ledger_e2e.py

A child process loads the plugin through Hermes's own plugin discovery and sends short WAV
recordings through Hermes's own speech-to-text dispatcher (``transcribe_audio``), the call the
gateway makes for an incoming voice message and CLI voice mode makes for a recording. The STT
provider is a ``type: command`` provider from config.yaml whose command is a tiny local script
that writes a fixed transcript, so no audio leaves the machine and no STT package is needed.

The parent then checks the recorded rows (sizes match the files, nothing of the path, the
vocabulary prompt or the transcript is kept) and the `/voice-log` report. No network is used.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("VOICE_E2E_HOME") or tempfile.mkdtemp(prefix="voice-ledger-e2e-"))
# (source, model override, seconds of audio) per recording, in order.
PLAN = [("gateway", None, 0.5), ("gateway", None, 1.0), ("voice_mode", "e2e-tiny", 0.25)]

FAKE_STT = '''\
import sys
from pathlib import Path
Path(sys.argv[2]).write_text("SECRET-TRANSCRIPT hello", encoding="utf-8")
'''


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "GROQ", "MISTRAL", "GIT_"))}
    env.update(HERMES_HOME=str(HOME), HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", VOICE_E2E_HOME=str(HOME))
    return env


def _clip(path: Path, seconds: float) -> int:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * int(16000 * seconds))
    return path.stat().st_size


def child() -> int:
    from hermes_cli.plugins import discover_plugins
    from tools.transcription_tools import transcribe_audio

    discover_plugins(force=True)
    clips = HOME / "SECRET-CHAT-clips"
    clips.mkdir()
    sizes = []
    for i, (source, model, seconds) in enumerate(PLAN):
        path = clips / f"voice_SECRET_{i}.wav"
        sizes.append(_clip(path, seconds))
        result = transcribe_audio(str(path), model, source)
        assert result.get("success") and "hello" in result.get("transcript", ""), result
    (HOME / "sizes.json").write_text(json.dumps(sizes), encoding="utf-8")
    return 0


def main() -> int:
    shutil.copytree(REPO / "voice-ledger", HOME / "plugins" / "voice-ledger")
    script = HOME / "fake_stt.py"
    script.write_text(FAKE_STT, encoding="utf-8")
    command = f"{Path(sys.executable).as_posix()} {script.as_posix()} {{input_path}} {{output_path}}"
    (HOME / "config.yaml").write_text(
        "plugins:\n  enabled: [voice-ledger]\n"
        "stt:\n  enabled: true\n  provider: e2e-stt\n  prompt: SECRET-VOCABULARY\n"
        "  providers:\n    e2e-stt:\n      type: command\n"
        f"      command: {json.dumps(command)}\n      format: txt\n"
        # Hermes reads a provider's language from stt.<provider>.language.
        "  e2e-stt:\n    language: en\n",
        encoding="utf-8")
    run = subprocess.run([sys.executable, __file__, "child"], env=_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=300)
    print(run.stdout.strip())
    assert run.returncode == 0, run.stderr[-4000:]

    data = HOME / "plugin-data" / "voice-ledger" / "voice.jsonl"
    assert data.exists(), "no recording was recorded"
    raw = data.read_text(encoding="utf-8-sig")
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        print("  ", row)
    assert "SECRET" not in raw and ".wav" not in raw, "a path, prompt or transcript reached the ledger"
    sizes = json.loads((HOME / "sizes.json").read_text(encoding="utf-8"))
    assert [(r["source"], r["provider"], r["model"], r["bytes"]) for r in rows] == [
        (source, "e2e-stt", model or "default", size) for (source, model, _), size in zip(PLAN, sizes)]

    os.environ.update(_env())
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler

    discover_plugins(force=True)
    voice_log = get_plugin_command_handler("voice-log")
    assert voice_log is not None, "/voice-log was not registered"
    report = voice_log("all")
    print(report)
    lines = report.splitlines()
    assert lines[0] == "Transcribed recordings, all recorded:"
    assert lines[2].split() == ["provider", "recordings", "audio", "median", "last"]
    assert lines[3].split()[:2] == ["e2e-stt", "3"]
    assert lines[-1] == "fails to transcribe still counts."
    week = voice_log("7d").splitlines()
    assert week[0] == "Transcribed recordings, last 7 days:" and week[3].split()[:2] == ["e2e-stt", "3"]
    sources = voice_log("all sources").splitlines()
    print("\n".join(sources))
    assert [line.split()[:2] for line in sources[3:6]] == [["gateway", "2"], ["voice_mode", "1"], ["total", "3"]]
    models = voice_log("all models").splitlines()
    assert [line.split()[:2] for line in models[3:6]] == [["default", "2"], ["e2e-tiny", "1"], ["total", "3"]]
    languages = voice_log("all languages").splitlines()
    assert {r["language"] for r in rows} == {"en"} and languages[3].split()[:2] == ["en", "3"]
    days = voice_log("all days").splitlines()
    expected_days = {}
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.localtime(row["ts"]))
        expected_days[day] = expected_days.get(day, 0) + 1
    assert [line.split()[:2] for line in days[3:3 + len(expected_days)]] == [
        [day, str(count)] for day, count in sorted(expected_days.items(), reverse=True)]
    listing = voice_log("all recent").splitlines()
    print("\n".join(listing))
    assert listing[0] == "Transcribed recordings, all recorded, newest 3 of 3:"
    assert [line.split()[2:4] for line in listing[3:]] == [[s, "e2e-stt"] for s, _, _ in reversed(PLAN)]
    two = voice_log("all recent 2").splitlines()
    assert two[0] == "Transcribed recordings, all recorded, newest 2 of 3:" and len(two) == 5
    assert voice_log("recent 0").startswith("Unknown option: 0")
    assert voice_log("clear") == "voice-ledger: deleted 3 recorded recording(s)." and not data.exists()
    print("E2E OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(child())
    code = main()
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(code)
