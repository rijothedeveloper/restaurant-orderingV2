"""The words used to read an order back. Built by code, never by the model."""

from app.orders.models import Order, OrderLine


def format_cents(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def _line_text(line: OrderLine) -> str:
    details = [modifier.name for modifier in line.modifiers]
    if line.notes:
        details.append(f"note: {line.notes}")
    text = f"{line.quantity} x {line.name}"
    return f"{text} ({', '.join(details)})" if details else text


def read_back_text(order: Order) -> str:
    """Lines, total with tax and how to pay, ending with the question.

    Plain text for every channel; the voice adapter will need a spoken form.
    """
    lines = "; ".join(_line_text(line) for line in order.lines)
    return (
        f"Your order: {lines}. "
        f"Total with tax: {format_cents(order.total_cents)}. "
        "Pay at pickup. Shall I place it?"
    )
