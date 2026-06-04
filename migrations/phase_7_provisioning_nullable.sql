-- Phase 7 — make master_accounts.metaapi_account_id / metaapi_region optional
-- (populated by auto-provisioning).
-- Apply this in the aurum-customers Supabase project
-- (project: etwlurpjrqlvrxgsbhkd)
-- Via Dashboard → SQL Editor → New query → paste → Run
-- (or the Supabase MCP apply_migration tool).
--
-- POST /masters can now auto-provision a MetaApi account from the MT5 login +
-- password instead of requiring a pre-supplied metaapi_account_id. When the
-- password path is used these identifiers are produced by MetaApi and written
-- by the engine, so the columns can no longer be NOT NULL at insert time.
--
-- Safe + backward compatible: existing rows already have values, and the
-- metaapi_account_id path still always supplies both columns.
--
-- NOTE: this change has ALREADY been applied to the live project
-- (etwlurpjrqlvrxgsbhkd) — both columns report is_nullable = YES. This file
-- exists so a fresh/clean database reaches the same schema; the statements are
-- idempotent and safe to re-run.

ALTER TABLE master_accounts
  ALTER COLUMN metaapi_account_id DROP NOT NULL;

ALTER TABLE master_accounts
  ALTER COLUMN metaapi_region DROP NOT NULL;
