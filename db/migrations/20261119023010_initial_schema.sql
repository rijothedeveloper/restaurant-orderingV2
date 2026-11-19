-- migrate:up
CREATE TABLE public.restaurants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    timezone text NOT NULL DEFAULT 'America/Los_Angeles',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.menu_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL
        REFERENCES public.restaurants(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    description text,
    price_cents bigint NOT NULL CHECK (price_cents >= 0),
    is_available boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (restaurant_id, name),
    -- Target for composite FKs that keep child rows in the same restaurant.
    UNIQUE (restaurant_id, id)
);

CREATE TABLE public.orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL REFERENCES public.restaurants(id),
    order_number bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    channel text NOT NULL
        CHECK (channel IN ('voice', 'web', 'whatsapp', 'sms')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'confirmed', 'preparing', 'ready', 'completed', 'cancelled'
    )),
    customer_name text,
    customer_phone text,
    notes text,
    currency text NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    subtotal_cents bigint NOT NULL CHECK (subtotal_cents >= 0),
    tax_cents bigint NOT NULL CHECK (tax_cents >= 0),
    total_cents bigint NOT NULL CHECK (total_cents = subtotal_cents + tax_cents),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (restaurant_id, id)
);

CREATE INDEX orders_restaurant_created_at_idx
    ON public.orders (restaurant_id, created_at DESC);

CREATE TABLE public.order_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    restaurant_id uuid NOT NULL,
    order_id uuid NOT NULL,
    menu_item_id uuid NOT NULL,
    -- Snapshot of the menu item at order time; menu edits must not rewrite history.
    item_name text NOT NULL,
    unit_price_cents bigint NOT NULL CHECK (unit_price_cents >= 0),
    quantity integer NOT NULL CHECK (quantity > 0),
    line_total_cents bigint NOT NULL
        CHECK (line_total_cents = unit_price_cents * quantity),
    notes text,
    FOREIGN KEY (restaurant_id, order_id)
        REFERENCES public.orders (restaurant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (restaurant_id, menu_item_id)
        REFERENCES public.menu_items (restaurant_id, id) ON DELETE RESTRICT
);

CREATE INDEX order_items_order_id_idx ON public.order_items (order_id);
CREATE INDEX order_items_menu_item_idx
    ON public.order_items (restaurant_id, menu_item_id);

-- migrate:down
DROP TABLE public.order_items;
DROP TABLE public.orders;
DROP TABLE public.menu_items;
DROP TABLE public.restaurants;