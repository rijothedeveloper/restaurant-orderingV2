-- migrate:up

ALTER TABLE public.orders
    -- The revision the customer last heard read back; NULL until a read-back.
    ADD COLUMN read_back_revision integer CHECK (read_back_revision >= 0),
    -- Set together when the order is placed. The number counts per restaurant
    -- per business date (the date in the restaurant's timezone).
    ADD COLUMN business_date date,
    ADD COLUMN order_number integer CHECK (order_number > 0),
    ADD COLUMN placed_at timestamptz,
    ADD CONSTRAINT orders_number_key
        UNIQUE (restaurant_id, business_date, order_number),
    ADD CONSTRAINT orders_placement_check CHECK (
        (order_number IS NULL) = (business_date IS NULL)
        AND (order_number IS NULL) = (placed_at IS NULL)
    ),
    -- A draft has no number, a cancelled order may have been a draft, and
    -- every other status was placed first.
    ADD CONSTRAINT orders_number_status_check CHECK (
        CASE status
            WHEN 'draft' THEN order_number IS NULL
            WHEN 'cancelled' THEN true
            ELSE order_number IS NOT NULL
        END
    ),
    -- E.164.
    ADD CONSTRAINT orders_customer_phone_check
        CHECK (customer_phone ~ '^\+[1-9][0-9]{7,14}$');

-- Last order number handed out for a restaurant on a business date.
CREATE TABLE public.order_counters (
    restaurant_id uuid NOT NULL
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    business_date date NOT NULL,
    last_number integer NOT NULL CHECK (last_number > 0),
    PRIMARY KEY (restaurant_id, business_date)
);

-- Append-only log of status changes. Drafts have no rows.
CREATE TABLE public.order_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL,
    order_id uuid NOT NULL,
    from_status text NOT NULL,
    to_status text NOT NULL,
    actor_type text NOT NULL
        CHECK (actor_type IN ('customer', 'staff', 'system')),
    -- The staff user, once staff_users exists.
    actor_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- Order of events within one order; created_at ties inside a transaction.
    seq bigint GENERATED ALWAYS AS IDENTITY,
    FOREIGN KEY (restaurant_id, order_id)
        REFERENCES public.orders (restaurant_id, id) ON DELETE CASCADE
);

CREATE INDEX order_events_order_idx
    ON public.order_events (restaurant_id, order_id, seq);

-- migrate:down
DROP TABLE public.order_events;
DROP TABLE public.order_counters;

ALTER TABLE public.orders
    DROP CONSTRAINT orders_customer_phone_check,
    DROP CONSTRAINT orders_number_status_check,
    DROP CONSTRAINT orders_placement_check,
    DROP CONSTRAINT orders_number_key,
    DROP COLUMN placed_at,
    DROP COLUMN order_number,
    DROP COLUMN business_date,
    DROP COLUMN read_back_revision;
