from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.db import get_pool
from app.menu.models import Menu
from app.menu.repository import fetch_menu

router = APIRouter(tags=["menu"])


@router.get("/restaurants/{restaurant_id}/menu")
async def get_menu(
    restaurant_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> Menu:
    async with (
        pool.acquire() as conn,
        # One snapshot for all of fetch_menu's queries.
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        menu = await fetch_menu(conn, restaurant_id)

    if menu is None:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return menu
