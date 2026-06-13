-- Phase 6.6 — Admin manual analysis publish
-- Apply this in the aurum-customers Supabase project
-- (project: etwlurpjrqlvrxgsbhkd)
-- Via Dashboard → SQL Editor → New query → paste → Run

-- Tracks how an analysis_posts row entered the feed. The Pine V.2 webhook
-- (sniper) leaves it NULL; the admin manual publish endpoint stamps
-- 'admin_manual'. Nullable with no default so existing rows are unaffected.
ALTER TABLE analysis_posts
  ADD COLUMN IF NOT EXISTS source TEXT;
