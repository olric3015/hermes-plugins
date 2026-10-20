"""timestamp-ref: ``@ts:`` references that turn timestamps into readable times.

``@ts:1729468800`` puts the moment into the message in UTC, in this computer's time zone and as
ISO 8601, with its weekday, how long ago (or how far ahead) it is, and the same moment in Unix
seconds, milliseconds and microseconds. Bare numbers are read as seconds, milliseconds,
microseconds or nanoseconds by their length (or by an ``s``/``ms``/``us``/``ns`` suffix); ISO 8601
and RFC 2822 dates work too, and several values in one reference also get the gaps between them.
In the TUI and the desktop app, ``@ts:`` followed by a value previews it as a suggestion.
"""

from __future__ import annotations

import logging

from . import stamps

logger = logging.getLogger(__name__)

PREFIX = "ts"
DESCRIPTION = "Turn Unix timestamps and dates into readable UTC and local times"


def make_provider():
    """A ContextReferenceProvider for ``@ts:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class TimestampReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=text, display=display, meta=meta)
                    for text, display, meta in stamps.preview(query)[:limit]]

        async def expand(self, target: str) -> str:
            return stamps.expand(target)

    return TimestampReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
