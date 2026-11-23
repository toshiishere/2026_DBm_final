# Shoes & Apparel Database Demo

Containerized backend and database for the [project proposal](shoes_clothing_final_demo_proposal_v2.md).
FastAPI + psycopg 3 + PostgreSQL 17; exactly 11 application tables. No login, tokens, or roles.
The frontend can choose a `member_id` and call the API directly.

## Start with Docker

Requires Docker Engine/Desktop with Docker Compose v2.

```bash
cp .env.example .env
docker compose up --build -d --wait
```

- API documentation / interactive requests: http://localhost:8000/docs
- OpenAPI specification: http://localhost:8000/openapi.json
- Health: http://localhost:8000/health
- PostgreSQL: `localhost:5432`, database `shopping_demo`, user `demo`, password from `.env`.
- Default frontend CORS origins: `http://localhost:5173` and `http://localhost:3000`.

Compose creates the schema and synthetic seed data on the **first startup of an empty volume**.
The API waits for PostgreSQL readiness. Ports bind to localhost for the classroom demo.
Change `API_PORT`, `DB_PORT`, or `CORS_ORIGINS` in `.env` if needed.

```bash
docker compose logs -f api db
docker compose exec db psql -U demo -d shopping_demo
docker compose down
```

`down` keeps the named database volume. To intentionally erase demo data and rerun schema/seed initialization:

```bash
docker compose down -v
docker compose up --build -d --wait
```

Editing SQL files does not modify an existing database volume. This initial implementation uses ordered
bootstrap SQL files rather than a migration framework. For later schema changes, add explicit migrations
or reset only disposable demo data. If initialization fails, inspect the database logs before resetting.

## Test inside containers

```bash
docker compose --profile test run --build --rm test
```

The tests create a randomly named PostgreSQL schema per test, load schema and seeds, then drop only
that test schema. They do not erase the existing demo tables. The database user needs schema creation
permission. Tests cover the proposal's acceptance cases, multi-item rollback, deferred constraint
errors, a race for the last unit, and payment racing with cancellation.

## Local Python development (optional)

With PostgreSQL running in Docker:

```bash
uv sync --locked
export DATABASE_URL=postgresql://demo:demo_local_only@localhost:5432/shopping_demo
uv run uvicorn app.main:app --reload
```

You can instead set standard `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD` variables;
this avoids URL escaping if your password contains special characters.

```bash
export TEST_DATABASE_URL=postgresql://demo:demo_local_only@localhost:5432/shopping_demo
uv run pytest -q
uv run ruff check app tests
uv run ruff format --check app tests
```

`uv.lock` and the exported `requirements*.lock` pin the dependency versions used by development and
container builds. After changing dependencies, regenerate all three together:

```bash
uv lock
uv export --no-dev --no-hashes --no-emit-project -o requirements.lock
uv export --no-hashes --no-emit-project -o requirements-dev.lock
```

## Handoff and database demonstration

- [API integration and complete purchase example](docs/api.md)
- [Database design, dictionary, and transaction rules](docs/database.md)
- [Runnable SQL demonstration queries](docs/queries.sql)
- `app/main.py`: API routes and transaction orchestration.
- `app/models.py`: request validation.
- `db/001_schema.sql`: tables, indexes, views, triggers, and constraints.
- `db/002_seed.sql`: 12 members, 24 addresses, 4 categories, 20 products, 60 SKUs, 40 orders,
  plus failed/successful payments, shipments, reviews, and inventory logs.
- `tests/`: real PostgreSQL integration and concurrency tests.

Seed member `1` owns addresses `1` and `2`; SKUs `1`, `2`, `3` belong to product `1`.
Some seeded orders are pending, cancelled, paid, shipped, or delivered. SKUs `55`–`60` start with
three units to demonstrate the low-stock report. Initial RESTOCK logs establish stock baselines.

The deliverable covers the backend/database assignment. A separate HTML/JavaScript frontend can use
the documented API; Swagger UI at `/docs` provides an immediate interactive demo.
