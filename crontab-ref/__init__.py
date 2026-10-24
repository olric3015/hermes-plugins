"""crontab-ref: ``@crontab:`` references that explain a cron expression.

``@crontab:"0 9 * * 1-5"`` puts the expression into the message in plain words ("At 09:00 on
Monday through Friday."), field by field, with whether Hermes's ``/cron`` accepts it, its next
five runs in Hermes's timezone, and notes for the usual surprises (day of month OR day of week,
``*`` in the minute field, ``*/7`` restarting every hour, day 31 skipping short months). The
reading is croniter's, the library Hermes's own scheduler uses. In the TUI and the desktop app,
``@crontab:`` followed by an expression previews it as a suggestion.
"""

from __future__ import annotations

from . import crons

PREFIX = "crontab"
DESCRIPTION = "Explain a cron expression in words, with its next runs in Hermes's timezone"


def make_provider():
    """A ContextReferenceProvider for ``@crontab:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class CrontabReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=text, display=display, meta=meta)
                    for text, display, meta in crons.preview(query)[:limit]]

        async def expand(self, target: str) -> str:
            return crons.expand(target)

    return CrontabReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
