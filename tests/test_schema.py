import asyncpg
import pytest

from tests.factories import (
    create_category,
    create_menu_item,
    create_modifier_group,
    create_modifier_option,
    create_order,
    create_order_item,
    create_restaurant,
)

pytestmark = pytest.mark.integration


async def test_tables_exist(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    tables = {row["table_name"] for row in rows}
    assert {
        "restaurants",
        "menu_categories",
        "menu_items",
        "item_aliases",
        "modifier_groups",
        "modifier_options",
        "item_modifier_groups",
        "settings",
        "orders",
        "order_items",
        "order_item_modifiers",
    } <= tables


async def test_line_total_includes_modifier_total(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn)
    category_id = await create_category(conn, restaurant_id)
    menu_item_id = await create_menu_item(conn, restaurant_id, category_id)
    order_id = await create_order(conn, restaurant_id)
    insert = """
        INSERT INTO order_items (restaurant_id, order_id, menu_item_id, item_name,
                                 unit_price_cents, modifier_total_cents, quantity,
                                 line_total_cents)
        VALUES ($1, $2, $3, 'Burger', 500, 150, 2, $4)
    """

    await conn.execute(insert, restaurant_id, order_id, menu_item_id, 1300)

    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(insert, restaurant_id, order_id, menu_item_id, 1000)


async def test_new_order_defaults_to_draft_without_a_number(
    conn: asyncpg.Connection,
) -> None:
    restaurant_id = await create_restaurant(conn)
    order_id = await create_order(conn, restaurant_id)

    row = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)

    assert row["status"] == "draft"
    assert row["revision"] == 0
    assert "order_number" not in row.keys()


async def test_valid_order_item_inserts(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn)
    category_id = await create_category(conn, restaurant_id)
    menu_item_id = await create_menu_item(conn, restaurant_id, category_id)
    order_id = await create_order(conn, restaurant_id)

    await create_order_item(conn, restaurant_id, order_id, menu_item_id)


async def test_line_total_must_match(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn)
    category_id = await create_category(conn, restaurant_id)
    menu_item_id = await create_menu_item(conn, restaurant_id, category_id)
    order_id = await create_order(conn, restaurant_id)

    with pytest.raises(asyncpg.CheckViolationError):
        await create_order_item(
            conn, restaurant_id, order_id, menu_item_id, line_total_cents=999
        )


async def test_order_item_cannot_use_other_restaurants_menu_item(
    conn: asyncpg.Connection,
) -> None:
    restaurant_a = await create_restaurant(conn, "Diner A")
    restaurant_b = await create_restaurant(conn, "Diner B")
    category_b = await create_category(conn, restaurant_b)
    other_menu_item = await create_menu_item(conn, restaurant_b, category_b)
    order_id = await create_order(conn, restaurant_a)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_order_item(conn, restaurant_a, order_id, other_menu_item)


async def test_menu_item_cannot_use_other_restaurants_category(
    conn: asyncpg.Connection,
) -> None:
    restaurant_a = await create_restaurant(conn, "Diner A")
    restaurant_b = await create_restaurant(conn, "Diner B")
    category_b = await create_category(conn, restaurant_b)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_menu_item(conn, restaurant_a, category_b)


async def test_remove_option_must_be_free(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn)
    group_id = await create_modifier_group(conn, restaurant_id, "Remove")

    with pytest.raises(asyncpg.CheckViolationError):
        await create_modifier_option(
            conn,
            restaurant_id,
            group_id,
            "No onions",
            kind="remove",
            price_delta_cents=50,
        )


async def test_group_max_cannot_be_below_min(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn)

    with pytest.raises(asyncpg.CheckViolationError):
        await create_modifier_group(
            conn, restaurant_id, min_selections=2, max_selections=1
        )
