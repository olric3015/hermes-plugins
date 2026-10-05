"""Render docs/error-ledger-card.png, the 1200x600 catalog card for error-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_error_ledger_card.py

The table on the card is the plugin's real ``/errors`` output for the sample failures below,
produced by importing ``error-ledger/ledger.py``; nothing on the card is typed by hand. Layout,
colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for
aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "error-ledger-card.png"


def _ledger():
    spec = importlib.util.spec_from_file_location("error_ledger_card", ROOT / "error-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()

    def rows(model, reason, status, count, retryable=True):
        payload = {"model": model, "provider": "custom", "reason": reason, "status_code": status,
                   "retryable": retryable, "error": {"type": "APIError"}}
        return [ledger.build_row(payload) for _ in range(count)]

    sample = (rows("model-large", "rate_limit", 429, 9) + rows("model-large", "overloaded", 529, 2)
              + rows("model-large", "context_overflow", 400, 1, retryable=False)
              + rows("model-small", "timeout", None, 3) + rows("model-small", "server_error", 500, 1)
              + rows("local-8b", "context_overflow", 400, 2, retryable=False))
    lines = ledger.summarize(sample, title="last 24 hours").splitlines()[2:]
    return lines[:lines.index("")]  # the table; the one-line footnote under it is wider than the card


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "error-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Which provider calls failed, and why: rate limits, overloads, timeouts.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/errors", font=mono, fill=ACCENT)
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
