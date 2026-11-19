from datetime import date, datetime, time, timedelta
from uuid import uuid4

import asyncpg
import pytest

from app.orders import repository, service
from app.orders.errors import (
    LinesUnavailable,
    MissingRequiredChoices,
    OrderError,
    PickupUnavailable,
)
from app.orders.models import AddLine, ChangeLine, CustomerDetails, Order, SetPickup
from app.orders.readback import confirmation_text
from tests.conftest import NOON, FakeClock
from tests.factories import (
    CUSTOMER,
    Shop,
    create_hours,
    create_restaurant,
    create_shop,
    place_order,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(conn: asyncpg.Connection) -> Shop:
    return await create_shop(conn)


@pytest.fixture
async def draft(conn: asyncpg.Connection, shop: Shop) -> Order:
    return await service.create_draft(conn, shop.restaurant_id, "web")


async def add(conn, shop: Shop, draft: Order, item, options=(), quantity=1, notes=None):
    return await service.add_item(
        conn,
        shop.restaurant_id,
        draft.id,
        AddLine(
            menu_item_id=item, quantity=quantity, option_ids=list(options), notes=notes
        ),
    )


def minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


async def set_pickup(conn, shop: Shop, draft: Order, requested_at: datetime | None):
    return await service.set_pickup(
        conn, shop.restaurant_id, draft.id, SetPickup(requested_at=requested_at)
    )


def assert_totals(order: Order, subtotal: int, tax: int) -> None:
    assert order.subtotal_cents == subtotal
    assert order.tax_cents == tax
    assert order.total_cents == subtotal + tax
    assert order.subtotal_cents == sum(line.line_total_cents for line in order.lines)


# --- create_draft ---------------------------------------------------------


async def test_create_draft_is_empty(conn: asyncpg.Connection, shop: Shop) -> None:
    order = await service.create_draft(conn, shop.restaurant_id, "storefront")

    assert order.status == "draft"
    assert order.channel == "storefront"
    assert order.revision == 0
    assert order.lines == []
    assert order.tax_rate_bps == 875
    assert_totals(order, 0, 0)
    assert await service.get_order(conn, shop.restaurant_id, order.id) == order


async def test_create_draft_unknown_restaurant(conn: asyncpg.Connection) -> None:
    with pytest.raises(OrderError) as error:
        await service.create_draft(conn, uuid4(), "web")

    assert error.value.code == "restaurant_not_found"


async def test_create_draft_needs_settings(conn: asyncpg.Connection) -> None:
    restaurant_id = await create_restaurant(conn, "No Settings Diner")

    with pytest.raises(OrderError) as error:
        await service.create_draft(conn, restaurant_id, "web")

    assert error.value.code == "restaurant_not_configured"


# --- add_item -------------------------------------------------------------


async def test_add_item_copies_item_and_options_and_totals(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(
        conn,
        shop,
        draft,
        shop.sandwich,
        options=[shop.bacon, shop.crispy],
        quantity=2,
        notes="  extra napkins ",
    )

    assert order.revision == 1
    (line,) = order.lines
    assert line.menu_item_id == shop.sandwich
    assert line.name == "Chicken Sandwich"
    assert line.quantity == 2
    assert line.unit_price_cents == 1099
    assert line.modifier_total_cents == 150
    assert line.line_total_cents == 2498
    assert line.notes == "extra napkins"
    # Menu order, not the order they were sent in.
    assert [
        (m.group_name, m.name, m.kind, m.price_delta_cents) for m in line.modifiers
    ] == [
        ("Style", "Crispy", "choice", 0),
        ("Extras", "Add bacon", "add", 150),
    ]
    assert line.modifiers[0].option_id == shop.crispy
    # 2498 * 8.75% = 218.575, rounded half-up.
    assert_totals(order, 2498, 219)


async def test_add_item_appends_lines_in_order(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    order = await add(conn, shop, draft, shop.sandwich, options=[shop.grilled])
    order = await add(conn, shop, draft, shop.fries, quantity=3)

    assert [(line.name, line.quantity) for line in order.lines] == [
        ("Fries", 1),
        ("Chicken Sandwich", 1),
        ("Fries", 3),
    ]
    assert order.revision == 3
    # 399 + 1099 + 1197 = 2695; tax 235.8125 -> 236
    assert_totals(order, 2695, 236)


async def test_add_item_missing_required_choice_reports_the_group(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    with pytest.raises(MissingRequiredChoices) as error:
        await add(conn, shop, draft, shop.sandwich, options=[shop.bacon])

    (group,) = error.value.groups
    assert group.name == "Style"
    assert [o.name for o in group.options] == ["Grilled", "Crispy"]

    unchanged = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert unchanged.lines == []
    assert unchanged.revision == 0


@pytest.mark.parametrize(
    ("item", "options", "code"),
    [
        ("shake", [], "item_unavailable"),
        ("sandwich", ["grilled", "sold_out_extra"], "option_unavailable"),
        ("fries", ["bacon"], "option_not_found"),
        ("sandwich", ["grilled", "crispy"], "too_many_selections"),
        ("sandwich", ["grilled", "bacon", "cheese", "avocado"], "too_many_selections"),
        ("sandwich", ["grilled", "bacon", "bacon"], "duplicate_option"),
    ],
)
async def test_add_item_rejections(
    conn: asyncpg.Connection,
    shop: Shop,
    draft: Order,
    item: str,
    options: list[str],
    code: str,
) -> None:
    with pytest.raises(OrderError) as error:
        await add(
            conn,
            shop,
            draft,
            getattr(shop, item),
            options=[getattr(shop, name) for name in options],
        )

    assert error.value.code == code
    unchanged = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert unchanged.lines == []
    assert unchanged.revision == 0


async def test_add_item_from_another_restaurant(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    other = await create_shop(conn, "Other Diner")

    with pytest.raises(OrderError) as error:
        await add(conn, shop, draft, other.fries)

    assert error.value.code == "item_not_found"


async def test_add_item_unknown_item(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    with pytest.raises(OrderError) as error:
        await add(conn, shop, draft, uuid4())

    assert error.value.code == "item_not_found"


# --- change_item ----------------------------------------------------------


async def test_change_item_quantity_keeps_options(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(
        conn, shop, draft, shop.sandwich, options=[shop.crispy, shop.bacon]
    )
    line_id = order.lines[0].id

    order = await service.change_item(
        conn, shop.restaurant_id, draft.id, line_id, ChangeLine(quantity=3)
    )

    (line,) = order.lines
    assert line.id == line_id
    assert line.quantity == 3
    assert [m.name for m in line.modifiers] == ["Crispy", "Add bacon"]
    assert line.line_total_cents == (1099 + 150) * 3
    assert order.revision == 2
    # 3747 * 8.75% = 327.8625 -> 328
    assert_totals(order, 3747, 328)


async def test_change_item_replaces_options(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(
        conn, shop, draft, shop.sandwich, options=[shop.crispy, shop.bacon]
    )

    order = await service.change_item(
        conn,
        shop.restaurant_id,
        draft.id,
        order.lines[0].id,
        ChangeLine(option_ids=[shop.grilled, shop.cheese, shop.avocado]),
    )

    (line,) = order.lines
    assert [m.name for m in line.modifiers] == ["Grilled", "Add cheese", "Add avocado"]
    assert line.modifier_total_cents == 250
    assert line.line_total_cents == 1349
    assert_totals(order, 1349, 118)


async def test_change_item_cannot_drop_a_required_choice(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.sandwich, options=[shop.crispy])

    with pytest.raises(MissingRequiredChoices):
        await service.change_item(
            conn,
            shop.restaurant_id,
            draft.id,
            order.lines[0].id,
            ChangeLine(option_ids=[shop.bacon]),
        )

    unchanged = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert unchanged == order


async def test_change_item_notes_set_and_clear(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries, notes="well done")
    line_id = order.lines[0].id

    # A quantity-only change leaves the note alone.
    order = await service.change_item(
        conn, shop.restaurant_id, draft.id, line_id, ChangeLine(quantity=2)
    )
    assert order.lines[0].notes == "well done"

    order = await service.change_item(
        conn, shop.restaurant_id, draft.id, line_id, ChangeLine(notes="no salt")
    )
    assert order.lines[0].notes == "no salt"
    assert order.lines[0].quantity == 2

    order = await service.change_item(
        conn, shop.restaurant_id, draft.id, line_id, ChangeLine(notes=None)
    )
    assert order.lines[0].notes is None


async def test_change_item_unknown_line(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    with pytest.raises(OrderError) as error:
        await service.change_item(
            conn, shop.restaurant_id, draft.id, uuid4(), ChangeLine(quantity=2)
        )

    assert error.value.code == "line_not_found"


async def test_change_item_line_of_another_order(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    other_draft = await service.create_draft(conn, shop.restaurant_id, "web")
    other = await add(conn, shop, other_draft, shop.fries)

    with pytest.raises(OrderError) as error:
        await service.change_item(
            conn,
            shop.restaurant_id,
            draft.id,
            other.lines[0].id,
            ChangeLine(quantity=5),
        )

    assert error.value.code == "line_not_found"


# --- remove_item ----------------------------------------------------------


async def test_remove_item_recalculates(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(
        conn, shop, draft, shop.sandwich, options=[shop.grilled, shop.bacon]
    )
    order = await add(conn, shop, draft, shop.fries)
    sandwich_line = order.lines[0].id

    order = await service.remove_item(conn, shop.restaurant_id, draft.id, sandwich_line)

    assert [line.name for line in order.lines] == ["Fries"]
    assert order.revision == 3
    assert_totals(order, 399, 35)
    leftover = await conn.fetchval(
        "SELECT count(*) FROM order_item_modifiers WHERE order_item_id = $1",
        sandwich_line,
    )
    assert leftover == 0

    order = await service.remove_item(
        conn, shop.restaurant_id, draft.id, order.lines[0].id
    )
    assert order.lines == []
    assert_totals(order, 0, 0)


async def test_remove_item_unknown_line(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    with pytest.raises(OrderError) as error:
        await service.remove_item(conn, shop.restaurant_id, draft.id, uuid4())

    assert error.value.code == "line_not_found"


# --- rules that cut across ------------------------------------------------


async def test_menu_price_change_leaves_existing_lines_alone(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    await conn.execute(
        "UPDATE menu_items SET price_cents = 499, name = 'Large Fries' WHERE id = $1",
        shop.fries,
    )

    order = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert (order.lines[0].name, order.lines[0].unit_price_cents) == ("Fries", 399)

    # A new line uses the new price; the old line keeps its copy.
    order = await add(conn, shop, draft, shop.fries)
    assert [(line.name, line.unit_price_cents) for line in order.lines] == [
        ("Fries", 399),
        ("Large Fries", 499),
    ]
    assert_totals(order, 898, 79)

    # Changing the old line re-prices it from the current menu.
    order = await service.change_item(
        conn, shop.restaurant_id, draft.id, order.lines[0].id, ChangeLine(quantity=1)
    )
    assert order.lines[0].unit_price_cents == 499


async def test_tax_rate_change_applies_at_next_change(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)
    assert_totals(order, 399, 35)
    await conn.execute(
        "UPDATE settings SET tax_rate_bps = 1000 WHERE restaurant_id = $1",
        shop.restaurant_id,
    )

    order = await add(conn, shop, draft, shop.fries)

    assert order.tax_rate_bps == 1000
    assert_totals(order, 798, 80)


async def test_only_drafts_can_change(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)
    line_id = order.lines[0].id
    restaurant_id = shop.restaurant_id
    await place_order(conn, restaurant_id, draft.id)

    for attempt in (
        add(conn, shop, draft, shop.fries),
        service.change_item(
            conn, restaurant_id, draft.id, line_id, ChangeLine(quantity=2)
        ),
        service.remove_item(conn, restaurant_id, draft.id, line_id),
        service.set_customer(conn, restaurant_id, draft.id, CUSTOMER),
        set_pickup(conn, shop, draft, NOON + minutes(60)),
        service.read_back(conn, restaurant_id, draft.id),
    ):
        with pytest.raises(OrderError) as error:
            await attempt
        assert error.value.code == "order_not_draft"

    placed = await service.get_order(conn, restaurant_id, draft.id)
    assert placed.status == "placed"
    assert placed.revision == 1
    assert len(placed.lines) == 1


async def test_order_is_invisible_to_another_restaurant(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    other = await create_shop(conn, "Other Diner")
    order = await add(conn, shop, draft, shop.fries)
    line_id = order.lines[0].id
    line = AddLine(menu_item_id=other.fries)

    for attempt in (
        service.get_order(conn, other.restaurant_id, draft.id),
        service.add_item(conn, other.restaurant_id, draft.id, line),
        service.change_item(
            conn, other.restaurant_id, draft.id, line_id, ChangeLine(quantity=2)
        ),
        service.remove_item(conn, other.restaurant_id, draft.id, line_id),
        service.set_customer(conn, other.restaurant_id, draft.id, CUSTOMER),
        service.read_back(conn, other.restaurant_id, draft.id),
        service.confirm_order(conn, other.restaurant_id, draft.id, 1, CUSTOMER),
    ):
        with pytest.raises(OrderError) as error:
            await attempt
        assert error.value.code == "order_not_found"


# --- set_customer -----------------------------------------------------------


async def test_set_customer_stores_details_and_keeps_the_revision(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)

    order = await service.set_customer(
        conn,
        shop.restaurant_id,
        draft.id,
        CustomerDetails(name=" Sam ", phone="(415) 555-2671"),
    )

    assert order.customer_name == "Sam"
    assert order.customer_phone == "+14155552671"
    assert order.revision == 1
    assert order.status == "draft"


# --- set_pickup -------------------------------------------------------------


async def test_set_pickup_asap_quotes_and_keeps_the_revision(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)

    pickup = await set_pickup(conn, shop, draft, None)

    assert pickup.ready_at == NOON + minutes(10)
    assert pickup.order.requested_pickup_at is None
    assert pickup.order.promised_pickup_at is None
    assert pickup.order.revision == 1


async def test_set_pickup_stores_a_new_time_and_bumps_the_revision(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    one_pm = NOON + minutes(60)

    pickup = await set_pickup(conn, shop, draft, one_pm)

    assert pickup.ready_at == one_pm
    assert pickup.order.requested_pickup_at == one_pm
    assert pickup.order.revision == 2
    # The same time again is no change; back to ASAP is one.
    assert (await set_pickup(conn, shop, draft, one_pm)).order.revision == 2
    asap = await set_pickup(conn, shop, draft, None)
    assert asap.order.requested_pickup_at is None
    assert asap.order.revision == 3


async def test_set_pickup_too_early_leaves_the_draft_as_it_was(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)

    with pytest.raises(PickupUnavailable) as error:
        await set_pickup(conn, shop, draft, NOON + minutes(5))

    assert error.value.code == "pickup_too_early"
    assert error.value.earliest_at == NOON + minutes(10)
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored.requested_pickup_at is None
    assert stored.revision == 1


async def test_set_pickup_counts_the_orders_the_kitchen_has_open(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    # Three open orders at a capacity of three per 10 minutes add 10 minutes.
    for _ in range(3):
        earlier = await service.create_draft(conn, shop.restaurant_id, "web")
        await add(conn, shop, earlier, shop.fries)
        await place_order(conn, shop.restaurant_id, earlier.id)
    await add(conn, shop, draft, shop.fries)

    assert (await set_pickup(conn, shop, draft, None)).ready_at == NOON + minutes(20)

    # Orders the kitchen has finished no longer count.
    await conn.execute(
        "UPDATE orders SET status = 'ready' WHERE restaurant_id = $1 AND status <> $2",
        shop.restaurant_id,
        "draft",
    )
    assert (await set_pickup(conn, shop, draft, None)).ready_at == NOON + minutes(10)


async def test_set_pickup_when_closed_gives_the_next_opening(
    conn: asyncpg.Connection, shop: Shop, draft: Order, clock: FakeClock
) -> None:
    await add(conn, shop, draft, shop.fries)
    clock.now = NOON + timedelta(hours=10)  # 10 PM; the shop closed at 9

    with pytest.raises(PickupUnavailable) as error:
        await set_pickup(conn, shop, draft, None)

    assert error.value.code == "restaurant_closed"
    # 9 AM the next morning.
    assert error.value.next_opening_at == NOON + timedelta(hours=21)


# --- read_back --------------------------------------------------------------


async def test_read_back_records_the_revision_it_read(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.sandwich, [shop.grilled], quantity=2)
    await add(conn, shop, draft, shop.fries)

    read = await service.read_back(conn, shop.restaurant_id, draft.id)

    # 2 x 1099 + 399 = 2597; tax 227.24 -> 227. Noon plus 10 minutes of prep.
    assert read.text == (
        "Your order: 2 x Chicken Sandwich (Grilled); 1 x Fries. "
        "Total with tax: $28.24. Ready for pickup about 12:10 PM. "
        "Pay at pickup. Shall I place it?"
    )
    assert read.pickup_at == NOON + minutes(10)
    assert read.order.revision == 2
    assert read.order.read_back_revision == 2
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored == read.order


async def test_a_change_after_read_back_leaves_it_behind(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    read = await service.read_back(conn, shop.restaurant_id, draft.id)

    order = await add(conn, shop, draft, shop.fries)

    assert order.revision == 2
    assert order.read_back_revision == read.order.revision == 1
    with pytest.raises(OrderError) as error:
        await service.confirm_order(
            conn, shop.restaurant_id, draft.id, order.read_back_revision, CUSTOMER
        )
    assert error.value.code == "order_changed"


async def test_read_back_empty_order(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    with pytest.raises(OrderError) as error:
        await service.read_back(conn, shop.restaurant_id, draft.id)

    assert error.value.code == "order_empty"


async def test_read_back_flags_a_line_that_sold_out(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.sandwich, [shop.crispy])
    order = await add(conn, shop, draft, shop.fries)
    await conn.execute(
        "UPDATE menu_items SET is_available = false WHERE id = $1", shop.fries
    )

    with pytest.raises(LinesUnavailable) as error:
        await service.read_back(conn, shop.restaurant_id, draft.id)

    assert error.value.code == "lines_unavailable"
    assert error.value.lines == [order.lines[1]]
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored.read_back_revision is None


async def test_read_back_speaks_the_requested_pickup_time(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    await set_pickup(conn, shop, draft, NOON + minutes(390))

    read = await service.read_back(conn, shop.restaurant_id, draft.id)

    assert "Ready for pickup about 6:30 PM." in read.text
    assert read.pickup_at == NOON + minutes(390)
    assert read.order.read_back_revision == read.order.revision == 2


async def test_read_back_while_ordering_is_paused(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    await conn.execute(
        "UPDATE settings SET accepting_orders = false WHERE restaurant_id = $1",
        shop.restaurant_id,
    )

    with pytest.raises(PickupUnavailable) as error:
        await service.read_back(conn, shop.restaurant_id, draft.id)

    assert error.value.code == "ordering_paused"
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored.read_back_revision is None


# --- confirm_order ----------------------------------------------------------


async def test_confirm_order_places_it_with_the_next_number(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)

    placed = await service.confirm_order(
        conn, shop.restaurant_id, draft.id, order.revision, CUSTOMER
    )

    assert placed.status == "placed"
    assert placed.order_number == 1
    assert placed.placed_at is not None
    assert placed.customer_name == "Sam"
    assert placed.customer_phone == "+14155552671"
    assert placed.revision == order.revision
    assert placed.lines == order.lines
    assert_totals(placed, 399, 35)

    second = await service.create_draft(conn, shop.restaurant_id, "web")
    await add(conn, shop, second, shop.fries)
    assert (await place_order(conn, shop.restaurant_id, second.id)).order_number == 2


async def test_confirm_order_uses_the_customer_set_earlier(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)
    await service.set_customer(conn, shop.restaurant_id, draft.id, CUSTOMER)

    placed = await service.confirm_order(
        conn, shop.restaurant_id, draft.id, order.revision
    )

    assert placed.status == "placed"
    assert placed.customer_name == "Sam"


async def test_confirm_order_writes_one_event(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    await place_order(conn, shop.restaurant_id, draft.id)

    events = await conn.fetch(
        "SELECT * FROM order_events WHERE order_id = $1", draft.id
    )

    (event,) = events
    assert event["restaurant_id"] == shop.restaurant_id
    assert (event["from_status"], event["to_status"]) == ("draft", "placed")
    assert event["actor_type"] == "customer"
    assert event["actor_id"] is None


async def test_confirm_order_again_returns_the_same_order(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)
    placed = await place_order(conn, shop.restaurant_id, draft.id)

    for revision in (placed.revision, placed.revision + 5):
        again = await service.confirm_order(
            conn,
            shop.restaurant_id,
            draft.id,
            revision,
            CustomerDetails(name="Someone Else", phone="2125550100"),
        )
        assert again == placed

    events = await conn.fetchval(
        "SELECT count(*) FROM order_events WHERE order_id = $1", draft.id
    )
    assert events == 1
    other = await service.create_draft(conn, shop.restaurant_id, "web")
    await add(conn, shop, other, shop.fries)
    assert (await place_order(conn, shop.restaurant_id, other.id)).order_number == 2


@pytest.mark.parametrize("offset", [-1, 1])
async def test_confirm_order_needs_the_current_revision(
    conn: asyncpg.Connection, shop: Shop, draft: Order, offset: int
) -> None:
    order = await add(conn, shop, draft, shop.fries)

    with pytest.raises(OrderError) as error:
        await service.confirm_order(
            conn, shop.restaurant_id, draft.id, order.revision + offset, CUSTOMER
        )

    assert error.value.code == "order_changed"
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored.status == "draft"
    assert stored.customer_name is None


async def test_confirm_order_empty_order(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    with pytest.raises(OrderError) as error:
        await service.confirm_order(conn, shop.restaurant_id, draft.id, 0, CUSTOMER)

    assert error.value.code == "order_empty"


async def test_confirm_order_needs_a_customer(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)

    with pytest.raises(OrderError) as error:
        await service.confirm_order(conn, shop.restaurant_id, draft.id, order.revision)

    assert error.value.code == "customer_required"


async def test_confirm_order_rejects_a_line_that_sold_out(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.sandwich, [shop.grilled, shop.bacon])
    await conn.execute(
        "UPDATE modifier_options SET is_available = false WHERE id = $1", shop.bacon
    )

    with pytest.raises(LinesUnavailable) as error:
        await service.confirm_order(
            conn, shop.restaurant_id, draft.id, order.revision, CUSTOMER
        )

    assert error.value.lines == order.lines
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored.status == "draft"
    assert stored.order_number is None
    # Nothing of the failed attempt is kept, the customer included.
    assert stored.customer_name is None
    counters = await conn.fetchval(
        "SELECT count(*) FROM order_counters WHERE restaurant_id = $1",
        shop.restaurant_id,
    )
    assert counters == 0


async def test_confirm_order_keeps_the_price_the_customer_saw(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)
    await conn.execute(
        "UPDATE menu_items SET price_cents = 999 WHERE id = $1", shop.fries
    )

    placed = await place_order(conn, shop.restaurant_id, draft.id)

    assert placed.lines == order.lines
    assert_totals(placed, 399, 35)


async def test_confirm_order_promises_the_pickup_time(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    await add(conn, shop, draft, shop.fries)

    placed = await place_order(conn, shop.restaurant_id, draft.id)

    assert placed.promised_pickup_at == NOON + minutes(10)
    assert placed.requested_pickup_at is None
    assert confirmation_text(placed) == (
        "Your order is placed. Your order number is 1. "
        "It will be ready for pickup about 12:10 PM. Pay at pickup."
    )

    later = await service.create_draft(conn, shop.restaurant_id, "web")
    await add(conn, shop, later, shop.fries)
    await set_pickup(conn, shop, later, NOON + minutes(60))
    placed_later = await place_order(conn, shop.restaurant_id, later.id)
    assert placed_later.promised_pickup_at == NOON + minutes(60)
    assert placed_later.requested_pickup_at == NOON + minutes(60)


async def test_confirm_order_quotes_again_when_it_places(
    conn: asyncpg.Connection, shop: Shop, draft: Order, clock: FakeClock
) -> None:
    await add(conn, shop, draft, shop.fries)
    read = await service.read_back(conn, shop.restaurant_id, draft.id)
    assert read.pickup_at == NOON + minutes(10)
    clock.now = NOON + minutes(7)  # a slow yes

    placed = await service.confirm_order(
        conn, shop.restaurant_id, draft.id, read.order.revision, CUSTOMER
    )

    # 12:07 plus 10 minutes, rounded up.
    assert placed.promised_pickup_at == NOON + minutes(20)


async def test_confirm_order_rejects_while_closed_or_paused(
    conn: asyncpg.Connection, shop: Shop, draft: Order, clock: FakeClock
) -> None:
    order = await add(conn, shop, draft, shop.fries)

    async def confirm() -> PickupUnavailable:
        with pytest.raises(PickupUnavailable) as error:
            await service.confirm_order(
                conn, shop.restaurant_id, draft.id, order.revision, CUSTOMER
            )
        return error.value

    clock.now = NOON + timedelta(hours=8, minutes=50)  # 8:50 PM, closing at 9
    assert (await confirm()).code == "past_last_order"
    clock.now = NOON + timedelta(hours=10)
    closed = await confirm()
    assert closed.code == "restaurant_closed"
    assert closed.next_opening_at == NOON + timedelta(hours=21)

    clock.now = NOON
    await conn.execute(
        "UPDATE settings SET accepting_orders = false WHERE restaurant_id = $1",
        shop.restaurant_id,
    )
    assert (await confirm()).code == "ordering_paused"

    # Nothing of the failed attempts is kept.
    stored = await service.get_order(conn, shop.restaurant_id, draft.id)
    assert stored.status == "draft"
    assert stored.customer_name is None
    assert stored.promised_pickup_at is None
    counters = await conn.fetchval(
        "SELECT count(*) FROM order_counters WHERE restaurant_id = $1",
        shop.restaurant_id,
    )
    assert counters == 0


async def test_confirm_order_rejects_a_requested_time_that_has_slipped_away(
    conn: asyncpg.Connection, shop: Shop, draft: Order, clock: FakeClock
) -> None:
    await add(conn, shop, draft, shop.fries)
    pickup = await set_pickup(conn, shop, draft, NOON + minutes(15))
    clock.now = NOON + minutes(10)

    with pytest.raises(PickupUnavailable) as error:
        await service.confirm_order(
            conn, shop.restaurant_id, draft.id, pickup.order.revision, CUSTOMER
        )

    assert error.value.code == "pickup_too_early"
    assert error.value.earliest_at == NOON + minutes(20)


async def test_cancelled_draft_cannot_be_placed(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    order = await add(conn, shop, draft, shop.fries)
    await conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = $1", draft.id)

    with pytest.raises(OrderError) as error:
        await service.confirm_order(
            conn, shop.restaurant_id, draft.id, order.revision, CUSTOMER
        )

    assert error.value.code == "order_not_draft"


async def test_order_numbers_count_per_restaurant(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    other = await create_shop(conn, "Other Diner")
    other_draft = await service.create_draft(conn, other.restaurant_id, "web")
    await add(conn, shop, draft, shop.fries)
    await add(conn, other, other_draft, other.fries)

    first = await place_order(conn, shop.restaurant_id, draft.id)
    other_first = await place_order(conn, other.restaurant_id, other_draft.id)

    assert first.order_number == 1
    assert other_first.order_number == 1


async def test_order_numbers_restart_each_business_date(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    async def take(day: date) -> int:
        return await repository.next_order_number(conn, shop.restaurant_id, day)

    monday, tuesday = date(2026, 11, 16), date(2026, 11, 17)

    assert [await take(monday), await take(monday), await take(monday)] == [1, 2, 3]
    assert await take(tuesday) == 1
    assert await take(monday) == 4


async def test_business_date_is_the_day_the_restaurant_opened(
    conn: asyncpg.Connection, shop: Shop, clock: FakeClock
) -> None:
    # An evening that runs to 1 AM: after midnight still counts as Wednesday.
    await conn.execute(
        "DELETE FROM opening_hours WHERE restaurant_id = $1", shop.restaurant_id
    )
    await create_hours(conn, shop.restaurant_id, time(17), time(1))

    async def place() -> asyncpg.Record:
        order = await service.create_draft(conn, shop.restaurant_id, "web")
        await add(conn, shop, order, shop.fries)
        await place_order(conn, shop.restaurant_id, order.id)
        return await conn.fetchrow(
            "SELECT business_date, order_number FROM orders WHERE id = $1", order.id
        )

    wednesday, thursday = date(2026, 11, 18), date(2026, 11, 19)
    clock.now = NOON + timedelta(hours=6)  # Wednesday 6 PM
    assert tuple(await place()) == (wednesday, 1)
    clock.now = NOON + timedelta(hours=12, minutes=30)  # Thursday 12:30 AM
    assert tuple(await place()) == (wednesday, 2)
    clock.now = NOON + timedelta(hours=30)  # Thursday 6 PM
    assert tuple(await place()) == (thursday, 1)


async def test_business_date_follows_the_restaurant_timezone(
    conn: asyncpg.Connection,
) -> None:
    # 25 hours apart: noon in Los Angeles is Thursday 10 AM in Kiritimati and
    # Wednesday 9 AM in Niue, so both are open, on different dates.
    east = await create_shop(conn, "East Diner")
    west = await create_shop(conn, "West Diner")
    for shop, timezone in ((east, "Pacific/Kiritimati"), (west, "Pacific/Niue")):
        await conn.execute(
            "UPDATE restaurants SET timezone = $2 WHERE id = $1",
            shop.restaurant_id,
            timezone,
        )
        order = await service.create_draft(conn, shop.restaurant_id, "web")
        await add(conn, shop, order, shop.fries)
        await place_order(conn, shop.restaurant_id, order.id)

    dates = await conn.fetch(
        "SELECT restaurant_id, business_date FROM orders WHERE restaurant_id = ANY($1)",
        [east.restaurant_id, west.restaurant_id],
    )
    by_restaurant = {row["restaurant_id"]: row["business_date"] for row in dates}

    assert by_restaurant[east.restaurant_id] == date(2026, 11, 19)
    assert by_restaurant[west.restaurant_id] == date(2026, 11, 18)


# --- ordering_status --------------------------------------------------------


async def test_ordering_status_while_open_gives_the_wait(
    conn: asyncpg.Connection, shop: Shop, draft: Order
) -> None:
    status = await service.ordering_status(conn, shop.restaurant_id)
    assert status.accepting is True
    assert status.wait_minutes == 0
    assert status.reason is None
    assert status.timezone == "America/Los_Angeles"

    await add(conn, shop, draft, shop.fries)
    await place_order(conn, shop.restaurant_id, draft.id)
    await conn.execute(
        "UPDATE settings SET busy_extra_minutes = 20 WHERE restaurant_id = $1",
        shop.restaurant_id,
    )

    # One open order at three per 10 minutes is 4 minutes, plus busy mode.
    status = await service.ordering_status(conn, shop.restaurant_id)
    assert status.wait_minutes == 24


async def test_ordering_status_while_closed_gives_the_next_opening(
    conn: asyncpg.Connection, shop: Shop, clock: FakeClock
) -> None:
    clock.now = NOON - timedelta(hours=4)  # 8 AM

    status = await service.ordering_status(conn, shop.restaurant_id)

    assert status.accepting is False
    assert status.reason == "restaurant_closed"
    assert status.message == "The restaurant is closed right now"
    assert status.next_opening_at == NOON - timedelta(hours=3)
    assert status.wait_minutes is None


async def test_ordering_status_needs_a_configured_restaurant(
    conn: asyncpg.Connection,
) -> None:
    with pytest.raises(OrderError) as error:
        await service.ordering_status(conn, uuid4())
    assert error.value.code == "restaurant_not_found"

    restaurant_id = await create_restaurant(conn)
    with pytest.raises(OrderError) as error:
        await service.ordering_status(conn, restaurant_id)
    assert error.value.code == "restaurant_not_configured"


# --- request models -------------------------------------------------------


def test_add_line_bounds() -> None:
    item = uuid4()
    assert AddLine(menu_item_id=item).quantity == 1
    assert AddLine(menu_item_id=item, notes="   ").notes is None
    for bad in ({"quantity": 0}, {"quantity": 100}, {"notes": "x" * 201}, {"extra": 1}):
        with pytest.raises(ValueError):
            AddLine(menu_item_id=item, **bad)


def test_change_line_needs_a_change() -> None:
    with pytest.raises(ValueError):
        ChangeLine()
    with pytest.raises(ValueError):
        ChangeLine(quantity=None)
    assert ChangeLine(notes=None).notes is None
    assert ChangeLine(option_ids=[]).option_ids == []
