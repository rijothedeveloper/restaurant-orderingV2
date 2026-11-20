from datetime import time
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app import cli
from app.orders import service as orders
from app.orders.errors import OrderError
from app.orders.pickup import OpenWindow, windows_overlap
from app.staff import service
from app.staff.models import KitchenSettings, OpenHours, StaffChange, StaffUser
from tests.factories import STAFF_PIN, Shop, create_shop, create_staff

MANAGER_PIN = "135790"


def window(weekday: int, opens: str, closes: str) -> OpenWindow:
    return OpenWindow(weekday, time.fromisoformat(opens), time.fromisoformat(closes))


@pytest.mark.parametrize(
    ("windows", "overlap"),
    [
        ([], False),
        ([window(0, "09:00", "21:00")], False),
        # A full day, every day: each touches the next.
        ([window(day, "00:00", "00:00") for day in range(7)], False),
        ([window(0, "09:00", "14:00"), window(0, "14:00", "21:00")], False),
        ([window(0, "09:00", "14:30"), window(0, "14:00", "21:00")], True),
        ([window(0, "09:00", "21:00"), window(0, "09:00", "12:00")], True),
        # Past midnight into the next day's opening.
        ([window(4, "17:00", "02:00"), window(5, "02:00", "10:00")], False),
        ([window(4, "17:00", "02:00"), window(5, "01:00", "10:00")], True),
        # Sunday night runs into Monday morning.
        ([window(6, "20:00", "03:00"), window(0, "03:00", "09:00")], False),
        ([window(6, "20:00", "03:00"), window(0, "02:00", "09:00")], True),
    ],
)
def test_windows_overlap(windows: list[OpenWindow], overlap: bool) -> None:
    assert windows_overlap(windows) is overlap
    assert windows_overlap(windows[::-1]) is overlap


def test_hours_are_whole_local_minutes() -> None:
    with pytest.raises(ValidationError, match="whole minutes"):
        OpenHours(weekday=0, opens_at="09:00:30", closes_at="21:00")
    with pytest.raises(ValidationError):
        OpenHours(weekday=7, opens_at="09:00", closes_at="21:00")


@pytest.fixture
async def shop(conn: asyncpg.Connection) -> Shop:
    return await create_shop(conn)


@pytest.fixture
async def manager(conn: asyncpg.Connection, shop: Shop) -> StaffUser:
    return await create_staff(conn, shop.restaurant_id, "Maria", MANAGER_PIN, "manager")


@pytest.fixture
async def cook(conn: asyncpg.Connection, shop: Shop) -> StaffUser:
    return await create_staff(conn, shop.restaurant_id)


async def error_code(awaitable) -> str:
    with pytest.raises(OrderError) as raised:
        await awaitable
    return raised.value.code


@pytest.mark.integration
async def test_staff_carry_their_role(
    conn: asyncpg.Connection, shop: Shop, manager: StaffUser, cook: StaffUser
) -> None:
    rid = shop.restaurant_id
    assert (manager.role, cook.role) == ("manager", "staff")
    session = await service.login(conn, rid, MANAGER_PIN)
    assert session.staff.role == "manager"
    assert (await service.authenticate(conn, rid, session.token)).role == "manager"

    listed = await service.list_staff(conn, rid)
    assert [(member.name, member.role, member.is_active) for member in listed] == [
        ("Alex", "staff", True),
        ("Maria", "manager", True),
    ]


@pytest.mark.integration
async def test_staff_user_is_renamed_and_promoted(
    conn: asyncpg.Connection, shop: Shop, cook: StaffUser
) -> None:
    rid = shop.restaurant_id
    session = await service.login(conn, rid, STAFF_PIN)

    changed = await service.update_staff_user(
        conn, rid, cook.id, StaffChange(name=" Alexis ", role="manager")
    )

    assert (changed.name, changed.role, changed.is_active) == (
        "Alexis",
        "manager",
        True,
    )
    # The open session stays, and has the new role at once.
    staff = await service.authenticate(conn, rid, session.token)
    assert (staff.name, staff.role) == ("Alexis", "manager")

    other = await create_shop(conn, "Other Diner")
    for restaurant_id, staff_id in ((other.restaurant_id, cook.id), (rid, uuid4())):
        change = service.update_staff_user(
            conn, restaurant_id, staff_id, StaffChange(name="X")
        )
        assert await error_code(change) == "staff_not_found"


@pytest.mark.integration
async def test_new_pin_replaces_the_old_and_ends_sessions(
    conn: asyncpg.Connection, shop: Shop, manager: StaffUser, cook: StaffUser
) -> None:
    rid = shop.restaurant_id
    session = await service.login(conn, rid, STAFF_PIN)

    taken = service.update_staff_user(conn, rid, cook.id, StaffChange(pin=MANAGER_PIN))
    assert await error_code(taken) == "pin_taken"
    # Setting your own PIN again is not a clash.
    await service.update_staff_user(conn, rid, manager.id, StaffChange(pin=MANAGER_PIN))

    await service.update_staff_user(conn, rid, cook.id, StaffChange(pin="111222"))

    assert await service.authenticate(conn, rid, session.token) is None
    with pytest.raises(service.LoginFailed):
        await service.login(conn, rid, STAFF_PIN)
    assert (await service.login(conn, rid, "111222")).staff.id == cook.id


@pytest.mark.integration
async def test_staff_user_is_turned_off_and_back_on(
    conn: asyncpg.Connection, shop: Shop, manager: StaffUser, cook: StaffUser
) -> None:
    rid = shop.restaurant_id
    session = await service.login(conn, rid, STAFF_PIN)

    off = await service.update_staff_user(
        conn, rid, cook.id, StaffChange(is_active=False)
    )

    assert off.is_active is False
    assert await service.authenticate(conn, rid, session.token) is None
    with pytest.raises(service.LoginFailed):
        await service.login(conn, rid, STAFF_PIN)
    # Their PIN is free for someone else, so coming back needs a new one.
    await create_staff(conn, rid, "Sam", STAFF_PIN)
    back = service.update_staff_user(conn, rid, cook.id, StaffChange(is_active=True))
    assert await error_code(back) == "pin_required"
    back = service.update_staff_user(
        conn, rid, cook.id, StaffChange(is_active=True, pin=STAFF_PIN)
    )
    assert await error_code(back) == "pin_taken"
    back = await service.update_staff_user(
        conn, rid, cook.id, StaffChange(is_active=True, pin="111222")
    )
    assert back.is_active is True
    assert (await service.login(conn, rid, "111222")).staff.id == cook.id


@pytest.mark.integration
async def test_restaurant_keeps_one_active_manager(
    conn: asyncpg.Connection, shop: Shop, manager: StaffUser, cook: StaffUser
) -> None:
    rid = shop.restaurant_id
    for change in (StaffChange(role="staff"), StaffChange(is_active=False)):
        refused = service.update_staff_user(conn, rid, manager.id, change)
        assert await error_code(refused) == "last_manager"
    # A manager who is turned off does not count.
    second = await create_staff(conn, rid, "Lee", "999000", "manager")
    await service.update_staff_user(conn, rid, second.id, StaffChange(is_active=False))
    refused = service.update_staff_user(
        conn, rid, manager.id, StaffChange(role="staff")
    )
    assert await error_code(refused) == "last_manager"

    await service.update_staff_user(conn, rid, cook.id, StaffChange(role="manager"))
    demoted = await service.update_staff_user(
        conn, rid, manager.id, StaffChange(role="staff")
    )
    assert demoted.role == "staff"


@pytest.mark.integration
async def test_settings_and_hours_change_what_orders_get(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    setup = await service.get_setup(conn, rid)
    assert (setup.restaurant_name, setup.timezone) == (
        "Test Diner",
        "America/Los_Angeles",
    )
    assert setup.settings == KitchenSettings(
        tax_rate_bps=875, kitchen_capacity=3, last_order_minutes=15
    )
    assert len(setup.hours) == 7

    await service.set_kitchen_settings(
        conn,
        rid,
        KitchenSettings(tax_rate_bps=1000, kitchen_capacity=1, last_order_minutes=30),
    )
    # NOON is a Wednesday; close that day.
    hours = [
        OpenHours(weekday=day, opens_at="09:00", closes_at="21:00")
        for day in range(7)
        if day != 2
    ]
    await service.set_hours(conn, rid, hours)

    setup = await service.get_setup(conn, rid)
    assert setup.settings.tax_rate_bps == 1000
    assert setup.hours == hours
    assert (await orders.ordering_status(conn, rid)).accepting is False
    # The pause and busy switches are untouched.
    assert (await service.get_controls(conn, rid)).accepting_orders is True

    overlapping = [
        OpenHours(weekday=0, opens_at="09:00", closes_at="15:00"),
        OpenHours(weekday=0, opens_at="14:00", closes_at="21:00"),
    ]
    assert await error_code(service.set_hours(conn, rid, overlapping)) == (
        "hours_overlap"
    )
    assert (await service.get_setup(conn, rid)).hours == hours


@pytest.mark.integration
async def test_cli_restaurant_is_ready_for_its_first_manager(
    conn: asyncpg.Connection,
) -> None:
    rid = await cli.create_restaurant(conn, "Luigi's", "America/New_York", 888)

    manager = await create_staff(conn, rid, "Maria", MANAGER_PIN, "manager")
    assert (await service.login(conn, rid, MANAGER_PIN)).staff == manager
    setup = await service.get_setup(conn, rid)
    assert (setup.timezone, setup.settings.tax_rate_bps) == ("America/New_York", 888)
    assert setup.hours == []
    assert (await service.get_controls(conn, rid)).accepting_orders is True


def test_cli_reads_a_tax_percentage() -> None:
    assert cli._tax_rate_bps("8.75") == 875
    assert cli._tax_rate_bps("0") == 0
    for bad in ("8.755", "101", "-1", "abc"):
        with pytest.raises(Exception, match="percentage"):
            cli._tax_rate_bps(bad)
