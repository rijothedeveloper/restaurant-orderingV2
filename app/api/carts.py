"""Cart endpoints for the web storefront. A cart is a draft order.

The cart id is the order's random UUID and is the only handle on the cart.
There is no auth, rate limiting or bot protection yet; add them before launch.
"""

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from app.db import get_pool
from app.orders import service
from app.orders.errors import LinesUnavailable, MissingRequiredChoices, OrderError
from app.orders.models import AddLine, ChangeLine, CustomerDetails, Order

router = APIRouter(prefix="/restaurants/{restaurant_id}/carts", tags=["carts"])

Pool = Annotated[asyncpg.Pool, Depends(get_pool)]

# Codes not listed here are problems with the request body: 422.
ERROR_STATUS = {
    "restaurant_not_found": 404,
    "order_not_found": 404,
    "line_not_found": 404,
    "order_not_draft": 409,
    "restaurant_not_configured": 409,
    "order_changed": 409,
    "order_empty": 409,
    "customer_required": 409,
    "lines_unavailable": 409,
}


class Checkout(CustomerDetails):
    """Who picks the order up, and the revision of the cart they agreed to."""

    revision: int = Field(ge=0)


async def order_error_handler(request: Request, exc: OrderError) -> JSONResponse:
    body: dict = {"code": exc.code, "message": exc.message}
    if isinstance(exc, MissingRequiredChoices):
        body["missing_groups"] = [group.model_dump(mode="json") for group in exc.groups]
    if isinstance(exc, LinesUnavailable):
        body["lines"] = [line.model_dump(mode="json") for line in exc.lines]
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


@router.post("/{cart_id}/checkout")
async def checkout(
    restaurant_id: UUID, cart_id: UUID, body: Checkout, pool: Pool
) -> Order:
    """Place the cart as an order. 409 `order_changed` if the cart is newer than
    the revision sent; repeating a checkout returns the placed order again."""
    async with pool.acquire() as conn:
        return await service.confirm_order(
            conn, restaurant_id, cart_id, body.revision, customer=body
        )
