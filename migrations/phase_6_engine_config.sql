-- Phase 6 — Engine freeze/unfreeze
-- Apply this in the aurum-customers Supabase project
-- (project: etwlurpjrqlvrxgsbhkd)
-- Via Dashboard → SQL Editor → New query → paste → Run

CREATE TABLE IF NOT EXISTS engine_config (
  id            TEXT        PRIMARY KEY,
  frozen        BOOLEAN     NOT NULL DEFAULT FALSE,
  frozen_reason TEXT,
  frozen_at     TIMESTAMPTZ,
  frozen_by     TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the single global config row used by aurum-ai-engine.
INSERT INTO engine_config (id, frozen)
VALUES ('global', FALSE)
ON CONFLICT (id) DO NOTHING;

-- Lock the table down — only the service_role can read/write.
-- service_role bypasses RLS by default; no permissive policy is added on
-- purpose so anon/authenticated keys cannot see or change freeze state.
ALTER TABLE engine_config ENABLE ROW LEVEL SECURITY;

-- Auto-touch updated_at on UPDATE.
CREATE OR REPLACE FUNCTION engine_config_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_engine_config_updated_at ON engine_config;
CREATE TRIGGER trg_engine_config_updated_at
  BEFORE UPDATE ON engine_config
  FOR EACH ROW
  EXECUTE FUNCTION engine_config_touch_updated_at();
