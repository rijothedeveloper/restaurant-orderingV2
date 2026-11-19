from uuid import uuid4

import pytest

from app.menu.models import Menu, MenuCategory, MenuItem, ModifierGroup, ModifierOption
from app.orders.errors import MissingRequiredChoices, OrderError
from app.orders.models import CustomerDetails, Order, OrderLine, OrderLineModifier
from app.orders.phone import normalize_phone
from app.orders.pricing import (
    line_total_cents,
    select_options,
    tax_cents,
    unavailable_lines,
)
from app.orders.readback import format_cents, read_back_text


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


# --- unavailable_lines ------------------------------------------------------


def make_menu(*items: MenuItem) -> Menu:
    return Menu(
        restaurant_id=uuid4(),
        restaurant_name="Test Diner",
        categories=[MenuCategory(id=uuid4(), name="Mains", items=list(items))],
    )


def make_line(
    item: MenuItem, *options: ModifierOption, quantity: int = 1, notes=None
) -> OrderLine:
    modifier_total = sum(o.price_delta_cents for o in options)
    return OrderLine(
        id=uuid4(),
        menu_item_id=item.id,
        name=item.name,
        quantity=quantity,
        unit_price_cents=item.price_cents,
        modifier_total_cents=modifier_total,
        line_total_cents=(item.price_cents + modifier_total) * quantity,
        notes=notes,
        modifiers=[
            OrderLineModifier(
                option_id=o.id,
                group_name="Group",
                name=o.name,
                kind=o.kind,
                price_delta_cents=o.price_delta_cents,
            )
            for o in options
        ],
    )


def make_order(*lines: OrderLine) -> Order:
    subtotal = sum(line.line_total_cents for line in lines)
    tax = tax_cents(subtotal, 875)
    return Order(
        id=uuid4(),
        restaurant_id=uuid4(),
        status="draft",
        channel="web",
        revision=len(lines),
        lines=list(lines),
        subtotal_cents=subtotal,
        tax_rate_bps=875,
        tax_cents=tax,
        total_cents=subtotal + tax,
    )


def test_unavailable_lines_is_empty_when_everything_can_be_made() -> None:
    style, extras = style_group(), extras_group()
    item = make_item(style, extras)
    order = make_order(make_line(item, style.options[0], extras.options[0]))

    assert unavailable_lines(order, make_menu(item)) == []


def test_unavailable_lines_finds_sold_out_and_missing_items() -> None:
    fine = make_item()
    sold_out = make_item(available=False)
    gone = make_item()
    lines = [make_line(fine), make_line(sold_out), make_line(gone)]

    failed = unavailable_lines(make_order(*lines), make_menu(fine, sold_out))

    assert failed == lines[1:]


def test_unavailable_lines_finds_a_sold_out_option() -> None:
    extras = extras_group()
    item = make_item(extras)
    line = make_line(item, extras.options[0])
    extras.options[0].is_available = False

    assert unavailable_lines(make_order(line), make_menu(item)) == [line]


def test_unavailable_lines_finds_a_newly_required_choice() -> None:
    plain = make_item()
    line = make_line(plain)
    now_with_style = plain.model_copy(update={"modifier_groups": [style_group()]})

    assert unavailable_lines(make_order(line), make_menu(now_with_style)) == [line]


# --- read-back --------------------------------------------------------------


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(0, "$0.00"), (5, "$0.05"), (399, "$3.99"), (120000, "$1200.00")],
)
def test_format_cents(cents: int, expected: str) -> None:
    assert format_cents(cents) == expected


def test_read_back_text_lists_lines_options_notes_and_total() -> None:
    style, extras = style_group(), extras_group()
    sandwich = make_item(style, extras)
    fries = make_item().model_copy(update={"name": "Fries", "price_cents": 399})
    order = make_order(
        make_line(sandwich, style.options[0], extras.options[0], quantity=2),
        make_line(fries, notes="well done"),
    )

    # 2 x (1099 + 150) + 399 = 2897; tax 253.49 -> 253.
    assert read_back_text(order) == (
        "Your order: 2 x Chicken Sandwich (Grilled, Add bacon); "
        "1 x Fries (note: well done). "
        "Total with tax: $31.50. Pay at pickup. Shall I place it?"
    )


# --- customer details -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4155552671", "+14155552671"),
        ("(415) 555-2671", "+14155552671"),
        ("415.555.2671", "+14155552671"),
        ("1 415 555 2671", "+14155552671"),
        ("+1 415-555-2671", "+14155552671"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "555-2671",
        "0155552671",
        "415555267",
        "41555526711",
        "+0123456789",
        "+1234567",
        "call me",
        "４１５５５５２６７１",
    ],
)
def test_normalize_phone_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_customer_details_are_cleaned() -> None:
    customer = CustomerDetails(name="  Sam  ", phone=" (415) 555-2671 ")

    assert customer.name == "Sam"
    assert customer.phone == "+14155552671"
    for bad in (
        {"name": "   ", "phone": "4155552671"},
        {"name": "x" * 81, "phone": "4155552671"},
        {"name": "Sam", "phone": "nope"},
        {"name": "Sam", "phone": "4155552671", "extra": 1},
    ):
        with pytest.raises(ValueError):
            CustomerDetails(**bad)
