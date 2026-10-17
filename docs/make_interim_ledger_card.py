"""Render docs/interim-ledger-card.png, the 1200x600 catalog card for interim-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_interim_ledger_card.py

The table on the card is the plugin's real ``/interim-log`` output for the sample messages below,
produced by importing ``interim-ledger/ledger.py``; nothing on the card is typed by hand. Layout,
colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for
aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "interim-ledger-card.png"
NOW = 1_800_000_000.0


def _ledger():
    spec = importlib.util.spec_from_file_location("interim_ledger_card", ROOT / "interim-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()
    sample = []

    def turn(model, session, number, lengths, minutes_ago):
        for step, length in enumerate(lengths, 1):
            sample.append(ledger.build_row({
                "text": "x" * length, "turn_id": f"{session}:turn-{number}", "iteration": step,
                "session_id": session, "model": model, "provider": "openrouter", "surface": "telegram",
            }, now=NOW - minutes_ago * 60 + step))

    turn("model-chatty", "s1", 1, [64, 88, 71], 600)
    turn("model-chatty", "s1", 2, [52, 95, 80, 66], 420)
    turn("model-chatty", "s2", 1, [77, 59], 130)
    turn("model-chatty", "s2", 2, [90, 61, 73], 6)
    turn("model-large", "s3", 1, [142], 900)
    turn("model-large", "s3", 2, [118, 131], 310)
    turn("model-large", "s4", 1, [126], 45)
    turn("model-small", "s5", 1, [38], 700)
    sample.sort(key=lambda r: r["ts"])
    lines = ledger.summarize(sample, NOW, title="last 24 hours").splitlines()[2:]
    return lines[:lines.index("")]  # the table; the footnote under it is drawn on its own


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "interim-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Which models chatter mid-turn before they answer, and how much.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/interim-log", font=mono, fill=ACCENT)
    for i, line in enumerate(table):
        y += 29
        last = i == len(table) - 1
        draw.text((MARGIN_X, y), line, font=mono, fill=FG if i and not last else MUTED if not i else ACCENT)

    left, top, right, bottom = ink.getbbox()
    assert BAND[0] <= top and bottom <= BAND[1], f"ink rows {top}..{bottom} leave the safe band {BAND}"
    assert MARGIN_X - 4 <= left and right <= W - MARGIN_X, f"ink columns {left}..{right} leave the margins"

    card = Image.new("RGBA", (W, H), BG)
    ImageDraw.Draw(card).rectangle((MARGIN_X, 244, W - MARGIN_X, 245), fill=RULE)
    card.alpha_composite(ink)
    card.convert("RGB").save(OUT, optimize=True)
    print(f"wrote {OUT} ink rows {top}..{bottom} columns {left}..{right}")
    print("\n".join(table))


if __name__ == "__main__":
    main()
