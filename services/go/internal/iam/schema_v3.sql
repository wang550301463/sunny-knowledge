-- A channel audience is an additional read constraint, never an IAM membership.
CREATE TABLE IF NOT EXISTS iam_channel_audiences (
  id text PRIMARY KEY,
  channel_id text NOT NULL,
  group_key text NOT NULL,
  space_ids text[] NOT NULL CHECK (cardinality(space_ids) BETWEEN 1 AND 100),
  active boolean NOT NULL DEFAULT true,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_by text NOT NULL REFERENCES iam_users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (channel_id, group_key)
);
INSERT INTO iam_schema_version(version) VALUES(3) ON CONFLICT DO NOTHING;
