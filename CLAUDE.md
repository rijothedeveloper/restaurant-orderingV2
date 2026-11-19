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
- Orders: `app/orders/service.py` is the order engine and the only way to change an order (`create_draft`, `add_item`, `change_item`, `remove_item`, `set_customer`, `read_back`, `confirm_order`, `get_order`). Each function opens its own transaction (a savepoint if nested) and locks the order row. Line changes require status `draft`, recompute totals and bump `revision`; `set_customer` does not bump it. Rules with no database live in `app/orders/pricing.py` (`select_options`, `tax_cents`, `unavailable_lines`), `phone.py` (`normalize_phone`, E.164, US default) and `readback.py` (read-back wording); SQL in `app/orders/repository.py`
- Placing: `confirm_order(…, revision, customer=None)` is the one gate for every channel. It places the draft only if `revision` (what the customer saw or heard) equals the current one, the order has lines the menu still allows, and a pickup name and phone are set. `read_back` stores `read_back_revision` for the agent to pass later; the storefront sends the revision it displayed. Confirming an already placed order returns it unchanged
- Order numbers count per restaurant per business date (today in `restaurants.timezone`, not yet the opening time) from `order_counters`. Placing writes one `order_events` row (`draft` → `placed`)
- Rejections raise `OrderError(code, message)`; `MissingRequiredChoices` carries the unmet modifier groups and `LinesUnavailable` the lines that can no longer be made. `app/api/carts.py` maps codes to HTTP status (404/409, otherwise 422)
- Order lines copy item name, price and options when added or changed; a changed line is re-priced from the current menu, and placing re-checks availability but keeps the copied prices. Tax rate comes from the `settings` table (one row per restaurant, required before it can take orders), rounded half-up
- Cart routes (`/restaurants/{id}/carts…`) are for the storefront: a cart is a draft order addressed by its UUID, and `POST …/carts/{cart_id}/checkout` (`revision`, `name`, `phone`) places it. No auth, rate limiting or bot protection yet
- Not built yet: pickup-time quoting and opening hours, the `customers` table, outbox and notifications, staff status changes, draft expiry, the agent loop
- `db/seed.sql`: demo restaurant with fixed id `00000000-0000-4000-8000-000000000001`; placeholder menu, idempotent
- Tests: `tests/factories.py` has insert helpers and `place_order`; `conn` fixture = rolled-back transaction (cannot nest a transaction with its own isolation level); `db_client` fixture = real app on the test DB, sees only committed rows, so those tests commit and delete their own data
- Integration tests are marked `@pytest.mark.integration` and need a migrated `TEST_DATABASE_URL`; schema tests run inside a rolled-back transaction
- Money is integer cents; order_items snapshot name/price and use composite `(restaurant_id, …)` FKs to stay within one restaurant