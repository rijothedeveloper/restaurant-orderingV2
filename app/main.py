from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.carts import register_order_error_handler
from app.api.carts import router as carts_router
from app.api.health import router as health_router
from app.api.menu import router as menu_router
from app.api.ordering import router as ordering_router
from app.api.staff import router as staff_router
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
    app.include_router(menu_router)
    app.include_router(carts_router)
    app.include_router(ordering_router)
    app.include_router(staff_router)
    register_order_error_handler(app)
    return app
