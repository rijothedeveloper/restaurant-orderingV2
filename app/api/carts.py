"""Cart endpoints for the web storefront. A cart is a draft order.

The cart id is the order's random UUID and is the only handle on the cart.
There is no auth, rate limiting or bot protection yet; add them before launch.
"""

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import get_pool
from app.orders import service
from app.orders.errors import MissingRequiredChoices, OrderError
from app.orders.models import AddLine, ChangeLine, Order

router = APIRouter(prefix="/restaurants/{restaurant_id}/carts", tags=["carts"])

Pool = Annotated[asyncpg.Pool, Depends(get_pool)]

# Codes not listed here are problems with the request body: 422.
ERROR_STATUS = {
    "restaurant_not_found": 404,
    "order_not_found": 404,
    "line_not_found": 404,
    "order_not_draft": 409,
    "restaurant_not_configured": 409,
}


async def order_error_handler(request: Request, exc: OrderError) -> JSONResponse:
    body: dict = {"code": exc.code, "message": exc.message}
    if isinstance(exc, MissingRequiredChoices):
        body["missing_groups"] = [group.model_dump(mode="json") for group in exc.groups]
    return JSONResponse(status_code=ERROR_STATUS.get(exc.code, 422), content=body)


def register_order_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(OrderError, order_error_handler)


@router.post("", status_code=201)
async def create_cart(restaurant_id: UUID, pool: Pool) -> Order:
    async with pool.acquire() as conn:
        return await service.create_draft(conn, restaurant_id, "storefront")


@router.get("/{cart_id}")
async def get_cart(restaurant_id: UUID, cart_id: UUID, pool: Pool) -> Order:
    async with pool.acquire() as conn:
        return await service.get_order(conn, restaurant_id, cart_id)


@router.post("/{cart_id}/lines")
async def add_line(
    restaurant_id: UUID, cart_id: UUID, line: AddLine, pool: Pool
) -> Order:
    async with pool.acquire() as conn:
        return await service.add_item(conn, restaurant_id, cart_id, line)


@router.patch("/{cart_id}/lines/{line_id}")
async def change_line(
    restaurant_id: UUID, cart_id: UUID, line_id: UUID, change: ChangeLine, pool: Pool
) -> Order:
    async with pool.acquire() as conn:
        return await service.change_item(conn, restaurant_id, cart_id, line_id, change)


@router.delete("/{cart_id}/lines/{line_id}")
async def remove_line(
    restaurant_id: UUID, cart_id: UUID, line_id: UUID, pool: Pool
) -> Order:
    async with pool.acquire() as conn:
        return await service.remove_item(conn, restaurant_id, cart_id, line_id)
