-- Phase 6.5 — Trade history + per-product statistics
-- Apply this in the aurum-customers Supabase project
-- (project: etwlurpjrqlvrxgsbhkd)
-- Via Dashboard → SQL Editor → New query → paste → Run
--
-- This is the canonical closed-trades store for the admin dashboard stats.
-- The engine writes one row per closed master-account position from the tick
-- loop (src/scheduler/tick_runner.py), attributed by the Phase 6.4 scheme
-- (symbol ∈ product set AND comment "AURUM_AI <setup>"). Paper trades are kept
-- too, flagged dry_run=TRUE, so the dashboard can filter them out by default.

CREATE TABLE IF NOT EXISTS master_closed_trades (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  position_id      TEXT        NOT NULL,
  product          TEXT        NOT NULL
                     CHECK (product IN ('gold_ai', 'multi_cfd_ai')),
  symbol           TEXT        NOT NULL,
  symbol_norm      TEXT        NOT NULL,
  side             TEXT        CHECK (side IN ('BUY', 'SELL')),
  lot              NUMERIC,
  setup            TEXT,
  entry_price      NUMERIC,
  exit_price       NUMERIC,
  pnl              NUMERIC     NOT NULL,
  gross_profit     NUMERIC,
  swap             NUMERIC,
  commission       NUMERIC,
  opened_at        TIMESTAMPTZ,
  closed_at        TIMESTAMPTZ NOT NULL,
  duration_seconds INTEGER,
  dry_run          BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Idempotency: a tick retry or restart must not double-log the same close.
  CONSTRAINT master_closed_trades_position_uniq UNIQUE (position_id)
);

-- Time-window queries: GET /stats/{slug}?period=7d and GET /trades/{slug}.
CREATE INDEX IF NOT EXISTS idx_master_closed_trades_product_closed
  ON master_closed_trades (product, closed_at DESC);

-- Per-symbol breakdown for multi_cfd_ai.
CREATE INDEX IF NOT EXISTS idx_master_closed_trades_symbol_closed
  ON master_closed_trades (symbol_norm, closed_at DESC);

-- Dashboard filters dry_run=FALSE by default for "real production" stats.
CREATE INDEX IF NOT EXISTS idx_master_closed_trades_dryrun
  ON master_closed_trades (dry_run);

-- Lock the table down — only the service_role can read/write.
-- service_role bypasses RLS by default; no permissive policy is added on
-- purpose so anon/authenticated keys cannot see the trade ledger.
ALTER TABLE master_closed_trades ENABLE ROW LEVEL SECURITY;
