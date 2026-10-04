"""Render docs/card.png, the 1200x600 catalog card for aux-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_card.py

The table on the card is the plugin's real ``/aux`` output for the sample rows below, produced by
importing ``aux-ledger/ledger.py``; nothing on the card is typed by hand. All ink is asserted to
sit inside rows 132..468, the band every catalog surface shows (the plugin page hero crops a 2:1
image to rows 110..490 at its widest).

Fonts are the docs site's faces, both OFL, fetched from google/fonts at commit
9710da1eacb3be272583c3224dcb70f9da6eadbb and checked by sha256:
  ofl/dmsans/DMSans[opsz,wght].ttf                8cd08d97e89c24d0aa92edd2f0f4c8ee6195eee9b7c9f154865a58b02f0c1c0d
  ofl/jetbrainsmono/JetBrainsMono[wght].ttf       48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda
"""

import hashlib
import importlib.util
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "card.png"
FONTS_COMMIT = "9710da1eacb3be272583c3224dcb70f9da6eadbb"
FONTS = {
    "sans": ("ofl/dmsans/DMSans[opsz,wght].ttf",
             "8cd08d97e89c24d0aa92edd2f0f4c8ee6195eee9b7c9f154865a58b02f0c1c0d"),
    "mono": ("ofl/jetbrainsmono/JetBrainsMono[wght].ttf",
             "48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda"),
}
W, H = 1200, 600
BAND = (132, 468)
MARGIN_X = 88
BG, FG, MUTED, ACCENT, RULE = "#101418", "#e8eaed", "#9aa3ad", "#7fd6a6", "#262d35"


def _font_file(key: str) -> Path:
    rel, digest = FONTS[key]
    cache = Path(tempfile.gettempdir()) / f"aux-ledger-card-{digest[:12]}.ttf"
    if not cache.exists():
        url = f"https://raw.githubusercontent.com/google/fonts/{FONTS_COMMIT}/{urllib.parse.quote(rel)}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            cache.write_bytes(resp.read())
    if hashlib.sha256(cache.read_bytes()).hexdigest() != digest:
        cache.unlink()
        raise SystemExit(f"font checksum mismatch for {rel}")
    return cache


def _font(key: str, size: int, axes: list) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_font_file(key)), size)
    font.set_variation_by_axes(axes)
    return font


def _ledger():
    spec = importlib.util.spec_from_file_location("aux_ledger_card", ROOT / "aux-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample_table() -> list:
    ledger = _ledger()

    def row(task, secs, tokens_in=0, tokens_out=0, error=None):
        usage = None if error else {"input_tokens": tokens_in, "output_tokens": tokens_out}
        return ledger.build_row({"aux_task": task, "api_duration": secs, "usage": usage, "error_type": error})

    rows = [row("compression", 9.4, 41200, 1850), row("compression", 8.1, 38900, 1720),
            row("compression", 30.0, error="APITimeoutError"),
            row("vision", 3.2, 2140, 310), row("title_generation", 0.9, 620, 14),
            row("title_generation", 1.1, 655, 12), row("approval", 0.7, 480, 6)]
    text = ledger.summarize(rows, title="last 24 hours")
    return [line for line in text.splitlines()[2:] if line and not line[0].isdigit()]


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    table = _sample_table()

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "aux-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "What Hermes's background LLM calls cost: titling, compression, vision, approval.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/aux", font=mono, fill=ACCENT)
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
