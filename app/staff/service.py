"""Staff sign-in and the dashboard controls.

A staff member signs in with a PIN alone, so a wrong PIN cannot be counted
against one person: wrong PINs are counted per restaurant, and too many in a
row lock sign-in for that restaurant for a short while. Sessions already open
keep working during a lock.
"""

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID

import asyncpg

from app import clock
from app.orders.errors import OrderError
from app.orders.pickup import OpenWindow, windows_overlap
from app.staff import repository
from app.staff.models import (
    Controls,
    ControlsChange,
    KitchenSettings,
    OpenHours,
    RestaurantSetup,
    Role,
    Session,
    StaffChange,
    StaffMember,
    StaffUser,
)
from app.staff.pins import hash_pin, verify_pin

SESSION_LIFETIME = timedelta(hours=12)
MAX_FAILED_LOGINS = 5
LOGIN_LOCK = timedelta(minutes=5)


class LoginFailed(Exception):
    """No active staff user of the restaurant has that PIN."""


class LoginLocked(Exception):
    """Too many wrong PINs; sign-in opens again at `locked_until`."""

    def __init__(self, locked_until: datetime) -> None:
        super().__init__("Sign-in is locked")
        self.locked_until = locked_until


class PinTaken(OrderError):
    """Another active staff user of the restaurant already has that PIN."""

    def __init__(self) -> None:
        super().__init__("pin_taken", "Someone else already uses that PIN")


async def create_staff_user(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    name: str,
    pin: str,
    role: Role = "staff",
) -> StaffUser:
    async with conn.transaction():
        # The counter row doubles as the lock that keeps two PINs from racing.
        await repository.lock_throttle(conn, restaurant_id)
        if await _match_pin(conn, restaurant_id, pin) is not None:
            raise PinTaken()
        pin_hash = await asyncio.to_thread(hash_pin, pin)
        staff_user_id = await repository.insert_staff_user(
            conn, restaurant_id, name, pin_hash, role
        )
        return StaffUser(
            id=staff_user_id, restaurant_id=restaurant_id, name=name, role=role
        )


async def list_staff(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> list[StaffMember]:
    return await repository.fetch_staff(conn, restaurant_id)


async def update_staff_user(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    staff_user_id: UUID,
    change: StaffChange,
) -> StaffMember:
    """Rename, change role or PIN, or turn a staff user off or back on.

    A restaurant always keeps one active manager (`last_manager`). A new PIN
    or turning someone off ends their sessions; turning someone back on needs
    a new PIN (`pin_required`), since theirs may have been taken meanwhile.
    """
    async with conn.transaction():
        # Also serializes the last-manager check and the PIN check.
        await repository.lock_throttle(conn, restaurant_id)
        before = await repository.fetch_staff_member(conn, restaurant_id, staff_user_id)
        if before is None:
            raise OrderError("staff_not_found", "Staff user not found")
        after = before.model_copy(
            update=change.model_dump(exclude={"pin"}, exclude_none=True)
        )

        was_manager = before.is_active and before.role == "manager"
        if was_manager and not (after.is_active and after.role == "manager"):
            everyone = await repository.fetch_staff(conn, restaurant_id)
            if not any(
                other.is_active and other.role == "manager" and other.id != before.id
                for other in everyone
            ):
                raise OrderError(
                    "last_manager", "The restaurant needs at least one manager"
                )
        if after.is_active and not before.is_active and change.pin is None:
            raise OrderError("pin_required", "Set a new PIN to turn this person on")

        pin_hash = None
        if change.pin is not None:
            holder = await _match_pin(conn, restaurant_id, change.pin)
            if holder is not None and holder.id != before.id:
                raise PinTaken()
            pin_hash = await asyncio.to_thread(hash_pin, change.pin)

        await repository.update_staff_user(
            conn,
            restaurant_id,
            staff_user_id,
            after.name,
            after.role,
            after.is_active,
            pin_hash,
        )
        if pin_hash is not None or not after.is_active:
            await repository.delete_staff_sessions(conn, restaurant_id, staff_user_id)
        return after


async def login(conn: asyncpg.Connection, restaurant_id: UUID, pin: str) -> Session:
    """Open a session for the staff user with this PIN.

    Raises LoginFailed for a wrong PIN and LoginLocked while sign-in is locked.
    """
    now = clock.utcnow()
    async with conn.transaction():
        if not await repository.restaurant_exists(conn, restaurant_id):
            raise LoginFailed()
        throttle = await repository.lock_throttle(conn, restaurant_id)
        locked_until = throttle["locked_until"]
        if locked_until is not None and locked_until > now:
            raise LoginLocked(locked_until)

        staff = await _match_pin(conn, restaurant_id, pin)
        if staff is not None:
            await repository.update_throttle(conn, restaurant_id, 0, None)
            token = secrets.token_urlsafe(32)
            expires_at = now + SESSION_LIFETIME
            await repository.insert_session(
                conn, restaurant_id, staff.id, _token_hash(token), expires_at
            )
            return Session(token=token, expires_at=expires_at, staff=staff)

        failed = throttle["failed_attempts"] + 1
        if failed >= MAX_FAILED_LOGINS:
            await repository.update_throttle(conn, restaurant_id, 0, now + LOGIN_LOCK)
        else:
            await repository.update_throttle(conn, restaurant_id, failed, None)
    # Raised after the transaction, so the failed attempt stays counted.
    raise LoginFailed()


async def authenticate(
    conn: asyncpg.Connection, restaurant_id: UUID, token: str
) -> StaffUser | None:
    """The staff user a session token belongs to, if it is still good here."""
    return await repository.fetch_session_staff(
        conn, restaurant_id, _token_hash(token), clock.utcnow()
    )


async def logout(conn: asyncpg.Connection, restaurant_id: UUID, token: str) -> None:
    await repository.delete_session(conn, restaurant_id, _token_hash(token))


async def get_controls(conn: asyncpg.Connection, restaurant_id: UUID) -> Controls:
    return _configured(await repository.fetch_controls(conn, restaurant_id))


async def set_controls(
    conn: asyncpg.Connection, restaurant_id: UUID, change: ControlsChange
) -> Controls:
    """Pause or resume ordering, or set busy mode. New quotes use it at once."""
    return _configured(
        await repository.update_controls(
            conn, restaurant_id, change.accepting_orders, change.busy_extra_minutes
        )
    )


async def get_setup(conn: asyncpg.Connection, restaurant_id: UUID) -> RestaurantSetup:
    """Call inside one transaction for a consistent read."""
    restaurant = await repository.fetch_restaurant(conn, restaurant_id)
    if restaurant is None:
        raise OrderError("restaurant_not_found", "Restaurant not found")
    return RestaurantSetup(
        restaurant_name=restaurant["name"],
        timezone=restaurant["timezone"],
        settings=_configured(
            await repository.fetch_kitchen_settings(conn, restaurant_id)
        ),
        hours=await repository.fetch_hours(conn, restaurant_id),
    )


async def set_kitchen_settings(
    conn: asyncpg.Connection, restaurant_id: UUID, settings: KitchenSettings
) -> None:
    """New orders and quotes use the new values at once; placed orders keep
    the tax they were placed with."""
    if not await repository.update_kitchen_settings(conn, restaurant_id, settings):
        _configured(None)


async def set_hours(
    conn: asyncpg.Connection, restaurant_id: UUID, hours: list[OpenHours]
) -> None:
    """Replace the week's opening hours. Windows may touch but not overlap."""
    windows = [
        OpenWindow(window.weekday, window.opens_at, window.closes_at)
        for window in hours
    ]
    if windows_overlap(windows):
        raise OrderError("hours_overlap", "Two opening windows overlap")
    async with conn.transaction():
        await repository.replace_hours(conn, restaurant_id, hours)


def _configured[T](row: T | None) -> T:
    if row is None:
        raise OrderError(
            "restaurant_not_configured",
            "This restaurant is not set up to take orders yet",
        )
    return row


async def _match_pin(
    conn: asyncpg.Connection, restaurant_id: UUID, pin: str
) -> StaffUser | None:
    rows = await repository.fetch_active_staff(conn, restaurant_id)

    def find() -> asyncpg.Record | None:
        return next((row for row in rows if verify_pin(pin, row["pin_hash"])), None)

    # scrypt is slow on purpose; keep it off the event loop.
    row = await asyncio.to_thread(find)
    if row is None:
        return None
    return StaffUser(
        id=row["id"], restaurant_id=restaurant_id, name=row["name"], role=row["role"]
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
