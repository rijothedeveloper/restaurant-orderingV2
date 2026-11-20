"""Insert helpers for integration tests. Each returns the new row's id."""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import time
from uuid import UUID

import asyncpg

from app.orders import service
from app.orders.models import CustomerDetails, Order
from app.staff import service as staff_service
from app.staff.models import StaffUser


async def create_restaurant(conn: asyncpg.Connection, name: str = "Test Diner") -> UUID:
    return await conn.fetchval(
        "INSERT INTO restaurants (name) VALUES ($1) RETURNING id", name
    )


async def create_settings(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    tax_rate_bps: int = 875,
    *,
    kitchen_capacity: int = 3,
    busy_extra_minutes: int = 0,
    last_order_minutes: int = 15,
    accepting_orders: bool = True,
) -> None:
    await conn.execute(
        """
        INSERT INTO settings (restaurant_id, tax_rate_bps, kitchen_capacity,
                              busy_extra_minutes, last_order_minutes,
                              accepting_orders)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        restaurant_id,
        tax_rate_bps,
        kitchen_capacity,
        busy_extra_minutes,
        last_order_minutes,
        accepting_orders,
    )


async def create_hours(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    opens_at: time = time(9),
    closes_at: time = time(21),
    weekdays: Iterable[int] = range(7),
) -> None:
    """Open the restaurant from opens_at to closes_at on each of weekdays."""
    await conn.executemany(
        """
        INSERT INTO opening_hours (restaurant_id, weekday, opens_at, closes_at)
        VALUES ($1, $2, $3, $4)
        """,
        [(restaurant_id, weekday, opens_at, closes_at) for weekday in weekdays],
    )


async def create_category(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    name: str = "Mains",
    sort_order: int = 0,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO menu_categories (restaurant_id, name, sort_order)
        VALUES ($1, $2, $3) RETURNING id
        """,
        restaurant_id,
        name,
        sort_order,
    )


async def create_menu_item(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    category_id: UUID,
    name: str = "Burger",
    *,
    description: str | None = None,
    price_cents: int = 500,
    prep_minutes: int = 10,
    is_available: bool = True,
    sort_order: int = 0,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO menu_items (restaurant_id, category_id, name, description,
                                price_cents, prep_minutes, is_available, sort_order)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id
        """,
        restaurant_id,
        category_id,
        name,
        description,
        price_cents,
        prep_minutes,
        is_available,
        sort_order,
    )


async def create_alias(
    conn: asyncpg.Connection, restaurant_id: UUID, menu_item_id: UUID, alias: str
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO item_aliases (restaurant_id, menu_item_id, alias)
        VALUES ($1, $2, $3) RETURNING id
        """,
        restaurant_id,
        menu_item_id,
        alias,
    )


async def create_modifier_group(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    name: str = "Extras",
    *,
    min_selections: int = 0,
    max_selections: int = 1,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO modifier_groups (restaurant_id, name, min_selections,
                                     max_selections)
        VALUES ($1, $2, $3, $4) RETURNING id
        """,
        restaurant_id,
        name,
        min_selections,
        max_selections,
    )


async def create_modifier_option(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    group_id: UUID,
    name: str,
    *,
    kind: str = "choice",
    price_delta_cents: int = 0,
    is_available: bool = True,
    sort_order: int = 0,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO modifier_options (restaurant_id, group_id, name, kind,
                                      price_delta_cents, is_available, sort_order)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
        """,
        restaurant_id,
        group_id,
        name,
        kind,
        price_delta_cents,
        is_available,
        sort_order,
    )


async def attach_modifier_group(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    menu_item_id: UUID,
    group_id: UUID,
    sort_order: int = 0,
) -> None:
    await conn.execute(
        """
        INSERT INTO item_modifier_groups (restaurant_id, menu_item_id, group_id,
                                          sort_order)
        VALUES ($1, $2, $3, $4)
        """,
        restaurant_id,
        menu_item_id,
        group_id,
        sort_order,
    )


async def create_order(conn: asyncpg.Connection, restaurant_id: UUID) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO orders (restaurant_id, channel, tax_rate_bps, subtotal_cents,
                            tax_cents, total_cents)
        VALUES ($1, 'web', 0, 1000, 0, 1000) RETURNING id
        """,
        restaurant_id,
    )


async def create_order_item(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    menu_item_id: UUID,
    line_total_cents: int = 1000,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO order_items (restaurant_id, order_id, menu_item_id, item_name,
                                 unit_price_cents, quantity, line_total_cents)
        VALUES ($1, $2, $3, 'Burger', 500, 2, $4) RETURNING id
        """,
        restaurant_id,
        order_id,
        menu_item_id,
        line_total_cents,
    )


@dataclass
class Shop:
    """A restaurant with settings, hours and a small menu, for order tests.

    Open 09:00 to 21:00 every day; every item takes 10 minutes to prepare.
    """

    restaurant_id: UUID
    sandwich: UUID  # 1099; Style (required, pick 1) and Extras (optional, up to 2)
    fries: UUID  # 399; no options
    shake: UUID  # sold out
    grilled: UUID
    crispy: UUID
    bacon: UUID  # +150
    cheese: UUID  # +100
    avocado: UUID  # +150
    sold_out_extra: UUID


async def create_shop(conn: asyncpg.Connection, name: str = "Test Diner") -> Shop:
    restaurant_id = await create_restaurant(conn, name)
    await create_settings(conn, restaurant_id, tax_rate_bps=875)
    await create_hours(conn, restaurant_id)
    mains = await create_category(conn, restaurant_id, "Mains")

    sandwich = await create_menu_item(
        conn, restaurant_id, mains, "Chicken Sandwich", price_cents=1099
    )
    fries = await create_menu_item(conn, restaurant_id, mains, "Fries", price_cents=399)
    shake = await create_menu_item(
        conn, restaurant_id, mains, "Shake", price_cents=549, is_available=False
    )

    style = await create_modifier_group(
        conn, restaurant_id, "Style", min_selections=1, max_selections=1
    )
    extras = await create_modifier_group(
        conn, restaurant_id, "Extras", min_selections=0, max_selections=2
    )
    await attach_modifier_group(conn, restaurant_id, sandwich, style, sort_order=10)
    await attach_modifier_group(conn, restaurant_id, sandwich, extras, sort_order=20)

    async def add_option(group: UUID, name: str, order: int, **kwargs) -> UUID:
        return await create_modifier_option(
            conn, restaurant_id, group, name, sort_order=order, **kwargs
        )

    return Shop(
        restaurant_id=restaurant_id,
        sandwich=sandwich,
        fries=fries,
        shake=shake,
        grilled=await add_option(style, "Grilled", 10),
        crispy=await add_option(style, "Crispy", 20),
        bacon=await add_option(
            extras, "Add bacon", 10, kind="add", price_delta_cents=150
        ),
        cheese=await add_option(
            extras, "Add cheese", 20, kind="add", price_delta_cents=100
        ),
        avocado=await add_option(
            extras, "Add avocado", 30, kind="add", price_delta_cents=150
        ),
        sold_out_extra=await add_option(
            extras,
            "Add egg",
            40,
            kind="add",
            price_delta_cents=100,
            is_available=False,
        ),
    )


CUSTOMER = CustomerDetails(name="Sam", phone="+14155552671")


async def place_order(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> Order:
    """Place a draft that already has lines, at its current revision."""
    order = await service.get_order(conn, restaurant_id, order_id)
    return await service.confirm_order(
        conn, restaurant_id, order_id, order.revision, CUSTOMER
    )


STAFF_PIN = "246810"


async def create_staff(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    name: str = "Alex",
    pin: str = STAFF_PIN,
) -> StaffUser:
    return await staff_service.create_staff_user(conn, restaurant_id, name, pin)


@asynccontextmanager
async def committed_shop(database_url: str, name: str) -> AsyncIterator[Shop]:
    """A committed shop, for tests that go through the app's own pool.

    Its orders and the restaurant itself are deleted afterwards.
    """
    connection = await asyncpg.connect(database_url)
    restaurant_id = None
    try:
        async with connection.transaction():
            shop = await create_shop(connection, name)
        restaurant_id = shop.restaurant_id
        yield shop
    finally:
        if restaurant_id is not None:
            # Orders first: order lines hold references to the menu.
            await connection.execute(
                "DELETE FROM orders WHERE restaurant_id = $1", restaurant_id
            )
            await connection.execute(
                "DELETE FROM restaurants WHERE id = $1", restaurant_id
            )
        await connection.close()
