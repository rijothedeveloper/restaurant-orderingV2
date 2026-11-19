"""Order rules that need no database: option validation and money math."""

from collections import Counter
from collections.abc import Sequence
from uuid import UUID

from app.menu.models import Menu, MenuItem, ModifierGroup, ModifierOption
from app.orders.errors import MissingRequiredChoices, OrderError
from app.orders.models import Order, OrderLine

SelectedOption = tuple[ModifierGroup, ModifierOption]


def tax_cents(subtotal_cents: int, tax_rate_bps: int) -> int:
    """Tax on a subtotal, rounded half-up to the cent. 875 bps = 8.75%."""
    return (subtotal_cents * tax_rate_bps + 5000) // 10000


def line_total_cents(unit_price_cents: int, modifier_total_cents: int, qty: int) -> int:
    return (unit_price_cents + modifier_total_cents) * qty


def select_options(item: MenuItem, option_ids: Sequence[UUID]) -> list[SelectedOption]:
    """Check a selection against the item's modifier groups.

    Returns the chosen options in menu order. Raises OrderError, or
    MissingRequiredChoices when a required group has too few selections.
    """
    if not item.is_available:
        raise OrderError("item_unavailable", f"{item.name} is not available right now")

    if len(set(option_ids)) != len(option_ids):
        raise OrderError("duplicate_option", "An option was selected more than once")

    options_by_id: dict[UUID, SelectedOption] = {
        option.id: (group, option)
        for group in item.modifier_groups
        for option in group.options
    }

    chosen: set[UUID] = set()
    per_group: Counter[UUID] = Counter()
    for option_id in option_ids:
        found = options_by_id.get(option_id)
        if found is None:
            raise OrderError(
                "option_not_found",
                f"Option {option_id} is not offered for {item.name}",
            )
        group, option = found
        if not option.is_available:
            raise OrderError(
                "option_unavailable", f"{option.name} is not available right now"
            )
        chosen.add(option_id)
        per_group[group.id] += 1

    for group in item.modifier_groups:
        if per_group[group.id] > group.max_selections:
            raise OrderError(
                "too_many_selections",
                f"{group.name} allows at most {group.max_selections} selection(s)",
            )

    missing = [
        group
        for group in item.modifier_groups
        if per_group[group.id] < group.min_selections
    ]
    if missing:
        raise MissingRequiredChoices(item.name, missing)

    return [
        (group, option)
        for group in item.modifier_groups
        for option in group.options
        if option.id in chosen
    ]


def unavailable_lines(order: Order, menu: Menu) -> list[OrderLine]:
    """Lines the current menu no longer allows as they were ordered.

    A line fails when its item or a chosen option is sold out or gone, or when
    its options no longer satisfy the item's modifier groups.
    """
    failed = []
    for line in order.lines:
        item = menu.find_item(line.menu_item_id)
        try:
            if item is None:
                raise OrderError("item_not_found", "That item is not on the menu")
            select_options(item, [modifier.option_id for modifier in line.modifiers])
        except OrderError:
            failed.append(line)
    return failed
