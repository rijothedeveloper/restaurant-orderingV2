"""Endpoints for a restaurant's manager: staff, the menu, hours and settings.

They sit under the staff routes and need the session of a staff user whose
role is `manager`; anyone else gets 403.
"""

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.api.staff import Staff
from app.db import get_pool
from app.menu import admin as menu_admin
from app.menu.models import (
    CategoryInput,
    EditableMenu,
    GroupInput,
    ItemInput,
    Reorder,
)
from app.staff import service
from app.staff.models import (
    HoursChange,
    KitchenSettings,
    NewStaff,
    RestaurantSetup,
    StaffChange,
    StaffMember,
    StaffUser,
)


async def current_manager(staff: Staff) -> StaffUser:
    if staff.role != "manager":
        raise HTTPException(status_code=403, detail="Only a manager can do that")
    return staff


router = APIRouter(
    prefix="/restaurants/{restaurant_id}/staff/admin",
    tags=["admin"],
    dependencies=[Depends(current_manager)],
)

Pool = Annotated[asyncpg.Pool, Depends(get_pool)]


@router.get("/users")
async def list_staff(restaurant_id: UUID, pool: Pool) -> list[StaffMember]:
    async with pool.acquire() as conn:
        return await service.list_staff(conn, restaurant_id)


@router.post("/users", status_code=201)
async def add_staff(restaurant_id: UUID, body: NewStaff, pool: Pool) -> StaffMember:
    """409 `pin_taken` when someone active already uses the PIN."""
    async with pool.acquire() as conn:
        staff = await service.create_staff_user(
            conn, restaurant_id, body.name, body.pin, body.role
        )
    return StaffMember(id=staff.id, name=staff.name, role=staff.role, is_active=True)


@router.patch("/users/{staff_user_id}")
async def change_staff(
    restaurant_id: UUID, staff_user_id: UUID, change: StaffChange, pool: Pool
) -> StaffMember:
    """Rename, change role or PIN, or turn someone off (`is_active` false).

    409 `last_manager` rather than leave the restaurant without a manager.
    """
    async with pool.acquire() as conn:
        return await service.update_staff_user(
            conn, restaurant_id, staff_user_id, change
        )


async def _menu(conn: asyncpg.Connection, restaurant_id: UUID) -> EditableMenu:
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        return await menu_admin.get_menu(conn, restaurant_id)


# Every menu change answers with the menu as it now is.


@router.get("/menu")
async def get_menu(restaurant_id: UUID, pool: Pool) -> EditableMenu:
    async with pool.acquire() as conn:
        return await _menu(conn, restaurant_id)


@router.post("/menu/categories", status_code=201)
async def add_category(
    restaurant_id: UUID, body: CategoryInput, pool: Pool
) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.create_category(conn, restaurant_id, body)
        return await _menu(conn, restaurant_id)


@router.put("/menu/categories/order")
async def reorder_categories(
    restaurant_id: UUID, body: Reorder, pool: Pool
) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.reorder_categories(conn, restaurant_id, body.ids)
        return await _menu(conn, restaurant_id)


@router.patch("/menu/categories/{category_id}")
async def rename_category(
    restaurant_id: UUID, category_id: UUID, body: CategoryInput, pool: Pool
) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.rename_category(conn, restaurant_id, category_id, body)
        return await _menu(conn, restaurant_id)


@router.delete("/menu/categories/{category_id}")
async def delete_category(
    restaurant_id: UUID, category_id: UUID, pool: Pool
) -> EditableMenu:
    """409 `category_not_empty` while the category still has items."""
    async with pool.acquire() as conn:
        await menu_admin.delete_category(conn, restaurant_id, category_id)
        return await _menu(conn, restaurant_id)


@router.put("/menu/categories/{category_id}/items/order")
async def reorder_items(
    restaurant_id: UUID, category_id: UUID, body: Reorder, pool: Pool
) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.reorder_items(conn, restaurant_id, category_id, body.ids)
        return await _menu(conn, restaurant_id)


@router.post("/menu/items", status_code=201)
async def add_item(restaurant_id: UUID, body: ItemInput, pool: Pool) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.create_item(conn, restaurant_id, body)
        return await _menu(conn, restaurant_id)


@router.put("/menu/items/{item_id}")
async def change_item(
    restaurant_id: UUID, item_id: UUID, body: ItemInput, pool: Pool
) -> EditableMenu:
    """Replaces the item's details, aliases and modifier groups."""
    async with pool.acquire() as conn:
        await menu_admin.update_item(conn, restaurant_id, item_id, body)
        return await _menu(conn, restaurant_id)


@router.delete("/menu/items/{item_id}")
async def delete_item(restaurant_id: UUID, item_id: UUID, pool: Pool) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.delete_item(conn, restaurant_id, item_id)
        return await _menu(conn, restaurant_id)


@router.post("/menu/groups", status_code=201)
async def add_group(restaurant_id: UUID, body: GroupInput, pool: Pool) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.create_group(conn, restaurant_id, body)
        return await _menu(conn, restaurant_id)


@router.put("/menu/groups/{group_id}")
async def change_group(
    restaurant_id: UUID, group_id: UUID, body: GroupInput, pool: Pool
) -> EditableMenu:
    """Replaces the group and its options; options left out are removed."""
    async with pool.acquire() as conn:
        await menu_admin.update_group(conn, restaurant_id, group_id, body)
        return await _menu(conn, restaurant_id)


@router.delete("/menu/groups/{group_id}")
async def delete_group(restaurant_id: UUID, group_id: UUID, pool: Pool) -> EditableMenu:
    async with pool.acquire() as conn:
        await menu_admin.delete_group(conn, restaurant_id, group_id)
        return await _menu(conn, restaurant_id)


async def _setup(conn: asyncpg.Connection, restaurant_id: UUID) -> RestaurantSetup:
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        return await service.get_setup(conn, restaurant_id)


@router.get("/settings")
async def get_setup(restaurant_id: UUID, pool: Pool) -> RestaurantSetup:
    async with pool.acquire() as conn:
        return await _setup(conn, restaurant_id)


@router.put("/settings")
async def set_settings(
    restaurant_id: UUID, body: KitchenSettings, pool: Pool
) -> RestaurantSetup:
    async with pool.acquire() as conn:
        await service.set_kitchen_settings(conn, restaurant_id, body)
        return await _setup(conn, restaurant_id)


@router.put("/hours")
async def set_hours(
    restaurant_id: UUID, body: HoursChange, pool: Pool
) -> RestaurantSetup:
    """Replaces the week's opening hours. 422 `hours_overlap` if two overlap."""
    async with pool.acquire() as conn:
        await service.set_hours(conn, restaurant_id, body.hours)
        return await _setup(conn, restaurant_id)
