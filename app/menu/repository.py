from uuid import UUID

import asyncpg

from app.menu.models import (
    Menu,
    MenuCategory,
    MenuItem,
    ModifierGroup,
    ModifierOption,
)


async def fetch_menu(conn: asyncpg.Connection, restaurant_id: UUID) -> Menu | None:
    """Load one restaurant's full menu, or None if the restaurant does not exist.

    Runs several queries, so call it inside one transaction for a consistent read.
    Unavailable items and options are included, flagged by is_available.
    """
    restaurant = await conn.fetchrow(
        "SELECT id, name FROM restaurants WHERE id = $1", restaurant_id
    )
    if restaurant is None:
        return None

    category_rows = await conn.fetch(
        """
        SELECT id, name
        FROM menu_categories
        WHERE restaurant_id = $1
        ORDER BY sort_order, name, id
        """,
        restaurant_id,
    )
    item_rows = await conn.fetch(
        """
        SELECT id, category_id, name, description, price_cents, prep_minutes,
               is_available
        FROM menu_items
        WHERE restaurant_id = $1
        ORDER BY sort_order, name, id
        """,
        restaurant_id,
    )
    alias_rows = await conn.fetch(
        """
        SELECT menu_item_id, alias
        FROM item_aliases
        WHERE restaurant_id = $1
        ORDER BY lower(alias), id
        """,
        restaurant_id,
    )
    option_rows = await conn.fetch(
        """
        SELECT id, group_id, name, kind, price_delta_cents, is_available
        FROM modifier_options
        WHERE restaurant_id = $1
        ORDER BY sort_order, name, id
        """,
        restaurant_id,
    )
    item_group_rows = await conn.fetch(
        """
        SELECT ig.menu_item_id, g.id, g.name, g.min_selections, g.max_selections
        FROM item_modifier_groups AS ig
        JOIN modifier_groups AS g
          ON g.restaurant_id = ig.restaurant_id AND g.id = ig.group_id
        WHERE ig.restaurant_id = $1
        ORDER BY ig.sort_order, g.name, g.id
        """,
        restaurant_id,
    )

    options_by_group: dict[UUID, list[ModifierOption]] = {}
    for row in option_rows:
        options_by_group.setdefault(row["group_id"], []).append(
            ModifierOption(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                price_delta_cents=row["price_delta_cents"],
                is_available=row["is_available"],
            )
        )

    groups_by_item: dict[UUID, list[ModifierGroup]] = {}
    for row in item_group_rows:
        groups_by_item.setdefault(row["menu_item_id"], []).append(
            ModifierGroup(
                id=row["id"],
                name=row["name"],
                min_selections=row["min_selections"],
                max_selections=row["max_selections"],
                options=options_by_group.get(row["id"], []),
            )
        )

    aliases_by_item: dict[UUID, list[str]] = {}
    for row in alias_rows:
        aliases_by_item.setdefault(row["menu_item_id"], []).append(row["alias"])

    categories = {
        row["id"]: MenuCategory(id=row["id"], name=row["name"]) for row in category_rows
    }
    for row in item_rows:
        categories[row["category_id"]].items.append(
            MenuItem(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                price_cents=row["price_cents"],
                prep_minutes=row["prep_minutes"],
                is_available=row["is_available"],
                aliases=aliases_by_item.get(row["id"], []),
                modifier_groups=groups_by_item.get(row["id"], []),
            )
        )

    return Menu(
        restaurant_id=restaurant["id"],
        restaurant_name=restaurant["name"],
        categories=list(categories.values()),
    )
