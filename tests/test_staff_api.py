from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest
from httpx import AsyncClient

from app.staff import service as staff_service
from tests.factories import STAFF_PIN, Shop, committed_shop, create_staff

pytestmark = pytest.mark.integration

CUSTOMER = {"name": "Sam", "phone": "(415) 555-2671"}


@pytest.fixture
async def shop(test_database_url: str) -> AsyncIterator[Shop]:
    async with committed_shop(test_database_url, f"Staff Diner {uuid4()}") as shop:
        connection = await asyncpg.connect(test_database_url)
        try:
            await create_staff(connection, shop.restaurant_id)
        finally:
            await connection.close()
        yield shop


def staff_url(shop: Shop) -> str:
    return f"/restaurants/{shop.restaurant_id}/staff"


@pytest.fixture
async def auth(db_client: AsyncClient, shop: Shop) -> dict[str, str]:
    response = await db_client.post(f"{staff_url(shop)}/login", json={"pin": STAFF_PIN})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def place_fries(db_client: AsyncClient, shop: Shop) -> dict:
    carts = f"/restaurants/{shop.restaurant_id}/carts"
    cart = (await db_client.post(carts)).json()
    cart = (
        await db_client.post(
            f"{carts}/{cart['id']}/lines", json={"menu_item_id": str(shop.fries)}
        )
    ).json()
    response = await db_client.post(
        f"{carts}/{cart['id']}/checkout",
        json={"revision": cart["revision"], **CUSTOMER},
    )
    assert response.status_code == 200
    return response.json()


async def test_login_returns_a_session(db_client: AsyncClient, shop: Shop) -> None:
    response = await db_client.post(f"{staff_url(shop)}/login", json={"pin": STAFF_PIN})

    assert response.status_code == 200
    body = response.json()
    assert body["staff"]["name"] == "Alex"
    assert body["token"] and body["expires_at"]


async def test_wrong_pins_are_refused_then_locked(
    db_client: AsyncClient, shop: Shop
) -> None:
    login = f"{staff_url(shop)}/login"

    # Not a PIN at all: refused without counting as an attempt.
    assert (await db_client.post(login, json={"pin": "12"})).status_code == 422

    for _ in range(staff_service.MAX_FAILED_LOGINS):
        response = await db_client.post(login, json={"pin": "000000"})
        assert response.status_code == 401

    response = await db_client.post(login, json={"pin": STAFF_PIN})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) == 300


async def test_staff_routes_need_a_session_for_this_restaurant(
    db_client: AsyncClient, shop: Shop, auth: dict[str, str], test_database_url: str
) -> None:
    board = f"{staff_url(shop)}/orders"

    assert (await db_client.get(board)).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert (await db_client.get(board, headers=bad)).status_code == 401
    assert (await db_client.get(board, headers=auth)).status_code == 200

    async with committed_shop(test_database_url, f"Other Diner {uuid4()}") as other:
        response = await db_client.get(f"{staff_url(other)}/orders", headers=auth)
        assert response.status_code == 401
        order = await place_fries(db_client, other)
        # This shop's staff cannot reach the other shop's order through their own.
        response = await db_client.post(f"{board}/{order['id']}/accept", headers=auth)
        assert response.status_code == 404


async def test_logout_ends_the_session(
    db_client: AsyncClient, shop: Shop, auth: dict[str, str]
) -> None:
    response = await db_client.post(f"{staff_url(shop)}/logout", headers=auth)
    assert response.status_code == 204

    response = await db_client.get(f"{staff_url(shop)}/orders", headers=auth)
    assert response.status_code == 401


async def test_board_and_order_actions(
    db_client: AsyncClient, shop: Shop, auth: dict[str, str]
) -> None:
    board_url = f"{staff_url(shop)}/orders"
    order = await place_fries(db_client, shop)
    order_url = f"{board_url}/{order['id']}"

    board = (await db_client.get(board_url, headers=auth)).json()
    assert board["controls"] == {"accepting_orders": True, "busy_extra_minutes": 0}
    (listed,) = board["orders"]
    assert listed["id"] == order["id"]
    assert listed["status"] == "placed"
    assert listed["order_number"] == 1
    assert listed["customer_name"] == "Sam"
    assert [line["name"] for line in listed["lines"]] == ["Fries"]

    # Skipping a step is refused.
    response = await db_client.post(f"{order_url}/ready", headers=auth)
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"

    response = await db_client.post(
        f"{order_url}/accept", headers=auth, json={"extra_minutes": 5}
    )
    assert response.status_code == 200
    accepted = response.json()
    assert accepted["status"] == "accepted"
    assert accepted["promised_pickup_at"] != order["promised_pickup_at"]

    # Not one of the allowed pushes, and not an action.
    response = await db_client.post(
        f"{order_url}/accept", headers=auth, json={"extra_minutes": 7}
    )
    assert response.status_code == 422
    response = await db_client.post(f"{order_url}/explode", headers=auth)
    assert response.status_code == 422

    for action, status in [
        ("prepare", "preparing"),
        ("ready", "ready"),
        ("picked_up", "picked_up"),
    ]:
        response = await db_client.post(f"{order_url}/{action}", headers=auth)
        assert (response.status_code, response.json()["status"]) == (200, status)

    board = (await db_client.get(board_url, headers=auth)).json()
    assert board["orders"] == []


async def test_reject_needs_a_reason(
    db_client: AsyncClient, shop: Shop, auth: dict[str, str]
) -> None:
    order = await place_fries(db_client, shop)
    reject_url = f"{staff_url(shop)}/orders/{order['id']}/reject"

    response = await db_client.post(reject_url, headers=auth)
    assert response.status_code == 422
    assert response.json()["code"] == "reason_required"

    response = await db_client.post(
        reject_url, headers=auth, json={"reason": "Out of fries"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_controls_pause_ordering_and_set_busy_mode(
    db_client: AsyncClient, shop: Shop, auth: dict[str, str]
) -> None:
    controls_url = f"{staff_url(shop)}/controls"
    status_url = f"/restaurants/{shop.restaurant_id}/ordering-status"

    assert (await db_client.patch(controls_url, json={})).status_code == 401
    response = await db_client.patch(controls_url, headers=auth, json={})
    assert response.status_code == 422

    response = await db_client.patch(
        controls_url,
        headers=auth,
        json={"accepting_orders": False, "busy_extra_minutes": 20},
    )
    assert response.status_code == 200
    assert response.json() == {"accepting_orders": False, "busy_extra_minutes": 20}
    assert (await db_client.get(controls_url, headers=auth)).json() == response.json()
    status = (await db_client.get(status_url)).json()
    assert (status["accepting"], status["reason"]) == (False, "ordering_paused")

    response = await db_client.patch(
        controls_url, headers=auth, json={"accepting_orders": True}
    )
    assert response.json() == {"accepting_orders": True, "busy_extra_minutes": 20}
    assert (await db_client.get(status_url)).json()["accepting"] is True


async def test_sold_out_toggles_show_on_the_menu(
    db_client: AsyncClient, shop: Shop, auth: dict[str, str]
) -> None:
    menu_url = f"/restaurants/{shop.restaurant_id}/menu"
    item_url = f"{staff_url(shop)}/menu/items/{shop.fries}"
    option_url = f"{staff_url(shop)}/menu/options/{shop.bacon}"
    sold_out = {"is_available": False}

    assert (await db_client.patch(item_url, json=sold_out)).status_code == 401
    assert (
        await db_client.patch(item_url, headers=auth, json=sold_out)
    ).status_code == 204
    assert (
        await db_client.patch(option_url, headers=auth, json=sold_out)
    ).status_code == 204
    for missing in (f"menu/items/{uuid4()}", f"menu/options/{uuid4()}"):
        response = await db_client.patch(
            f"{staff_url(shop)}/{missing}", headers=auth, json=sold_out
        )
        assert response.status_code == 404

    (mains,) = (await db_client.get(menu_url)).json()["categories"]
    items = {item["name"]: item for item in mains["items"]}
    assert items["Fries"]["is_available"] is False
    options = {
        option["name"]: option
        for group in items["Chicken Sandwich"]["modifier_groups"]
        for option in group["options"]
    }
    assert options["Add bacon"]["is_available"] is False
    assert options["Add cheese"]["is_available"] is True

    # A sold-out item can no longer be added to a cart.
    carts = f"/restaurants/{shop.restaurant_id}/carts"
    cart = (await db_client.post(carts)).json()
    response = await db_client.post(
        f"{carts}/{cart['id']}/lines", json={"menu_item_id": str(shop.fries)}
    )
    assert response.status_code != 200

    back_on = {"is_available": True}
    await db_client.patch(item_url, headers=auth, json=back_on)
    response = await db_client.post(
        f"{carts}/{cart['id']}/lines", json={"menu_item_id": str(shop.fries)}
    )
    assert response.status_code == 200
