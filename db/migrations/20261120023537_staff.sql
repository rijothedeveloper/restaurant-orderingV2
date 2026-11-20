-- migrate:up

-- People who work the staff dashboard. Each belongs to one restaurant and
-- signs in with a PIN. Staff are deactivated, not deleted, so the order log
-- keeps its actors.
CREATE TABLE public.staff_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    -- scrypt$n$r$p$salt$hash (see app/staff/pins.py). PINs are unique among a
    -- restaurant's active staff; the app checks that, since hashes are salted.
    pin_hash text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (restaurant_id, id)
);

-- A signed-in dashboard. The token itself is never stored, only its SHA-256.
CREATE TABLE public.staff_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL,
    staff_user_id uuid NOT NULL,
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (restaurant_id, staff_user_id)
        REFERENCES public.staff_users (restaurant_id, id) ON DELETE CASCADE
);

CREATE INDEX staff_sessions_staff_user_idx
    ON public.staff_sessions (restaurant_id, staff_user_id);

-- Wrong PINs in a row for a restaurant, and until when sign-in is locked.
-- A PIN alone does not name a staff user, so the count is per restaurant.
CREATE TABLE public.staff_login_throttle (
    restaurant_id uuid PRIMARY KEY
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until timestamptz
);

ALTER TABLE public.order_events
    -- Why staff rejected or cancelled the order.
    ADD COLUMN reason text,
    -- Checked at the end of the statement, so deleting a restaurant can
    -- cascade through both tables.
    ADD CONSTRAINT order_events_actor_fkey
        FOREIGN KEY (restaurant_id, actor_id)
        REFERENCES public.staff_users (restaurant_id, id),
    ADD CONSTRAINT order_events_actor_check
        CHECK ((actor_type = 'staff') = (actor_id IS NOT NULL));

ALTER TABLE public.orders
    -- When the order entered its current status.
    ADD COLUMN status_changed_at timestamptz NOT NULL DEFAULT now();

UPDATE public.orders
SET status_changed_at = COALESCE(placed_at, created_at);

-- migrate:down
ALTER TABLE public.orders
    DROP COLUMN status_changed_at;

ALTER TABLE public.order_events
    DROP CONSTRAINT order_events_actor_check,
    DROP CONSTRAINT order_events_actor_fkey,
    DROP COLUMN reason;

DROP TABLE public.staff_login_throttle;
DROP TABLE public.staff_sessions;
DROP TABLE public.staff_users;
