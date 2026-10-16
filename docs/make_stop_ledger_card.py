"""Render docs/stop-ledger-card.png, the 1200x600 catalog card for stop-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_stop_ledger_card.py

The table on the card is the plugin's real ``/stop-log`` output for the sample stops below,
produced by importing ``stop-ledger/ledger.py``; nothing on the card is typed by hand. The
platforms and labels are the ones Hermes's ``agent_loop_stopped`` hook really reports. Layout,
colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for
aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "stop-ledger-card.png"
NOW = 1_800_000_000.0


def _ledger():
    spec = importlib.util.spec_from_file_location("stop_ledger_card", ROOT / "stop-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()
    stop = ("Stop requested", "stop_command")
    new = ("Session reset requested", "new_command")
    tui = ("user_stop", "session_interrupt")

    def rows(platform, labels, chat, minutes_ago):
        return [ledger.build_row({"session_key": f"agent:main:{platform}:dm:{chat}", "platform": platform,
                                  "reason": labels[0], "invalidation_reason": labels[1]},
                                 now=NOW - minutes * 60) for minutes in minutes_ago]

    sample = (rows("telegram", stop, "a", [700, 640, 300]) + rows("telegram", new, "a", [290])
              + rows("telegram", stop, "b", [42, 12]) + rows("discord", stop, "c", [500])
              + rows("discord", new, "c", [480, 95]) + rows("tui", tui, "d", [1100, 260, 3]))
    sample.sort(key=lambda r: r["ts"])
    lines = ledger.summarize(sample, NOW, title="last 24 hours").splitlines()[2:]
    return lines[:lines.index("")]  # the table; the footnote under it is wider than the card


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "stop-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "How often you had to stop Hermes mid-turn, and where.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/stop-log", font=mono, fill=ACCENT)
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
