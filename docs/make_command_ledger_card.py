"""Render docs/command-ledger-card.png, the 1200x600 catalog card for command-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_command_ledger_card.py

The table on the card is the plugin's real ``/command-log`` output for the sample commands
below, produced by importing ``command-ledger/ledger.py``; nothing on the card is typed by hand.
The command names are Hermes's own built-in commands. Layout, colors, fonts and
the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "command-ledger-card.png"
NOW = 1_800_000_000.0


def _ledger():
    spec = importlib.util.spec_from_file_location("command_ledger_card", ROOT / "command-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()

    def rows(command, ages, platform="cli", session="cli-1", alias=""):
        payload = {"command": command, "alias_used": alias or command, "session_key": session,
                   "surface": "cli" if platform == "cli" else "gateway", "platform": platform}
        return [ledger.build_row(payload, now=NOW - age) for age in ages]

    sample = (rows("model", [20000, 14000, 9000, 5200, 2400, 1500, 600])
              + rows("model", [7300, 3100], platform="telegram", session="tg-1")
              + rows("new", [21000, 15500, 8000, 4100], session="cli-2")
              + rows("new", [11000, 2000], platform="telegram", session="tg-2", alias="reset")
              + rows("usage", [16000, 6100, 250], platform="telegram", session="tg-1")
              + rows("compress", [18500], session="cli-3"))
    lines = ledger.summarize(sample, title="last 24 hours", now=NOW).splitlines()[2:]
    return lines[:lines.index("")]  # the table; the one-line footnote under it is wider than the card


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "command-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Which slash commands you run, how often, and when you last did.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/command-log", font=mono, fill=ACCENT)
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
