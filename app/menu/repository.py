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
    Unavailable items and options are included, flagged by is_available;
    archived rows are left out.
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
        WHERE restaurant_id = $1 AND archived_at IS NULL
        ORDER BY sort_order, name, id
        """,
        restaurant_id,
    )
    item_rows = await conn.fetch(
        """
        SELECT id, category_id, name, description, price_cents, prep_minutes,
               is_available
        FROM menu_items
        WHERE restaurant_id = $1 AND archived_at IS NULL
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
    item_group_rows = await conn.fetch(
        """
        SELECT ig.menu_item_id, g.id, g.name, g.min_selections, g.max_selections
        FROM item_modifier_groups AS ig
        JOIN modifier_groups AS g
          ON g.restaurant_id = ig.restaurant_id AND g.id = ig.group_id
        WHERE ig.restaurant_id = $1 AND g.archived_at IS NULL
        ORDER BY ig.sort_order, g.name, g.id
        """,
        restaurant_id,
    )

    options_by_group = await _fetch_options_by_group(conn, restaurant_id)

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


async def _fetch_options_by_group(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> dict[UUID, list[ModifierOption]]:
    rows = await conn.fetch(
        """
        SELECT id, group_id, name, kind, price_delta_cents, is_available
        FROM modifier_options
        WHERE restaurant_id = $1 AND archived_at IS NULL
        ORDER BY sort_order, name, id
        """,
        restaurant_id,
    )
    options_by_group: dict[UUID, list[ModifierOption]] = {}
    for row in rows:
        options_by_group.setdefault(row["group_id"], []).append(
            ModifierOption(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                price_delta_cents=row["price_delta_cents"],
                is_available=row["is_available"],
            )
        )
    return options_by_group


async def set_item_available(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID, is_available: bool
) -> bool:
    """Mark an item sold out or back on. False if the item does not exist."""
    updated = await conn.fetchval(
        """
        UPDATE menu_items
        SET is_available = $3, updated_at = now()
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        item_id,
        is_available,
    )
    return updated is not None


async def set_option_available(
    conn: asyncpg.Connection, restaurant_id: UUID, option_id: UUID, is_available: bool
) -> bool:
    """Mark an option sold out or back on. False if the option does not exist."""
    updated = await conn.fetchval(
        """
        UPDATE modifier_options
        SET is_available = $3
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        option_id,
        is_available,
    )
    return updated is not None


# Menu editing. Rows that orders may point at are archived, never deleted.


async def fetch_groups(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> list[ModifierGroup]:
    """Every modifier group still on the menu, attached to an item or not."""
    rows = await conn.fetch(
        """
        SELECT id, name, min_selections, max_selections
        FROM modifier_groups
        WHERE restaurant_id = $1 AND archived_at IS NULL
        ORDER BY name, id
        """,
        restaurant_id,
    )
    options_by_group = await _fetch_options_by_group(conn, restaurant_id)
    return [
        ModifierGroup(**row, options=options_by_group.get(row["id"], []))
        for row in rows
    ]


async def insert_category(
    conn: asyncpg.Connection, restaurant_id: UUID, name: str
) -> UUID:
    """Add a category after the ones already there."""
    return await conn.fetchval(
        """
        INSERT INTO menu_categories (restaurant_id, name, sort_order)
        SELECT $1, $2, COALESCE(max(sort_order), 0) + 10
        FROM menu_categories
        WHERE restaurant_id = $1 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        name,
    )


async def rename_category(
    conn: asyncpg.Connection, restaurant_id: UUID, category_id: UUID, name: str
) -> bool:
    updated = await conn.fetchval(
        """
        UPDATE menu_categories
        SET name = $3
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        category_id,
        name,
    )
    return updated is not None


async def lock_category(
    conn: asyncpg.Connection, restaurant_id: UUID, category_id: UUID
) -> bool:
    """Lock a live category until the transaction ends. False if there is none."""
    locked = await conn.fetchval(
        """
        SELECT id
        FROM menu_categories
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        FOR UPDATE
        """,
        restaurant_id,
        category_id,
    )
    return locked is not None


async def fetch_category_item_ids(
    conn: asyncpg.Connection, restaurant_id: UUID, category_id: UUID
) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM menu_items
        WHERE restaurant_id = $1 AND category_id = $2 AND archived_at IS NULL
        """,
        restaurant_id,
        category_id,
    )
    return [row["id"] for row in rows]


async def archive_category(
    conn: asyncpg.Connection, restaurant_id: UUID, category_id: UUID
) -> None:
    await conn.execute(
        """
        UPDATE menu_categories
        SET archived_at = now()
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        category_id,
    )


async def fetch_category_ids(
    conn: asyncpg.Connection, restaurant_id: UUID
) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM menu_categories
        WHERE restaurant_id = $1 AND archived_at IS NULL
        FOR UPDATE
        """,
        restaurant_id,
    )
    return [row["id"] for row in rows]


async def set_category_order(
    conn: asyncpg.Connection, restaurant_id: UUID, category_ids: list[UUID]
) -> None:
    await conn.execute(
        """
        UPDATE menu_categories AS c
        SET sort_order = o.position * 10
        FROM unnest($2::uuid[]) WITH ORDINALITY AS o (id, position)
        WHERE c.restaurant_id = $1 AND c.id = o.id
        """,
        restaurant_id,
        category_ids,
    )


async def set_item_order(
    conn: asyncpg.Connection, restaurant_id: UUID, item_ids: list[UUID]
) -> None:
    await conn.execute(
        """
        UPDATE menu_items AS i
        SET sort_order = o.position * 10, updated_at = now()
        FROM unnest($2::uuid[]) WITH ORDINALITY AS o (id, position)
        WHERE i.restaurant_id = $1 AND i.id = o.id
        """,
        restaurant_id,
        item_ids,
    )


async def insert_item(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    category_id: UUID,
    name: str,
    description: str | None,
    price_cents: int,
    prep_minutes: int,
) -> UUID:
    """Add an item at the end of its category."""
    return await conn.fetchval(
        """
        INSERT INTO menu_items (restaurant_id, category_id, name, description,
                                price_cents, prep_minutes, sort_order)
        SELECT $1, $2, $3, $4, $5, $6, COALESCE(max(sort_order), 0) + 10
        FROM menu_items
        WHERE restaurant_id = $1 AND category_id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        category_id,
        name,
        description,
        price_cents,
        prep_minutes,
    )


async def lock_item(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID
) -> UUID | None:
    """Lock a live item and return its category, or None if there is none."""
    return await conn.fetchval(
        """
        SELECT category_id
        FROM menu_items
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        FOR UPDATE
        """,
        restaurant_id,
        item_id,
    )


async def update_item(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    item_id: UUID,
    category_id: UUID,
    name: str,
    description: str | None,
    price_cents: int,
    prep_minutes: int,
    *,
    move_to_end: bool,
) -> None:
    """Change an item; `move_to_end` puts it last in its (new) category."""
    await conn.execute(
        """
        UPDATE menu_items
        SET category_id = $3, name = $4, description = $5, price_cents = $6,
            prep_minutes = $7, updated_at = now(),
            sort_order = CASE WHEN $8 THEN (
                SELECT COALESCE(max(sort_order), 0) + 10
                FROM menu_items
                WHERE restaurant_id = $1 AND category_id = $3
                  AND archived_at IS NULL
            ) ELSE sort_order END
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        item_id,
        category_id,
        name,
        description,
        price_cents,
        prep_minutes,
        move_to_end,
    )


async def archive_item(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID
) -> bool:
    archived = await conn.fetchval(
        """
        UPDATE menu_items
        SET archived_at = now(), updated_at = now()
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        item_id,
    )
    return archived is not None


async def replace_aliases(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID, aliases: list[str]
) -> None:
    await conn.execute(
        "DELETE FROM item_aliases WHERE restaurant_id = $1 AND menu_item_id = $2",
        restaurant_id,
        item_id,
    )
    await conn.executemany(
        """
        INSERT INTO item_aliases (restaurant_id, menu_item_id, alias)
        VALUES ($1, $2, $3)
        """,
        [(restaurant_id, item_id, alias) for alias in aliases],
    )


async def count_live_groups(
    conn: asyncpg.Connection, restaurant_id: UUID, group_ids: list[UUID]
) -> int:
    return await conn.fetchval(
        """
        SELECT count(*)
        FROM modifier_groups
        WHERE restaurant_id = $1 AND id = ANY($2::uuid[]) AND archived_at IS NULL
        """,
        restaurant_id,
        group_ids,
    )


async def replace_item_groups(
    conn: asyncpg.Connection, restaurant_id: UUID, item_id: UUID, group_ids: list[UUID]
) -> None:
    """Attach exactly these groups to the item, asked in this order."""
    await conn.execute(
        """
        DELETE FROM item_modifier_groups
        WHERE restaurant_id = $1 AND menu_item_id = $2
        """,
        restaurant_id,
        item_id,
    )
    await conn.executemany(
        """
        INSERT INTO item_modifier_groups (restaurant_id, menu_item_id, group_id,
                                          sort_order)
        VALUES ($1, $2, $3, $4)
        """,
        [
            (restaurant_id, item_id, group_id, position * 10)
            for position, group_id in enumerate(group_ids, start=1)
        ],
    )


async def insert_group(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    name: str,
    min_selections: int,
    max_selections: int,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO modifier_groups (restaurant_id, name, min_selections,
                                     max_selections)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        restaurant_id,
        name,
        min_selections,
        max_selections,
    )


async def update_group(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    group_id: UUID,
    name: str,
    min_selections: int,
    max_selections: int,
) -> bool:
    updated = await conn.fetchval(
        """
        UPDATE modifier_groups
        SET name = $3, min_selections = $4, max_selections = $5
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        group_id,
        name,
        min_selections,
        max_selections,
    )
    return updated is not None


async def archive_group(
    conn: asyncpg.Connection, restaurant_id: UUID, group_id: UUID
) -> bool:
    """Archive a group and its options, and take it off every item."""
    archived = await conn.fetchval(
        """
        UPDATE modifier_groups
        SET archived_at = now()
        WHERE restaurant_id = $1 AND id = $2 AND archived_at IS NULL
        RETURNING id
        """,
        restaurant_id,
        group_id,
    )
    if archived is None:
        return False
    await conn.execute(
        """
        UPDATE modifier_options
        SET archived_at = now()
        WHERE restaurant_id = $1 AND group_id = $2 AND archived_at IS NULL
        """,
        restaurant_id,
        group_id,
    )
    await conn.execute(
        "DELETE FROM item_modifier_groups WHERE restaurant_id = $1 AND group_id = $2",
        restaurant_id,
        group_id,
    )
    return True


async def fetch_option_ids(
    conn: asyncpg.Connection, restaurant_id: UUID, group_id: UUID
) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT id
        FROM modifier_options
        WHERE restaurant_id = $1 AND group_id = $2 AND archived_at IS NULL
        """,
        restaurant_id,
        group_id,
    )
    return [row["id"] for row in rows]


async def archive_options(
    conn: asyncpg.Connection, restaurant_id: UUID, option_ids: list[UUID]
) -> None:
    await conn.execute(
        """
        UPDATE modifier_options
        SET archived_at = now()
        WHERE restaurant_id = $1 AND id = ANY($2::uuid[])
        """,
        restaurant_id,
        option_ids,
    )


async def update_option(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    option_id: UUID,
    name: str,
    kind: str,
    price_delta_cents: int,
    sort_order: int,
) -> None:
    await conn.execute(
        """
        UPDATE modifier_options
        SET name = $3, kind = $4, price_delta_cents = $5, sort_order = $6
        WHERE restaurant_id = $1 AND id = $2
        """,
        restaurant_id,
        option_id,
        name,
        kind,
        price_delta_cents,
        sort_order,
    )


async def insert_option(
    conn: asyncpg.Connection,
    restaurant_id: UUID,
    group_id: UUID,
    name: str,
    kind: str,
    price_delta_cents: int,
    sort_order: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO modifier_options (restaurant_id, group_id, name, kind,
                                      price_delta_cents, sort_order)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        restaurant_id,
        group_id,
        name,
        kind,
        price_delta_cents,
        sort_order,
    )
