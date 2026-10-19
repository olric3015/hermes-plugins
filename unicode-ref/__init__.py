"""unicode-ref: ``@unicode:`` references that spell text out character by character.

``@unicode:"some text"`` puts a table of every character of the quoted text into the message,
with its code point, Unicode name and category, plus notes on what does not show on screen:
invisible characters, text-direction controls, unusual spaces, tag characters and words that mix
lookalike scripts. ``@unicode:U+200B`` describes one code point. In the TUI and the desktop app,
``@unicode:`` followed by part of a name (``@unicode:zero width``) suggests matching characters.
"""

from __future__ import annotations

import logging

from . import chars

logger = logging.getLogger(__name__)

PREFIX = "unicode"
DESCRIPTION = "Spell text out character by character: hidden characters, code points, names"


def make_provider():
    """A ContextReferenceProvider for ``@unicode:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class UnicodeReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=f"U+{cp:04X}", display=f"U+{cp:04X} {name}",
                                          meta=chars.unicodedata.category(chr(cp)))
                    for cp, name in chars.search(query, limit)]

        async def expand(self, target: str) -> str:
            return chars.expand(target)

    return UnicodeReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
