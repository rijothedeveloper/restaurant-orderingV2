from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from app.menu import admin
from app.menu.models import (
    CategoryInput,
    EditableCategory,
    EditableMenu,
    GroupInput,
    ItemInput,
    ModifierGroup,
    OptionInput,
)
from app.menu.repository import fetch_menu
from app.orders import service
from app.orders.errors import LinesUnavailable, OrderError
from app.orders.models import AddLine
from tests.factories import CUSTOMER, Shop, create_shop, place_order

pytestmark = pytest.mark.integration


@pytest.fixture
async def shop(conn: asyncpg.Connection) -> Shop:
    return await create_shop(conn)


def mains(menu: EditableMenu) -> EditableCategory:
    return next(category for category in menu.categories if category.name == "Mains")


def group_named(menu: EditableMenu, name: str) -> ModifierGroup:
    return next(group for group in menu.groups if group.name == name)


def item_input(category_id, name: str = "Soup", **changes) -> ItemInput:
    return ItemInput(
        **{
            "category_id": category_id,
            "name": name,
            "price_cents": 650,
            "prep_minutes": 8,
            **changes,
        }
    )


async def error_code(awaitable) -> str:
    with pytest.raises(OrderError) as raised:
        await awaitable
    return raised.value.code


def test_inputs_are_checked() -> None:
    option = {"name": "No onions", "kind": "remove"}
    with pytest.raises(ValidationError, match="cannot have a price"):
        OptionInput(**option, price_delta_cents=50)
    with pytest.raises(ValidationError, match="more options"):
        GroupInput(name="Style", min_selections=2, max_selections=2, options=[option])
    with pytest.raises(ValidationError, match="at least min_selections"):
        GroupInput(name="Style", min_selections=2, max_selections=1, options=[])
    with pytest.raises(ValidationError, match="listed twice"):
        GroupInput(
            name="Style", min_selections=0, max_selections=1, options=[option, option]
        )
    with pytest.raises(ValidationError, match="alias is listed twice"):
        item_input(uuid4(), aliases=["Coke", "coke "])
    assert item_input(uuid4(), name="  Soup ", description=" ").description is None


async def test_get_menu_lists_items_with_group_ids_and_every_group(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    await admin.create_group(
        conn,
        shop.restaurant_id,
        GroupInput(name="Unused", min_selections=0, max_selections=1, options=[]),
    )

    menu = await admin.get_menu(conn, shop.restaurant_id)

    assert [item.name for item in mains(menu).items] == [
        "Chicken Sandwich",
        "Fries",
        "Shake",
    ]
    sandwich = mains(menu).items[0]
    assert sandwich.group_ids == [
        group_named(menu, "Style").id,
        group_named(menu, "Extras").id,
    ]
    assert [group.name for group in menu.groups] == ["Extras", "Style", "Unused"]
    assert [option.name for option in group_named(menu, "Style").options] == [
        "Grilled",
        "Crispy",
    ]


async def test_categories_are_added_renamed_reordered_and_deleted(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    drinks = await admin.create_category(conn, rid, CategoryInput(name=" Drinks "))
    menu = await admin.get_menu(conn, rid)
    assert [category.name for category in menu.categories] == ["Mains", "Drinks"]

    taken = admin.create_category(conn, rid, CategoryInput(name="Mains"))
    assert await error_code(taken) == "name_taken"

    await admin.rename_category(conn, rid, drinks, CategoryInput(name="Cold drinks"))
    main_id = mains(menu).id
    await admin.reorder_categories(conn, rid, [drinks, main_id])
    menu = await admin.get_menu(conn, rid)
    assert [category.name for category in menu.categories] == ["Cold drinks", "Mains"]

    # Not the whole list, or not this restaurant's.
    assert await error_code(admin.reorder_categories(conn, rid, [drinks])) == (
        "order_mismatch"
    )
    assert await error_code(admin.reorder_categories(conn, rid, [drinks, uuid4()])) == (
        "order_mismatch"
    )

    assert await error_code(admin.delete_category(conn, rid, main_id)) == (
        "category_not_empty"
    )
    await admin.delete_category(conn, rid, drinks)
    assert await error_code(admin.delete_category(conn, rid, drinks)) == (
        "menu_category_not_found"
    )
    menu = await admin.get_menu(conn, rid)
    assert [category.name for category in menu.categories] == ["Mains"]
    # An archived name is free again.
    await admin.create_category(conn, rid, CategoryInput(name="Cold drinks"))


async def test_item_is_created_changed_and_moved(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    menu = await admin.get_menu(conn, rid)
    main_id = mains(menu).id
    style, extras = group_named(menu, "Style").id, group_named(menu, "Extras").id

    soup = await admin.create_item(
        conn,
        rid,
        item_input(
            main_id, aliases=["tomato soup", "Soup of the day"], group_ids=[extras]
        ),
    )

    item = (await fetch_menu(conn, rid)).find_item(soup)
    assert (item.name, item.price_cents, item.prep_minutes) == ("Soup", 650, 8)
    assert item.is_available
    assert item.aliases == ["Soup of the day", "tomato soup"]
    assert [group.id for group in item.modifier_groups] == [extras]
    menu = await admin.get_menu(conn, rid)
    assert mains(menu).items[-1].id == soup

    sides = await admin.create_category(conn, rid, CategoryInput(name="Sides"))
    await service.add_item(
        conn,
        rid,
        (await service.create_draft(conn, rid, "storefront")).id,
        AddLine(menu_item_id=soup),
    )
    await admin.update_item(
        conn,
        rid,
        soup,
        item_input(
            sides,
            name="Big Soup",
            price_cents=800,
            aliases=["soup"],
            group_ids=[style, extras],
        ),
    )

    menu = await admin.get_menu(conn, rid)
    moved = menu.categories[1].items[0]
    assert (moved.id, moved.name, moved.price_cents) == (soup, "Big Soup", 800)
    assert moved.aliases == ["soup"]
    assert moved.group_ids == [style, extras]
    assert soup not in [item.id for item in mains(menu).items]


async def test_item_changes_are_checked(conn: asyncpg.Connection, shop: Shop) -> None:
    rid = shop.restaurant_id
    main_id = mains(await admin.get_menu(conn, rid)).id
    other = await create_shop(conn, "Other Diner")
    other_menu = await admin.get_menu(conn, other.restaurant_id)

    create = admin.create_item(conn, rid, item_input(main_id, name="Fries"))
    assert await error_code(create) == "name_taken"
    create = admin.create_item(conn, rid, item_input(uuid4()))
    assert await error_code(create) == "menu_category_not_found"
    # Another restaurant's category and group are not found either.
    create = admin.create_item(conn, rid, item_input(mains(other_menu).id))
    assert await error_code(create) == "menu_category_not_found"
    create = admin.create_item(
        conn, rid, item_input(main_id, group_ids=[other_menu.groups[0].id])
    )
    assert await error_code(create) == "modifier_group_not_found"
    update = admin.update_item(conn, rid, other.fries, item_input(main_id))
    assert await error_code(update) == "menu_item_not_found"
    update = admin.update_item(conn, rid, shop.fries, item_input(main_id, name="Shake"))
    assert await error_code(update) == "name_taken"
    # Nothing above left a mark.
    assert len(mains(await admin.get_menu(conn, rid)).items) == 3


async def test_items_are_reordered_within_their_category(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    main_id = mains(await admin.get_menu(conn, rid)).id

    await admin.reorder_items(
        conn, rid, main_id, [shop.shake, shop.sandwich, shop.fries]
    )

    menu = await admin.get_menu(conn, rid)
    assert [item.name for item in mains(menu).items] == [
        "Shake",
        "Chicken Sandwich",
        "Fries",
    ]
    reorder = admin.reorder_items(conn, rid, main_id, [shop.shake, shop.fries])
    assert await error_code(reorder) == "order_mismatch"
    reorder = admin.reorder_items(conn, rid, uuid4(), [])
    assert await error_code(reorder) == "menu_category_not_found"


async def test_deleted_item_leaves_the_menu_but_not_placed_orders(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    add_fries = AddLine(menu_item_id=shop.fries)
    placed = await service.add_item(
        conn, rid, (await service.create_draft(conn, rid, "storefront")).id, add_fries
    )
    placed = await place_order(conn, rid, placed.id)
    draft = await service.add_item(
        conn, rid, (await service.create_draft(conn, rid, "storefront")).id, add_fries
    )

    await admin.delete_item(conn, rid, shop.fries)

    assert (await fetch_menu(conn, rid)).find_item(shop.fries) is None
    assert await error_code(admin.delete_item(conn, rid, shop.fries)) == (
        "menu_item_not_found"
    )
    # The placed order still shows its line; the draft can no longer be placed.
    order = await service.get_order(conn, rid, placed.id)
    assert [line.name for line in order.lines] == ["Fries"]
    with pytest.raises(LinesUnavailable):
        await service.confirm_order(conn, rid, draft.id, draft.revision, CUSTOMER)
    assert await error_code(service.add_item(conn, rid, draft.id, add_fries)) == (
        "item_not_found"
    )
    # The name can be used again.
    main_id = mains(await admin.get_menu(conn, rid)).id
    await admin.create_item(conn, rid, item_input(main_id, name="Fries"))


async def test_group_update_keeps_changes_adds_and_removes_options(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    extras = group_named(await admin.get_menu(conn, rid), "Extras")
    # An order line holds the option that is about to be removed.
    draft = await service.create_draft(conn, rid, "storefront")
    draft = await service.add_item(
        conn,
        rid,
        draft.id,
        AddLine(menu_item_id=shop.sandwich, option_ids=[shop.grilled, shop.bacon]),
    )

    await admin.update_group(
        conn,
        rid,
        extras.id,
        GroupInput(
            name="Add-ons",
            min_selections=0,
            max_selections=3,
            options=[
                OptionInput(name="Add pickles", kind="add", price_delta_cents=50),
                OptionInput(
                    id=shop.cheese,
                    name="Extra cheese",
                    kind="add",
                    price_delta_cents=125,
                ),
                OptionInput(
                    id=shop.sold_out_extra,
                    name="Add egg",
                    kind="add",
                    price_delta_cents=100,
                ),
                # Bacon was removed and comes back as a new option.
                OptionInput(name="Add bacon", kind="add", price_delta_cents=200),
            ],
        ),
    )

    group = group_named(await admin.get_menu(conn, rid), "Add-ons")
    assert group.max_selections == 3
    assert [
        (option.name, option.price_delta_cents, option.is_available)
        for option in group.options
    ] == [
        ("Add pickles", 50, True),
        ("Extra cheese", 125, True),
        ("Add egg", 100, False),
        ("Add bacon", 200, True),
    ]
    assert group.options[1].id == shop.cheese
    assert shop.bacon not in [option.id for option in group.options]
    # The sandwich uses the group, so it sees the change too.
    sandwich = (await fetch_menu(conn, rid)).find_item(shop.sandwich)
    assert [g.name for g in sandwich.modifier_groups] == ["Style", "Add-ons"]
    # The draft keeps its copy of the line but cannot be placed as it is.
    with pytest.raises(LinesUnavailable):
        await service.confirm_order(conn, rid, draft.id, draft.revision, CUSTOMER)


async def test_group_changes_are_checked_and_groups_deleted(
    conn: asyncpg.Connection, shop: Shop
) -> None:
    rid = shop.restaurant_id
    menu = await admin.get_menu(conn, rid)
    style = group_named(menu, "Style")
    stranger = OptionInput(id=shop.bacon, name="Bacon", kind="add")

    def group(*options: OptionInput) -> GroupInput:
        return GroupInput(
            name="Style", min_selections=0, max_selections=1, options=list(options)
        )

    # An option of another group, and an id on a brand new group.
    assert await error_code(
        admin.update_group(conn, rid, style.id, group(stranger))
    ) == ("modifier_option_not_found")
    assert await error_code(admin.create_group(conn, rid, group(stranger))) == (
        "modifier_option_not_found"
    )
    assert await error_code(admin.update_group(conn, rid, uuid4(), group())) == (
        "modifier_group_not_found"
    )

    await admin.delete_group(conn, rid, style.id)

    menu = await admin.get_menu(conn, rid)
    assert [g.name for g in menu.groups] == ["Extras"]
    assert mains(menu).items[0].group_ids == [group_named(menu, "Extras").id]
    assert await error_code(admin.delete_group(conn, rid, style.id)) == (
        "modifier_group_not_found"
    )
    # With the required Style group gone, a plain sandwich can be ordered.
    draft = await service.create_draft(conn, rid, "storefront")
    await service.add_item(conn, rid, draft.id, AddLine(menu_item_id=shop.sandwich))
