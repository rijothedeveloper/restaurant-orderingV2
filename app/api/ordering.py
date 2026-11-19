"""Whether a restaurant is taking orders right now."""

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from app.db import get_pool
from app.orders import service
from app.orders.models import OrderingStatus

router = APIRouter(tags=["ordering"])


@router.get("/restaurants/{restaurant_id}/ordering-status")
async def get_ordering_status(
    restaurant_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> OrderingStatus:
    """Open or not, when it opens next, and the current wait in minutes."""
    async with pool.acquire() as conn:
        return await service.ordering_status(conn, restaurant_id)
