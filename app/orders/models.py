from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.orders.phone import normalize_phone

# Sanity bound on one line, not a business rule; order caps come from settings later.
MAX_LINE_QUANTITY = 99
MAX_NOTES_LENGTH = 200
MAX_NAME_LENGTH = 80

Channel = Literal["voice", "web", "whatsapp", "sms", "storefront"]
OrderStatus = Literal[
    "draft",
    "placed",
    "accepted",
    "preparing",
    "ready",
    "picked_up",
    "rejected",
    "cancelled",
    "no_show",
]


class OrderLineModifier(BaseModel):
    option_id: UUID
    group_name: str
    name: str
    kind: Literal["choice", "add", "remove"]
    price_delta_cents: int


class OrderLine(BaseModel):
    id: UUID
    menu_item_id: UUID
    name: str
    quantity: int
    # Item price for one unit, without options.
    unit_price_cents: int
    # Sum of the option price deltas for one unit.
    modifier_total_cents: int
    # (unit_price_cents + modifier_total_cents) * quantity
    line_total_cents: int
    notes: str | None
    modifiers: list[OrderLineModifier] = Field(default_factory=list)


class Order(BaseModel):
    id: UUID
    restaurant_id: UUID
    # The restaurant's IANA timezone, for showing and speaking times.
    timezone: str
    status: OrderStatus
    channel: Channel
    # Bumped by every change to the lines; pickup name and phone do not count.
    revision: int
    # The revision last read back to the customer, if any.
    read_back_revision: int | None = None
    # Set when the order is placed; the number counts per restaurant per day.
    order_number: int | None = None
    placed_at: datetime | None = None
    # The pickup time the customer asked for; None means as soon as possible.
    requested_pickup_at: datetime | None = None
    # The pickup time promised when the order was placed.
    promised_pickup_at: datetime | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    lines: list[OrderLine] = Field(default_factory=list)
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int


class ReadBack(BaseModel):
    """The order as read to the customer, and the words to say."""

    order: Order
    text: str
    # When the order would be ready if it were placed now.
    pickup_at: datetime


class Pickup(BaseModel):
    """The order after its pickup time was set, and when it would be ready."""

    order: Order
    # An estimate until the order is placed; placing fixes the promise.
    ready_at: datetime


class OrderingStatus(BaseModel):
    """Whether the restaurant can take an order right now."""

    accepting: bool
    # Why not, as an error code and its message; None while accepting.
    reason: str | None = None
    message: str | None = None
    next_opening_at: datetime | None = None
    # Queue delay plus busy-mode extra, before the items' own prep time.
    wait_minutes: int | None = None
    timezone: str


class SetPickup(BaseModel):
    """The pickup time the customer wants; null means as soon as possible."""

    model_config = ConfigDict(extra="forbid")

    requested_at: AwareDatetime | None


class CustomerDetails(BaseModel):
    """Who picks the order up. The phone is stored in E.164 form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    phone: str = Field(min_length=1, max_length=32)

    _normalize = field_validator("phone")(normalize_phone)


def _clean_notes(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class AddLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    menu_item_id: UUID
    quantity: int = Field(default=1, ge=1, le=MAX_LINE_QUANTITY)
    option_ids: list[UUID] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    _clean = field_validator("notes")(_clean_notes)


class ChangeLine(BaseModel):
    """A partial edit of one line. Fields that are not sent stay as they are.

    quantity and option_ids: null means "no change". option_ids replaces the
    whole selection. notes: sending null (or blank) clears the note.
    """

    model_config = ConfigDict(extra="forbid")

    quantity: int | None = Field(default=None, ge=1, le=MAX_LINE_QUANTITY)
    option_ids: list[UUID] | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    _clean = field_validator("notes")(_clean_notes)

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if (
            self.quantity is None
            and self.option_ids is None
            and "notes" not in self.model_fields_set
        ):
            raise ValueError("send at least one of quantity, option_ids, notes")
        return self
