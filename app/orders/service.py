"""The order engine: draft orders, their lines, and their totals.

Every channel (agent tools, storefront cart) changes an order only through these
functions. Each one runs in its own transaction (a savepoint when the caller
already has one open) and returns the order as it stands afterwards.

Lines can change only while the order is a draft. `confirm_order` places it,
and only at the revision the customer agreed to.
"""

from uuid import UUID

import asyncpg

from app.menu.models import MenuItem
from app.menu.repository import fetch_menu
from app.orders import repository
from app.orders.errors import LinesUnavailable, OrderError
from app.orders.models import (
    AddLine,
    ChangeLine,
    Channel,
    CustomerDetails,
    Order,
    ReadBack,
)
from app.orders.pricing import (
    line_total_cents,
    select_options,
    tax_cents,
    unavailable_lines,
)
from app.orders.readback import read_back_text


async def create_draft(
    conn: asyncpg.Connection, restaurant_id: UUID, channel: Channel
) -> Order:
    async with conn.transaction():
        tax_rate_bps = await _tax_rate_bps(conn, restaurant_id)
        order_id = await repository.insert_draft(
            conn, restaurant_id, channel, tax_rate_bps
        )
        return await get_order(conn, restaurant_id, order_id)


async def get_order(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> Order:
    order = await repository.fetch_order(conn, restaurant_id, order_id)
    if order is None:
        raise OrderError("order_not_found", "Order not found")
    return order


async def add_item(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID, line: AddLine
) -> Order:
    """Add a line. Raises MissingRequiredChoices if a required choice is absent."""
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        item = await _menu_item(conn, restaurant_id, line.menu_item_id)
        selected = select_options(item, line.option_ids)
        modifier_total = sum(option.price_delta_cents for _, option in selected)

        line_id = await repository.insert_line(
            conn,
            restaurant_id,
            order_id,
            item,
            line.quantity,
            modifier_total,
            line_total_cents(item.price_cents, modifier_total, line.quantity),
            line.notes,
        )
        await repository.replace_line_modifiers(conn, restaurant_id, line_id, selected)
        await _retotal(conn, restaurant_id, order_id)
        return await get_order(conn, restaurant_id, order_id)


async def change_item(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    line_id: UUID,
    change: ChangeLine,
) -> Order:
    """Edit a line's quantity, options or note.

    The line is re-checked and re-priced against the current menu, so a change
    fails if its item or one of its options has since become unavailable.
    """
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        current = await repository.fetch_line(conn, restaurant_id, order_id, line_id)
        if current is None:
            raise OrderError("line_not_found", "Order line not found")

        item = await _menu_item(conn, restaurant_id, current["menu_item_id"])
        if change.option_ids is not None:
            option_ids = change.option_ids
        else:
            option_ids = await repository.fetch_line_option_ids(
                conn, restaurant_id, line_id
            )
        quantity = (
            change.quantity if change.quantity is not None else current["quantity"]
        )
        notes = change.notes if "notes" in change.model_fields_set else current["notes"]

        selected = select_options(item, option_ids)
        modifier_total = sum(option.price_delta_cents for _, option in selected)

        await repository.update_line(
            conn,
            restaurant_id,
            line_id,
            item,
            quantity,
            modifier_total,
            line_total_cents(item.price_cents, modifier_total, quantity),
            notes,
        )
        await repository.replace_line_modifiers(conn, restaurant_id, line_id, selected)
        await _retotal(conn, restaurant_id, order_id)
        return await get_order(conn, restaurant_id, order_id)


async def remove_item(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID, line_id: UUID
) -> Order:
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        if not await repository.delete_line(conn, restaurant_id, order_id, line_id):
            raise OrderError("line_not_found", "Order line not found")
        await _retotal(conn, restaurant_id, order_id)
        return await get_order(conn, restaurant_id, order_id)


async def set_customer(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    customer: CustomerDetails,
) -> Order:
    """Record who picks the order up. The revision stays as it is."""
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        await repository.update_customer(
            conn, restaurant_id, order_id, customer.name, customer.phone
        )
        return await get_order(conn, restaurant_id, order_id)


async def read_back(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> ReadBack:
    """Build the read-back of the draft and record which revision was read.

    Raises OrderError if the order is empty, or LinesUnavailable if a line can
    no longer be made.
    """
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        order = await get_order(conn, restaurant_id, order_id)
        await _check_lines(conn, order)
        await repository.set_read_back_revision(
            conn, restaurant_id, order_id, order.revision
        )
        order = order.model_copy(update={"read_back_revision": order.revision})
        return ReadBack(order=order, text=read_back_text(order))


async def confirm_order(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    revision: int,
    customer: CustomerDetails | None = None,
) -> Order:
    """Place a draft the customer has agreed to, and give it its order number.

    `revision` is the revision the customer saw or heard; the order is placed
    only if nothing has changed since. `customer` sets the pickup name and
    phone in the same step. Confirming an order that was already placed
    returns it unchanged, so a repeated yes is harmless.
    """
    async with conn.transaction():
        locked = await repository.lock_order(conn, restaurant_id, order_id)
        if locked is None:
            raise OrderError("order_not_found", "Order not found")
        if locked["status"] != "draft":
            if locked["order_number"] is None:
                raise OrderError(
                    "order_not_draft", "This order can no longer be placed"
                )
            return await get_order(conn, restaurant_id, order_id)
        if locked["revision"] != revision:
            raise OrderError(
                "order_changed", "The order has changed since it was reviewed"
            )

        if customer is not None:
            await repository.update_customer(
                conn, restaurant_id, order_id, customer.name, customer.phone
            )
        order = await get_order(conn, restaurant_id, order_id)
        await _check_lines(conn, order)
        if not order.customer_name or not order.customer_phone:
            raise OrderError(
                "customer_required", "A pickup name and phone number are needed"
            )

        business_date = await repository.fetch_business_date(conn, restaurant_id)
        number = await repository.next_order_number(conn, restaurant_id, business_date)
        await repository.mark_placed(
            conn, restaurant_id, order_id, business_date, number
        )
        await repository.insert_event(
            conn, restaurant_id, order_id, "draft", "placed", "customer"
        )
        return await get_order(conn, restaurant_id, order_id)


async def _tax_rate_bps(conn: asyncpg.Connection, restaurant_id: UUID) -> int:
    row = await repository.fetch_restaurant_tax_rate(conn, restaurant_id)
    if row is None:
        raise OrderError("restaurant_not_found", "Restaurant not found")
    if row["tax_rate_bps"] is None:
        raise OrderError(
            "restaurant_not_configured",
            "This restaurant is not set up to take orders yet",
        )
    return row["tax_rate_bps"]


async def _lock_draft(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> None:
    """Serialize changes to one order, and allow them only while it is a draft."""
    locked = await repository.lock_order(conn, restaurant_id, order_id)
    if locked is None:
        raise OrderError("order_not_found", "Order not found")
    if locked["status"] != "draft":
        raise OrderError("order_not_draft", "This order can no longer be changed")


async def _check_lines(conn: asyncpg.Connection, order: Order) -> None:
    """Reject an order with no lines, or with lines the menu no longer allows."""
    if not order.lines:
        raise OrderError("order_empty", "The order has no items yet")
    menu = await fetch_menu(conn, order.restaurant_id)
    if menu is None:
        raise OrderError("restaurant_not_found", "Restaurant not found")
    lines = unavailable_lines(order, menu)
    if lines:
        raise LinesUnavailable(lines)


async def _menu_item(
    conn: asyncpg.Connection, restaurant_id: UUID, menu_item_id: UUID
) -> MenuItem:
    # Loads the whole menu per call; the in-memory menu cache replaces this later.
    menu = await fetch_menu(conn, restaurant_id)
    item = menu.find_item(menu_item_id) if menu is not None else None
    if item is None:
        raise OrderError("item_not_found", "That item is not on the menu")
    return item


async def _retotal(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> None:
    tax_rate_bps = await _tax_rate_bps(conn, restaurant_id)
    subtotal = await repository.fetch_subtotal_cents(conn, restaurant_id, order_id)
    await repository.update_totals(
        conn,
        restaurant_id,
        order_id,
        subtotal,
        tax_rate_bps,
        tax_cents(subtotal, tax_rate_bps),
    )
