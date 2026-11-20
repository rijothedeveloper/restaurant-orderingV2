from datetime import datetime, time
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.orders.models import MAX_REASON_LENGTH, Order

# Long enough that guessing runs into the sign-in lock first.
PIN_PATTERN = r"^[0-9]{6,8}$"

# A manager can also manage staff, the menu, hours and settings.
Role = Literal["manager", "staff"]
StaffName = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class StaffUser(BaseModel):
    id: UUID
    restaurant_id: UUID
    name: str
    role: Role


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


class StaffMember(BaseModel):
    """A staff user as a manager sees them. The PIN is never shown."""

    id: UUID
    name: str
    role: Role
    is_active: bool


class NewStaff(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: StaffName
    pin: str = Field(pattern=PIN_PATTERN)
    role: Role = "staff"


class StaffChange(BaseModel):
    """A partial change. Fields that are not sent stay as they are.

    A new PIN, or `is_active` false, signs the person out everywhere. Turning
    someone back on needs a new PIN.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: StaffName | None = None
    role: Role | None = None
    pin: str | None = Field(default=None, pattern=PIN_PATTERN)
    is_active: bool | None = None


class OpenHours(BaseModel):
    """One opening window, in the restaurant's local time.

    `weekday` is the day it opens, 0 = Monday. `closes_at` at or before
    `opens_at` means it closes the next day.
    """

    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def whole_minutes(self) -> Self:
        for at in (self.opens_at, self.closes_at):
            if at.second or at.microsecond or at.tzinfo is not None:
                raise ValueError("times are local, in whole minutes")
        return self


class HoursChange(BaseModel):
    """The whole week; it replaces the hours there were."""

    model_config = ConfigDict(extra="forbid")

    hours: list[OpenHours] = Field(max_length=50)


class KitchenSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 875 = 8.75%.
    tax_rate_bps: int = Field(ge=0, le=10000)
    # Orders the kitchen finishes per 10 minutes.
    kitchen_capacity: int = Field(ge=1, le=100)
    # No new orders in the last minutes before closing.
    last_order_minutes: int = Field(ge=0, le=240)


class RestaurantSetup(BaseModel):
    """What the settings screen shows."""

    restaurant_name: str
    timezone: str
    settings: KitchenSettings
    hours: list[OpenHours]
