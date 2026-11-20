"""The order engine: draft orders, their lines, and their totals.

Every channel (agent tools, storefront cart) changes an order only through these
functions. Each one runs in its own transaction (a savepoint when the caller
already has one open) and returns the order as it stands afterwards.

Lines can change only while the order is a draft. `confirm_order` places it,
and only at the revision the customer agreed to. After that only staff move it,
through `apply_staff_action`.

Pickup times are quoted by `app.orders.pickup` from the opening hours, the
kitchen's load and `clock.utcnow()`. A quote is an estimate until the order is
placed; placing computes it once more and stores it as the promise.
"""

from datetime import datetime
from uuid import UUID

import asyncpg

from app import clock
from app.menu.models import Menu, MenuItem
from app.menu.repository import fetch_menu
from app.orders import repository
from app.orders.errors import LinesUnavailable, OrderError, PickupUnavailable
from app.orders.models import (
    AddLine,
    ChangeLine,
    Channel,
    CustomerDetails,
    Order,
    OrderingStatus,
    OrderStatus,
    Pickup,
    ReadBack,
    SetPickup,
    StaffAction,
)
from app.orders.pickup import (
    PickupQuote,
    PickupRules,
    longest_prep_minutes,
    quote_pickup,
    wait_minutes,
)
from app.orders.pricing import (
    line_total_cents,
    select_options,
    tax_cents,
    unavailable_lines,
)
from app.orders.readback import read_back_text

# For each staff action: the statuses it can start from, and where it leads.
# Rejecting or cancelling is possible only until cooking starts.
STAFF_ACTIONS: dict[StaffAction, tuple[frozenset[OrderStatus], OrderStatus]] = {
    "accept": (frozenset({"placed"}), "accepted"),
    "prepare": (frozenset({"accepted"}), "preparing"),
    "ready": (frozenset({"preparing"}), "ready"),
    "picked_up": (frozenset({"ready"}), "picked_up"),
    "reject": (frozenset({"placed"}), "rejected"),
    "cancel": (frozenset({"placed", "accepted"}), "cancelled"),
    "no_show": (frozenset({"ready"}), "no_show"),
}


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


async def set_pickup(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID, pickup: SetPickup
) -> Pickup:
    """Ask for a pickup time, or for as soon as possible, and quote it.

    A new time bumps the revision, because the pickup time is part of what the
    customer agrees to. Raises PickupUnavailable, leaving the draft as it was,
    when no order can be taken now or the time cannot be met.
    """
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        order = await get_order(conn, restaurant_id, order_id)
        menu = await fetch_menu(conn, restaurant_id)
        quote = await _quote(conn, order, menu, pickup.requested_at)
        await repository.update_requested_pickup(
            conn, restaurant_id, order_id, pickup.requested_at
        )
        return Pickup(
            order=await get_order(conn, restaurant_id, order_id),
            ready_at=quote.ready_at,
        )


async def read_back(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> ReadBack:
    """Build the read-back of the draft and record which revision was read.

    Raises OrderError if the order is empty, LinesUnavailable if a line can no
    longer be made, or PickupUnavailable if it cannot be placed right now.
    """
    async with conn.transaction():
        await _lock_draft(conn, restaurant_id, order_id)
        order = await get_order(conn, restaurant_id, order_id)
        menu = await _check_lines(conn, order)
        quote = await _quote(conn, order, menu, order.requested_pickup_at)
        await repository.set_read_back_revision(
            conn, restaurant_id, order_id, order.revision
        )
        order = order.model_copy(update={"read_back_revision": order.revision})
        return ReadBack(
            order=order,
            text=read_back_text(order, quote.ready_at),
            pickup_at=quote.ready_at,
        )


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
    phone in the same step. The pickup time is quoted once more and stored as
    the promise. Confirming an order that was already placed returns it
    unchanged, so a repeated yes is harmless.
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
        menu = await _check_lines(conn, order)
        if not order.customer_name or not order.customer_phone:
            raise OrderError(
                "customer_required", "A pickup name and phone number are needed"
            )

        quote = await _quote(conn, order, menu, order.requested_pickup_at)
        number = await repository.next_order_number(
            conn, restaurant_id, quote.business_date
        )
        await repository.mark_placed(
            conn, restaurant_id, order_id, quote.business_date, number, quote.ready_at
        )
        await repository.insert_event(
            conn, restaurant_id, order_id, "draft", "placed", "customer"
        )
        return await get_order(conn, restaurant_id, order_id)


async def apply_staff_action(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    action: StaffAction,
    staff_user_id: UUID,
    *,
    extra_minutes: int = 0,
    reason: str | None = None,
) -> Order:
    """Move a placed order along its lifecycle, and log who did it.

    `extra_minutes` pushes the promised pickup time back when accepting;
    `reason` is kept for a reject (which needs one) or a cancel. Repeating an
    action on an order that is already there returns it unchanged, so a
    double tap is harmless.
    """
    allowed_from, to_status = STAFF_ACTIONS[action]
    if action == "reject" and not reason:
        raise OrderError("reason_required", "Rejecting an order needs a reason")
    async with conn.transaction():
        locked = await repository.lock_order(conn, restaurant_id, order_id)
        if locked is None:
            raise OrderError("order_not_found", "Order not found")
        from_status = locked["status"]
        if from_status != to_status:
            if from_status not in allowed_from:
                raise OrderError(
                    "invalid_transition",
                    f"An order that is {_spoken(from_status)} cannot be moved"
                    f" to {_spoken(to_status)}",
                )
            await repository.update_status(
                conn,
                restaurant_id,
                order_id,
                to_status,
                extra_minutes if action == "accept" else 0,
            )
            await repository.insert_event(
                conn,
                restaurant_id,
                order_id,
                from_status,
                to_status,
                "staff",
                actor_id=staff_user_id,
                reason=reason if action in ("reject", "cancel") else None,
            )
        return await get_order(conn, restaurant_id, order_id)


async def active_orders(conn: asyncpg.Connection, restaurant_id: UUID) -> list[Order]:
    """The orders staff still have to act on, soonest pickup first."""
    order_ids = await repository.fetch_active_order_ids(conn, restaurant_id)
    return await repository.fetch_orders(conn, restaurant_id, order_ids)


def _spoken(status: str) -> str:
    return status.replace("_", " ")


async def ordering_status(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> OrderingStatus:
    """Whether an order could be placed right now, and the current wait."""
    rules = await _pickup_rules(conn, restaurant_id)
    open_orders = await repository.count_open_orders(conn, restaurant_id)
    timezone = rules.timezone.key
    try:
        quote_pickup(rules, clock.utcnow(), None, 0, open_orders)
    except PickupUnavailable as unavailable:
        return OrderingStatus(
            accepting=False,
            reason=unavailable.code,
            message=unavailable.message,
            next_opening_at=unavailable.next_opening_at,
            timezone=timezone,
        )
    return OrderingStatus(
        accepting=True,
        wait_minutes=wait_minutes(rules, open_orders),
        timezone=timezone,
    )


async def _pickup_rules(conn: asyncpg.Connection, restaurant_id: UUID) -> PickupRules:
    rules = await repository.fetch_pickup_rules(conn, restaurant_id)
    if rules is None:
        # Raises the error that says why: no such restaurant, or no settings.
        await _tax_rate_bps(conn, restaurant_id)
        raise OrderError("restaurant_not_found", "Restaurant not found")
    return rules


async def _quote(
    conn: asyncpg.Connection,
    order: Order,
    menu: Menu | None,
    requested_at: datetime | None,
) -> PickupQuote:
    """When `order` would be ready if it were placed now."""
    rules = await _pickup_rules(conn, order.restaurant_id)
    open_orders = await repository.count_open_orders(conn, order.restaurant_id)
    return quote_pickup(
        rules,
        clock.utcnow(),
        requested_at,
        longest_prep_minutes(order, menu),
        open_orders,
    )


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


async def _check_lines(conn: asyncpg.Connection, order: Order) -> Menu:
    """Reject an order with no lines, or with lines the menu no longer allows.

    Returns the menu it checked against.
    """
    if not order.lines:
        raise OrderError("order_empty", "The order has no items yet")
    menu = await fetch_menu(conn, order.restaurant_id)
    if menu is None:
        raise OrderError("restaurant_not_found", "Restaurant not found")
    lines = unavailable_lines(order, menu)
    if lines:
        raise LinesUnavailable(lines)
    return menu


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
