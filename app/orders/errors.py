from datetime import datetime

from app.menu.models import ModifierGroup
from app.orders.models import OrderLine


class OrderError(Exception):
    """A request the order rules reject. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MissingRequiredChoices(OrderError):
    """The item needs a choice from each of `groups` before it can be added."""

    def __init__(self, item_name: str, groups: list[ModifierGroup]) -> None:
        names = ", ".join(group.name for group in groups)
        super().__init__(
            "missing_required_choices",
            f"{item_name} needs a choice for: {names}",
        )
        self.groups = groups


class LinesUnavailable(OrderError):
    """`lines` can no longer be made as ordered and must be changed or removed."""

    def __init__(self, lines: list[OrderLine]) -> None:
        names = ", ".join(line.name for line in lines)
        super().__init__(
            "lines_unavailable",
            f"No longer available as ordered: {names}",
        )
        self.lines = lines


class PickupUnavailable(OrderError):
    """No order can be taken now, or the pickup time asked for cannot be met.

    `next_opening_at` is when ordering opens again, and `earliest_at` the
    earliest time the order could be ready; each is set only when it applies.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_opening_at: datetime | None = None,
        earliest_at: datetime | None = None,
    ) -> None:
        super().__init__(code, message)
        self.next_opening_at = next_opening_at
        self.earliest_at = earliest_at
