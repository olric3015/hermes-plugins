"""b64-ref: ``@b64:`` references that decode base64, base64url, hex and percent-encoded text.

``@b64:SGVsbG8=`` puts what the value holds into the message: text as text, JSON pretty-printed,
and anything else named by its format (PNG with its size, gzip inflated, ZIP, PDF, ELF ...) with a
hex dump of its first bytes. The encoding is told by the value's shape; ``base64:``, ``base64url:``,
``hex:`` or ``url:`` in front of the value picks one. Notes point out a value that was cut short,
a second layer of encoding, or a JWT inside.
In the TUI and the desktop app, ``@b64:`` followed by a value previews it as a suggestion.
"""

from __future__ import annotations

from . import blobs

PREFIX = "b64"
DESCRIPTION = "Decode base64, base64url, hex or percent-encoded text and show what it holds"


def make_provider():
    """A ContextReferenceProvider for ``@b64:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class B64ReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=text, display=display, meta=meta)
                    for text, display, meta in blobs.preview(query)[:limit]]

        async def expand(self, target: str) -> str:
            return blobs.expand(target)

    return B64ReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
