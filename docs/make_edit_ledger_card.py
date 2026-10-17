"""Render docs/edit-ledger-card.png, the 1200x600 catalog card for edit-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_edit_ledger_card.py

The table on the card is the plugin's real ``/edit-log`` output for the sample turns below,
produced by importing ``edit-ledger/ledger.py``; nothing on the card is typed by hand. Layout,
colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses for
aux-ledger.
"""

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "edit-ledger-card.png"
NOW = 1_800_000_000.0


def _ledger():
    spec = importlib.util.spec_from_file_location("edit_ledger_card", ROOT / "edit-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()
    sample = []

    def turn(model, session, files, minutes_ago):
        sample.append(ledger.build_row({"session_id": session, "platform": "cli", "model": model,
                                        "attempt": 0, "changed_paths": [f"src/{f}" for f in files]},
                                       now=NOW - minutes_ago * 60))

    turn("model-large", "s1", ["api.py", "models.py", "test_api.py", "README.md"], 600)
    turn("model-large", "s1", ["api.py", "test_api.py"], 540)
    turn("model-large", "s2", ["views.py", "urls.py", "test_views.py"], 200)
    turn("model-large", "s2", ["views.py"], 9)
    turn("model-fast", "s3", ["App.tsx", "App.css"], 720)
    turn("model-fast", "s3", ["App.tsx"], 300)
    turn("model-fast", "s4", ["Nav.tsx", "Nav.css", "index.ts"], 48)
    turn("model-small", "s5", ["CHANGELOG.md"], 900)
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
    draw.text((MARGIN_X, 118), "edit-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Which models change your files, how many per turn, and of what kind.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/edit-log", font=mono, fill=ACCENT)
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
