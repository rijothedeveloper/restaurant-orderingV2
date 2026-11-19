from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import AsyncClient

from app.menu.repository import fetch_menu
from tests.factories import (
    attach_modifier_group,
    create_alias,
    create_category,
    create_menu_item,
    create_modifier_group,
    create_modifier_option,
    create_restaurant,
)

pytestmark = pytest.mark.integration

SEED_SQL = Path(__file__).resolve().parent.parent / "db" / "seed.sql"
SEED_RESTAURANT_ID = UUID("00000000-0000-4000-8000-000000000001")


async def test_fetch_menu_unknown_restaurant(conn: asyncpg.Connection) -> None:
    assert await fetch_menu(conn, uuid4()) is None


async def test_fetch_menu_restaurant_without_menu(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn, "Empty Diner")

    menu = await fetch_menu(conn, restaurant_id)

    assert menu is not None
    assert menu.restaurant_name == "Empty Diner"
    assert menu.categories == []


async def test_fetch_menu_builds_nested_menu_in_sort_order(
    conn: asyncpg.Connection,
) -> None:
    restaurant_id = await create_restaurant(conn)
    # sort_order, not name or insert order, decides the order.
    drinks = await create_category(conn, restaurant_id, "Drinks", sort_order=20)
    mains = await create_category(conn, restaurant_id, "Mains", sort_order=10)
    wrap = await create_menu_item(
        conn, restaurant_id, mains, "Wrap", price_cents=999, sort_order=20
    )
    sandwich = await create_menu_item(
        conn,
        restaurant_id,
        mains,
        "Chicken Sandwich",
        description="On a bun.",
        price_cents=1099,
        prep_minutes=12,
        sort_order=10,
    )
    await create_menu_item(conn, restaurant_id, drinks, "Shake", is_available=False)
    await create_alias(conn, restaurant_id, sandwich, "chick sando")

    extras = await create_modifier_group(
        conn, restaurant_id, "Extras", min_selections=0, max_selections=2
    )
    style = await create_modifier_group(
        conn, restaurant_id, "Style", min_selections=1, max_selections=1
    )
    crispy = await create_modifier_option(
        conn, restaurant_id, style, "Crispy", sort_order=20
    )
    grilled = await create_modifier_option(
        conn, restaurant_id, style, "Grilled", sort_order=10
    )
    await create_modifier_option(
        conn, restaurant_id, extras, "Add bacon", kind="add", price_delta_cents=150
    )
    await attach_modifier_group(conn, restaurant_id, sandwich, extras, sort_order=20)
    await attach_modifier_group(conn, restaurant_id, sandwich, style, sort_order=10)
    # A group can be shared between items.
    await attach_modifier_group(conn, restaurant_id, wrap, extras)

    menu = await fetch_menu(conn, restaurant_id)

    assert menu is not None
    assert menu.restaurant_id == restaurant_id
    assert [c.name for c in menu.categories] == ["Mains", "Drinks"]

    mains_items = menu.categories[0].items
    assert [i.name for i in mains_items] == ["Chicken Sandwich", "Wrap"]

    item = mains_items[0]
    assert item.id == sandwich
    assert item.description == "On a bun."
    assert item.price_cents == 1099
    assert item.prep_minutes == 12
    assert item.is_available is True
    assert item.aliases == ["chick sando"]
    assert [g.name for g in item.modifier_groups] == ["Style", "Extras"]

    style_group = item.modifier_groups[0]
    assert (style_group.min_selections, style_group.max_selections) == (1, 1)
    assert [o.id for o in style_group.options] == [grilled, crispy]

    bacon = item.modifier_groups[1].options[0]
    assert (bacon.name, bacon.kind, bacon.price_delta_cents) == (
        "Add bacon",
        "add",
        150,
    )

    assert [g.name for g in mains_items[1].modifier_groups] == ["Extras"]
    assert mains_items[1].aliases == []

    shake = menu.categories[1].items[0]
    assert shake.is_available is False
    assert shake.description is None


async def test_fetch_menu_excludes_other_restaurants(conn: asyncpg.Connection) -> None:
    restaurant_a = await create_restaurant(conn, "Diner A")
    restaurant_b = await create_restaurant(conn, "Diner B")
    category_a = await create_category(conn, restaurant_a, "Mains")
    category_b = await create_category(conn, restaurant_b, "Mains")
    await create_menu_item(conn, restaurant_a, category_a, "Burger A")
    await create_menu_item(conn, restaurant_b, category_b, "Burger B")

    menu = await fetch_menu(conn, restaurant_a)

    assert menu is not None
    assert [i.name for c in menu.categories for i in c.items] == ["Burger A"]


async def test_seed_runs_twice_and_loads(conn: asyncpg.Connection) -> None:
    seed = SEED_SQL.read_text()
    await conn.execute(seed)
    await conn.execute(seed)

    menu = await fetch_menu(conn, SEED_RESTAURANT_ID)

    assert menu is not None
    assert [c.name for c in menu.categories] == ["Sandwiches", "Sides", "Drinks"]
    items = {i.name: i for c in menu.categories for i in c.items}
    assert len(items) == 8

    chicken = items["Chicken Sandwich"]
    # Alias order follows the database collation, so compare as a set.
    assert set(chicken.aliases) == {"chick sando", "chicken burger"}
    assert [g.name for g in chicken.modifier_groups] == [
        "Chicken style",
        "Extras",
        "Remove",
    ]
    assert chicken.modifier_groups[0].min_selections == 1
    assert items["Milkshake"].is_available is False


@pytest.fixture
async def committed_restaurant(test_database_url: str) -> AsyncIterator[UUID]:
    """A committed restaurant with a one-item menu, deleted again afterwards."""
    connection = await asyncpg.connect(test_database_url)
    try:
        restaurant_id = await create_restaurant(connection, f"Route Diner {uuid4()}")
        try:
            category_id = await create_category(connection, restaurant_id, "Mains")
            await create_menu_item(connection, restaurant_id, category_id, "Burger")
            yield restaurant_id
        finally:
            await connection.execute(
                "DELETE FROM restaurants WHERE id = $1", restaurant_id
            )
    finally:
        await connection.close()


async def test_get_menu_route(
    db_client: AsyncClient, committed_restaurant: UUID
) -> None:
    response = await db_client.get(f"/restaurants/{committed_restaurant}/menu")

    assert response.status_code == 200
    body = response.json()
    assert body["restaurant_id"] == str(committed_restaurant)
    assert [c["name"] for c in body["categories"]] == ["Mains"]
    item = body["categories"][0]["items"][0]
    assert item["name"] == "Burger"
    assert item["price_cents"] == 500
    assert item["aliases"] == []
    assert item["modifier_groups"] == []


async def test_get_menu_route_unknown_restaurant(db_client: AsyncClient) -> None:
    response = await db_client.get(f"/restaurants/{uuid4()}/menu")

    assert response.status_code == 404


async def test_get_menu_route_rejects_bad_id(db_client: AsyncClient) -> None:
    response = await db_client.get("/restaurants/not-a-uuid/menu")

    assert response.status_code == 422
