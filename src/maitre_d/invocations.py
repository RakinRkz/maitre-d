"""Time every server-tool request and record it, after the response has gone.

This is plain ASGI middleware rather than per-route code so that every request to /tools/* is
covered, including ones rejected for a bad token or a malformed body, and so the clock runs from
the first byte of the request to the last byte of the response: parsing, validation, the handler
and serialisation, which is the server's share of the pause a caller hears.

The database write happens after the final `send`, so the response is already on its way to
ElevenLabs and the write adds nothing to the measured or the experienced latency.
"""

import json
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from maitre_d.models import ToolInvocation

log = logging.getLogger(__name__)

TOOLS_PREFIX = "/tools/"


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
        request_body = bytearray()
        response_body = bytearray()
        status = 500
        elapsed_ms = 0.0

        async def receive_and_keep() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                request_body.extend(message.get("body", b""))
            return message

        async def send_and_time(message: Message) -> None:
            nonlocal status, elapsed_ms
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                elapsed_ms = (time.perf_counter() - started) * 1000

        try:
            await self.app(scope, receive_and_keep, send_and_time)
        finally:
            await self._record(scope, bytes(request_body), bytes(response_body), status, elapsed_ms)

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
