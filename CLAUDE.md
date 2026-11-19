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
- Orders: `app/orders/service.py` is the order engine and the only way to change an order (`create_draft`, `add_item`, `change_item`, `remove_item`, `get_order`). Each function opens its own transaction (a savepoint if nested), locks the order row, requires status `draft`, recomputes totals and bumps `revision`. Rules with no database live in `app/orders/pricing.py` (`select_options`, `tax_cents`); SQL in `app/orders/repository.py`
- Rejections raise `OrderError(code, message)`; `MissingRequiredChoices` carries the unmet modifier groups. `app/api/carts.py` maps codes to HTTP status (404/409, otherwise 422)
- Order lines copy item name, price and options when added or changed; a changed line is re-priced from the current menu. Tax rate comes from the `settings` table (one row per restaurant, required before it can take orders), rounded half-up
- Cart routes (`/restaurants/{id}/carts…`) are for the storefront: a cart is a draft order addressed by its UUID. No auth, rate limiting or bot protection yet
- Not built yet: pickup name/phone, read-back, confirmation and per-day order numbers, order events, pickup-time quoting, draft expiry
- `db/seed.sql`: demo restaurant with fixed id `00000000-0000-4000-8000-000000000001`; placeholder menu, idempotent
- Tests: `tests/factories.py` has insert helpers; `conn` fixture = rolled-back transaction (cannot nest a transaction with its own isolation level); `db_client` fixture = real app on the test DB, sees only committed rows, so those tests commit and delete their own data
- Integration tests are marked `@pytest.mark.integration` and need a migrated `TEST_DATABASE_URL`; schema tests run inside a rolled-back transaction
- Money is integer cents; order_items snapshot name/price and use composite `(restaurant_id, …)` FKs to stay within one restaurant