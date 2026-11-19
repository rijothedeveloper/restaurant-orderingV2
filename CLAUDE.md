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
- Integration tests are marked `@pytest.mark.integration` and need a migrated `TEST_DATABASE_URL`; schema tests run inside a rolled-back transaction
- Money is integer cents; order_items snapshot name/price and use composite `(restaurant_id, …)` FKs to stay within one restaurant