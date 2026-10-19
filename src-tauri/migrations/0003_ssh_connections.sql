CREATE TABLE ssh_trust (
  server_id TEXT PRIMARY KEY NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
  algorithm TEXT NOT NULL, host_key BLOB NOT NULL, fingerprint TEXT NOT NULL
);
CREATE TABLE ssh_challenges (
  challenge_id TEXT PRIMARY KEY NOT NULL,
  server_id TEXT NOT NULL REFERENCES servers(server_id) ON DELETE CASCADE,
  record_version INTEGER NOT NULL, algorithm TEXT NOT NULL,
  host_key BLOB NOT NULL, fingerprint TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX ssh_challenges_expiry_idx ON ssh_challenges(expires_at);
CREATE TABLE credential_cleanup (
  credential_ref TEXT PRIMARY KEY NOT NULL,
  last_error_code TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE credential_receipts (
  operation_id TEXT PRIMARY KEY NOT NULL, server_id TEXT NOT NULL,
  credential_ref TEXT NOT NULL, base_version INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
