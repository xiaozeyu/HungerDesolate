CREATE TABLE host_initialization_plans (
  plan_id TEXT PRIMARY KEY NOT NULL,
  operation_id TEXT UNIQUE NOT NULL,
  server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
  record_version INTEGER NOT NULL,
  payload TEXT NOT NULL,
  started INTEGER NOT NULL DEFAULT 0,
  finished INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE UNIQUE INDEX host_initialization_pending_idx
  ON host_initialization_plans(server_id,record_version) WHERE started=1 AND finished=0;
