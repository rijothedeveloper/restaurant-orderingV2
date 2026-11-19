-- migrate:up

-- Per-restaurant settings. A restaurant without a row cannot take orders.
CREATE TABLE public.settings (
    restaurant_id uuid PRIMARY KEY
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    tax_rate_bps integer NOT NULL CHECK (tax_rate_bps BETWEEN 0 AND 10000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- orders and order_items must be empty here: the new NOT NULL column has no
-- default, and the old status values are not carried over.
ALTER TABLE public.orders
    -- A draft has no number; the per-day number is assigned at confirmation.
    DROP COLUMN order_number,
    DROP CONSTRAINT orders_status_check,
    DROP CONSTRAINT orders_channel_check,
    ALTER COLUMN status SET DEFAULT 'draft',
    ADD CONSTRAINT orders_status_check CHECK (status IN (
        'draft', 'placed', 'accepted', 'preparing', 'ready', 'picked_up',
        'rejected', 'cancelled', 'no_show'
    )),
    ADD CONSTRAINT orders_channel_check CHECK (channel IN (
        'voice', 'web', 'whatsapp', 'sms', 'storefront'
    )),
    -- Bumped by every change to the order's lines.
    ADD COLUMN revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    -- The rate the stored tax_cents was computed with.
    ADD COLUMN tax_rate_bps integer NOT NULL
        CHECK (tax_rate_bps BETWEEN 0 AND 10000);

ALTER TABLE public.order_items
    -- Stable display order of lines within an order.
    ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY,
    -- Per-unit sum of the line's option price deltas.
    ADD COLUMN modifier_total_cents bigint NOT NULL DEFAULT 0
        CHECK (modifier_total_cents >= 0),
    DROP CONSTRAINT order_items_check,
    ADD CONSTRAINT order_items_line_total_check CHECK (
        line_total_cents = (unit_price_cents + modifier_total_cents) * quantity
    ),
    ADD CONSTRAINT order_items_restaurant_id_id_key UNIQUE (restaurant_id, id);

-- Copies of the chosen options at the time the line was added or changed.
CREATE TABLE public.order_item_modifiers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL,
    order_item_id uuid NOT NULL,
    modifier_option_id uuid NOT NULL,
    group_name text NOT NULL,
    option_name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('choice', 'add', 'remove')),
    price_delta_cents bigint NOT NULL CHECK (price_delta_cents >= 0),
    seq bigint GENERATED ALWAYS AS IDENTITY,
    FOREIGN KEY (restaurant_id, order_item_id)
        REFERENCES public.order_items (restaurant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (restaurant_id, modifier_option_id)
        REFERENCES public.modifier_options (restaurant_id, id) ON DELETE RESTRICT,
    UNIQUE (order_item_id, modifier_option_id)
);

CREATE INDEX order_item_modifiers_option_idx
    ON public.order_item_modifiers (restaurant_id, modifier_option_id);

-- migrate:down
DROP TABLE public.order_item_modifiers;

ALTER TABLE public.order_items
    DROP CONSTRAINT order_items_restaurant_id_id_key,
    DROP CONSTRAINT order_items_line_total_check,
    DROP COLUMN modifier_total_cents,
    DROP COLUMN seq,
    ADD CONSTRAINT order_items_check
        CHECK (line_total_cents = unit_price_cents * quantity);

ALTER TABLE public.orders
    DROP COLUMN tax_rate_bps,
    DROP COLUMN revision,
    DROP CONSTRAINT orders_channel_check,
    DROP CONSTRAINT orders_status_check,
    ALTER COLUMN status SET DEFAULT 'pending',
    ADD CONSTRAINT orders_status_check CHECK (status IN (
        'pending', 'confirmed', 'preparing', 'ready', 'completed', 'cancelled'
    )),
    ADD CONSTRAINT orders_channel_check CHECK (channel IN (
        'voice', 'web', 'whatsapp', 'sms'
    )),
    ADD COLUMN order_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE;

DROP TABLE public.settings;