from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest
from httpx import AsyncClient

from tests.factories import STAFF_PIN, Shop, committed_shop, create_staff

pytestmark = pytest.mark.integration

MANAGER_PIN = "135790"


@pytest.fixture
async def shop(test_database_url: str) -> AsyncIterator[Shop]:
    async with committed_shop(test_database_url, f"Admin Diner {uuid4()}") as shop:
        connection = await asyncpg.connect(test_database_url)
        try:
            await create_staff(connection, shop.restaurant_id)
            await create_staff(
                connection, shop.restaurant_id, "Maria", MANAGER_PIN, "manager"
            )
        finally:
            await connection.close()
        yield shop


def admin_url(shop: Shop) -> str:
    return f"/restaurants/{shop.restaurant_id}/staff/admin"


async def sign_in(db_client: AsyncClient, shop: Shop, pin: str) -> dict[str, str]:
    response = await db_client.post(
        f"/restaurants/{shop.restaurant_id}/staff/login", json={"pin": pin}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
async def manager(db_client: AsyncClient, shop: Shop) -> dict[str, str]:
    return await sign_in(db_client, shop, MANAGER_PIN)


async def test_admin_routes_are_for_managers_only(
    db_client: AsyncClient, shop: Shop, manager: dict[str, str]
) -> None:
    cook = await sign_in(db_client, shop, STAFF_PIN)
    base = admin_url(shop)
    requests = [
        ("GET", "users", None),
        ("POST", "users", {"name": "Sam", "pin": "111222"}),
        ("GET", "menu", None),
        ("POST", "menu/categories", {"name": "Drinks"}),
        ("DELETE", f"menu/items/{shop.fries}", None),
        ("GET", "settings", None),
        ("PUT", "hours", {"hours": []}),
    ]
    for method, path, body in requests:
        url = f"{base}/{path}"
        assert (await db_client.request(method, url, json=body)).status_code == 401
        response = await db_client.request(method, url, json=body, headers=cook)
        assert response.status_code == 403, (method, path)

    # Nothing changed, and the manager gets in.
    menu = (await db_client.get(f"{base}/menu", headers=manager)).json()
    (mains,) = menu["categories"]
    assert len(mains["items"]) == 3
    response = await db_client.get(f"{base}/users", headers=manager)
    assert [member["name"] for member in response.json()] == ["Alex", "Maria"]


async def test_manager_adds_and_changes_staff(
    db_client: AsyncClient, shop: Shop, manager: dict[str, str]
) -> None:
    users = f"{admin_url(shop)}/users"

    response = await db_client.post(
        users, headers=manager, json={"name": "Sam", "pin": STAFF_PIN}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "pin_taken"
    response = await db_client.post(
        users, headers=manager, json={"name": "Sam", "pin": "12"}
    )
    assert response.status_code == 422

    response = await db_client.post(
        users, headers=manager, json={"name": "Sam", "pin": "111222"}
    )
    assert response.status_code == 201
    sam = response.json()
    assert sam == {"id": sam["id"], "name": "Sam", "role": "staff", "is_active": True}
    sam_session = await sign_in(db_client, shop, "111222")

    response = await db_client.patch(
        f"{users}/{sam['id']}", headers=manager, json={"is_active": False}
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    board = f"/restaurants/{shop.restaurant_id}/staff/orders"
    assert (await db_client.get(board, headers=sam_session)).status_code == 401

    response = await db_client.patch(
        f"{users}/{sam['id']}", headers=manager, json={"is_active": True}
    )
    assert (response.status_code, response.json()["code"]) == (422, "pin_required")

    maria = next(
        member
        for member in (await db_client.get(users, headers=manager)).json()
        if member["name"] == "Maria"
    )
    response = await db_client.patch(
        f"{users}/{maria['id']}", headers=manager, json={"role": "staff"}
    )
    assert (response.status_code, response.json()["code"]) == (409, "last_manager")
    response = await db_client.patch(
        f"{users}/{uuid4()}", headers=manager, json={"name": "Nobody"}
    )
    assert response.status_code == 404


async def test_manager_builds_a_menu_customers_can_order_from(
    db_client: AsyncClient, shop: Shop, manager: dict[str, str]
) -> None:
    base = f"{admin_url(shop)}/menu"

    response = await db_client.post(
        f"{base}/categories", headers=manager, json={"name": "Drinks"}
    )
    assert response.status_code == 201
    drinks = response.json()["categories"][1]
    assert drinks["name"] == "Drinks"

    response = await db_client.post(
        f"{base}/groups",
        headers=manager,
        json={
            "name": "Size",
            "min_selections": 1,
            "max_selections": 1,
            "options": [
                {"name": "Small", "kind": "choice"},
                {"name": "Large", "kind": "choice", "price_delta_cents": 100},
            ],
        },
    )
    assert response.status_code == 201
    size = next(g for g in response.json()["groups"] if g["name"] == "Size")

    lemonade = {
        "category_id": drinks["id"],
        "name": "Lemonade",
        "price_cents": 300,
        "prep_minutes": 2,
        "aliases": ["lemon drink"],
        "group_ids": [size["id"]],
    }
    response = await db_client.post(f"{base}/items", headers=manager, json=lemonade)
    assert response.status_code == 201
    (item,) = response.json()["categories"][1]["items"]
    response = await db_client.post(f"{base}/items", headers=manager, json=lemonade)
    assert (response.status_code, response.json()["code"]) == (409, "name_taken")
    response = await db_client.post(
        f"{base}/items", headers=manager, json={**lemonade, "price_cents": -1}
    )
    assert response.status_code == 422

    response = await db_client.put(
        f"{base}/items/{item['id']}",
        headers=manager,
        json={**lemonade, "price_cents": 350},
    )
    assert response.status_code == 200

    # A customer orders the new item at the new price.
    carts = f"/restaurants/{shop.restaurant_id}/carts"
    cart = (await db_client.post(carts)).json()
    large = size["options"][1]["id"]
    response = await db_client.post(
        f"{carts}/{cart['id']}/lines",
        json={"menu_item_id": item["id"], "option_ids": [large]},
    )
    assert response.status_code == 200
    assert response.json()["subtotal_cents"] == 450

    mains = (await db_client.get(base, headers=manager)).json()["categories"][0]
    ids = [entry["id"] for entry in mains["items"]]
    response = await db_client.put(
        f"{base}/categories/{mains['id']}/items/order",
        headers=manager,
        json={"ids": ids[::-1]},
    )
    assert response.status_code == 200
    assert [e["id"] for e in response.json()["categories"][0]["items"]] == ids[::-1]
    response = await db_client.put(
        f"{base}/categories/order",
        headers=manager,
        json={"ids": [drinks["id"], mains["id"]]},
    )
    assert [c["name"] for c in response.json()["categories"]] == ["Drinks", "Mains"]
    response = await db_client.patch(
        f"{base}/categories/{drinks['id']}", headers=manager, json={"name": "Cold"}
    )
    assert response.json()["categories"][0]["name"] == "Cold"

    response = await db_client.delete(
        f"{base}/categories/{drinks['id']}", headers=manager
    )
    assert (response.status_code, response.json()["code"]) == (
        409,
        "category_not_empty",
    )
    response = await db_client.delete(f"{base}/groups/{size['id']}", headers=manager)
    assert response.status_code == 200
    response = await db_client.delete(f"{base}/items/{item['id']}", headers=manager)
    assert response.status_code == 200
    response = await db_client.delete(
        f"{base}/categories/{drinks['id']}", headers=manager
    )
    assert [c["name"] for c in response.json()["categories"]] == ["Mains"]
    assert all(g["name"] != "Size" for g in response.json()["groups"])
    response = await db_client.delete(f"{base}/items/{item['id']}", headers=manager)
    assert response.status_code == 404

    public = (await db_client.get(f"/restaurants/{shop.restaurant_id}/menu")).json()
    assert [c["name"] for c in public["categories"]] == ["Mains"]


async def test_manager_sets_settings_and_hours(
    db_client: AsyncClient, shop: Shop, manager: dict[str, str]
) -> None:
    base = admin_url(shop)
    setup = (await db_client.get(f"{base}/settings", headers=manager)).json()
    assert setup["settings"] == {
        "tax_rate_bps": 875,
        "kitchen_capacity": 3,
        "last_order_minutes": 15,
    }
    assert setup["hours"][0] == {
        "weekday": 0,
        "opens_at": "09:00:00",
        "closes_at": "21:00:00",
    }

    response = await db_client.put(
        f"{base}/settings",
        headers=manager,
        json={"tax_rate_bps": 900, "kitchen_capacity": 2, "last_order_minutes": 0},
    )
    assert response.status_code == 200
    assert response.json()["settings"]["tax_rate_bps"] == 900
    response = await db_client.put(
        f"{base}/settings", headers=manager, json={"tax_rate_bps": 900}
    )
    assert response.status_code == 422

    overlap = [
        {"weekday": 0, "opens_at": "09:00", "closes_at": "15:00"},
        {"weekday": 0, "opens_at": "14:00", "closes_at": "21:00"},
    ]
    response = await db_client.put(
        f"{base}/hours", headers=manager, json={"hours": overlap}
    )
    assert (response.status_code, response.json()["code"]) == (422, "hours_overlap")

    # Closed all week: no orders are taken.
    response = await db_client.put(f"{base}/hours", headers=manager, json={"hours": []})
    assert response.json()["hours"] == []
    status = await db_client.get(f"/restaurants/{shop.restaurant_id}/ordering-status")
    assert status.json()["accepting"] is False
