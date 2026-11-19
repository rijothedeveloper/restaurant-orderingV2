from uuid import uuid4

import pytest

from app.menu.models import MenuItem, ModifierGroup, ModifierOption
from app.orders.errors import MissingRequiredChoices, OrderError
from app.orders.pricing import line_total_cents, select_options, tax_cents


def option(name: str, kind: str = "choice", delta: int = 0, available: bool = True):
    return ModifierOption(
        id=uuid4(),
        name=name,
        kind=kind,
        price_delta_cents=delta,
        is_available=available,
    )


def make_item(*groups: ModifierGroup, available: bool = True) -> MenuItem:
    return MenuItem(
        id=uuid4(),
        name="Chicken Sandwich",
        description=None,
        price_cents=1099,
        prep_minutes=12,
        is_available=available,
        modifier_groups=list(groups),
    )


def style_group() -> ModifierGroup:
    return ModifierGroup(
        id=uuid4(),
        name="Style",
        min_selections=1,
        max_selections=1,
        options=[option("Grilled"), option("Crispy")],
    )


def extras_group() -> ModifierGroup:
    return ModifierGroup(
        id=uuid4(),
        name="Extras",
        min_selections=0,
        max_selections=2,
        options=[
            option("Add bacon", "add", 150),
            option("Add cheese", "add", 100),
            option("Add avocado", "add", 150),
        ],
    )


@pytest.mark.parametrize(
    ("subtotal", "rate_bps", "expected"),
    [
        (0, 875, 0),
        (1000, 0, 0),
        (1000, 875, 88),  # 87.5 rounds up
        (1099, 875, 96),  # 96.16 rounds down
        (2498, 875, 219),  # 218.575 rounds up
        (1, 5000, 1),  # 0.5 rounds up
        (1, 4999, 0),
        (1000, 10000, 1000),
    ],
)
def test_tax_cents(subtotal: int, rate_bps: int, expected: int) -> None:
    assert tax_cents(subtotal, rate_bps) == expected


def test_line_total_cents() -> None:
    assert line_total_cents(1099, 150, 2) == 2498


def test_select_options_returns_choices_in_menu_order() -> None:
    style, extras = style_group(), extras_group()
    item = make_item(style, extras)
    crispy, bacon, cheese = style.options[1], extras.options[0], extras.options[1]

    selected = select_options(item, [cheese.id, crispy.id, bacon.id])

    assert [(g.name, o.name) for g, o in selected] == [
        ("Style", "Crispy"),
        ("Extras", "Add bacon"),
        ("Extras", "Add cheese"),
    ]


def test_select_options_item_without_groups() -> None:
    assert select_options(make_item(), []) == []


def test_select_options_optional_group_can_be_skipped() -> None:
    assert select_options(make_item(extras_group()), []) == []


def test_select_options_missing_required_choice_lists_the_group() -> None:
    style, extras = style_group(), extras_group()
    item = make_item(style, extras)

    with pytest.raises(MissingRequiredChoices) as error:
        select_options(item, [extras.options[0].id])

    assert error.value.code == "missing_required_choices"
    assert error.value.groups == [style]


def test_select_options_unavailable_item() -> None:
    with pytest.raises(OrderError) as error:
        select_options(make_item(available=False), [])

    assert error.value.code == "item_unavailable"


def test_select_options_unknown_option() -> None:
    with pytest.raises(OrderError) as error:
        select_options(make_item(extras_group()), [uuid4()])

    assert error.value.code == "option_not_found"


def test_select_options_duplicate_option() -> None:
    extras = extras_group()
    bacon = extras.options[0]

    with pytest.raises(OrderError) as error:
        select_options(make_item(extras), [bacon.id, bacon.id])

    assert error.value.code == "duplicate_option"


def test_select_options_unavailable_option() -> None:
    group = ModifierGroup(
        id=uuid4(),
        name="Extras",
        min_selections=0,
        max_selections=1,
        options=[option("Add bacon", "add", 150, available=False)],
    )

    with pytest.raises(OrderError) as error:
        select_options(make_item(group), [group.options[0].id])

    assert error.value.code == "option_unavailable"


def test_select_options_too_many_in_group() -> None:
    extras = extras_group()

    with pytest.raises(OrderError) as error:
        select_options(make_item(extras), [o.id for o in extras.options])

    assert error.value.code == "too_many_selections"


def test_select_options_too_many_in_required_group() -> None:
    style = style_group()

    with pytest.raises(OrderError) as error:
        select_options(make_item(style), [o.id for o in style.options])

    assert error.value.code == "too_many_selections"
