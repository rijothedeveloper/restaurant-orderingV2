from uuid import uuid4

import asyncpg
import pytest

from app.orders import service
from app.orders.errors import MissingRequiredChoices, OrderError
from app.orders.models import AddLine, ChangeLine, Order
from tests.factories import Shop, create_restaurant, create_shop

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
    await conn.execute("UPDATE orders SET status = 'placed' WHERE id = $1", draft.id)
    restaurant_id = shop.restaurant_id

    for attempt in (
        add(conn, shop, draft, shop.fries),
        service.change_item(
            conn, restaurant_id, draft.id, line_id, ChangeLine(quantity=2)
        ),
        service.remove_item(conn, restaurant_id, draft.id, line_id),
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
    ):
        with pytest.raises(OrderError) as error:
            await attempt
        assert error.value.code == "order_not_found"


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
