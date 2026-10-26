"""Render docs/uuid-ref-card.png, the 1200x600 catalog card for uuid-ref.

    uv run --no-project --with "pillow>=10,<12" python docs/make_uuid_ref_card.py

The text on the card is the plugin's real ``@uuid:`` expansion of the RFC 9562 version 7 test
vector, produced by importing ``uuid-ref/ids.py``: its first line, the version, created, clock and
random rows and the sorting note, in that order (the variant row and the Notes heading are left
out for space). So the card reads the same on every machine, the current time is pinned to
2026-10-26 09:00 UTC. Layout, colors, fonts and the safe band (ink rows 132..468) are the ones
``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "uuid-ref-card.png"
NOW = 1793005200  # 2026-10-26 09:00:00 UTC
WIDTH = 72  # mono 23 pt characters that fit between the margins
SAMPLE = "017f22e2-79b0-7cc3-98c4-dc0c0c07398f"  # RFC 9562 Appendix A.6


def _ids():
    spec = importlib.util.spec_from_file_location("uuid_ref_card", ROOT / "uuid-ref" / "ids.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    out = _ids().expand(SAMPLE, now=NOW).splitlines()
    wanted = ("  version ", "  created ", "  clock ", "  random ", "  - sorts ")
    rows = [line for line in out[1:] if line.startswith(wanted)]
    assert len(rows) == len(wanted), out
    return [out[0], *rows]


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()
    reference = f"@uuid:{SAMPLE}"

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "uuid-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Lets the model read when an ID was made, and by what.", font=tagline, fill=MUTED)
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
