import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    # Lifespan is not started, so no pool is created; override get_pool in tests.
    return create_app(Settings(database_url=SecretStr("postgres://unused")))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
async def conn(test_database_url: str) -> AsyncIterator[asyncpg.Connection]:
    """A connection inside a transaction that is always rolled back."""
    connection = await asyncpg.connect(test_database_url)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest.fixture
async def db_client(test_database_url: str) -> AsyncIterator[AsyncClient]:
    """A client for the real app, with its pool connected to the test database.

    Requests run on pool connections, so they only see committed rows.
    """
    app = create_app(Settings(database_url=SecretStr(test_database_url)))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client
