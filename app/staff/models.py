from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orders.models import MAX_REASON_LENGTH, Order

# Long enough that guessing runs into the sign-in lock first.
PIN_PATTERN = r"^[0-9]{6,8}$"


class StaffUser(BaseModel):
    id: UUID
    restaurant_id: UUID
    name: str


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(pattern=PIN_PATTERN)


class Session(BaseModel):
    """A sign-in. Send `token` as `Authorization: Bearer <token>`."""

    token: str
    expires_at: datetime
    staff: StaffUser


class Controls(BaseModel):
    """The switches at the top of the dashboard."""

    # False pauses ordering until staff turn it back on.
    accepting_orders: bool
    # Busy mode: minutes added to every new pickup quote.
    busy_extra_minutes: int


class ControlsChange(BaseModel):
    """A partial change. Fields that are not sent stay as they are."""

    model_config = ConfigDict(extra="forbid")

    accepting_orders: bool | None = None
    busy_extra_minutes: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if self.accepting_orders is None and self.busy_extra_minutes is None:
            raise ValueError("send accepting_orders or busy_extra_minutes")
        return self


class Board(BaseModel):
    """Everything one dashboard poll needs."""

    orders: list[Order]
    controls: Controls


class ActionDetails(BaseModel):
    """What goes with a staff action on an order."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Accept only: minutes added to the promised pickup time.
    extra_minutes: Literal[0, 5, 10] = 0
    # Reject (required) and cancel (optional): why.
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)


class Availability(BaseModel):
    """False marks a menu item or option sold out."""

    model_config = ConfigDict(extra="forbid")

    is_available: bool
