"""FastAPI application factory.

Run with `uvicorn maitre_d.main:create_app --factory`. There is no module-level `app` because
importing this module must not need the environment; settings are read when the app is created.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from maitre_d.config import Settings, get_settings
from maitre_d.db import make_engine, make_sessionmaker
from maitre_d.invocations import TOOLS_PREFIX, ToolInvocationLog
from maitre_d.routes import tools


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Tools answer the model in a sentence it can act on; everything else keeps FastAPI's 422.
    if request.url.path.startswith(TOOLS_PREFIX):
        return await tools.tool_validation_error(request, exc)
    return await request_validation_exception_handler(request, exc)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("maitre_d").setLevel(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Maître D", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(ToolInvocationLog)
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(tools.router)

    return app
