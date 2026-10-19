"""reaction-ledger: what one reaction change becomes, how it is stored and how /reaction-log reads."""

import json
import time

import pytest

NOW = 1_800_000_000.0
CHAT = "-100987654321"


def _payload(emojis=("👍",), chat=CHAT, message="42", custom=()):
    return {"emojis": list(emojis), "custom_emoji_ids": list(custom), "chat_id": chat,
            "message_id": message, "thread_id": None}


def _row(rxledger, emojis=("👍",), chat=CHAT, message="42", ts=NOW, custom=()):
    return rxledger.build_row("telegram", "reaction", _payload(emojis, chat, message, custom), now=ts)


def test_row_keeps_the_reactions_and_only_hashes_of_the_chat_and_message(rxledger):
    row = _row(rxledger, emojis=("👍", "🔥"), custom=("5368324170671202286",))
    assert row == {"ts": NOW, "platform": "telegram", "chat": rxledger.tag("telegram", CHAT),
                   "message": rxledger.tag("telegram", CHAT, "42"), "emojis": ["👍", "🔥"], "custom": 1}
    assert len(row["chat"]) == len(row["message"]) == 12 and row["chat"] != row["message"]
    text = json.dumps(row)
    assert CHAT not in text and "5368324170671202286" not in text
    # The same message id in another chat is another message.
    assert _row(rxledger, chat="777")["message"] != row["message"]


@pytest.mark.parametrize("platform, event_type, payload", [
    ("telegram", "message_edited", {"chat_id": CHAT, "message_id": "42", "text": "hi"}),
    ("telegram", "reaction", None), ("telegram", "reaction", ["👍"]),
    ("telegram", "reaction", {"emojis": ["👍"], "message_id": "42"}),
    ("telegram", "reaction", {"emojis": ["👍"], "chat_id": CHAT, "message_id": ""}),
    ("telegram", "reaction", {"emojis": ["👍"], "chat_id": None, "message_id": "42"}),
])
def test_anything_but_a_reaction_on_an_identifiable_message_is_ignored(rxledger, platform, event_type, payload):
    assert rxledger.build_row(platform, event_type, payload, now=NOW) is None


def test_row_tolerates_an_odd_payload(rxledger):
    row = rxledger.build_row(None, "reaction", {"emojis": ["👍", "👍", "", 7, " ", "x" * 99] + ["🎉"] * 3,
                                                 "custom_emoji_ids": "nope", "chat_id": 0, "message_id": 1},
                             now=NOW)
    assert row["platform"] == "unknown" and row["custom"] == 0
    assert row["emojis"] == ["👍", "x" * 16, "🎉"]
    many = rxledger.build_row("telegram", "reaction", {"emojis": [chr(0x1F600 + i) for i in range(30)],
                                                       "chat_id": 1, "message_id": 2}, now=NOW)
    assert len(many["emojis"]) == rxledger.MAX_EMOJIS
    # A removed reaction is a row too: the message has no reactions any more.
    assert _row(rxledger, emojis=())["emojis"] == []


@pytest.mark.parametrize("emoji, name", [
    ("👍", "thumbs up sign"), ("❤️", "heavy black heart"), ("👍🏽", "thumbs up sign"),
    ("👨‍💻", "man + personal computer"), ("custom", "custom emoji"), ("x" * 40, "latin small letter x + latin sma")])
def test_emoji_name(rxledger, emoji, name):
    assert rxledger.emoji_name(emoji) == name


def test_storage_round_trip_bom_bound_and_clear(rxledger, tmp_path, monkeypatch):
    path = tmp_path / rxledger.FILENAME
    assert rxledger.read(path) == [] and rxledger.clear(path) == 0
    for message in ("1", "2", "3"):
        rxledger.append(path, _row(rxledger, message=message))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{\"torn\": \n[1, 2]\n")
    assert len(rxledger.read(path)) == 3 and rxledger.read(path)[0]["emojis"] == ["👍"]
    assert b"\r\n" not in path.read_bytes()

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert len(rxledger.read(bom)) == 3

    monkeypatch.setattr(rxledger, "MAX_BYTES", 400)
    monkeypatch.setattr(rxledger, "KEEP_LINES", 2)
    rxledger.append(path, _row(rxledger, emojis=("🔥",), message="4"))
    # The two newest LINES survive: the foreign "[1, 2]" line and the row just written.
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2
    assert [r["emojis"] for r in rxledger.read(path)] == [["🔥"]]
    assert not list(tmp_path.glob(".reactions-*"))
    assert rxledger.clear(path) == 1 and not path.exists()

    # A file another tool saved with a BOM is still trimmed to rows that parse.
    monkeypatch.setattr(rxledger, "KEEP_LINES", 5)
    bom.write_bytes(b"\xef\xbb\xbf" + (json.dumps(_row(rxledger, message="9")) + "\n").encode("utf-8"))
    rxledger.append(bom, _row(rxledger, emojis=("🔥",), message="4"))
    assert [r["emojis"] for r in rxledger.read(bom)] == [["👍"], ["🔥"]]


def test_select_windows(rxledger):
    rows = [_row(rxledger, ts=NOW - 2 * 86400), _row(rxledger, message="2"), {"emojis": ["👍"]}]
    assert len(rxledger.select(rows, NOW, None)) == 3
    assert len(rxledger.select(rows, NOW)) == 1
    assert len(rxledger.select(rows, NOW, 3 * 86400)) == 2


def test_latest_keeps_each_messages_newest_row_in_change_order(rxledger):
    rows = [_row(rxledger, ("👍",), message="1", ts=NOW - 50), _row(rxledger, ("❤️",), message="2", ts=NOW - 40),
            _row(rxledger, ("👎",), message="1", ts=NOW - 30), {"emojis": ["🔥"]}]
    assert [(r["emojis"], r["ts"]) for r in rxledger.latest(rows)] == [(["❤️"], NOW - 40), (["👎"], NOW - 30)]


def _sample(rxledger):
    return [
        _row(rxledger, ("👍",), message="1", ts=NOW - 7200),
        _row(rxledger, ("👍",), message="2", ts=NOW - 3600),
        _row(rxledger, ("👍",), chat="777", message="3", ts=NOW - 3000),
        # Message 2 changed its mind: it counts as 👎 now, not 👍.
        _row(rxledger, ("👎",), message="2", ts=NOW - 600),
        _row(rxledger, ("🔥", "👍"), chat="777", message="4", ts=NOW - 300, custom=("99",)),
        _row(rxledger, ("😂",), message="5", ts=NOW - 200),
        # ...and message 5's reaction was taken back.
        _row(rxledger, (), message="5", ts=NOW - 40),
    ]


def test_summary_counts_each_message_once_with_the_reactions_it_has_now(rxledger):
    lines = rxledger.summarize(_sample(rxledger), NOW, title="last 24 hours").splitlines()
    assert lines[0] == "Reactions, last 24 hours:"
    assert lines[2].split() == ["emoji", "name", "messages", "chats", "last"]
    assert lines[3].split() == ["👍", "thumbs", "up", "sign", "3", "2", "5m", "ago"]
    # Ties: real emoji first, custom emoji last.
    assert lines[4].split() == ["👎", "thumbs", "down", "sign", "1", "1", "10m", "ago"]
    assert lines[5].split() == ["🔥", "fire", "1", "1", "5m", "ago"]
    assert lines[6].split() == ["custom", "custom", "emoji", "1", "1", "5m", "ago"]
    assert lines[7].split() == ["all", "4", "2", "5m", "ago"]
    assert lines[-2:] == ["Each message counts once, with the reactions it has now.",
                          "Reactions were removed again from 1 message(s)."]
    assert "😂" not in "\n".join(lines)


def test_summary_names_line_up_and_numbers_align_right(rxledger):
    lines = rxledger.summarize(_sample(rxledger), NOW, title="x").splitlines()[2:8]
    end = lines[0].index("messages") + len("messages")
    # Every count ends where the "messages" header ends, and every name starts under "name".
    assert all(line[end - 1].isdigit() and line[end] == " " for line in lines[1:]), lines
    assert [line[8:12] for line in lines[1:5]] == ["thum", "thum", "fire", "cust"], lines


def test_summary_when_nothing_or_only_removed(rxledger):
    assert rxledger.summarize([], NOW, title="all recorded") == "Reactions, all recorded: none recorded."
    removed = [_row(rxledger, ("👍",), ts=NOW - 9), _row(rxledger, (), ts=NOW - 5)]
    assert rxledger.summarize(removed, NOW, title="x") == \
        "Reactions, x: none recorded (reactions removed from 1 message(s))."
    only = rxledger.summarize([_row(rxledger)], NOW, title="x").splitlines()
    assert only[-1] == "Each message counts once, with the reactions it has now."


def test_days_newest_first_unknown_last_with_the_days_top_emoji(rxledger):
    rows = [_row(rxledger, ("👎",), message="1", ts=NOW - 86400), _row(rxledger, ("🔥", "👍"), message="2"),
            _row(rxledger, ("👍",), message="3"), {"message": "x", "chat": "c", "emojis": ["🎉"]}]
    out = rxledger.summarize(rows, NOW, title="all recorded", by="day").splitlines()
    assert out[2].split() == ["day", "messages", "chats", "top", "last"]
    assert [line.split()[:4] for line in out[3:6]] == [
        [rxledger.local_day(NOW), "2", "1", "👍"], [rxledger.local_day(NOW - 86400), "1", "1", "👎"],
        ["unknown", "1", "1", "🎉"]]
    assert out[6] == ""
    # A day whose reactions tie is topped by a real emoji, not by "custom".
    tie = [_row(rxledger, (), message="1", custom=("9",)), _row(rxledger, ("👏",), message="2")]
    assert rxledger.summarize(tie, NOW, title="x", by="day").splitlines()[3].split()[3] == "👏"


def test_recent_lists_reaction_changes_newest_first(rxledger):
    rows = _sample(rxledger)
    out = rxledger.recent(rows, NOW, title="all recorded", limit=3).splitlines()
    assert out[0] == "Reaction changes, all recorded, newest 3 of 7:"
    assert out[2].split() == ["when", "reactions", "message", "chat"]
    assert out[3].split() == ["40s", "ago", "(removed)", rows[6]["message"], rows[6]["chat"]]
    assert out[4].split() == ["3m", "ago", "😂", rows[5]["message"], rows[5]["chat"]]
    assert out[5].split() == ["5m", "ago", "🔥", "👍", "custom", rows[4]["message"], rows[4]["chat"]]
    assert rxledger.recent([], NOW, title="x") == "Reactions, x: none recorded."
    assert rxledger.recent([{"ts": NOW}], NOW, title="x").splitlines()[3].split() == ["0s", "ago", "(removed)", "-", "-"]


@pytest.mark.parametrize("word, count", [("1", 1), ("25", 25), ("100", 100), ("0", None), ("101", None),
                                         ("9" * 5000, None), ("-5", None), ("٣", None), ("7d", None)])
def test_parse_count(rxledger, word, count):
    assert rxledger.parse_count(word) == count


@pytest.mark.parametrize("word, seconds", [("6h", 6 * 3600.0), ("7d", 7 * 86400.0), ("0d", None),
                                           ("12345h", None), ("7w", None), ("d", None), ("٣d", None)])
def test_parse_span(rxledger, word, seconds):
    assert rxledger.parse_span(word) == seconds


@pytest.fixture
def ledger_file(reactions_plugin, tmp_path, monkeypatch):
    path = tmp_path / "reactions.jsonl"
    monkeypatch.setattr(reactions_plugin, "_ledger_path", lambda: path)
    return path


def test_hook_records_reactions_and_skips_other_events(reactions_plugin, rxledger, ledger_file):
    assert reactions_plugin._on_platform_event(platform="telegram", event_type="reaction",
                                               payload=_payload(("❤️",))) is None
    reactions_plugin._on_platform_event(platform="telegram", event_type="message_edited",
                                        payload={"chat_id": CHAT, "message_id": "42", "text": "secret"})
    reactions_plugin._on_platform_event(platform="discord", event_type="thread_created", payload={}, extra=1)
    [row] = rxledger.read(ledger_file)
    assert row["emojis"] == ["❤️"] and "secret" not in ledger_file.read_text(encoding="utf-8")
    assert len(ledger_file.read_text(encoding="utf-8").splitlines()) == 1  # nothing written for the others


def test_hook_never_raises(reactions_plugin, monkeypatch):
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(reactions_plugin, "_ledger_path", boom)
    reactions_plugin._on_platform_event(platform="telegram", event_type="reaction", payload=_payload())
    reactions_plugin._on_platform_event()
    reactions_plugin._on_platform_event("telegram", "reaction", {"chat_id": object(), "message_id": object()})


def test_command(reactions_plugin, rxledger, ledger_file):
    now = time.time()
    # Oldest first, as the hook writes them.
    for emojis, message, ts in ((("👎",), "1", now - 3 * 86400), (("👍",), "2", now - 60), (("🔥",), "3", now - 30)):
        rxledger.append(ledger_file, _row(rxledger, emojis, message=message, ts=ts))
    run = reactions_plugin._handle_reaction_log
    out = run("")
    assert out.startswith("Reactions, last 24 hours:") and "👎" not in out
    assert "👎" in run("7d") and run("7d").startswith("Reactions, last 7 days:")
    assert run("all").startswith("Reactions, all recorded:") and "👎" in run("all")
    assert run("all 7d").startswith("Reactions, all recorded:")
    assert run("all days").splitlines()[2].split()[0] == "day"
    assert run("all recent 2").splitlines()[0] == "Reaction changes, all recorded, newest 2 of 3:"
    assert run("all recent").splitlines()[0] == "Reaction changes, all recorded, newest 3 of 3:"
    assert run("recent").splitlines()[0] == "Reaction changes, last 24 hours, newest 2 of 2:"
    assert run("recent 0").startswith("Unknown option: 0")
    assert run("all 5").startswith("Unknown option: 5")
    assert run("session").startswith("Unknown option: session")
    assert run("bogus").startswith("Unknown option: bogus")
    assert run("help").startswith("/reaction-log:")
    assert run("clear") == "reaction-ledger: deleted 3 recorded reaction change(s)."
    assert run("all") == "Reactions, all recorded: none recorded."


def test_register_wires_the_hook_and_the_command(reactions_plugin):
    calls = []

    class Ctx:
        def register_hook(self, name, cb):
            calls.append(("hook", name, cb))

        def register_command(self, name, handler, **kw):
            calls.append(("command", name, handler))

    reactions_plugin.register(Ctx())
    assert calls == [("hook", "gateway_platform_event", reactions_plugin._on_platform_event),
                     ("command", "reaction-log", reactions_plugin._handle_reaction_log)]


def test_help_lines_up(reactions_plugin):
    options = [line for line in reactions_plugin._HELP.splitlines() if line.startswith("  /")]
    assert len(options) == 7 and len({len(line) - len(line[2:].split("  ", 1)[1].lstrip()) for line in options}) == 1
