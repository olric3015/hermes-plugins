"""unicode-ref: what an @unicode: reference expands to, and the suggestions it offers."""

import asyncio
import sys
import types

import pytest

ZWSP, RLO, NBSP, CYR_ER = "\u200b", "\u202e", "\u00a0", "\u0440"


def _table(text):
    """The table rows of a breakdown, split on runs of two spaces."""
    lines = text.splitlines()
    end = lines.index("", 2)
    return [line.split("  ") for line in lines[2:end]]


def test_breakdown_lists_every_character_and_flags_the_hidden_ones(uchars):
    out = uchars.expand(f"pa{ZWSP}y {CYR_ER}ay")
    lines = out.splitlines()
    assert lines[0] == "Unicode breakdown of the quoted text: 8 character(s), 11 UTF-8 byte(s)."
    assert lines[2].split() == ["#", "char", "code", "name", "cat"]
    assert lines[3].split() == ["1", "p", "U+0070", "LATIN", "SMALL", "LETTER", "P", "Ll"]
    assert lines[5].split() == ["3", "U+200B", "ZERO", "WIDTH", "SPACE", "Cf", "invisible"]
    assert lines[7].split() == ["5", "U+0020", "SPACE", "Zs"]
    assert lines[8].split() == ["6", CYR_ER, "U+0440", "CYRILLIC", "SMALL", "LETTER", "ER", "Ll"]
    assert lines[11:] == [
        "",
        "Notes:",
        "- 1 invisible character(s): U+200B ZERO WIDTH SPACE at 3.",
        f"- The word '{CYR_ER}ay' mixes CYRILLIC and LATIN letters: {CYR_ER} U+0440 CYRILLIC SMALL LETTER ER at 6.",
    ]


def test_columns_line_up(uchars):
    lines = uchars.expand(f"a{ZWSP}\U0001F44D{NBSP}b").splitlines()[2:8]
    code_at = {line.index("U+") for line in lines[1:]}
    assert len(code_at) == 1 and lines[0].index("code") in code_at, lines


def test_plain_text_says_nothing_is_hidden(uchars):
    out = uchars.expand("hello")
    assert out.splitlines()[-1] == ("Nothing hidden: no invisible characters, direction controls, unusual spaces "
                                    "or lookalike letters.")
    assert "Notes:" not in out


def test_direction_controls_spaces_and_their_positions(uchars):
    notes = uchars.notes(f"ab{RLO}cd{NBSP}e{NBSP}f\u2066x\u2069")
    assert notes[0] == ("3 invisible character(s): U+202E RIGHT-TO-LEFT OVERRIDE at 3; "
                        "U+2066 LEFT-TO-RIGHT ISOLATE at 10; U+2069 POP DIRECTIONAL ISOLATE at 12.")
    assert notes[1].startswith("Text direction controls (U+202E, U+2066, U+2069) change the order")
    assert notes[2] == "Spaces other than the ordinary one: U+00A0 NO-BREAK SPACE at 6, 8."


def test_positions_are_capped_in_a_note(uchars):
    [note] = uchars.notes(ZWSP * 13)
    assert note == ("13 invisible character(s): U+200B ZERO WIDTH SPACE at 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 "
                    "and 3 more.")


def test_tag_characters_are_decoded(uchars):
    hidden = "".join(chr(0xE0000 + ord(c)) for c in "rm -rf")
    notes = uchars.notes("ok" + hidden + "\U000E007F")
    assert notes == ["7 Unicode tag character(s), which can carry hidden ASCII text; they spell 'rm -rf'."]


def test_emoji_joiners_are_explained_not_alarming(uchars):
    notes = uchars.notes("\U0001F468\u200d\U0001F4BB \u2764\ufe0f")
    assert notes[0] == ("2 invisible character(s): U+200D ZERO WIDTH JOINER at 2; "
                        "U+FE0F VARIATION SELECTOR-16 at 6.")
    assert notes[1].startswith("U+200D and U+FE0F are also how emoji are joined")


@pytest.mark.parametrize("word, flagged", [
    ("p\u0430ypal", True),          # Cyrillic a
    ("\u03bfpen", True),            # Greek omicron
    ("\uff41pple", True),           # fullwidth a
    ("caf\u00e9", False),           # e with acute is Latin
    ("\u043f\u0440\u0438\u0432\u0435\u0442", False),  # all Cyrillic
    ("abc\u4e2d\u6587", False),     # CJK next to Latin is not a lookalike
])
def test_mixed_scripts_only_for_lookalikes_next_to_latin(uchars, word, flagged):
    assert any("mixes" in n for n in uchars.notes(word)) is flagged


def test_mixed_script_note_lists_at_most_five_letters(uchars):
    [note] = uchars.notes("a" + "\u0430" * 7)
    assert note.count("U+0430") == 5 and note.endswith(" and more.")


def test_private_use_and_unassigned_are_noted(uchars):
    notes = uchars.notes("\ue000\U000E01F0")
    assert notes[-2:] == ["U+E000 is private use at 1: no font shows it the same way.",
                          "U+E01F0 is unassigned at 2: no font shows it the same way."]


def test_long_text_is_listed_up_to_the_cap_but_noted_in_full(uchars, monkeypatch):
    monkeypatch.setattr(uchars, "MAX_LISTED", 3)
    out = uchars.expand("abcde" + ZWSP)
    rows = _table(out)
    assert len(rows) == 5 and rows[-1] == ["... 3 more character(s) not listed; the notes cover them."]
    assert "U+200B ZERO WIDTH SPACE at 6" in out
    monkeypatch.setattr(uchars, "MAX_TEXT", 4)
    head = uchars.expand("abcde" + ZWSP).splitlines()[0]
    assert head == ("Unicode breakdown of the quoted text: 4 character(s), 4 UTF-8 byte(s) "
                    "(only the first 4 characters were read).")


@pytest.mark.parametrize("cp, name", [(0x41, "LATIN CAPITAL LETTER A"), (0x09, "CHARACTER TABULATION"),
                                      (0x01, "<control>"), (0xD800, "<surrogate>"), (0xE000, "<private use>"),
                                      (0x0378, "<unassigned>")])
def test_names_for_every_kind_of_code_point(uchars, cp, name):
    assert uchars.name(cp) == name


@pytest.mark.parametrize("cp, invisible", [(0x200B, True), (0x0009, True), (0x2028, True), (0xFE0F, True),
                                           (0xE0100, True), (0x3164, True), (0x034F, True), (0x180B, True),
                                           (0x0020, False), (0x00A0, False), (0x0301, False), (0x0041, False)])
def test_is_invisible(uchars, cp, invisible):
    assert uchars.is_invisible(cp) is invisible


def test_combining_marks_and_spaces_are_not_drawn_alone(uchars):
    rows = _table(uchars.expand("e\u0301 \t"))
    assert [r[1].strip() for r in rows[1:]] == ["e", "", "", ""]
    assert rows[4][-1].strip() == "invisible" and rows[2][-1].strip() == "Mn"


def test_code_point_lookup(uchars):
    out = uchars.expand("U+202E, u+e9")
    assert out.split("\n\n") == [
        "U+202E  RIGHT-TO-LEFT OVERRIDE\n  category Cf (format), invisible, changes text direction\n  UTF-8 E2 80 AE",
        "U+00E9  LATIN SMALL LETTER E WITH ACUTE\n  category Ll (lowercase letter)\n  UTF-8 C3 A9\n"
        "  looks like: \u00e9\n  decomposes to: 0065 0301",
    ]
    assert uchars.expand("U+110000") == "U+110000 is not a Unicode code point (the last one is U+10FFFF)."
    assert uchars.expand("U+D800").splitlines()[2] == "  UTF-8 ED A0 80"


@pytest.mark.parametrize("target, points", [("U+41", [0x41]), (" U+41 U+42 ", [0x41, 0x42]), ("U+41,U+42", [0x41, 0x42]),
                                            ("U+41 x", None), ("200B", None), ("U+1234567", None), ("U+", None)])
def test_code_points_only_for_lists_of_u_plus_values(uchars, target, points):
    assert uchars.code_points(target) == points


def test_text_that_merely_contains_a_code_point_is_broken_down(uchars):
    assert uchars.expand("see U+200B").startswith("Unicode breakdown of the quoted text: 10 character(s)")


def test_limits_raise_with_a_clear_message(uchars):
    with pytest.raises(ValueError, match="at most 20"):
        uchars.expand(" ".join(["U+41"] * 21))
    assert len(uchars.expand(" ".join(["U+41"] * 20)).split("\n\n")) == 20
    with pytest.raises(ValueError, match="put the text in quotes"):
        uchars.expand("")


def test_search_by_name_words(uchars):
    hits = uchars.search("zero width", 10)
    assert hits[0] == (0x200B, "ZERO WIDTH SPACE")
    assert all(name.startswith("ZERO WIDTH") for _, name in hits)  # not FULLWIDTH DIGIT ZERO
    assert uchars.search("no-break", 3)[0] == (0x00A0, "NO-BREAK SPACE")
    assert uchars.search("latin small letter a", 1) == [(0x61, "LATIN SMALL LETTER A")]
    assert (0x2192, "RIGHTWARDS ARROW") in uchars.search("rightwards arrow", 5)
    assert uchars.search("thumbs up", 2)[0] == (0x1F44D, "THUMBS UP SIGN")


def test_search_by_code_point_and_short_queries(uchars):
    assert uchars.search("u+202e") == [(0x202E, "RIGHT-TO-LEFT OVERRIDE")]
    assert uchars.search("U+0001") == [(0x1, "<control>")]
    assert uchars.search("U+110000") == []
    assert uchars.search("a") == [] and uchars.search("   ") == [] and uchars.search("zero", 0) == []
    assert len(uchars.search("arrow", 7)) == 7


@pytest.fixture
def hermes_api(monkeypatch):
    """Stand-ins for the two classes the plugin takes from agent.context_references."""
    module = types.ModuleType("agent.context_references")

    class ContextCompletionItem:
        def __init__(self, text, display="", meta=""):
            self.text, self.display, self.meta = text, display or text, meta

    class ContextReferenceProvider:
        prefix = ""
        description = ""

    module.ContextCompletionItem = ContextCompletionItem
    module.ContextReferenceProvider = ContextReferenceProvider
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.context_references", module)
    return module


def test_provider_expands_and_suggests(unicode_plugin, hermes_api):
    provider = unicode_plugin.make_provider()
    assert isinstance(provider, hermes_api.ContextReferenceProvider)
    assert provider.prefix == "unicode" and provider.description
    out = asyncio.run(provider.expand(f"a{ZWSP}b"))
    assert "U+200B ZERO WIDTH SPACE at 2" in out
    items = asyncio.run(provider.autocomplete("zero width", limit=2))
    assert [(i.text, i.display, i.meta) for i in items] == [
        ("U+200B", "U+200B ZERO WIDTH SPACE", "Cf"), ("U+200D", "U+200D ZERO WIDTH JOINER", "Cf")]


def test_register_registers_one_context_reference(unicode_plugin, hermes_api):
    calls = []

    class Ctx:
        def register_context_reference(self, provider):
            calls.append(provider)

    unicode_plugin.register(Ctx())
    assert len(calls) == 1 and calls[0].prefix == "unicode"
