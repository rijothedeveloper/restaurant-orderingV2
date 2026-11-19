from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.db import pool_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with pool_lifespan(app):
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(title="Restaurant Ordering API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db_pool = None

    app.include_router(health_router)
    return app
