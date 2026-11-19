import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.db import get_pool
from app.main import create_app


class FakePool:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def fetchval(self, query: str) -> int:
        if self.error is not None:
            raise self.error
        return 1


async def test_health_ok(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_pool] = lambda: FakePool()

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_database_unavailable(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_pool] = lambda: FakePool(OSError("connection refused"))

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}


@pytest.mark.integration
async def test_health_with_real_database(test_database_url: str) -> None:
    app = create_app(Settings(database_url=SecretStr(test_database_url)))

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert app.state.db_pool is None  # closed on shutdown
