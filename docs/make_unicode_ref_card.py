"""Render docs/unicode-ref-card.png, the 1200x600 catalog card for unicode-ref.

    uv run --no-project --with "pillow>=10,<12" python docs/make_unicode_ref_card.py

The text on the card is the plugin's real ``@unicode:`` expansion of the sample below, produced
by importing ``unicode-ref/chars.py``: its first line, then its notes wrapped to the card's width.
Nothing on the card is typed by hand. The sample hides a zero-width space and uses a Cyrillic
a (U+0430) for the first Latin "a"; the reference line on the card is drawn without the zero-width
space, which is how it looks on screen. Layout, colors, fonts and the safe band (ink rows
132..468) are the ones ``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "unicode-ref-card.png"
SAMPLE = "p\u0430ypal\u200b.com"
WIDTH = 72  # mono 23 pt characters that fit between the margins


def _chars():
    spec = importlib.util.spec_from_file_location("unicode_ref_card", ROOT / "unicode-ref" / "chars.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    out = _chars().expand(SAMPLE).splitlines()
    notes = out[out.index("Notes:") + 1:]
    lines = [out[0], "", "Notes:"]
    for note in notes:
        lines += textwrap.wrap(note, WIDTH, subsequent_indent="  ")
    return lines


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "unicode-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Shows the model the characters you cannot see.", font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), f'@unicode:"{SAMPLE.replace(chr(0x200B), "")}"', font=mono, fill=ACCENT)
    for i, line in enumerate(lines):
        y += 29
        assert len(line) <= WIDTH, line
        draw.text((MARGIN_X, y), line, font=mono, fill=MUTED if i == 0 else FG)

    left, top, right, bottom = ink.getbbox()
    assert BAND[0] <= top and bottom <= BAND[1], f"ink rows {top}..{bottom} leave the safe band {BAND}"
    assert MARGIN_X - 4 <= left and right <= W - MARGIN_X, f"ink columns {left}..{right} leave the margins"

    card = Image.new("RGBA", (W, H), BG)
    ImageDraw.Draw(card).rectangle((MARGIN_X, 244, W - MARGIN_X, 245), fill=RULE)
    card.alpha_composite(ink)
    card.convert("RGB").save(OUT, optimize=True)
    print(f"wrote {OUT} ink rows {top}..{bottom} columns {left}..{right}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
