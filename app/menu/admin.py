"""Menu editing for managers.

Order lines copy names and prices, so an edit never rewrites an order. Rows
that orders may point at (categories, items, modifier groups and options) are
archived, not deleted; `fetch_menu` leaves archived rows out, and a draft that
still holds one fails `unavailable_lines` when it is placed.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import asyncpg

from app.menu import repository
from app.menu.models import (
    CategoryInput,
    EditableCategory,
    EditableItem,
    EditableMenu,
    GroupInput,
    ItemInput,
)
from app.orders.errors import OrderError


async def get_menu(conn: asyncpg.Connection, restaurant_id: UUID) -> EditableMenu:
    """Call inside one transaction for a consistent read."""
    menu = await repository.fetch_menu(conn, restaurant_id)
    if menu is None:
        raise OrderError("restaurant_not_found", "Restaurant not found")
    return EditableMenu(
        categories=[
            EditableCategory(
                id=category.id,
                name=category.name,
                items=[
                    EditableItem(
                        **item.model_dump(exclude={"modifier_groups"}),
                        group_ids=[group.id for group in item.modifier_groups],
                    )
                    for item in category.items
                ],
            )
            for category in menu.categories
        ],
        groups=await repository.fetch_groups(conn, restaurant_id),
    )


async def create_category(
    conn: asyncpg.Connection, restaurant_id: UUID, category: CategoryInput
) -> UUID:
    with _unique_name("category"):
        async with conn.transaction():
            return await repository.insert_category(conn, restaurant_id, category.name)


async def rename_category(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    category_id: UUID,
    category: CategoryInput,
) -> None:
    with _unique_name("category"):
        async with conn.transaction():
            if not await repository.rename_category(
                conn, restaurant_id, category_id, category.name
            ):
                raise _category_not_found()


async def delete_category(
    conn: asyncpg.Connection, restaurant_id: UUID, category_id: UUID
) -> None:
    """Take an empty category off the menu."""
    async with conn.transaction():
        if not await repository.lock_category(conn, restaurant_id, category_id):
            raise _category_not_found()
        if await repository.fetch_category_item_ids(conn, restaurant_id, category_id):
            raise OrderError(
                "category_not_empty",
                "Move or delete the category's items first",
            )
        await repository.archive_category(conn, restaurant_id, category_id)


async def reorder_categories(
    conn: asyncpg.Connection, restaurant_id: UUID, category_ids: list[UUID]
) -> None:
    async with conn.transaction():
        current = await repository.fetch_category_ids(conn, restaurant_id)
        _check_same_ids(category_ids, current)
        await repository.set_category_order(conn, restaurant_id, category_ids)


async def reorder_items(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    category_id: UUID,
    item_ids: list[UUID],
) -> None:
    async with conn.transaction():
        if not await repository.lock_category(conn, restaurant_id, category_id):
            raise _category_not_found()
        current = await repository.fetch_category_item_ids(
            conn, restaurant_id, category_id
        )
        _check_same_ids(item_ids, current)
        await repository.set_item_order(conn, restaurant_id, item_ids)


async def create_item(
    conn: asyncpg.Connection, restaurant_id: UUID, item: ItemInput
) -> UUID:
    """Add an item, available, at the end of its category."""
    with _unique_name("item"):
        async with conn.transaction():
            await _check_item_links(conn, restaurant_id, item)
            item_id = await repository.insert_item(
                conn,
                restaurant_id,
                item.category_id,
                item.name,
                item.description,
                item.price_cents,
                item.prep_minutes,
            )
            await repository.replace_aliases(conn, restaurant_id, item_id, item.aliases)
            await repository.replace_item_groups(
                conn, restaurant_id, item_id, item.group_ids
            )
            return item_id


async def update_item(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID, item: ItemInput
) -> None:
    """Replace an item's details, aliases and modifier groups.

    Whether it is sold out stays as it is. Lines already in orders keep the
    name and price they copied.
    """
    with _unique_name("item"):
        async with conn.transaction():
            category_id = await repository.lock_item(conn, restaurant_id, item_id)
            if category_id is None:
                raise _item_not_found()
            await _check_item_links(conn, restaurant_id, item)
            await repository.update_item(
                conn,
                restaurant_id,
                item_id,
                item.category_id,
                item.name,
                item.description,
                item.price_cents,
                item.prep_minutes,
                move_to_end=item.category_id != category_id,
            )
            await repository.replace_aliases(conn, restaurant_id, item_id, item.aliases)
            await repository.replace_item_groups(
                conn, restaurant_id, item_id, item.group_ids
            )


async def delete_item(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID
) -> None:
    if not await repository.archive_item(conn, restaurant_id, item_id):
        raise _item_not_found()


async def create_group(
    conn: asyncpg.Connection, restaurant_id: UUID, group: GroupInput
) -> UUID:
    if any(option.id is not None for option in group.options):
        raise _option_not_found()
    with _unique_name("option"):
        async with conn.transaction():
            group_id = await repository.insert_group(
                conn,
                restaurant_id,
                group.name,
                group.min_selections,
                group.max_selections,
            )
            await _sync_options(conn, restaurant_id, group_id, group)
            return group_id


async def update_group(
    conn: asyncpg.Connection, restaurant_id: UUID, group_id: UUID, group: GroupInput
) -> None:
    """Replace a group and its options, for every item that uses the group."""
    with _unique_name("option"):
        async with conn.transaction():
            if not await repository.update_group(
                conn,
                restaurant_id,
                group_id,
                group.name,
                group.min_selections,
                group.max_selections,
            ):
                raise _group_not_found()
            await _sync_options(conn, restaurant_id, group_id, group)


async def delete_group(
    conn: asyncpg.Connection, restaurant_id: UUID, group_id: UUID
) -> None:
    """Take a group and its options off the menu and off every item."""
    async with conn.transaction():
        if not await repository.archive_group(conn, restaurant_id, group_id):
            raise _group_not_found()


async def _check_item_links(
    conn: asyncpg.Connection, restaurant_id: UUID, item: ItemInput
) -> None:
    if not await repository.lock_category(conn, restaurant_id, item.category_id):
        raise _category_not_found()
    live = await repository.count_live_groups(conn, restaurant_id, item.group_ids)
    if live != len(item.group_ids):
        raise _group_not_found()


async def _sync_options(
    conn: asyncpg.Connection, restaurant_id: UUID, group_id: UUID, group: GroupInput
) -> None:
    current = set(await repository.fetch_option_ids(conn, restaurant_id, group_id))
    kept = {option.id for option in group.options if option.id is not None}
    if not kept <= current:
        raise _option_not_found()
    # Archive first, so a new option can take a removed one's name.
    await repository.archive_options(conn, restaurant_id, list(current - kept))
    for position, option in enumerate(group.options, start=1):
        details = (option.name, option.kind, option.price_delta_cents, position * 10)
        if option.id is None:
            await repository.insert_option(conn, restaurant_id, group_id, *details)
        else:
            await repository.update_option(conn, restaurant_id, option.id, *details)


def _check_same_ids(sent: list[UUID], current: list[UUID]) -> None:
    if len(sent) != len(current) or set(sent) != set(current):
        raise OrderError(
            "order_mismatch",
            "The list has changed. Reload and try again.",
        )


@contextmanager
def _unique_name(what: str) -> Iterator[None]:
    try:
        yield
    except asyncpg.UniqueViolationError:
        raise OrderError(
            "name_taken", f"Another {what} already has that name"
        ) from None


def _category_not_found() -> OrderError:
    return OrderError("menu_category_not_found", "Menu category not found")


def _item_not_found() -> OrderError:
    return OrderError("menu_item_not_found", "Menu item not found")


def _group_not_found() -> OrderError:
    return OrderError("modifier_group_not_found", "Modifier group not found")


def _option_not_found() -> OrderError:
    return OrderError("modifier_option_not_found", "Modifier option not found")
