"""Render docs/crontab-ref-card.png, the 1200x600 catalog card for crontab-ref.

    uv run --no-project --with "pillow>=10,<12" --with "croniter>=6,<7" python docs/make_crontab_ref_card.py

The text on the card is the plugin's real ``@crontab:`` expansion of ``0 9 1 * 1``, produced by
importing ``crontab-ref/crons.py``: the summary line, the Hermes /cron line, the "Next runs"
heading and the first three runs, in that order (the field rows and the rest are left out for
space). So the card reads the same on every machine, the current time is pinned to
2026-10-24 12:00 at UTC+03:00 and labelled as an Europe/Istanbul timezone setting. Layout,
colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for
aux-ledger.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "crontab-ref-card.png"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone(timedelta(hours=3)))
LABEL = "Europe/Istanbul (Hermes's timezone setting)"
EXPRESSION = "0 9 1 * 1"
WIDTH = 72  # mono 23 pt characters that fit between the margins


def _crons():
    spec = importlib.util.spec_from_file_location("crontab_ref_card", ROOT / "crontab-ref" / "crons.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    out = _crons().expand(f'"{EXPRESSION}"', now=NOW, label=LABEL).splitlines()
    heading = next(i for i, line in enumerate(out) if line.startswith("Next "))
    verdict = next(line for line in out if line.startswith("Hermes /cron: "))
    lines = [out[1].strip(), verdict, out[heading], *out[heading + 1:heading + 4]]
    assert lines[0] == "At 09:00 on day 1 of the month or on Monday.", lines
    return lines


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()
    reference = f'@crontab:"{EXPRESSION}"'

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "crontab-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Reads a cron expression the way Hermes's scheduler does.", font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), reference, font=mono, fill=ACCENT)
    for i, line in enumerate(lines):
        y += 29
        assert len(line) <= WIDTH, line
        draw.text((MARGIN_X, y), line, font=mono, fill=FG if i == 0 else MUTED if i < 3 else FG)

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
