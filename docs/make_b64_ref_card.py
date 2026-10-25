"""Render docs/b64-ref-card.png, the 1200x600 catalog card for b64-ref.

    uv run --no-project --python 3.12 --with "pillow>=10,<12" python docs/make_b64_ref_card.py

The text on the card is the plugin's real ``@b64:`` expansion of the sample value below (a gzip
of a small JSON object, base64-encoded), produced by importing ``b64-ref/blobs.py``: every line
of it except the "JSON" heading, in order. gzip is written with mtime=0, but its header and
deflate stream still differ a little between Python versions, so the card is made with 3.12.
Layout, colors, fonts and the safe band (ink rows 132..468) are the ones ``make_card.py`` uses
for aux-ledger.
"""

import base64
import gzip
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "b64-ref-card.png"
WIDTH = 72  # mono 23 pt characters that fit between the margins

SAMPLE = base64.b64encode(gzip.compress(json.dumps({"event": "deploy failed"}).encode(), mtime=0)).decode("ascii")


def _blobs():
    spec = importlib.util.spec_from_file_location("b64_ref_card", ROOT / "b64-ref" / "blobs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    out = _blobs().expand(SAMPLE).splitlines()
    lines = [line for line in out if line != "JSON"]
    assert len(lines) == 6 and lines[2] == "Inflated: JSON object, 1 key", out
    return lines


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()
    reference = f"@b64:{SAMPLE}"
    if len(reference) > WIDTH:
        reference = reference[:WIDTH - 3] + "..."

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "b64-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Shows the model what base64, hex and gzip really hold.", font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), reference, font=mono, fill=ACCENT)
    for i, line in enumerate(lines):
        y += 29
        assert len(line) <= WIDTH, line
        draw.text((MARGIN_X, y), line, font=mono, fill=MUTED if i == 0 else FG)

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
