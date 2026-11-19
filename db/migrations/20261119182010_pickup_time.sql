-- migrate:up

-- When the restaurant takes orders, in its own timezone (restaurants.timezone).
-- weekday is the day the window opens: 0 = Monday … 6 = Sunday.
-- closes_at <= opens_at means the window ends on the next day, so 17:00–01:00
-- is an evening that runs past midnight and 00:00–00:00 is a full day.
-- Several rows per day allow a lunch and dinner split; they must not overlap.
CREATE TABLE public.opening_hours (
    restaurant_id uuid NOT NULL
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    opens_at time NOT NULL,
    closes_at time NOT NULL,
    PRIMARY KEY (restaurant_id, weekday, opens_at)
);

ALTER TABLE public.settings
    -- Orders the kitchen finishes per 10 minutes; sets the queue delay.
    ADD COLUMN kitchen_capacity integer NOT NULL DEFAULT 3
        CHECK (kitchen_capacity > 0),
    -- Busy mode: minutes added to every new pickup quote.
    ADD COLUMN busy_extra_minutes integer NOT NULL DEFAULT 0
        CHECK (busy_extra_minutes BETWEEN 0 AND 120),
    -- No new orders in the last minutes before closing.
    ADD COLUMN last_order_minutes integer NOT NULL DEFAULT 15
        CHECK (last_order_minutes BETWEEN 0 AND 240),
    -- Pause ordering: false stops new orders until staff turn it back on.
    ADD COLUMN accepting_orders boolean NOT NULL DEFAULT true;

ALTER TABLE public.orders
    -- The time the customer asked for; NULL means as soon as possible.
    ADD COLUMN requested_pickup_at timestamptz,
    -- The time promised when the order was placed.
    ADD COLUMN promised_pickup_at timestamptz;

-- Orders placed before pickup times existed were ready "now".
UPDATE public.orders
SET promised_pickup_at = placed_at
WHERE placed_at IS NOT NULL;

ALTER TABLE public.orders
    DROP CONSTRAINT orders_placement_check,
    ADD CONSTRAINT orders_placement_check CHECK (
        (order_number IS NULL) = (business_date IS NULL)
        AND (order_number IS NULL) = (placed_at IS NULL)
        AND (order_number IS NULL) = (promised_pickup_at IS NULL)
    );

-- migrate:down
ALTER TABLE public.orders
    DROP CONSTRAINT orders_placement_check,
    DROP COLUMN promised_pickup_at,
    DROP COLUMN requested_pickup_at,
    ADD CONSTRAINT orders_placement_check CHECK (
        (order_number IS NULL) = (business_date IS NULL)
        AND (order_number IS NULL) = (placed_at IS NULL)
    );

ALTER TABLE public.settings
    DROP COLUMN accepting_orders,
    DROP COLUMN last_order_minutes,
    DROP COLUMN busy_extra_minutes,
    DROP COLUMN kitchen_capacity;

DROP TABLE public.opening_hours;