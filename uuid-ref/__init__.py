"""uuid-ref: ``@uuid:`` references that decode a UUID or ULID.

``@uuid:017f22e2-79b0-7cc3-98c4-dc0c0c07398f`` puts what the identifier says about itself into the
message: its version and variant in words, the creation time a v1, v6, v7 or ULID carries (in UTC,
with how long ago), the clock sequence and node of a v1 or v6 (and whether the node is a real
network card address), the local id of a v2, and the Nil, Max and RFC namespace UUIDs by name.
Several identifiers in one reference are compared by creation time. Nothing is looked up.
In the TUI and the desktop app, ``@uuid:`` followed by an identifier previews it as a suggestion.
"""

from __future__ import annotations

from . import ids

PREFIX = "uuid"
DESCRIPTION = "Decode a UUID or ULID: version, variant and the creation time it carries"


def make_provider():
    """A ContextReferenceProvider for ``@uuid:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class UuidReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=text, display=display, meta=meta)
                    for text, display, meta in ids.preview(query)[:limit]]

        async def expand(self, target: str) -> str:
            return ids.expand(target)

    return UuidReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
