"""Render docs/stream-speed-card.png, the 1200x600 catalog card for stream-speed.

    uv run --no-project --with "pillow>=10,<12" python docs/make_stream_speed_card.py

The table on the card is the plugin's real ``/speed`` output for the sample rows below, produced
by importing ``stream-speed/timing.py``; nothing on the card is typed by hand. Layout, colors,
fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "stream-speed-card.png"


def _timing():
    spec = importlib.util.spec_from_file_location("stream_speed_card", ROOT / "stream-speed" / "timing.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    timing = _timing()

    def row(model, ttft, duration, chars, failed=False):
        return {"ts": 0.0, "model": model, "ttft_s": ttft, "duration_s": duration, "chars": chars,
                "finished": not failed, "failed": failed}

    rows = [row("model-small", 0.38, 4.1, 1460), row("model-small", 0.44, 6.0, 2130),
            row("model-small", 0.41, 3.2, 1050), row("model-small", 0.97, 5.5, 1690),
            row("model-large", 1.62, 14.8, 2240), row("model-large", 2.05, 11.3, 1580),
            row("model-large", None, 30.0, 0, failed=True),
            row("local-8b", 0.21, 19.4, 1120), row("local-8b", 0.26, 24.9, 1410)]
    return [line for line in timing.summarize(rows, title="last 24 hours").splitlines()[2:] if line]


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "stream-speed", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "How fast each model starts answering, and how fast it writes.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/speed", font=mono, fill=ACCENT)
    for i, line in enumerate(table):
        y += 29
        last = i == len(table) - 1  # the plugin's own footnote about text-less streams
        y += 12 if last else 0
        draw.text((MARGIN_X, y), line, font=mono, fill=FG if i and not last else MUTED)

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
