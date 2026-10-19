CREATE TABLE servers (
  server_id TEXT PRIMARY KEY NOT NULL,
  display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 64),
  host TEXT NOT NULL CHECK(length(host) BETWEEN 1 AND 253),
  ssh_port INTEGER NOT NULL DEFAULT 22 CHECK(ssh_port BETWEEN 1 AND 65535),
  username TEXT NOT NULL CHECK(length(username) BETWEEN 1 AND 64),
  auth_kind TEXT NOT NULL CHECK(auth_kind IN ('password', 'key')),
  credential_ref TEXT,
  host_id TEXT,
  record_version INTEGER NOT NULL DEFAULT 1 CHECK(record_version > 0)
);
CREATE INDEX servers_host_id_idx ON servers(host_id);
