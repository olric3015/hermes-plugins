"""Render docs/wait-ledger-card.png, the 1200x600 catalog card for wait-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_wait_ledger_card.py

The table on the card is the plugin's real ``/waits`` output for the sample waits below,
produced by importing ``wait-ledger/ledger.py``; nothing on the card is typed by hand. The kinds
and outcomes are the ones Hermes's human-input hooks really report. Layout, colors, fonts and
the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "wait-ledger-card.png"
NOW = 1_800_000_000.0


def _ledger():
    spec = importlib.util.spec_from_file_location("wait_ledger_card", ROOT / "wait-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()

    def rows(kind, outcome, waits):
        payload = {"kind": kind, "outcome": outcome, "platform": "cli"}
        return [ledger.build_row(payload, now=NOW, wait=wait) for wait in waits]

    sample = (rows("approval", "once", [3.1, 4.2, 4.8, 6.0, 8.5, 9.4, 12.0])
              + rows("approval", "deny", [11.0, 14.6]) + rows("approval", "timeout", [300.0])
              + rows("clarify", "submitted", [41.0, 95.0, 128.0, 380.0])
              + rows("clarify", "timed_out", [600.0, 600.0])
              + rows("sudo", "provided", [7.5, 9.1]) + rows("sudo", "skipped", [2.4]))
    lines = ledger.summarize(sample, title="last 24 hours").splitlines()[2:]
    return lines[:lines.index("")]  # the table; the footnote under it is wider than the card


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "wait-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "How often and how long Hermes stood still, waiting for you.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/waits", font=mono, fill=ACCENT)
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
