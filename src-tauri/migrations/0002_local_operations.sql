CREATE TABLE local_operations (
  operation_id TEXT PRIMARY KEY NOT NULL,
  payload TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX local_operations_created_at_idx ON local_operations(created_at);
