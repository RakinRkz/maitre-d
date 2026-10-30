"""Time every server-tool request and record it, after the response has gone.

This is plain ASGI middleware rather than per-route code so that every request to /tools/* is
covered, including ones rejected for a bad token or a malformed body, and so the clock runs from
the first byte of the request to the last byte of the response: parsing, validation, the handler
and serialisation, which is the server's share of the pause a caller hears.

The database write happens after the final `send`, so the response is already on its way to
ElevenLabs and the write adds nothing to the measured or the experienced latency.

The local tunnel makes `/tools/*` reachable by anyone who finds the hostname, so two things are
deliberately not done for strangers. A body over `MAX_BODY_BYTES` is refused before the app reads
it, and a request the app refused (401, 404, 405, 413) is logged as a line but not stored: those
are cheap to produce, so they must not be cheap ways to grow a table.
"""

import json
import logging
import time
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from maitre_d.models import ToolInvocation

log = logging.getLogger(__name__)

TOOLS_PREFIX = "/tools/"
# Real tool bodies are a few hundred bytes; the longest field, a message, stops at 1000 characters.
MAX_BODY_BYTES = 64 * 1024


def _is_recorded(status: int) -> bool:
    """Only what the app answered (200) or failed on (5xx) is worth a row.

    Everything else is a refusal that anyone can produce without the secret.
    """
    return status == 200 or status >= 500


async def _read_body(receive: Receive) -> bytes | None:
    """The whole request body, or None if it is larger than `MAX_BODY_BYTES`.

    Read here, before the app sees anything, because FastAPI turns any error raised while it reads
    a body into its own 400. Counted as it arrives, since a chunked body declares no length.
    """
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break  # the client went away; the app will see the disconnect itself
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _json_or_text(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"unparsed": raw.decode("utf-8", errors="replace")}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class ToolInvocationLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(TOOLS_PREFIX):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        response_body = bytearray()
        status = 500
        elapsed_ms = 0.0

        async def send_and_time(message: Message) -> None:
            nonlocal status, elapsed_ms
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                elapsed_ms = (time.perf_counter() - started) * 1000

        request_body = await _read_body(receive)
        if request_body is None:
            await JSONResponse({"detail": "Request body too large"}, status_code=413)(
                scope, receive, send_and_time
            )
            await self._record(scope, b"", bytes(response_body), status, elapsed_ms)
            return

        replayed = False

        async def replay() -> Message:
            # The app gets the body we already read, once, then the real connection's events
            # (a later `receive` is how it learns that the client disconnected).
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay, send_and_time)
        finally:
            await self._record(scope, request_body, bytes(response_body), status, elapsed_ms)

    async def _record(
        self, scope: Scope, request: bytes, response: bytes, status: int, elapsed_ms: float
    ) -> None:
        tool = scope["path"].removeprefix(TOOLS_PREFIX).strip("/") or "unknown"
        request_json = _json_or_text(request)
        response_json = _json_or_text(response) if response else None
        ok = bool(response_json and response_json.get("ok") is True)
        error = response_json.get("error") if response_json else None
        log.info(
            "tool=%s status=%s ok=%s error=%s server_ms=%.1f", tool, status, ok, error, elapsed_ms
        )
        # Request bodies hold names and phone numbers: never above DEBUG.
        log.debug("tool=%s request=%s", tool, request_json)
        if not _is_recorded(status):
            return
        try:
            sessionmaker = scope["app"].state.sessionmaker
            async with sessionmaker() as session:
                session.add(
                    ToolInvocation(
                        conversation_id=str(request_json.get("conversation_id") or "unknown"),
                        tool=tool,
                        ok=ok,
                        error=error if isinstance(error, str) else None,
                        http_status=status,
                        server_ms=elapsed_ms,
                        request=request_json,
                        response=response_json,
                    )
                )
                await session.commit()
        except Exception:
            # Losing a latency sample must never turn into an error for the caller.
            log.exception("could not record tool invocation for %s", tool)
