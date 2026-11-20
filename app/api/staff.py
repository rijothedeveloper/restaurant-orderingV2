"""Endpoints for the staff dashboard.

Staff sign in with a PIN and get a session token, sent afterwards as
`Authorization: Bearer <token>`. A session is good only for its own restaurant.
"""

import math
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import clock
from app.db import get_pool
from app.menu import repository as menu_repository
from app.orders import service as orders
from app.orders.models import Order, StaffAction
from app.staff import service
from app.staff.models import (
    ActionDetails,
    Availability,
    Board,
    Controls,
    ControlsChange,
    Login,
    Session,
    StaffUser,
)

router = APIRouter(prefix="/restaurants/{restaurant_id}/staff", tags=["staff"])

Pool = Annotated[asyncpg.Pool, Depends(get_pool)]
Credentials = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]


async def current_staff(
    restaurant_id: UUID, pool: Pool, credentials: Credentials
) -> StaffUser:
    if credentials is not None:
        async with pool.acquire() as conn:
            staff = await service.authenticate(
                conn, restaurant_id, credentials.credentials
            )
        if staff is not None:
            return staff
    raise HTTPException(
        status_code=401,
        detail="Sign in to continue",
        headers={"WWW-Authenticate": "Bearer"},
    )


Staff = Annotated[StaffUser, Depends(current_staff)]


@router.post("/login")
async def login(restaurant_id: UUID, body: Login, pool: Pool) -> Session:
    """401 for a wrong PIN; 429 with `Retry-After` after too many wrong ones."""
    async with pool.acquire() as conn:
        try:
            return await service.login(conn, restaurant_id, body.pin)
        except service.LoginFailed:
            raise HTTPException(status_code=401, detail="Wrong PIN") from None
        except service.LoginLocked as locked:
            wait = (locked.locked_until - clock.utcnow()).total_seconds()
            raise HTTPException(
                status_code=429,
                detail="Too many wrong PINs. Try again in a few minutes.",
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            ) from None


@router.post("/logout", status_code=204)
async def logout(
    restaurant_id: UUID, pool: Pool, credentials: Credentials, staff: Staff
) -> Response:
    async with pool.acquire() as conn:
        await service.logout(conn, restaurant_id, credentials.credentials)
    return Response(status_code=204)


@router.get("/orders")
async def get_board(restaurant_id: UUID, pool: Pool, staff: Staff) -> Board:
    """The orders to act on (placed, accepted, preparing, ready), soonest
    pickup first, and the controls. The dashboard polls this."""
    async with (
        pool.acquire() as conn,
        # One snapshot for all the queries.
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        return Board(
            orders=await orders.active_orders(conn, restaurant_id),
            controls=await service.get_controls(conn, restaurant_id),
        )


@router.post("/orders/{order_id}/{action}")
async def act_on_order(
    restaurant_id: UUID,
    order_id: UUID,
    action: StaffAction,
    pool: Pool,
    staff: Staff,
    details: ActionDetails | None = None,
) -> Order:
    """Move an order: accept, prepare, ready, picked_up, reject, cancel, no_show.

    409 `invalid_transition` when the order is not in a status the action
    starts from; repeating an action returns the order unchanged.
    """
    details = details or ActionDetails()
    async with pool.acquire() as conn:
        return await orders.apply_staff_action(
            conn,
            restaurant_id,
            order_id,
            action,
            staff.id,
            extra_minutes=details.extra_minutes,
            reason=details.reason,
        )


@router.get("/controls")
async def get_controls(restaurant_id: UUID, pool: Pool, staff: Staff) -> Controls:
    async with pool.acquire() as conn:
        return await service.get_controls(conn, restaurant_id)


@router.patch("/controls")
async def set_controls(
    restaurant_id: UUID, change: ControlsChange, pool: Pool, staff: Staff
) -> Controls:
    """Pause or resume ordering (`accepting_orders`), or set busy mode
    (`busy_extra_minutes`, 0 to turn it off)."""
    async with pool.acquire() as conn:
        return await service.set_controls(conn, restaurant_id, change)


@router.patch("/menu/items/{item_id}", status_code=204)
async def set_item_availability(
    restaurant_id: UUID, item_id: UUID, body: Availability, pool: Pool, staff: Staff
) -> Response:
    """Mark a menu item sold out, or back on."""
    async with pool.acquire() as conn:
        found = await menu_repository.set_item_available(
            conn, restaurant_id, item_id, body.is_available
        )
    if not found:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return Response(status_code=204)


@router.patch("/menu/options/{option_id}", status_code=204)
async def set_option_availability(
    restaurant_id: UUID, option_id: UUID, body: Availability, pool: Pool, staff: Staff
) -> Response:
    """Mark a modifier option sold out, or back on."""
    async with pool.acquire() as conn:
        found = await menu_repository.set_option_available(
            conn, restaurant_id, option_id, body.is_available
        )
    if not found:
        raise HTTPException(status_code=404, detail="Menu option not found")
    return Response(status_code=204)
