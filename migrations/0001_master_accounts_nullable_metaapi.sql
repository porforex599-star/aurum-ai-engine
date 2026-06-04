-- Make MetaApi identifiers nullable on master_accounts.
--
-- Context: POST /api/masters now auto-provisions the MetaApi account from MT5
-- credentials. metaapi_account_id (and metaapi_region) are populated only after
-- provisioning completes, which may happen asynchronously, so the columns can no
-- longer be NOT NULL at insert time.
--
-- Backward compatible: existing rows already have values; existing inserts that
-- supply both columns continue to work unchanged.

ALTER TABLE public.master_accounts ALTER COLUMN metaapi_account_id DROP NOT NULL;
ALTER TABLE public.master_accounts ALTER COLUMN metaapi_region DROP NOT NULL;
