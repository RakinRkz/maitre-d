"""Starting a web call: the browser gets a short-lived token, never the API key.

The agent is private (`enable_auth`), so its ID alone starts nothing. The key stays on this side;
the browser receives a token that is good for one conversation. The route is local-only because
it spends voice minutes and has no login (see `require_local_request`).
"""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from maitre_d.elevenlabs import ElevenLabsError, get_conversation_token, make_client
from maitre_d.security import require_local_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["session"], dependencies=[Depends(require_local_request)])


class SessionOut(BaseModel):
    """Everything the browser is given. There is deliberately no field that could hold the key."""

    token: str
    conversation_id: str
    environment: str | None


async def get_elevenlabs_client(request: Request) -> AsyncIterator[httpx.AsyncClient]:
    """A client for this one request. A dependency so tests can swap in a mock transport."""
    key = request.app.state.settings.elevenlabs_api_key
    if key is None or not key.get_secret_value().strip():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Voice is not configured")
    async with make_client(key.get_secret_value()) as client:
        yield client


Client = Annotated[httpx.AsyncClient, Depends(get_elevenlabs_client)]


@router.post("/session", response_model=SessionOut)
async def start_session(request: Request, client: Client) -> SessionOut:
    settings = request.app.state.settings
    agent_id: str | None = settings.elevenlabs_agent_id
    if agent_id is None or not agent_id.strip():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Voice is not configured")

    # The platform's reason is logged for us and never passed on: the browser needs to know that
    # it failed, not why, and an error body is not ours to forward.
    try:
        issued = await get_conversation_token(
            client, agent_id=agent_id, environment=settings.elevenlabs_environment
        )
    except ElevenLabsError as error:
        logger.warning("could not get a conversation token: %s", error)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Could not start a voice session"
        ) from None
    except httpx.HTTPError as error:
        # The class only: the exception text can include the request URL.
        logger.warning("could not reach ElevenLabs for a token (%s)", type(error).__name__)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="Could not start a voice session"
        ) from None

    logger.info("started a web session")
    return SessionOut(
        token=issued.token,
        conversation_id=issued.conversation_id,
        environment=settings.elevenlabs_environment,
    )
