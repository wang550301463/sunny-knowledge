-- Additive account model: existing records remain ordinary users with unchanged grants.
ALTER TABLE iam_users ADD COLUMN IF NOT EXISTS account_kind text NOT NULL DEFAULT 'user' CHECK (account_kind IN ('user','service'));
ALTER TABLE iam_users ADD COLUMN IF NOT EXISTS client_id text NOT NULL DEFAULT '';
ALTER TABLE iam_users ADD COLUMN IF NOT EXISTS version bigint NOT NULL DEFAULT 1 CHECK (version > 0);
ALTER TABLE iam_users ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='iam_account_client_binding' AND conrelid='iam_users'::regclass) THEN
    ALTER TABLE iam_users ADD CONSTRAINT iam_account_client_binding CHECK ((account_kind='user' AND client_id='') OR (account_kind='service' AND btrim(client_id)<>''));
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS iam_service_client_unique ON iam_users(client_id) WHERE account_kind='service';
INSERT INTO iam_schema_version(version) VALUES(2) ON CONFLICT DO NOTHING;