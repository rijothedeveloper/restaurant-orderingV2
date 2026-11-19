"""The words used to read an order back. Built by code, never by the model."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.orders.models import Order, OrderLine


def format_cents(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def format_time(moment: datetime, timezone: str) -> str:
    """The clock time in the restaurant's timezone, e.g. "6:40 PM"."""
    local = moment.astimezone(ZoneInfo(timezone))
    half = "AM" if local.hour < 12 else "PM"
    return f"{local.hour % 12 or 12}:{local.minute:02d} {half}"


def _line_text(line: OrderLine) -> str:
    details = [modifier.name for modifier in line.modifiers]
    if line.notes:
        details.append(f"note: {line.notes}")
    text = f"{line.quantity} x {line.name}"
    return f"{text} ({', '.join(details)})" if details else text


def read_back_text(order: Order, pickup_at: datetime) -> str:
    """Lines, total with tax, pickup time and how to pay, ending with the question.

    Plain text for every channel; the voice adapter will need a spoken form.
    """
    lines = "; ".join(_line_text(line) for line in order.lines)
    return (
        f"Your order: {lines}. "
        f"Total with tax: {format_cents(order.total_cents)}. "
        f"Ready for pickup about {format_time(pickup_at, order.timezone)}. "
        "Pay at pickup. Shall I place it?"
    )


def confirmation_text(order: Order) -> str:
    """What to say once the order is placed: its number and the promised time."""
    if order.order_number is None or order.promised_pickup_at is None:
        raise ValueError("the order has not been placed")
    ready = format_time(order.promised_pickup_at, order.timezone)
    return (
        f"Your order is placed. Your order number is {order.order_number}. "
        f"It will be ready for pickup about {ready}. Pay at pickup."
    )
