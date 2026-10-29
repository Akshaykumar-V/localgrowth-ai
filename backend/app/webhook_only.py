"""Public listener with only Meta verification and signed callback routes."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config as _config  # noqa: F401 - load local .env
from .db import engine
from .migrate import migrate_database
from .security import validate_secrets
from .webhook_routes import router

logging.getLogger("uvicorn.access").disabled = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_secrets()
    migrate_database(engine)
    yield


app = FastAPI(
    title="LocalGrowth WhatsApp webhook listener",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.include_router(router)
