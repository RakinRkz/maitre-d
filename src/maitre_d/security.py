"""Authentication for requests that come from ElevenLabs rather than from a person.

Server tools carry a bearer secret: the platform offers no request signing for tool webhooks, so a
long random secret over TLS, held as a workspace secret and never shown to the model, is what
there is. Post-call webhooks are HMAC-signed and are verified by `verify_webhook_signature`.

The secrets are optional in `Settings` so Alembic and the seed script can run without them, which
means an unconfigured deployment is a normal state. Auth therefore fails closed: with no secret, or
a blank one, every request is a 500 rather than a comparison that an empty token could win.
"""

import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Header, HTTPException, Request, status
from pydantic import SecretStr

# The platform's own SDK rejects only timestamps older than this. A timestamp that far in the
# future is just as wrong (a skewed clock or a replay prepared in advance), so both sides count.
WEBHOOK_TOLERANCE = timedelta(minutes=30)

# ASCII digits only: `\d` and `str.isdigit` also accept other scripts' digits, and a header the
# platform never sends has no business being parsed generously.
_TIMESTAMP = re.compile(r"[0-9]{1,12}")


def _configured(secret: SecretStr | None, what: str) -> str:
    """The secret's value, or a 500 if it is unset or blank.

    Checked before anything from the request is parsed: an empty secret must never authenticate
    an empty token, and a missing one must not be mistaken for a failed login.
    """
    if secret is None or not secret.get_secret_value().strip():
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{what} authentication is not configured",
        )
    return secret.get_secret_value()


def require_webhook_secret(request: Request) -> str:
    """Dependency: the webhook secret, before the route reads a byte of the body."""
    return _configured(request.app.state.settings.elevenlabs_webhook_secret, "Webhook")


def require_tool_bearer(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    expected = _configured(request.app.state.settings.tool_bearer_secret, "Tool")
    scheme, _, token = (authorization or "").partition(" ")
    # compare_digest takes as long for a near miss as for a wild guess, so response timing
    # reveals nothing about how much of the secret a guess got right.
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class WebhookSignatureError(Exception):
    """The request is not provably from ElevenLabs, or is too old or too new to trust.

    `reason` is for our own logs. The caller is told only that the signature is invalid, so a
    probe learns nothing about which of the checks it failed.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def verify_webhook_signature(
    raw_body: bytes, header: str | None, secret: str, now: datetime
) -> None:
    """Check an `ElevenLabs-Signature: t=<unix seconds>,v0=<hex>` header against the raw body.

    The signature is HMAC-SHA256 over `"<t>." + body`, keyed with the webhook secret. It covers
    the bytes as sent, so this takes the raw body: parsing the JSON first and re-serialising it
    would change whitespace and key order and fail every genuine request.

    The platform's SDK helper compares with `!=` and checks only the past side of the timestamp
    (decisions #6), so this is our own: constant-time compare, and a window on both sides.
    """
    if not header:
        raise WebhookSignatureError("missing signature header")

    timestamp: str | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v0":
            candidates.append(value)
    if timestamp is None or not _TIMESTAMP.fullmatch(timestamp):
        raise WebhookSignatureError("missing or malformed timestamp")
    if not candidates:
        raise WebhookSignatureError("no v0 signature")

    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    # compare_digest takes as long for a near miss as for a wild guess. Candidates are encoded
    # first because it refuses a str containing non-ASCII characters.
    if not any(hmac.compare_digest(c.encode(), expected.encode()) for c in candidates):
        raise WebhookSignatureError("signature does not match")

    # Checked after the signature, so only a genuine request can learn anything from the window.
    if abs(int(now.timestamp()) - int(timestamp)) > WEBHOOK_TOLERANCE.total_seconds():
        raise WebhookSignatureError("timestamp outside the tolerance window")
