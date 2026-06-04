-- Phase 7 — make master_accounts.currency optional (auto-filled from MetaApi)
-- Apply this in the aurum-customers Supabase project
-- (project: etwlurpjrqlvrxgsbhkd)
-- Via Dashboard → SQL Editor → New query → paste → Run
-- (or the Supabase MCP apply_migration tool).
--
-- The Add-master modal in Aurum-Admin no longer collects Currency. MT5 already
-- knows the broker currency (USD/USC/EUR), so the engine now auto-detects it
-- from the first successful MetaApi account-info snapshot and writes it back to
-- this row (see MasterAccountService.backfill_currency). To allow a master to be
-- registered without a currency, the column must accept NULL.
--
-- This is safe for the existing seed row (#97038939, currency='USC'): it keeps
-- its value, and the auto-fill only ever writes when currency IS NULL/blank.

ALTER TABLE master_accounts
  ALTER COLUMN currency DROP NOT NULL;
