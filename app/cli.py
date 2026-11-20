"""Set-up commands for what no screen can do yet.

    uv run python -m app.cli create-restaurant "Luigi's" \\
        --timezone America/Chicago --tax-rate 8.75
    uv run python -m app.cli create-manager <restaurant id> "Maria"

A manager creates everyone else, the menu and the hours from the dashboard.
"""

import argparse
import asyncio
import getpass
import re
import sys
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from app.config import get_settings
from app.staff import service
from app.staff.models import PIN_PATTERN


async def create_restaurant(
    conn: asyncpg.Connection, name: str, timezone: str, tax_rate_bps: int
) -> UUID:
    """A restaurant with its settings row, no hours and an empty menu."""
    async with conn.transaction():
        restaurant_id = await conn.fetchval(
            "INSERT INTO restaurants (name, timezone) VALUES ($1, $2) RETURNING id",
            name,
            timezone,
        )
        await conn.execute(
            "INSERT INTO settings (restaurant_id, tax_rate_bps) VALUES ($1, $2)",
            restaurant_id,
            tax_rate_bps,
        )
    return restaurant_id


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise argparse.ArgumentTypeError(f"unknown timezone: {value}") from None
    return value


def _tax_rate_bps(value: str) -> int:
    """A percentage such as 8.75, as basis points."""
    try:
        bps = Decimal(value) * 100
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a percentage: {value}") from None
    if bps != bps.to_integral_value() or not 0 <= bps <= 10000:
        raise argparse.ArgumentTypeError(
            "a percentage from 0 to 100 with at most 2 decimals"
        )
    return int(bps)


def _ask_pin() -> str:
    pin = getpass.getpass("PIN (6 to 8 digits): ")
    if not re.fullmatch(PIN_PATTERN, pin):
        sys.exit("The PIN must be 6 to 8 digits.")
    if getpass.getpass("PIN again: ") != pin:
        sys.exit("The PINs do not match.")
    return pin


async def _run(args: argparse.Namespace) -> None:
    pin = _ask_pin() if args.command == "create-manager" else None
    conn = await asyncpg.connect(get_settings().database_url.get_secret_value())
    try:
        if args.command == "create-restaurant":
            restaurant_id = await create_restaurant(
                conn, args.name, args.timezone, args.tax_rate
            )
            print(f"Created restaurant {restaurant_id}")
            print(f"Next: python -m app.cli create-manager {restaurant_id} <name>")
            return
        try:
            manager = await service.create_staff_user(
                conn, args.restaurant_id, args.name, pin, "manager"
            )
        except asyncpg.ForeignKeyViolationError:
            sys.exit(f"No restaurant has the id {args.restaurant_id}.")
        except service.PinTaken as taken:
            sys.exit(f"{taken.message}.")
        print(f"Created manager {manager.name}")
        print(f"Dashboard: /dashboard/?r={args.restaurant_id}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    restaurant = commands.add_parser(
        "create-restaurant", help="add a restaurant; prints its id"
    )
    restaurant.add_argument("name")
    restaurant.add_argument(
        "--timezone", type=_timezone, required=True, help="such as America/Chicago"
    )
    restaurant.add_argument(
        "--tax-rate",
        type=_tax_rate_bps,
        required=True,
        metavar="PERCENT",
        help="sales tax, such as 8.75",
    )

    manager = commands.add_parser(
        "create-manager", help="add a manager to a restaurant; asks for a PIN"
    )
    manager.add_argument("restaurant_id", type=UUID)
    manager.add_argument("name")

    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
