"""jwt-ref: ``@jwt:`` references that decode a JSON Web Token.

``@jwt:eyJhbGciOi...`` puts the token's header and claims into the message as readable rows:
the algorithm in words, every claim, the time claims (``exp``, ``nbf``, ``iat``, ``auth_time``)
in UTC with how long ago or how far ahead they are, whether the token has expired, and notes on
things worth a second look (``alg: none``, an empty signature, a header that names its own key,
names that appear twice). Encrypted tokens (JWE) show their header only. Nothing is verified:
the plugin decodes, it never checks a signature and never fetches a key.
In the TUI and the desktop app, ``@jwt:`` followed by a token previews it as a suggestion.
"""

from __future__ import annotations

import logging

from . import tokens

logger = logging.getLogger(__name__)

PREFIX = "jwt"
DESCRIPTION = "Decode a JSON Web Token: header, claims and expiry (signature not verified)"


def make_provider():
    """A ContextReferenceProvider for ``@jwt:``, built against Hermes's own base class."""
    from agent.context_references import ContextCompletionItem, ContextReferenceProvider

    class JwtReferenceProvider(ContextReferenceProvider):
        prefix = PREFIX
        description = DESCRIPTION

        async def autocomplete(self, query: str, *, limit: int = 10) -> list:
            return [ContextCompletionItem(text=text, display=display, meta=meta)
                    for text, display, meta in tokens.preview(query)[:limit]]

        async def expand(self, target: str) -> str:
            return tokens.expand(target)

    return JwtReferenceProvider()


def register(ctx) -> None:
    ctx.register_context_reference(make_provider())
