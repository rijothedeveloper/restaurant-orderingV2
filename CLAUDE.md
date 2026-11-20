# Restaurant AI Ordering App
AI phone-ordering assistant for a restaurant. Channels: voice, web chat, WhatsApp, SMS.

## Stack (decided)
- Python 3.12, FastAPI (async throughout)
- PostgreSQL via asyncpg with a connection pool — raw SQL, NO ORM
- Migrations: dbmate (plain .sql files in db/migrations)
- Telnyx for voice + SMS
- Hosting: Google Cloud (Cloud Run + Cloud SQL)

## Design notes
- Single restaurant for now, but keep a restaurant_id on tables so multi-tenant is easy later
- Channel adapters (voice/sms/whatsapp/web) all feed one order-taking agent core
- A click-to-order web storefront is also planned. It is NOT in the architecture doc; it uses the cart endpoints and the same order engine (channel `storefront`)

## Conventions
- uv for deps, ruff for lint, pytest + pytest-asyncio for tests
- Secrets via env vars, never committed

## Commands
```
cp .env.example .env                    # one .env for compose, dbmate and the app
docker compose up -d db                 # Postgres on 127.0.0.1:5432
dbmate up                               # migrate dev DB
dbmate --env TEST_DATABASE_URL up       # create + migrate restaurant_test
dbmate new <name>                       # new migration in db/migrations
# load the demo restaurant + menu into the dev DB (safe to rerun; psql runs in the container)
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/seed.sql
uv run uvicorn app.main:create_app --factory --reload
uv run pytest                           # unit only; integration tests skip
uv run --env-file .env pytest           # include integration tests
uv run pytest tests/test_health.py::test_health_ok
uv run ruff check . && uv run ruff format .
```

## Layout
- `app/main.py`: `create_app()` factory; lifespan opens/closes the asyncpg pool (`app/db.py`)
- Pool lives on `app.state.db_pool`; routes get it via `Depends(get_pool)`, which tests override
- `app/config.py`: pydantic-settings `Settings`, read from env / `.env`
- `docs/AI_Phone-Ordering_Assistant_Architecture_v2.md` is the design source of truth (data model, tools, build plan)
- Menu: `app/menu/repository.py` `fetch_menu(conn, restaurant_id)` builds the nested menu (categories → items → aliases, modifier groups → options) from several queries; it takes a connection and the caller owns the transaction. Route: `GET /restaurants/{restaurant_id}/menu`
- Orders: `app/orders/service.py` is the order engine and the only way to change an order (`create_draft`, `add_item`, `change_item`, `remove_item`, `set_customer`, `set_pickup`, `read_back`, `confirm_order`, `get_order`; `ordering_status` only reads). Each function opens its own transaction (a savepoint if nested) and locks the order row. Line changes require status `draft`, recompute totals and bump `revision`; `set_pickup` bumps it when the requested time changes; `set_customer` does not bump it. Rules with no database live in `app/orders/pricing.py` (`select_options`, `tax_cents`, `unavailable_lines`), `pickup.py` (opening hours and `quote_pickup`), `phone.py` (`normalize_phone`, E.164, US default) and `readback.py` (read-back and confirmation wording); SQL in `app/orders/repository.py`
- Placing: `confirm_order(…, revision, customer=None)` is the one gate for every channel. It places the draft only if `revision` (what the customer saw or heard) equals the current one, the order has lines the menu still allows, a pickup name and phone are set, and the restaurant can take the order now. `read_back` stores `read_back_revision` for the agent to pass later; the storefront sends the revision it displayed. Confirming an already placed order returns it unchanged
- Pickup time: earliest = now + longest prep among the lines (from the current menu) + open orders (`placed`, `accepted`, `preparing`, scheduled ones included) × 10 ÷ `kitchen_capacity` + `busy_extra_minutes`, rounded up to 5 minutes. `orders.requested_pickup_at` NULL means ASAP. A quote is an estimate: `set_pickup` and `read_back` return it, and `confirm_order` computes it again and stores `promised_pickup_at`, so a slow yes can move the time. Orders are same-day only and taken only while open: closed, paused (`settings.accepting_orders`), past `last_order_minutes` before closing, or a time that cannot be met all raise `PickupUnavailable`; building a cart stays allowed
- Opening hours: `opening_hours` rows in the restaurant's local time, `weekday` 0 = Monday, `closes_at <= opens_at` means it closes the next day, and windows with no gap between them count as one. No holiday or special hours yet
- Time: the order rules read `clock.utcnow()` from `app/clock.py` (never `datetime.now` or SQL `now()` for a rule), compute in UTC, and use `restaurants.timezone` only for opening hours and wording. `Order.timezone` carries the zone so clients and `readback.py` can show local times
- Order numbers count per restaurant per business date (the local date the current opening window started, so after midnight still counts as the evening before) from `order_counters`. Placing writes one `order_events` row (`draft` → `placed`)
- Rejections raise `OrderError(code, message)`; `MissingRequiredChoices` carries the unmet modifier groups, `LinesUnavailable` the lines that can no longer be made, and `PickupUnavailable` the `next_opening_at` or `earliest_at`. `app/api/carts.py` maps codes to HTTP status (404/409, otherwise 422)
- Order lines copy item name, price and options when added or changed; a changed line is re-priced from the current menu, and placing re-checks availability but keeps the copied prices. Tax rate comes from the `settings` table (one row per restaurant, required before it can take orders), rounded half-up
- Cart routes (`/restaurants/{id}/carts…`) are for the storefront: a cart is a draft order addressed by its UUID, `PUT …/carts/{cart_id}/pickup` (`requested_at`, null for ASAP) sets and quotes the pickup time, and `POST …/carts/{cart_id}/checkout` (`revision`, `name`, `phone`) places it. `GET /restaurants/{id}/ordering-status` says whether orders are being taken, the next opening and the current wait. No auth, rate limiting or bot protection yet
- Staff status changes: `service.apply_staff_action(…, action, staff_user_id, extra_minutes=0, reason=None)` moves a placed order by the `STAFF_ACTIONS` table (`accept`, `prepare`, `ready`, `picked_up`, `reject` from placed only and with a reason, `cancel` from placed or accepted, `no_show` from ready) and writes an `order_events` row with the staff actor. Any other move raises `invalid_transition` (409); repeating an action returns the order unchanged. `accept` can push `promised_pickup_at` back 5 or 10 minutes. `service.active_orders` lists the board (placed to ready, soonest pickup first)
- Staff: `app/staff/` (`service.py` sign-in and controls, `pins.py` scrypt hashing, SQL in `repository.py`). `login` takes a PIN alone (6–8 digits, unique among a restaurant's active staff) and returns a session token; only its SHA-256 is stored, it lasts 12 hours and is good only for its own restaurant. Wrong PINs are counted per restaurant (`staff_login_throttle`): 5 in a row lock sign-in for 5 minutes. No route creates staff yet; use `create_staff_user`
- Staff routes (`app/api/staff.py`, `/restaurants/{id}/staff…`, `Authorization: Bearer <token>` via `Depends(current_staff)`): `POST login`, `POST logout`, `GET orders` (board plus controls, what the dashboard polls), `POST orders/{order_id}/{action}`, `GET`/`PATCH controls` (pause = `accepting_orders`, busy mode = `busy_extra_minutes`), `PATCH menu/items/{id}` and `menu/options/{id}` (sold-out toggle)
- Not built yet: the dashboard UI, the `customers` table, outbox and notifications (no status texts), automatic no-show, draft expiry, full menu editing, the agent loop
- `db/seed.sql`: demo restaurant with fixed id `00000000-0000-4000-8000-000000000001`; placeholder menu, open 07:00–23:00 every day, a demo staff user with PIN `123456`, idempotent
- Tests: `tests/factories.py` has insert helpers, `place_order`, `create_staff` (PIN `STAFF_PIN`) and `committed_shop` (a committed shop for `db_client` tests, deleted afterwards); `conn` fixture = rolled-back transaction (cannot nest a transaction with its own isolation level); `db_client` fixture = real app on the test DB, sees only committed rows, so those tests commit and delete their own data. The autouse `clock` fixture fixes the time at `NOON` (a Wednesday, noon in Los Angeles); `create_shop` is open 09:00–21:00, and a test moves time with `clock.now = …`
- Integration tests are marked `@pytest.mark.integration` and need a migrated `TEST_DATABASE_URL`; schema tests run inside a rolled-back transaction
- Money is integer cents; order_items snapshot name/price and use composite `(restaurant_id, …)` FKs to stay within one restaurant