-- migrate:up
CREATE TABLE public.menu_categories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    sort_order integer NOT NULL DEFAULT 0,
    UNIQUE (restaurant_id, name),
    UNIQUE (restaurant_id, id)
);

-- menu_items must be empty here: the new NOT NULL columns have no default,
-- so every item states its category and its real prep time.
ALTER TABLE public.menu_items
    ADD COLUMN category_id uuid NOT NULL,
    ADD COLUMN prep_minutes integer NOT NULL
        CHECK (prep_minutes BETWEEN 0 AND 240),
    ADD COLUMN sort_order integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT menu_items_category_fkey
        FOREIGN KEY (restaurant_id, category_id)
        REFERENCES public.menu_categories (restaurant_id, id);

CREATE INDEX menu_items_category_idx
    ON public.menu_items (restaurant_id, category_id);

-- How people actually say an item ("chick sando", "coke").
CREATE TABLE public.item_aliases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL,
    menu_item_id uuid NOT NULL,
    alias text NOT NULL CHECK (length(btrim(alias)) > 0),
    FOREIGN KEY (restaurant_id, menu_item_id)
        REFERENCES public.menu_items (restaurant_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX item_aliases_item_alias_key
    ON public.item_aliases (menu_item_id, lower(alias));

-- min_selections >= 1 makes the group required (grilled or crispy);
-- 0 makes it optional (add bacon).
CREATE TABLE public.modifier_groups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    min_selections integer NOT NULL DEFAULT 0 CHECK (min_selections >= 0),
    max_selections integer NOT NULL DEFAULT 1 CHECK (max_selections >= 1),
    CHECK (max_selections >= min_selections),
    UNIQUE (restaurant_id, id)
);

CREATE TABLE public.modifier_options (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL,
    group_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    kind text NOT NULL CHECK (kind IN ('choice', 'add', 'remove')),
    price_delta_cents bigint NOT NULL DEFAULT 0 CHECK (price_delta_cents >= 0),
    is_available boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0,
    -- Removing an ingredient is always free.
    CHECK (kind <> 'remove' OR price_delta_cents = 0),
    FOREIGN KEY (restaurant_id, group_id)
        REFERENCES public.modifier_groups (restaurant_id, id) ON DELETE CASCADE,
    UNIQUE (group_id, name),
    UNIQUE (restaurant_id, id)
);

-- A group can be shared by several items (one "Drink size" for all drinks).
CREATE TABLE public.item_modifier_groups (
    restaurant_id uuid NOT NULL,
    menu_item_id uuid NOT NULL,
    group_id uuid NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    PRIMARY KEY (menu_item_id, group_id),
    FOREIGN KEY (restaurant_id, menu_item_id)
        REFERENCES public.menu_items (restaurant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (restaurant_id, group_id)
        REFERENCES public.modifier_groups (restaurant_id, id) ON DELETE CASCADE
);

CREATE INDEX item_modifier_groups_group_idx
    ON public.item_modifier_groups (group_id);

-- migrate:down
DROP TABLE public.item_modifier_groups;
DROP TABLE public.modifier_options;
DROP TABLE public.modifier_groups;
DROP TABLE public.item_aliases;
DROP INDEX public.menu_items_category_idx;
ALTER TABLE public.menu_items
    DROP CONSTRAINT menu_items_category_fkey,
    DROP COLUMN sort_order,
    DROP COLUMN prep_minutes,
    DROP COLUMN category_id;
DROP TABLE public.menu_categories;