from collections.abc import AsyncIterator

import asyncpg
import pytest

pytestmark = pytest.mark.integration


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


async def create_restaurant(conn: asyncpg.Connection, name: str):
    return await conn.fetchval(
        "INSERT INTO restaurants (name) VALUES ($1) RETURNING id", name
    )


async def create_menu_item(conn: asyncpg.Connection, restaurant_id, price_cents=500):
    return await conn.fetchval(
        """
        INSERT INTO menu_items (restaurant_id, name, price_cents)
        VALUES ($1, 'Burger', $2) RETURNING id
        """,
        restaurant_id,
        price_cents,
    )


async def create_order(conn: asyncpg.Connection, restaurant_id):
    return await conn.fetchval(
        """
        INSERT INTO orders (restaurant_id, channel, subtotal_cents, tax_cents,
                            total_cents)
        VALUES ($1, 'web', 1000, 0, 1000) RETURNING id
        """,
        restaurant_id,
    )


async def insert_order_item(
    conn, restaurant_id, order_id, menu_item_id, line_total_cents=1000
):
    await conn.execute(
        """
        INSERT INTO order_items (restaurant_id, order_id, menu_item_id, item_name,
                                 unit_price_cents, quantity, line_total_cents)
        VALUES ($1, $2, $3, 'Burger', 500, 2, $4)
        """,
        restaurant_id,
        order_id,
        menu_item_id,
        line_total_cents,
    )


async def test_tables_exist(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    tables = {row["table_name"] for row in rows}
    assert {"restaurants", "menu_items", "orders", "order_items"} <= tables


async def test_valid_order_item_inserts(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn, "Test Diner")
    menu_item_id = await create_menu_item(conn, restaurant_id)
    order_id = await create_order(conn, restaurant_id)

    await insert_order_item(conn, restaurant_id, order_id, menu_item_id)


async def test_line_total_must_match(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn, "Test Diner")
    menu_item_id = await create_menu_item(conn, restaurant_id)
    order_id = await create_order(conn, restaurant_id)

    with pytest.raises(asyncpg.CheckViolationError):
        await insert_order_item(
            conn, restaurant_id, order_id, menu_item_id, line_total_cents=999
        )


async def test_order_item_cannot_use_other_restaurants_menu_item(
    conn: asyncpg.Connection,
) -> None:
    restaurant_a = await create_restaurant(conn, "Diner A")
    restaurant_b = await create_restaurant(conn, "Diner B")
    other_menu_item = await create_menu_item(conn, restaurant_b)
    order_id = await create_order(conn, restaurant_a)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await insert_order_item(conn, restaurant_a, order_id, other_menu_item)
