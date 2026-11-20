from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.orders import service
from app.orders.errors import OrderError, PickupUnavailable
from app.orders.models import AddLine, Order
from app.staff import service as staff_service
from app.staff.models import ControlsChange, StaffUser
from app.staff.pins import hash_pin, verify_pin
from tests.conftest import FakeClock
from tests.factories import STAFF_PIN, Shop, create_shop, create_staff, place_order

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(conn: asyncpg.Connection) -> Shop:
    return await create_shop(conn)


@pytest.fixture
async def staff(conn: asyncpg.Connection, shop: Shop) -> StaffUser:
    return await create_staff(conn, shop.restaurant_id)


async def draft_with_fries(conn: asyncpg.Connection, shop: Shop) -> Order:
    draft = await service.create_draft(conn, shop.restaurant_id, "storefront")
    return await service.add_item(
        conn, shop.restaurant_id, draft.id, AddLine(menu_item_id=shop.fries)
    )


async def placed_order(conn: asyncpg.Connection, shop: Shop) -> Order:
    draft = await draft_with_fries(conn, shop)
    return await place_order(conn, shop.restaurant_id, draft.id)


async def act(
    conn: asyncpg.Connection, shop: Shop, order: Order, staff: StaffUser, *actions, **kw
) -> Order:
    for action in actions:
        order = await service.apply_staff_action(
            conn, shop.restaurant_id, order.id, action, staff.id, **kw
        )
    return order


def test_pin_hash_verifies_only_its_own_pin() -> None:
    stored = hash_pin("246810")

    assert stored != hash_pin("246810")  # salted
    assert verify_pin("246810", stored)
    assert not verify_pin("246811", stored)
    assert not verify_pin("246810", "not a hash")


async def test_order_moves_through_the_kitchen(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    order = await placed_order(conn, shop)

    statuses = []
    for action in ("accept", "prepare", "ready", "picked_up"):
        order = await act(conn, shop, order, staff, action)
        statuses.append(order.status)

    assert statuses == ["accepted", "preparing", "ready", "picked_up"]
    events = await conn.fetch(
        """
        SELECT from_status, to_status, actor_type, actor_id
        FROM order_events WHERE order_id = $1 ORDER BY seq
        """,
        order.id,
    )
    assert [(e["from_status"], e["to_status"]) for e in events] == [
        ("draft", "placed"),
        ("placed", "accepted"),
        ("accepted", "preparing"),
        ("preparing", "ready"),
        ("ready", "picked_up"),
    ]
    assert [(e["actor_type"], e["actor_id"]) for e in events] == [
        ("customer", None),
        *[("staff", staff.id)] * 4,
    ]


@pytest.mark.parametrize(
    ("before", "action"),
    [
        ((), "prepare"),
        ((), "ready"),
        ((), "picked_up"),
        ((), "no_show"),
        (("accept",), "reject"),
        (("accept",), "ready"),
        (("accept", "prepare"), "cancel"),
        (("accept", "prepare"), "reject"),
        (("accept", "prepare"), "accept"),
        (("accept", "prepare", "ready"), "cancel"),
        (("accept", "prepare", "ready", "picked_up"), "no_show"),
        (("cancel",), "accept"),
    ],
)
async def test_action_from_the_wrong_status_is_refused(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser, before: tuple, action: str
) -> None:
    order = await act(conn, shop, await placed_order(conn, shop), staff, *before)

    with pytest.raises(OrderError) as error:
        await act(conn, shop, order, staff, action, reason="Out of fries")

    assert error.value.code == "invalid_transition"
    after = await service.get_order(conn, shop.restaurant_id, order.id)
    assert after.status == order.status


async def test_draft_cannot_be_acted_on(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    draft = await draft_with_fries(conn, shop)

    for action in ("accept", "cancel"):
        with pytest.raises(OrderError) as error:
            await act(conn, shop, draft, staff, action)
        assert error.value.code == "invalid_transition"


async def test_repeated_action_changes_nothing(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    order = await placed_order(conn, shop)
    accepted = await act(conn, shop, order, staff, "accept", extra_minutes=10)

    again = await act(conn, shop, order, staff, "accept", extra_minutes=10)

    assert again == accepted
    count = await conn.fetchval(
        "SELECT count(*) FROM order_events WHERE order_id = $1", order.id
    )
    assert count == 2


async def test_accept_can_push_the_pickup_time_back(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    order = await placed_order(conn, shop)

    accepted = await act(conn, shop, order, staff, "accept", extra_minutes=10)

    assert accepted.promised_pickup_at == order.promised_pickup_at + timedelta(
        minutes=10
    )
    # Only accepting moves the time.
    preparing = await act(conn, shop, accepted, staff, "prepare", extra_minutes=10)
    assert preparing.promised_pickup_at == accepted.promised_pickup_at


async def test_reject_needs_a_reason_and_keeps_it(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    order = await placed_order(conn, shop)

    with pytest.raises(OrderError) as error:
        await act(conn, shop, order, staff, "reject")
    assert error.value.code == "reason_required"

    rejected = await act(conn, shop, order, staff, "reject", reason="Out of fries")

    assert rejected.status == "rejected"
    reason = await conn.fetchval(
        "SELECT reason FROM order_events WHERE order_id = $1 AND to_status = 'rejected'",
        order.id,
    )
    assert reason == "Out of fries"


async def test_cancel_and_no_show(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    first = await placed_order(conn, shop)
    second = await placed_order(conn, shop)

    cancelled = await act(conn, shop, first, staff, "accept", "cancel")
    no_show = await act(conn, shop, second, staff, "accept", "prepare", "ready")
    no_show = await act(conn, shop, no_show, staff, "no_show")

    assert (cancelled.status, no_show.status) == ("cancelled", "no_show")


async def test_action_on_another_restaurants_order_is_not_found(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    other = await create_shop(conn, "Other Diner")
    order = await placed_order(conn, other)

    with pytest.raises(OrderError) as error:
        await act(conn, shop, order, staff, "accept")

    assert error.value.code == "order_not_found"


async def test_active_orders_lists_the_board_soonest_first(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser, clock: FakeClock
) -> None:
    later = await placed_order(conn, shop)
    sooner = await placed_order(conn, shop)
    done = await placed_order(conn, shop)
    await draft_with_fries(conn, shop)
    # Pushed back past the order placed after it.
    later = await act(conn, shop, later, staff, "accept", extra_minutes=10)
    await act(conn, shop, done, staff, "accept", "prepare", "ready", "picked_up")
    assert sooner.promised_pickup_at < later.promised_pickup_at

    board = await service.active_orders(conn, shop.restaurant_id)

    assert [order.id for order in board] == [sooner.id, later.id]
    assert [order.status for order in board] == ["placed", "accepted"]
    assert [line.name for line in board[0].lines] == ["Fries"]


async def test_login_opens_a_session_for_its_restaurant(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser, clock: FakeClock
) -> None:
    other = await create_shop(conn, "Other Diner")

    session = await staff_service.login(conn, shop.restaurant_id, STAFF_PIN)

    assert session.staff == staff
    assert session.expires_at == clock.now + staff_service.SESSION_LIFETIME
    here = await staff_service.authenticate(conn, shop.restaurant_id, session.token)
    assert here == staff
    assert (
        await staff_service.authenticate(conn, other.restaurant_id, session.token)
        is None
    )
    assert await staff_service.authenticate(conn, shop.restaurant_id, "nope") is None
    # Only a hash of the token is stored.
    stored = await conn.fetchval("SELECT token_hash FROM staff_sessions")
    assert stored != session.token


async def test_session_ends_on_expiry_logout_and_deactivation(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser, clock: FakeClock
) -> None:
    restaurant_id = shop.restaurant_id
    expired = await staff_service.login(conn, restaurant_id, STAFF_PIN)
    clock.now += staff_service.SESSION_LIFETIME
    assert await staff_service.authenticate(conn, restaurant_id, expired.token) is None

    left = await staff_service.login(conn, restaurant_id, STAFF_PIN)
    await staff_service.logout(conn, restaurant_id, left.token)
    assert await staff_service.authenticate(conn, restaurant_id, left.token) is None

    fired = await staff_service.login(conn, restaurant_id, STAFF_PIN)
    await conn.execute(
        "UPDATE staff_users SET is_active = false WHERE id = $1", staff.id
    )
    assert await staff_service.authenticate(conn, restaurant_id, fired.token) is None
    with pytest.raises(staff_service.LoginFailed):
        await staff_service.login(conn, restaurant_id, STAFF_PIN)


async def test_login_with_a_wrong_pin_or_restaurant_fails(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    other = await create_shop(conn, "Other Diner")

    for restaurant_id, pin in [
        (shop.restaurant_id, "000000"),
        (other.restaurant_id, STAFF_PIN),
        (uuid4(), STAFF_PIN),
    ]:
        with pytest.raises(staff_service.LoginFailed):
            await staff_service.login(conn, restaurant_id, pin)


async def test_too_many_wrong_pins_lock_sign_in_for_a_while(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser, clock: FakeClock
) -> None:
    restaurant_id = shop.restaurant_id
    for _ in range(staff_service.MAX_FAILED_LOGINS):
        with pytest.raises(staff_service.LoginFailed):
            await staff_service.login(conn, restaurant_id, "000000")

    # Even the right PIN waits out the lock.
    with pytest.raises(staff_service.LoginLocked) as locked:
        await staff_service.login(conn, restaurant_id, STAFF_PIN)
    assert locked.value.locked_until == clock.now + staff_service.LOGIN_LOCK

    clock.now += staff_service.LOGIN_LOCK
    session = await staff_service.login(conn, restaurant_id, STAFF_PIN)
    assert session.staff == staff


async def test_a_good_login_resets_the_wrong_pin_count(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    restaurant_id = shop.restaurant_id
    for _ in range(2):
        for _ in range(staff_service.MAX_FAILED_LOGINS - 1):
            with pytest.raises(staff_service.LoginFailed):
                await staff_service.login(conn, restaurant_id, "000000")
        await staff_service.login(conn, restaurant_id, STAFF_PIN)


async def test_two_active_staff_cannot_share_a_pin(
    conn: asyncpg.Connection, shop: Shop, staff: StaffUser
) -> None:
    other = await create_shop(conn, "Other Diner")

    with pytest.raises(staff_service.PinTaken):
        await create_staff(conn, shop.restaurant_id, "Blake")

    # Another restaurant, or another PIN, is fine.
    await create_staff(conn, other.restaurant_id, "Blake")
    blake = await create_staff(conn, shop.restaurant_id, "Blake", pin="135791")
    session = await staff_service.login(conn, shop.restaurant_id, "135791")
    assert session.staff == blake


async def test_pausing_stops_new_orders_and_resuming_takes_them_again(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    restaurant_id = shop.restaurant_id
    draft = await draft_with_fries(conn, shop)

    controls = await staff_service.set_controls(
        conn, restaurant_id, ControlsChange(accepting_orders=False)
    )

    assert (controls.accepting_orders, controls.busy_extra_minutes) == (False, 0)
    assert not (await service.ordering_status(conn, restaurant_id)).accepting
    with pytest.raises(PickupUnavailable) as error:
        await place_order(conn, restaurant_id, draft.id)
    assert error.value.code == "ordering_paused"

    await staff_service.set_controls(
        conn, restaurant_id, ControlsChange(accepting_orders=True)
    )
    assert (await place_order(conn, restaurant_id, draft.id)).status == "placed"


async def test_busy_mode_adds_to_the_wait(conn: asyncpg.Connection, shop: Shop) -> None:
    restaurant_id = shop.restaurant_id
    before = await service.ordering_status(conn, restaurant_id)

    controls = await staff_service.set_controls(
        conn, restaurant_id, ControlsChange(busy_extra_minutes=20)
    )

    assert controls == await staff_service.get_controls(conn, restaurant_id)
    assert (controls.accepting_orders, controls.busy_extra_minutes) == (True, 20)
    after = await service.ordering_status(conn, restaurant_id)
    assert after.wait_minutes == before.wait_minutes + 20
