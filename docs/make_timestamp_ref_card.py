"""Render docs/timestamp-ref-card.png, the 1200x600 catalog card for timestamp-ref.

    uv run --no-project --with "pillow>=10,<12" python docs/make_timestamp_ref_card.py

The text on the card is the plugin's real ``@ts:`` expansion of the sample below, produced by
importing ``timestamp-ref/stamps.py``: its first line and the UTC, local, ISO 8601, relative and
Unix rows, in that order (the note and calendar rows are left out for space). So the card reads
the same on every machine, the current time is pinned to 2026-10-21 09:00 UTC and the local time
zone to UTC+3. Layout, colors, fonts and the safe band (ink rows 132..468) are the ones
``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
from datetime import timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "timestamp-ref-card.png"
SAMPLE = "1729468800123"
NOW = 1792573200.0  # 2026-10-21 09:00:00 UTC
LOCAL = timezone(timedelta(hours=3))
ROWS = ("UTC", "local", "ISO 8601", "relative", "Unix")
WIDTH = 72  # mono 23 pt characters that fit between the margins


def _stamps():
    spec = importlib.util.spec_from_file_location("timestamp_ref_card", ROOT / "timestamp-ref" / "stamps.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    stamps = _stamps()
    stamps._local = lambda moment: moment.astimezone(LOCAL)
    out = stamps.expand(SAMPLE, now=NOW).splitlines()
    rows = [line for line in out[1:] if line.split("  ")[1].strip() in ROWS]
    assert len(rows) == len(ROWS), out
    return [out[0], *rows]


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "timestamp-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Gives the model exact times instead of guesses.", font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), f"@ts:{SAMPLE}", font=mono, fill=ACCENT)
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
