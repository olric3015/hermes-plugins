"""Render docs/chmod-ref-card.png, the 1200x600 catalog card for chmod-ref.

    uv run --no-project --with "pillow>=10,<12" python docs/make_chmod_ref_card.py

The text on the card is the plugin's real ``@chmod:`` expansion of ``"664 u+x,go-w"``, produced by
importing ``chmod-ref/modes.py``: its first six lines (the heading, the mode before, each clause's
step, the mode after and what was added). Nothing on it depends on the time or the machine.
Layout, colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for
aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "chmod-ref-card.png"
WIDTH = 72  # mono 23 pt characters that fit between the margins
SAMPLE = '"664 u+x,go-w"'


def _modes():
    spec = importlib.util.spec_from_file_location("chmod_ref_card", ROOT / "chmod-ref" / "modes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    out = _modes().expand(SAMPLE).splitlines()
    lines = out[:6]
    assert lines[0] == "Mode 664 with u+x,go-w" and lines[5].startswith("  adds "), out
    return lines


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()
    reference = f"@chmod:{SAMPLE}"

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "chmod-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Lets the model read file modes, chmod changes and umasks exactly.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), reference, font=mono, fill=ACCENT)
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
    print(reference)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
