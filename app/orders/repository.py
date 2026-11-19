"""SQL for orders. Every query is scoped by restaurant_id."""

from datetime import date
from uuid import UUID

import asyncpg

from app.menu.models import MenuItem
from app.orders.models import Order, OrderLine, OrderLineModifier
from app.orders.pricing import SelectedOption


async def fetch_restaurant_tax_rate(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> asyncpg.Record | None:
    """Row with tax_rate_bps (NULL when the restaurant has no settings row)."""
    return await conn.fetchrow(
        """
        SELECT s.tax_rate_bps
        FROM restaurants AS r
        LEFT JOIN settings AS s ON s.restaurant_id = r.id
        WHERE r.id = $1
        """,
        restaurant_id,
    )


async def insert_draft(
    conn: asyncpg.Connection, restaurant_id: UUID, channel: str, tax_rate_bps: int
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO orders (restaurant_id, channel, status, tax_rate_bps,
                            subtotal_cents, tax_cents, total_cents)
        VALUES ($1, $2, 'draft', $3, 0, 0, 0)
        RETURNING id
        """,
        restaurant_id,
        channel,
        tax_rate_bps,
    )


async def lock_order(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> asyncpg.Record | None:
    """Lock the order row until the transaction ends.

    Returns its status, revision and order_number.
    """
    return await conn.fetchrow(
        """
        SELECT status, revision, order_number
        FROM orders
        WHERE restaurant_id = $1 AND id = $2
        FOR UPDATE
        """,
        restaurant_id,
        order_id,
    )


async def insert_line(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    item: MenuItem,
    quantity: int,
    modifier_total_cents: int,
    line_total_cents: int,
    notes: str | None,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO order_items (restaurant_id, order_id, menu_item_id, item_name,
                                 unit_price_cents, modifier_total_cents, quantity,
                                 line_total_cents, notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        restaurant_id,
        order_id,
        item.id,
        item.name,
        item.price_cents,
        modifier_total_cents,
        quantity,
        line_total_cents,
        notes,
    )


async def fetch_line(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID, line_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT menu_item_id, quantity, notes
        FROM order_items
        WHERE restaurant_id = $1 AND order_id = $2 AND id = $3
        """,
        restaurant_id,
        order_id,
        line_id,
    )


async def fetch_line_option_ids(
    conn: asyncpg.Connection, restaurant_id: UUID, line_id: UUID
) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT modifier_option_id
        FROM order_item_modifiers
        WHERE restaurant_id = $1 AND order_item_id = $2
        ORDER BY seq
        """,
        restaurant_id,
        line_id,
    )
    return [row["modifier_option_id"] for row in rows]


async def update_line(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    line_id: UUID,
    item: MenuItem,
    quantity: int,
    modifier_total_cents: int,
    line_total_cents: int,
    notes: str | None,
) -> None:
    await conn.execute(
        """
        UPDATE order_items
        SET item_name = $3, unit_price_cents = $4, modifier_total_cents = $5,
            quantity = $6, line_total_cents = $7, notes = $8
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        line_id,
        item.name,
        item.price_cents,
        modifier_total_cents,
        quantity,
        line_total_cents,
        notes,
    )


async def replace_line_modifiers(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    line_id: UUID,
    selected: list[SelectedOption],
) -> None:
    await conn.execute(
        "DELETE FROM order_item_modifiers WHERE restaurant_id = $1 AND order_item_id = $2",
        restaurant_id,
        line_id,
    )
    if not selected:
        return
    await conn.executemany(
        """
        INSERT INTO order_item_modifiers (restaurant_id, order_item_id,
                                          modifier_option_id, group_name,
                                          option_name, kind, price_delta_cents)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        [
            (
                restaurant_id,
                line_id,
                option.id,
                group.name,
                option.name,
                option.kind,
                option.price_delta_cents,
            )
            for group, option in selected
        ],
    )


async def delete_line(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID, line_id: UUID
) -> bool:
    deleted = await conn.fetchval(
        """
        DELETE FROM order_items
        WHERE restaurant_id = $1 AND order_id = $2 AND id = $3
        RETURNING id
        """,
        restaurant_id,
        order_id,
        line_id,
    )
    return deleted is not None


async def fetch_subtotal_cents(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> int:
    return await conn.fetchval(
        """
        SELECT COALESCE(sum(line_total_cents), 0)::bigint
        FROM order_items
        WHERE restaurant_id = $1 AND order_id = $2
        """,
        restaurant_id,
        order_id,
    )


async def update_totals(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    subtotal_cents: int,
    tax_rate_bps: int,
    tax_cents: int,
) -> None:
    """Store new totals and bump the revision."""
    await conn.execute(
        """
        UPDATE orders
        SET subtotal_cents = $3, tax_rate_bps = $4, tax_cents = $5,
            total_cents = $3::bigint + $5::bigint,
            revision = revision + 1, updated_at = now()
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        order_id,
        subtotal_cents,
        tax_rate_bps,
        tax_cents,
    )


async def update_customer(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    name: str,
    phone: str,
) -> None:
    await conn.execute(
        """
        UPDATE orders
        SET customer_name = $3, customer_phone = $4, updated_at = now()
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        order_id,
        name,
        phone,
    )


async def set_read_back_revision(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID, revision: int
) -> None:
    await conn.execute(
        """
        UPDATE orders
        SET read_back_revision = $3, updated_at = now()
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        order_id,
        revision,
    )


async def fetch_business_date(conn: asyncpg.Connection, restaurant_id: UUID) -> date:
    """Today's date in the restaurant's timezone."""
    return await conn.fetchval(
        "SELECT (now() AT TIME ZONE timezone)::date FROM restaurants WHERE id = $1",
        restaurant_id,
    )


async def next_order_number(
    conn: asyncpg.Connection, restaurant_id: UUID, business_date: date
) -> int:
    """Take the next order number for the day, starting at 1.

    The counter row stays locked until the transaction ends, so numbers are
    handed out one placement at a time and a rollback leaves no gap.
    """
    return await conn.fetchval(
        """
        INSERT INTO order_counters (restaurant_id, business_date, last_number)
        VALUES ($1, $2, 1)
        ON CONFLICT (restaurant_id, business_date)
        DO UPDATE SET last_number = order_counters.last_number + 1
        RETURNING last_number
        """,
        restaurant_id,
        business_date,
    )


async def mark_placed(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    business_date: date,
    order_number: int,
) -> None:
    await conn.execute(
        """
        UPDATE orders
        SET status = 'placed', business_date = $3, order_number = $4,
            placed_at = now(), updated_at = now()
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        order_id,
        business_date,
        order_number,
    )


async def insert_event(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    order_id: UUID,
    from_status: str,
    to_status: str,
    actor_type: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO order_events (restaurant_id, order_id, from_status, to_status,
                                  actor_type)
        VALUES ($1, $2, $3, $4, $5)
        """,
        restaurant_id,
        order_id,
        from_status,
        to_status,
        actor_type,
    )


async def fetch_order(
    conn: asyncpg.Connection, restaurant_id: UUID, order_id: UUID
) -> Order | None:
    order = await conn.fetchrow(
        """
        SELECT id, restaurant_id, status, channel, revision, read_back_revision,
               order_number, placed_at, customer_name, customer_phone,
               subtotal_cents, tax_rate_bps, tax_cents, total_cents
        FROM orders
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        order_id,
    )
    if order is None:
        return None

    line_rows = await conn.fetch(
        """
        SELECT id, menu_item_id, item_name, quantity, unit_price_cents,
               modifier_total_cents, line_total_cents, notes
        FROM order_items
        WHERE restaurant_id = $1 AND order_id = $2
        ORDER BY seq
        """,
        restaurant_id,
        order_id,
    )
    modifier_rows = await conn.fetch(
        """
        SELECT m.order_item_id, m.modifier_option_id, m.group_name, m.option_name,
               m.kind, m.price_delta_cents
        FROM order_item_modifiers AS m
        JOIN order_items AS i
          ON i.restaurant_id = m.restaurant_id AND i.id = m.order_item_id
        WHERE i.restaurant_id = $1 AND i.order_id = $2
        ORDER BY m.seq
        """,
        restaurant_id,
        order_id,
    )

    modifiers_by_line: dict[UUID, list[OrderLineModifier]] = {}
    for row in modifier_rows:
        modifiers_by_line.setdefault(row["order_item_id"], []).append(
            OrderLineModifier(
                option_id=row["modifier_option_id"],
                group_name=row["group_name"],
                name=row["option_name"],
                kind=row["kind"],
                price_delta_cents=row["price_delta_cents"],
            )
        )

    return Order(
        id=order["id"],
        restaurant_id=order["restaurant_id"],
        status=order["status"],
        channel=order["channel"],
        revision=order["revision"],
        read_back_revision=order["read_back_revision"],
        order_number=order["order_number"],
        placed_at=order["placed_at"],
        customer_name=order["customer_name"],
        customer_phone=order["customer_phone"],
        subtotal_cents=order["subtotal_cents"],
        tax_rate_bps=order["tax_rate_bps"],
        tax_cents=order["tax_cents"],
        total_cents=order["total_cents"],
        lines=[
            OrderLine(
                id=row["id"],
                menu_item_id=row["menu_item_id"],
                name=row["item_name"],
                quantity=row["quantity"],
                unit_price_cents=row["unit_price_cents"],
                modifier_total_cents=row["modifier_total_cents"],
                line_total_cents=row["line_total_cents"],
                notes=row["notes"],
                modifiers=modifiers_by_line.get(row["id"], []),
            )
            for row in line_rows
        ],
    )
