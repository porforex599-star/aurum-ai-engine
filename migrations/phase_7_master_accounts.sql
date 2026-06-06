-- Phase 7 Stage 1 — Multi-master account support (backend foundation)
-- Apply this in the aurum-customers Supabase project
-- (project: etwlurpjrqlvrxgsbhkd)
-- Via Dashboard → SQL Editor → New query → paste → Run
-- (or the Supabase MCP apply_migration tool).
--
-- This is the registry of MT5 master accounts the engine can trade from. Each
-- product (gold_ai / multi_cfd_ai) is assigned to at most one master at a time;
-- masters not assigned to a product sit in "standby". Stage 1 is DB + read/write
-- API only — the engine itself is NOT yet wired to this table (Stage 2).
--
-- FALLBACK CONTRACT (transition period, documented for Stage 2):
--   Today a single master (#97038939, InterStellarFinancial) serves BOTH
--   gold_ai and multi_cfd_ai. We seed ONLY the gold_ai row below. When the
--   engine (Stage 2) looks up the master for a product and finds NO row for
--   multi_cfd_ai, it MUST fall back to the gold_ai master so trading does not
--   break. Por will register the dedicated multi_cfd_ai master via the UI later;
--   once that row exists the fallback no longer triggers for multi_cfd_ai.

CREATE TABLE IF NOT EXISTS master_accounts (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  login               TEXT        NOT NULL UNIQUE,
  broker              TEXT        NOT NULL,
  server              TEXT        NOT NULL,
  currency            TEXT        NOT NULL,
  metaapi_account_id  TEXT        NOT NULL,
  metaapi_region      TEXT        NOT NULL DEFAULT 'eu-west',
  -- Which product this master currently serves. NULL = standby (unassigned).
  assigned_product    TEXT        CHECK (assigned_product IN ('gold_ai', 'multi_cfd_ai')),
  status              TEXT        NOT NULL DEFAULT 'standby'
                        CHECK (status IN ('live', 'standby', 'disconnected')),
  notes               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only one master may be assigned to a given product at a time. A partial unique
-- index lets many masters sit in standby (assigned_product IS NULL) while still
-- enforcing one-master-per-product for non-null assignments.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_master_accounts_assigned_product
  ON master_accounts (assigned_product)
  WHERE assigned_product IS NOT NULL;

-- Fast lookup of "which master serves product X" (the Stage 2 engine hot path).
CREATE INDEX IF NOT EXISTS idx_master_accounts_assigned_product
  ON master_accounts (assigned_product);

-- Lock the table down — only the service_role can read/write.
-- service_role bypasses RLS by default; no permissive policy is added on
-- purpose so anon/authenticated keys cannot see or change master config
-- (same posture as engine_config / master_closed_trades).
ALTER TABLE master_accounts ENABLE ROW LEVEL SECURITY;

-- Auto-touch updated_at on UPDATE (reuses the engine_config trigger style).
CREATE OR REPLACE FUNCTION master_accounts_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_master_accounts_updated_at ON master_accounts;
CREATE TRIGGER trg_master_accounts_updated_at
  BEFORE UPDATE ON master_accounts
  FOR EACH ROW
  EXECUTE FUNCTION master_accounts_touch_updated_at();

-- Backfill the existing live master (#97038939 InterStellarFinancial) as the
-- gold_ai master. We deliberately DO NOT create a multi_cfd_ai row — Stage 1
-- only sets up infrastructure, and the documented fallback above keeps
-- multi_cfd_ai trading on this same account until Por registers a dedicated one.
INSERT INTO master_accounts (
  login, broker, server, currency,
  metaapi_account_id, metaapi_region,
  assigned_product, status, notes
)
VALUES (
  '97038939',
  'InterStellarFinancial',
  'InterStellarFinancial-Server',
  'USC',
  'eb1eeff8-6653-49d6-b35d-fec36aae2a87',
  'eu-west',
  'gold_ai',
  'live',
  'Phase 7 backfill — original single master. Also serves multi_cfd_ai via '
  'fallback until a dedicated multi_cfd_ai master is registered.'
)
ON CONFLICT (login) DO NOTHING;
