"""SQL for staff users, their sessions and the dashboard controls."""

from datetime import datetime
from uuid import UUID

import asyncpg

from app.staff.models import Controls, StaffUser


async def restaurant_exists(conn: asyncpg.Connection, restaurant_id: UUID) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM restaurants WHERE id = $1)", restaurant_id
    )


async def insert_staff_user(
    conn: asyncpg.Connection, restaurant_id: UUID, name: str, pin_hash: str
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO staff_users (restaurant_id, name, pin_hash)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        restaurant_id,
        name,
        pin_hash,
    )


async def fetch_active_staff(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> list[asyncpg.Record]:
    """id, name and pin_hash of the staff who can sign in, oldest first."""
    return await conn.fetch(
        """
        SELECT id, name, pin_hash
        FROM staff_users
        WHERE restaurant_id = $1 AND is_active
        ORDER BY created_at, id
        """,
        restaurant_id,
    )


async def lock_throttle(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> asyncpg.Record:
    """Lock the restaurant's sign-in counter until the transaction ends.

    Returns failed_attempts and locked_until, creating the row if needed.
    """
    return await conn.fetchrow(
        """
        INSERT INTO staff_login_throttle (restaurant_id)
        VALUES ($1)
        ON CONFLICT (restaurant_id)
        DO UPDATE SET restaurant_id = excluded.restaurant_id
        RETURNING failed_attempts, locked_until
        """,
        restaurant_id,
    )


async def update_throttle(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    failed_attempts: int,
    locked_until: datetime | None,
) -> None:
    await conn.execute(
        """
        UPDATE staff_login_throttle
        SET failed_attempts = $2, locked_until = $3
        WHERE restaurant_id = $1
        """,
        restaurant_id,
        failed_attempts,
        locked_until,
    )


async def insert_session(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    staff_user_id: UUID,
    token_hash: str,
    expires_at: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO staff_sessions (restaurant_id, staff_user_id, token_hash,
                                    expires_at)
        VALUES ($1, $2, $3, $4)
        """,
        restaurant_id,
        staff_user_id,
        token_hash,
        expires_at,
    )


async def fetch_session_staff(
    conn: asyncpg.Connection, restaurant_id: UUID, token_hash: str, now: datetime
) -> StaffUser | None:
    """The active staff user behind an unexpired session of this restaurant."""
    row = await conn.fetchrow(
        """
        SELECT u.id, u.restaurant_id, u.name
        FROM staff_sessions AS s
        JOIN staff_users AS u
          ON u.restaurant_id = s.restaurant_id AND u.id = s.staff_user_id
        WHERE s.restaurant_id = $1 AND s.token_hash = $2 AND s.expires_at > $3
          AND u.is_active
        """,
        restaurant_id,
        token_hash,
        now,
    )
    return StaffUser(**row) if row is not None else None


async def delete_session(
    conn: asyncpg.Connection, restaurant_id: UUID, token_hash: str
) -> None:
    await conn.execute(
        "DELETE FROM staff_sessions WHERE restaurant_id = $1 AND token_hash = $2",
        restaurant_id,
        token_hash,
    )


async def fetch_controls(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> Controls | None:
    """None when the restaurant has no settings row."""
    row = await conn.fetchrow(
        """
        SELECT accepting_orders, busy_extra_minutes
        FROM settings
        WHERE restaurant_id = $1
        """,
        restaurant_id,
    )
    return Controls(**row) if row is not None else None


async def update_controls(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    accepting_orders: bool | None,
    busy_extra_minutes: int | None,
) -> Controls | None:
    """Change the controls that are not None. None without a settings row."""
    row = await conn.fetchrow(
        """
        UPDATE settings
        SET accepting_orders = COALESCE($2, accepting_orders),
            busy_extra_minutes = COALESCE($3, busy_extra_minutes),
            updated_at = now()
        WHERE restaurant_id = $1
        RETURNING accepting_orders, busy_extra_minutes
        """,
        restaurant_id,
        accepting_orders,
        busy_extra_minutes,
    )
    return Controls(**row) if row is not None else None
