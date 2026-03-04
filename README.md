# Odds Engine

Sport-agnostic odds aggregation service for the Bookie Genie platform. Pulls betting odds from The Odds API across multiple sportsbooks, enriches the data with derived analytics (best line, consensus, vig-free probabilities, movement deltas), persists historical snapshots to PostgreSQL, and serves normalized odds to downstream consumers via REST API and WebSocket.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- PostgreSQL with an `odds_engine` database created
- Redis

## Setup

```bash
cp .env.example .env
# Edit .env with your real credentials and API key

uv sync --dev

# Create the test database used by the integration test suite
createdb odds_engine_test

# Run migrations against the main database
uv run alembic upgrade head
```

## Development

```bash
# Run the development server (auto-reload)
uv run fastapi dev src/odds_engine/main.py

# Run tests (unit + integration + api)
uv run pytest tests/unit/ tests/integration/ tests/api/ -v

# Run a single test file
uv run pytest tests/integration/test_pipeline_smoke.py -v -s

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Create a new Alembic migration after changing ORM models
uv run alembic revision --autogenerate -m "description"
```

## Running Live API Tests (manual only)

Live tests hit the real Odds API and consume usage credits. They must never run in CI.

```bash
# Requires a real ODDS_API_KEY set in .env
uv run pytest tests/live/ -v -s -m live
```

## Architecture

The service follows a strict layered design: Router -> Service -> Repository -> Models. Every route, service method, repository query, and external HTTP call is fully async; the only synchronous code is Alembic migrations. Redis serves as a hot cache and pub/sub bus for WebSocket distribution, while PostgreSQL holds the normalized historical record of every odds snapshot. Dependencies flow exclusively through FastAPI's `Depends()` — no layer imports concrete implementations from another layer directly, making it straightforward to swap any dependency with a mock in tests.

## API

All REST endpoints require an `X-API-Key` header. The WebSocket endpoint accepts an `api_key` query parameter.

### REST (v1)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Service health, DB/Redis connectivity, budget state |
| GET | `/api/v1/budget` | Current credit usage — daily, monthly, remaining |
| GET | `/api/v1/sports` | Active sport keys with event counts |
| GET | `/api/v1/events` | List enriched events; filterable by `sport_group`, `sport_key`, `status`, `commence_from`, `commence_to` |
| GET | `/api/v1/events/{event_id}` | Single enriched event |
| GET | `/api/v1/events/{event_id}/history` | Historical snapshots for an event, ordered newest first |
| GET | `/api/v1/odds/best` | Best available lines across active events; filterable by `sport_group`, `market` |
| POST | `/api/v1/fetch` | Trigger a manual odds fetch for a sport key; body: `{"sport_key": "basketball_ncaab"}` |

### WebSocket

| Path | Description |
|------|-------------|
| `WS /api/v1/ws` | Real-time odds updates via Redis pub/sub. Optional `sport_group` query param to filter. Auth via `api_key` query param. |

## Deployment

Alembic migrations run automatically when the container starts.

```bash
docker build -t odds-engine .
docker run -p 8000:8000 --env-file .env odds-engine
```

For production deployments on Hetzner via Coolify, set all environment variables from `.env.example` in the Coolify environment configuration rather than mounting an `.env` file.

## Target Sports and Sportsbooks

Sports polled: Tennis ATP, Tennis WTA, College Basketball (NCAAB).

Sportsbooks: DraftKings, FanDuel, BetMGM, Caesars, Bovada, BetOnline.

Markets: moneyline (h2h), spreads, totals (over/under).

## Credit Budget

The Odds API free tier provides 500 credits/month. Each full odds fetch (h2h + spreads + totals for one sport key) costs 3 credits. The scheduler uses free `/sports` and `/events` endpoints to discover active keys and skip empty sports before spending credits on odds fetches. Credit usage is tracked per-call via response headers and persisted to the `api_usage` table.
