import asyncio
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(pool: Annotated[asyncpg.Pool, Depends(get_pool)]) -> JSONResponse:
    try:
        await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=2)
    except (asyncpg.PostgresError, OSError, TimeoutError):
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unavailable"},
        )
    return JSONResponse(content={"status": "ok", "database": "ok"})
