"""Render docs/approval-ledger-card.png, the 1200x600 catalog card for approval-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_approval_ledger_card.py

The table on the card is the plugin's real ``/approval-log`` output for the sample decisions
below, produced by importing ``approval-ledger/ledger.py``; nothing on the card is typed by hand.
The rule names are labels Hermes's dangerous-command check really uses. Layout, colors, fonts and
the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "approval-ledger-card.png"
NOW = 1_800_000_000.0


def _ledger():
    spec = importlib.util.spec_from_file_location("approval_ledger_card", ROOT / "approval-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()

    def rows(pattern, choice, waits, surface="cli", decided_by=""):
        payload = {"pattern_key": pattern, "choice": choice, "surface": surface, "decided_by": decided_by}
        return [ledger.build_row(payload, now=NOW, wait=wait) for wait in waits]

    sample = (rows("recursive delete", "once", [3.1, 4.2, 4.8, 6.0, 8.5, 9.4])
              + rows("recursive delete", "session", [2.2, 3.4]) + rows("recursive delete", "deny", [11.0, 14.6])
              + rows("recursive delete", "timeout", [None, None], surface="gateway")
              + rows("git branch force delete", "deny", [6.3, 7.1, 20.4])
              + rows("git branch force delete", "once", [12.0])
              + rows("git branch force delete", "cancelled", [None])
              + rows("delete in root path", "smart_deny", [0.9, 1.1], surface="smart", decided_by="aux_llm")
              + rows("delete in root path", "deny", [31.0])
              + rows("kill all processes", "deny", [5.2]))
    lines = ledger.summarize(sample, title="last 24 hours").splitlines()[2:]
    return lines[:lines.index("")]  # the table; the one-line footnote under it is wider than the card


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "approval-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Which rules ask for approval, how the prompts end, how long you take.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/approval-log", font=mono, fill=ACCENT)
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
