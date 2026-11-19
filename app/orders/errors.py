from app.menu.models import ModifierGroup


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
