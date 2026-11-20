-- migrate:up

ALTER TABLE public.staff_users
    -- A manager can also manage staff, the menu, hours and settings.
    ADD COLUMN role text NOT NULL DEFAULT 'staff'
        CHECK (role IN ('manager', 'staff'));

-- Staff from before roles existed could do everything there was to do, and a
-- restaurant needs a manager to create any others.
UPDATE public.staff_users SET role = 'manager';

-- Menu rows that orders may point at are archived, not deleted: an archived
-- row is off the menu for good, while is_available = false is sold out for now.
ALTER TABLE public.menu_categories ADD COLUMN archived_at timestamptz;
ALTER TABLE public.menu_items ADD COLUMN archived_at timestamptz;
ALTER TABLE public.modifier_groups ADD COLUMN archived_at timestamptz;
ALTER TABLE public.modifier_options ADD COLUMN archived_at timestamptz;

-- Names are unique among the rows still on the menu, so an archived name can
-- be used again.
ALTER TABLE public.menu_categories
    DROP CONSTRAINT menu_categories_restaurant_id_name_key;
CREATE UNIQUE INDEX menu_categories_live_name_key
    ON public.menu_categories (restaurant_id, name) WHERE archived_at IS NULL;

ALTER TABLE public.menu_items
    DROP CONSTRAINT menu_items_restaurant_id_name_key;
CREATE UNIQUE INDEX menu_items_live_name_key
    ON public.menu_items (restaurant_id, name) WHERE archived_at IS NULL;

ALTER TABLE public.modifier_options
    DROP CONSTRAINT modifier_options_group_id_name_key;
CREATE UNIQUE INDEX modifier_options_live_name_key
    ON public.modifier_options (group_id, name) WHERE archived_at IS NULL;

-- migrate:down

-- Fails if an archived row shares a name with a live one; delete or rename
-- the archived rows first.
DROP INDEX public.modifier_options_live_name_key;
ALTER TABLE public.modifier_options
    ADD CONSTRAINT modifier_options_group_id_name_key UNIQUE (group_id, name);

DROP INDEX public.menu_items_live_name_key;
ALTER TABLE public.menu_items
    ADD CONSTRAINT menu_items_restaurant_id_name_key UNIQUE (restaurant_id, name);

DROP INDEX public.menu_categories_live_name_key;
ALTER TABLE public.menu_categories
    ADD CONSTRAINT menu_categories_restaurant_id_name_key
        UNIQUE (restaurant_id, name);

ALTER TABLE public.modifier_options DROP COLUMN archived_at;
ALTER TABLE public.modifier_groups DROP COLUMN archived_at;
ALTER TABLE public.menu_items DROP COLUMN archived_at;
ALTER TABLE public.menu_categories DROP COLUMN archived_at;

ALTER TABLE public.staff_users DROP COLUMN role;