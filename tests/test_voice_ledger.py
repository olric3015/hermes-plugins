"""voice-ledger: what one transcribed recording becomes, how it is stored and how /voice-log reads."""

import json
import time

import pytest

NOW = 1_800_000_000.0


def _payload(**over):
    base = {"file_path": "/tmp/SECRET-CHAT/voice_SECRET.ogg", "provider": "groq",
            "model": "whisper-large-v3-turbo", "language": "en",
            "prompt": "SECRET-VOCABULARY", "source": "gateway"}
    base.update(over)
    return base


def test_row_keeps_source_provider_model_and_size_and_none_of_the_rest(vcledger):
    row = vcledger.build_row(_payload(), now=NOW, size=48_213)
    assert row == {"ts": NOW, "source": "gateway", "provider": "groq", "model": "whisper-large-v3-turbo",
                   "language": "en", "bytes": 48_213}
    assert "SECRET" not in json.dumps(row) and "ogg" not in json.dumps(row)


def test_row_tolerates_a_sparse_or_odd_payload(vcledger):
    row = vcledger.build_row({}, now=NOW, size=None)
    assert row == {"ts": NOW, "source": "unknown", "provider": "unknown", "model": "default",
                   "language": "auto", "bytes": None}
    odd = vcledger.build_row(_payload(source="s" * 500, provider="p" * 500, model="m" * 500,
                                      language="l" * 500), now=NOW, size=0)
    assert (len(odd["source"]), len(odd["provider"]), len(odd["model"]), len(odd["language"])) == (40, 60, 60, 20)


def test_audio_size_reads_the_file_system_only(vcledger, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"\0" * 1234)
    assert vcledger.audio_size(str(clip)) == 1234
    assert vcledger.audio_size(str(tmp_path / "missing.ogg")) is None
    assert vcledger.audio_size("") is None and vcledger.audio_size(None) is None
    assert vcledger.audio_size("bad\0path") is None


def test_storage_round_trip_bom_bound_and_clear(vcledger, tmp_path, monkeypatch):
    path = tmp_path / vcledger.FILENAME
    assert vcledger.read(path) == [] and vcledger.clear(path) == 0
    for provider in ("groq", "local", "openai"):
        vcledger.append(path, vcledger.build_row(_payload(provider=provider), now=NOW, size=10))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert [r["provider"] for r in vcledger.read(path)] == ["groq", "local", "openai"]
    assert b"\r\n" not in path.read_bytes()

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert [r["provider"] for r in vcledger.read(bom)] == ["groq", "local", "openai"]

    monkeypatch.setattr(vcledger, "MAX_BYTES", 400)
    monkeypatch.setattr(vcledger, "KEEP_LINES", 2)
    vcledger.append(path, vcledger.build_row(_payload(provider="mistral"), now=NOW, size=10))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["provider"] for r in vcledger.read(path)] == ["mistral"]
    assert not list(tmp_path.glob(".voice-*"))
    assert vcledger.clear(path) == 1 and not path.exists()

    # A file another tool saved with a BOM is still trimmed to rows that parse.
    monkeypatch.setattr(vcledger, "KEEP_LINES", 5)
    bom.write_bytes(b"\xef\xbb\xbf" + (json.dumps(_payload()) + "\n").encode("utf-8"))
    vcledger.append(bom, vcledger.build_row(_payload(provider="mistral"), now=NOW, size=10))
    assert [r["provider"] for r in vcledger.read(bom)] == ["groq", "mistral"]


def _row(provider="groq", size=1024, ts=NOW, source="gateway", model="whisper-large-v3-turbo", language="en"):
    return {"ts": ts, "source": source, "provider": provider, "model": model, "language": language,
            "bytes": size}


def test_select_windows(vcledger):
    rows = [_row(ts=NOW - 2 * 86400), _row(), _row("local")]
    assert len(vcledger.select(rows, "all", NOW)) == 3
    assert len(vcledger.select(rows, "day", NOW)) == 2
    assert len(vcledger.select(rows, "day", NOW, span=3 * 86400)) == 3


@pytest.mark.parametrize("size, text", [(None, "-"), (0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"),
                                        (1536, "1.5 KB"), (5 * 1024 ** 2, "5.0 MB"), (3 * 1024 ** 3, "3.0 GB")])
def test_size_text(vcledger, size, text):
    assert vcledger._size(size) == text


def test_summary_puts_the_most_recordings_first(vcledger):
    rows = [_row("groq", 40_000, NOW - 7200), _row("groq", 60_000, NOW - 600), _row("groq", None, NOW - 300),
            _row("local", 2 * 1024 ** 2, NOW - 40, source="voice_mode"), _row("openai", True, NOW - 3 * 3600)]
    out = vcledger.summarize(rows, NOW, title="last 24 hours")
    lines = out.splitlines()
    assert lines[0] == "Transcribed recordings, last 24 hours:"
    assert lines[2].split() == ["provider", "recordings", "audio", "median", "last"]
    assert lines[3].split() == ["groq", "3", "97.7", "KB", "48.8", "KB", "5m", "ago"]
    assert lines[4].split() == ["local", "1", "2.0", "MB", "2.0", "MB", "40s", "ago"]
    # A non-integer size (here True) is not a size.
    assert lines[5].split() == ["openai", "1", "-", "-", "3h", "ago"]
    assert lines[6].split() == ["total", "5", "2.1", "MB", "58.6", "KB", "40s", "ago"]
    assert lines[-2:] == ["Counted when Hermes hands a recording to the speech-to-text provider, so one that then",
                          "fails to transcribe still counts."]


def test_summary_single_group_has_no_total_and_empty_says_so(vcledger):
    lines = vcledger.summarize([_row()], NOW, title="all recorded", by="source").splitlines()
    assert lines[2].split()[0] == "source" and lines[3].split()[0] == "gateway"
    assert not any(line.startswith("total") for line in lines)
    assert vcledger.summarize([], NOW, title="all recorded") == "Transcribed recordings, all recorded: none recorded."


def test_days_newest_first_unknown_last(vcledger):
    rows = [_row(ts=NOW - 86400), _row(ts=NOW), _row(ts=NOW), {"provider": "groq", "bytes": 1}]
    out = vcledger.summarize(rows, NOW, title="all recorded", by="day").splitlines()
    names = [line.split()[0] for line in out[3:-3]]
    assert names == [vcledger.local_day(NOW), vcledger.local_day(NOW - 86400), "unknown", "total"]


def test_recent_lists_newest_first(vcledger):
    rows = [_row("groq", 40_000, NOW - 7200), _row("local", 2048, NOW - 30, source="voice_mode", model="base",
                                                     language="auto"),
            _row("openai", None, NOW - 5, model="default")]
    out = vcledger.recent(rows, NOW, title="all recorded", limit=2).splitlines()
    assert out[0] == "Transcribed recordings, all recorded, newest 2 of 3:"
    assert out[2].split() == ["when", "source", "provider", "model", "language", "audio"]
    assert out[3].split() == ["5s", "ago", "gateway", "openai", "default", "en", "-"]
    assert out[4].split() == ["30s", "ago", "voice_mode", "local", "base", "auto", "2.0", "KB"]
    assert vcledger.recent([], NOW, title="x") == "Transcribed recordings, x: none recorded."


@pytest.mark.parametrize("word, count", [("1", 1), ("25", 25), ("100", 100), ("0", None), ("101", None),
                                         ("9" * 5000, None), ("-5", None), ("٣", None), ("7d", None)])
def test_parse_count(vcledger, word, count):
    assert vcledger.parse_count(word) == count


@pytest.mark.parametrize("word, seconds", [("6h", 6 * 3600.0), ("7d", 7 * 86400.0), ("0d", None),
                                           ("12345h", None), ("7w", None), ("d", None), ("٣d", None)])
def test_parse_span(vcledger, word, seconds):
    assert vcledger.parse_span(word) == seconds


@pytest.fixture
def ledger_file(voice_plugin, tmp_path, monkeypatch):
    path = tmp_path / "voice.jsonl"
    monkeypatch.setattr(voice_plugin, "_ledger_path", lambda: path)
    return path


def test_hook_records_a_recording_and_changes_nothing(voice_plugin, vcledger, ledger_file, tmp_path):
    clip = tmp_path / "voice.ogg"
    clip.write_bytes(b"\0" * 700)
    assert voice_plugin._on_transcription(**_payload(file_path=str(clip))) is None
    [row] = vcledger.read(ledger_file)
    assert (row["provider"], row["source"], row["bytes"]) == ("groq", "gateway", 700)
    assert clip.read_bytes() == b"\0" * 700


def test_hook_never_raises(voice_plugin, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(voice_plugin, "_ledger_path", boom)
    assert voice_plugin._on_transcription(**_payload()) is None
    assert voice_plugin._on_transcription(file_path=object()) is None


def test_command(voice_plugin, vcledger, ledger_file):
    now = time.time()
    for provider, source, ts in (("groq", "gateway", now - 60), ("local", "voice_mode", now - 30),
                                 ("openai", "gateway", now - 3 * 86400)):
        vcledger.append(ledger_file, _row(provider, 1024, ts, source=source))
    out = voice_plugin._handle_voice_log("")
    assert out.startswith("Transcribed recordings, last 24 hours:") and "openai" not in out
    assert "openai" in voice_plugin._handle_voice_log("7d") and "last 7 days" in voice_plugin._handle_voice_log("7d")
    for word, column in (("models", "model"), ("sources", "source"), ("languages", "language"),
                         ("days", "day"), ("providers", "provider")):
        assert voice_plugin._handle_voice_log(f"all {word}").splitlines()[2].split()[0] == column
    assert voice_plugin._handle_voice_log("all recent 2").splitlines()[0] == \
        "Transcribed recordings, all recorded, newest 2 of 3:"
    assert voice_plugin._handle_voice_log("all recent").splitlines()[0] == \
        "Transcribed recordings, all recorded, newest 3 of 3:"
    assert voice_plugin._handle_voice_log("recent 0").startswith("Unknown option: 0")
    assert voice_plugin._handle_voice_log("all 5").startswith("Unknown option: 5")
    assert voice_plugin._handle_voice_log("session").startswith("Unknown option: session")
    assert voice_plugin._handle_voice_log("help").startswith("/voice-log:")
    assert voice_plugin._handle_voice_log("clear") == "voice-ledger: deleted 3 recorded recording(s)."
    assert voice_plugin._handle_voice_log("all") == "Transcribed recordings, all recorded: none recorded."


def test_register_wires_the_hook_and_the_command(voice_plugin):
    calls = []

    class Ctx:
        def register_hook(self, name, cb):
            calls.append(("hook", name, cb))

        def register_command(self, name, handler, **kw):
            calls.append(("command", name, handler))

    voice_plugin.register(Ctx())
    assert calls == [("hook", "pre_transcription", voice_plugin._on_transcription),
                     ("command", "voice-log", voice_plugin._handle_voice_log)]


def test_help_lines_up(voice_plugin):
    options = [line for line in voice_plugin._HELP.splitlines() if line.startswith("  /")]
    assert len(options) == 10 and len({len(line) - len(line[2:].split("  ", 1)[1].lstrip()) for line in options}) == 1
