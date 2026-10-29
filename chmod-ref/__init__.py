"""chmod-ref: ``@chmod:`` references that explain a file permission mode.

``@chmod:755``, ``@chmod:-rwsr-xr-x`` or ``@chmod:100644`` puts the mode into the message in both
forms (octal and ls -l), with each class's rights in words and what setuid, setgid and sticky do.
``@chmod:"644 u+x,go-w"`` shows what a chmod change does to a mode, step by step, the way GNU chmod
applies it; ``@chmod:"644 755"`` shows what changes between two modes; ``@chmod:"umask 022"`` shows
what new files and directories get. Nothing on disk is read.
In the TUI and the desktop app, ``@chmod:`` followed by a mode previews it as a suggestion.
"""

from __future__ import annotations

from . import modes

PREFIX = "chmod"
DESCRIPTION = "Explain a file mode, a chmod change or a umask: octal, ls -l and rights in words"


def make_provider():
    """A ContextReferenceProvider for ``@chmod:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class ChmodReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=text, display=display, meta=meta)
                    for text, display, meta in modes.preview(query)[:limit]]

        async def expand(self, target: str) -> str:
            return modes.expand(target)

    return ChmodReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
