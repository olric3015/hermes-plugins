"""Render docs/reaction-ledger-card.png, the 1200x600 catalog card for reaction-ledger.

    uv run --no-project --with "pillow>=10,<12" python docs/make_reaction_ledger_card.py

The table on the card is the plugin's real ``/reaction-log`` output for the sample reactions
below, produced by importing ``reaction-ledger/ledger.py``; nothing on the card is typed by hand.
Layout, colors, text fonts and the safe band (ink rows 132..468) are the ones ``make_card.py``
uses for aux-ledger.

JetBrains Mono has no emoji, so each emoji in the table is drawn from Noto Color Emoji (OFL),
fetched from googlefonts/noto-emoji at tag v2.048 (commit
b3e3051a088047d19fd4d49b1c3ac42fb8c3aaf8) and checked by sha256:
  fonts/NotoColorEmoji.ttf   3ed77810c203e1a67735dc19d395f32c23f2d7c0c3696690f4f78e15e57ab816
It is a bitmap font that only renders at 109 px, so each emoji is drawn at that size and scaled
down into the text cell it sits in.
"""

import hashlib
import importlib.util
import sys
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_card import ACCENT, BAND, BG, FG, H, MARGIN_X, MUTED, ROOT, RULE, W, _font  # noqa: E402

OUT = ROOT / "docs" / "reaction-ledger-card.png"
NOW = 1_800_000_000.0
EMOJI_URL = ("https://raw.githubusercontent.com/googlefonts/noto-emoji/"
             "b3e3051a088047d19fd4d49b1c3ac42fb8c3aaf8/fonts/NotoColorEmoji.ttf")
EMOJI_SHA256 = "3ed77810c203e1a67735dc19d395f32c23f2d7c0c3696690f4f78e15e57ab816"
EMOJI_PX = 26


def _ledger():
    spec = importlib.util.spec_from_file_location("reaction_ledger_card", ROOT / "reaction-ledger" / "ledger.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _emoji_font() -> ImageFont.FreeTypeFont:
    cache = Path(tempfile.gettempdir()) / f"noto-color-emoji-{EMOJI_SHA256[:12]}.ttf"
    if not cache.exists():
        with urllib.request.urlopen(EMOJI_URL, timeout=120) as resp:
            cache.write_bytes(resp.read())
    if hashlib.sha256(cache.read_bytes()).hexdigest() != EMOJI_SHA256:
        cache.unlink()
        raise SystemExit("emoji font checksum mismatch")
    return ImageFont.truetype(str(cache), 109)


def _emoji(font: ImageFont.FreeTypeFont, text: str) -> Image.Image:
    glyph = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(glyph).text((10, 10), text, font=font, embedded_color=True)
    glyph = glyph.crop(glyph.getbbox())
    scale = EMOJI_PX / max(glyph.size)
    return glyph.resize((round(glyph.width * scale), round(glyph.height * scale)), Image.LANCZOS)


def _sample_table(ledger) -> list:
    reactions = [  # (minutes ago, chat, message, emoji): oldest first, as the hook writes them
        (1300, 1, 10, "👍"), (1180, 1, 11, "👍"), (1100, 2, 20, "❤️"), (960, 1, 12, "👎"),
        (900, 1, 12, "👍"), (720, 3, 30, "👍"), (610, 2, 21, "🔥"), (540, 1, 13, "👎"),
        (400, 3, 31, "👍"), (330, 2, 22, "👍"), (300, 2, 23, "❤️"), (180, 1, 14, "👍"),
        (125, 3, 32, "🔥"), (95, 1, 15, "👎"), (40, 2, 24, "👍"), (16, 3, 33, "❤️"), (7, 1, 16, "👍"),
    ]
    rows = [ledger.build_row("telegram", "reaction",
                             {"emojis": [emoji], "custom_emoji_ids": [], "chat_id": f"chat-{chat}",
                              "message_id": str(message)}, now=NOW - minutes * 60)
            for minutes, chat, message, emoji in reactions]
    lines = ledger.summarize(rows, NOW, title="last 24 hours").splitlines()[2:]
    return lines[:lines.index("")]  # the table; the footnote under it is about the same rule


def main() -> None:
    ledger = _ledger()
    title = _font("sans", 60, [14, 700])
    tagline = _font("sans", 27, [14, 400])
    mono = _font("mono", 23, [500])
    emoji_font = _emoji_font()
    table = _sample_table(ledger)
    cell = mono.getlength("0")

    ink = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    draw.text((MARGIN_X, 118), "reaction-ledger", font=title, fill=FG)
    draw.text((MARGIN_X, 196), "Which reactions your Telegram chats with Hermes collect.",
              font=tagline, fill=MUTED)
    y = 258
    draw.text((MARGIN_X, y), "/reaction-log", font=mono, fill=ACCENT)
    for i, line in enumerate(table):
        y += 29
        last = i == len(table) - 1
        label = line.split()[0]
        is_emoji = 0 < i and not last and label != ledger.CUSTOM
        text = " " * len(label) + line[len(label):] if is_emoji else line
        draw.text((MARGIN_X, y), text, font=mono, fill=FG if i and not last else MUTED if not i else ACCENT)
        if is_emoji:
            glyph = _emoji(emoji_font, label)
            top = y + round((mono.getbbox("0")[1] + mono.getbbox("0")[3] - glyph.height) / 2)
            ink.alpha_composite(glyph, (MARGIN_X, top))
            assert glyph.width <= cell * (len("emoji") + 2), "an emoji runs into the name column"

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
