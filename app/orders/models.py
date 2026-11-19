from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Sanity bound on one line, not a business rule; order caps come from settings later.
MAX_LINE_QUANTITY = 99
MAX_NOTES_LENGTH = 200

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
    status: OrderStatus
    channel: Channel
    revision: int
    lines: list[OrderLine] = Field(default_factory=list)
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int


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
