"""Render docs/jwt-ref-card.png, the 1200x600 catalog card for jwt-ref.

    uv run --no-project --with "pillow>=10,<12" python docs/make_jwt_ref_card.py

The text on the card is the plugin's real ``@jwt:`` expansion of the sample token below,
produced by importing ``jwt-ref/tokens.py``: its first line, the alg row, the sub, iat and exp
rows and the status line, in that order (typ, the Status heading and the Notes are left out for
space). So the card reads the same on every machine, the current time is pinned to
2026-10-21 09:00 UTC. Layout, colors, fonts and the safe band (ink rows 132..468) are the ones
``make_card.py`` uses for aux-ledger.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "jwt-ref-card.png"
NOW = 1792573200  # 2026-10-21 09:00:00 UTC
WIDTH = 72  # mono 23 pt characters that fit between the margins


def _part(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).rstrip(b"=").decode("ascii")


SAMPLE = ".".join((
    _part({"alg": "RS256", "typ": "JWT"}),
    _part({"sub": "user-4821", "iat": NOW - 3 * 3600, "exp": NOW - 2 * 3600}),
    "c2lnbmF0dXJl",
))


def _tokens():
    spec = importlib.util.spec_from_file_location("jwt_ref_card", ROOT / "jwt-ref" / "tokens.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lines() -> list:
    out = _tokens().expand(SAMPLE, now=NOW).splitlines()
    wanted = ("  alg ", "  sub ", "  iat ", "  exp ", "  EXPIRED ")
    rows = [line for line in out[1:] if line.startswith(wanted)]
    assert len(rows) == len(wanted), out
    return [out[0], *rows]


def main() -> None:
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    lines = _lines()
    reference = f"@jwt:{SAMPLE}"
    reference = reference[:WIDTH - 3] + "..."

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "jwt-ref", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Lets the model read a token instead of guessing at it.", font=tagline, fill=MUTED)
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
