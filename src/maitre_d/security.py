"""Authentication for requests that come from ElevenLabs rather than from a person.

Server tools carry a bearer secret: the platform offers no request signing for tool webhooks, so a
long random secret over TLS, held as a workspace secret and never shown to the model, is what
there is. Post-call webhooks are HMAC-signed and are verified here too (added with the webhook).

The secret is optional in `Settings` so Alembic and the seed script can run without it, which means
an unconfigured deployment is a normal state. Auth therefore fails closed: with no secret, or a
blank one, every tool request is a 500 rather than a comparison that an empty token could win.
"""

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, Request, status
from pydantic import SecretStr


def require_tool_bearer(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    secret: SecretStr | None = request.app.state.settings.tool_bearer_secret
    # Checked before the header is parsed: an empty secret must never authenticate an empty token.
    if secret is None or not secret.get_secret_value().strip():
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool authentication is not configured",
        )

    expected = secret.get_secret_value()
    scheme, _, token = (authorization or "").partition(" ")
    # compare_digest takes as long for a near miss as for a wild guess, so response timing
    # reveals nothing about how much of the secret a guess got right.
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
