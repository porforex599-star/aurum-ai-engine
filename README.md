# Aurum AI Engine

Automated multi-strategy trading orchestrator built on FastAPI, MetaApi Cloud SDK, and Supabase.

This repository is being built in phases. **Current phase: 2.1 — skeleton + connectivity layer.**
Subsequent phases (2.2–2.5) will introduce strategy execution, risk control, product routing, scheduling, and the token bridge.

## Stack

- Python 3.12
- FastAPI + uvicorn
- metaapi-cloud-sdk (master account connectivity)
- supabase-py (service-role client)
- pydantic-settings (typed env loading)
- loguru (structured logging)
- Deploy target: Railway (region `europe-west4`)

## Quickstart

### Using `uv` (recommended)

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env  # then fill in secrets
uvicorn src.main:app --reload
```

### Using `pip`

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # then fill in secrets
uvicorn src.main:app --reload
```

### Run tests

```bash
pytest -q
```

## Environment variables

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service-role API key (server-side only) |
| `METAAPI_TOKEN` | MetaApi Cloud SDK auth token |
| `METAAPI_MASTER_ACCOUNT_ID` | MetaApi master account UUID |
| `APP_ENV` | `production` / `staging` / `development` |
| `PORT` | HTTP port (Railway provides this) |
| `TIMEZONE` | IANA timezone (default `Asia/Bangkok`) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AURUM_SNIPER_WEBHOOK_SECRET` | Shared secret for the `X-Webhook-Secret` header on the Sniper webhook |
| `ANALYSIS_SCHEMA` | Postgres schema for analysis posts (default `aurum-customers`) |
| `ANALYSIS_TABLE` | Table for analysis posts (default `analysis_posts`) |
| `TELEGRAM_BOT_TOKEN` | Bot token for @AurumAIEngineBot |
| `TELEGRAM_CHAT_ID` | Destination chat/channel id for alerts |

See `.env.example` for a template.

## Health check

`GET /health` returns the connectivity status of MetaApi and Supabase:

```json
{
  "status": "ok",
  "metaapi_connected": true,
  "supabase_connected": true,
  "version": "0.1.0"
}
```

## Aurum Sniper webhook

`POST /api/internal/aurum-sniper-alert` ingests Pine Script alert JSON.

- **Auth:** `X-Webhook-Secret` header must match `AURUM_SNIPER_WEBHOOK_SECRET` (else `401`).
- **Vocab normalization:** `buy`/`long`/`bull` → `bullish`, `sell`/`short`/`bear` → `bearish` before insert.
- **Persist:** inserts into `aurum-customers.analysis_posts` (service-role). Supabase Realtime
  then broadcasts the row to `/room` subscribers via `postgres_changes`.
- **Notify:** pushes a formatted alert to @AurumAIEngineBot (best-effort).

Request body:

```json
{
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "bias": "bullish",
  "key_level": 2345.67,
  "target_zones": [{ "id": "Z1", "price": 2350.0 }],
  "risk_level": "medium",
  "confidence": 85,
  "note": "optional Thai text",
  "timestamp_utc": "2026-06-06T00:00:00Z"
}
```

Response: `200 {"post_id": "...", "broadcast": true}`

## Phase plan

| Phase | Scope |
|---|---|
| 2.1 | Skeleton + connectivity layer (**this phase**) |
| 2.2 | Strategy engine |
| 2.3 | Risk control |
| 2.4 | Products + routing |
| 2.5 | Scheduler + token bridge |
