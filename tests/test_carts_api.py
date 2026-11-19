from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest
from httpx import AsyncClient

from tests.factories import Shop, create_shop

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(test_database_url: str) -> AsyncIterator[Shop]:
    """A committed restaurant with a menu; its orders and itself are deleted after."""
    connection = await asyncpg.connect(test_database_url)
    restaurant_ids = []
    try:
        async with connection.transaction():
            shop = await create_shop(connection, f"Cart Diner {uuid4()}")
        restaurant_ids.append(shop.restaurant_id)
        yield shop
    finally:
        for restaurant_id in restaurant_ids:
            # Orders first: order lines hold references to the menu.
            await connection.execute(
                "DELETE FROM orders WHERE restaurant_id = $1", restaurant_id
            )
            await connection.execute(
                "DELETE FROM restaurants WHERE id = $1", restaurant_id
            )
        await connection.close()


def carts_url(shop: Shop) -> str:
    return f"/restaurants/{shop.restaurant_id}/carts"


async def test_cart_flow(db_client: AsyncClient, shop: Shop) -> None:
    # Create.
    response = await db_client.post(carts_url(shop))
    assert response.status_code == 201
    cart = response.json()
    assert cart["status"] == "draft"
    assert cart["channel"] == "storefront"
    assert cart["lines"] == []
    assert cart["total_cents"] == 0
    cart_url = f"{carts_url(shop)}/{cart['id']}"

    # A required choice is missing: nothing is added, and the group is returned.
    response = await db_client.post(
        f"{cart_url}/lines", json={"menu_item_id": str(shop.sandwich)}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "missing_required_choices"
    (group,) = body["missing_groups"]
    assert group["name"] == "Style"
    assert {o["id"] for o in group["options"]} == {str(shop.grilled), str(shop.crispy)}

    # Add with the choice.
    response = await db_client.post(
        f"{cart_url}/lines",
        json={
            "menu_item_id": str(shop.sandwich),
            "quantity": 2,
            "option_ids": [str(shop.crispy), str(shop.bacon)],
            "notes": "cut in half",
        },
    )
    assert response.status_code == 200
    cart = response.json()
    (line,) = cart["lines"]
    assert line["name"] == "Chicken Sandwich"
    assert line["line_total_cents"] == 2498
    assert line["notes"] == "cut in half"
    assert [m["name"] for m in line["modifiers"]] == ["Crispy", "Add bacon"]
    assert (cart["subtotal_cents"], cart["tax_cents"], cart["total_cents"]) == (
        2498,
        219,
        2717,
    )
    assert cart["revision"] == 1

    # Change the quantity.
    response = await db_client.patch(
        f"{cart_url}/lines/{line['id']}", json={"quantity": 1}
    )
    assert response.status_code == 200
    cart = response.json()
    assert cart["lines"][0]["quantity"] == 1
    assert cart["lines"][0]["notes"] == "cut in half"
    assert cart["subtotal_cents"] == 1249

    # Fetch gives the same cart.
    response = await db_client.get(cart_url)
    assert response.status_code == 200
    assert response.json() == cart

    # Remove the line.
    response = await db_client.delete(f"{cart_url}/lines/{line['id']}")
    assert response.status_code == 200
    cart = response.json()
    assert cart["lines"] == []
    assert cart["total_cents"] == 0
    assert cart["revision"] == 3


async def test_cart_rejects_bad_requests(db_client: AsyncClient, shop: Shop) -> None:
    cart = (await db_client.post(carts_url(shop))).json()
    lines_url = f"{carts_url(shop)}/{cart['id']}/lines"

    sold_out = await db_client.post(lines_url, json={"menu_item_id": str(shop.shake)})
    assert sold_out.status_code == 422
    assert sold_out.json()["code"] == "item_unavailable"

    unknown_item = await db_client.post(lines_url, json={"menu_item_id": str(uuid4())})
    assert unknown_item.status_code == 422
    assert unknown_item.json()["code"] == "item_not_found"

    # Request-shape errors come from FastAPI's own validation.
    bad_quantity = await db_client.post(
        lines_url, json={"menu_item_id": str(shop.fries), "quantity": 0}
    )
    assert bad_quantity.status_code == 422

    empty_change = await db_client.patch(f"{lines_url}/{uuid4()}", json={})
    assert empty_change.status_code == 422

    missing_line = await db_client.patch(f"{lines_url}/{uuid4()}", json={"quantity": 2})
    assert missing_line.status_code == 404
    assert missing_line.json()["code"] == "line_not_found"

    missing_line = await db_client.delete(f"{lines_url}/{uuid4()}")
    assert missing_line.status_code == 404


async def test_cart_not_found(db_client: AsyncClient, shop: Shop) -> None:
    unknown_cart = await db_client.get(f"{carts_url(shop)}/{uuid4()}")
    assert unknown_cart.status_code == 404
    assert unknown_cart.json()["code"] == "order_not_found"

    unknown_restaurant = await db_client.post(f"/restaurants/{uuid4()}/carts")
    assert unknown_restaurant.status_code == 404
    assert unknown_restaurant.json()["code"] == "restaurant_not_found"


async def test_cart_is_not_reachable_through_another_restaurant(
    db_client: AsyncClient, shop: Shop, test_database_url: str
) -> None:
    cart = (await db_client.post(carts_url(shop))).json()

    connection = await asyncpg.connect(test_database_url)
    try:
        async with connection.transaction():
            other = await create_shop(connection, f"Other Cart Diner {uuid4()}")
        try:
            response = await db_client.get(
                f"/restaurants/{other.restaurant_id}/carts/{cart['id']}"
            )
            assert response.status_code == 404

            response = await db_client.post(
                f"/restaurants/{other.restaurant_id}/carts/{cart['id']}/lines",
                json={"menu_item_id": str(other.fries)},
            )
            assert response.status_code == 404
        finally:
            await connection.execute(
                "DELETE FROM restaurants WHERE id = $1", other.restaurant_id
            )
    finally:
        await connection.close()


async def test_placed_cart_cannot_change(
    db_client: AsyncClient, shop: Shop, test_database_url: str
) -> None:
    cart = (await db_client.post(carts_url(shop))).json()
    connection = await asyncpg.connect(test_database_url)
    try:
        await connection.execute(
            "UPDATE orders SET status = 'placed' WHERE id = $1", cart["id"]
        )
    finally:
        await connection.close()

    response = await db_client.post(
        f"{carts_url(shop)}/{cart['id']}/lines",
        json={"menu_item_id": str(shop.fries)},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "order_not_draft"
