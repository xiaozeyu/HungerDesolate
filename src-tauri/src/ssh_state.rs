use crate::{
    dto::{CommandError, CommandResult, ServerSummary},
    persistence,
    ssh_dto::{ConnectionRequest, FingerprintChallenge, SshConnectionState},
};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior};
use std::{
    fs::{File, OpenOptions},
    path::Path,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub struct HostKeyRecord {
    pub algorithm: String,
    pub key: Vec<u8>,
    pub fingerprint: String,
}
pub struct StoredConnection {
    pub server: ServerSummary,
    pub trust: Option<HostKeyRecord>,
    pub credential_ref: Option<String>,
}
pub struct StoredChallenge {
    pub connection: StoredConnection,
    pub key: HostKeyRecord,
}

pub fn now() -> CommandResult<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|time| time.as_secs())
        .map_err(|_| {
            CommandError::new(
                "CLOCK_INVALID",
                "environment",
                "ssh",
                "系统时间无效",
                "修正系统时间后重试",
            )
        })
}

pub fn mutation_guard(database: &Path) -> CommandResult<File> {
    const WAIT: Duration = Duration::from_secs(2);
    const RETRY: Duration = Duration::from_millis(25);
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(database.with_extension("credentials.lock"))
        .map_err(|_| CommandError::storage("localConnectionMutation"))?;
    let deadline = Instant::now() + WAIT;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(file),
            Err(_) if Instant::now() < deadline => std::thread::sleep(RETRY),
            Err(_) => {
                return Err(CommandError::new(
                    "CONNECTION_BUSY",
                    "conflict",
                    "localConnectionMutation",
                    "其他连接或凭据操作尚未结束",
                    "等待操作完成后重试",
                ))
            }
        }
    }
}
pub fn checked(
    connection: &Connection,
    request: &ConnectionRequest,
    operation: &'static str,
) -> CommandResult<ServerSummary> {
    request.validate(operation)?;
    let server = persistence::get_server(connection, &request.server_id, operation)?;
    if server.record_version != request.expected_record_version {
        return Err(CommandError::conflict(operation, server.record_version));
    }
    Ok(server)
}
pub fn load(
    database: &Path,
    request: &ConnectionRequest,
    operation: &'static str,
) -> CommandResult<StoredConnection> {
    let mut database_connection =
        persistence::open(database).map_err(|_| CommandError::storage(operation))?;
    let connection = database_connection
        .transaction()
        .map_err(|_| CommandError::storage(operation))?;
    let server = checked(&connection, request, operation)?;
    let trust = connection
        .query_row(
            "SELECT algorithm,host_key,fingerprint FROM ssh_trust WHERE server_id=?1",
            [&request.server_id],
            |row| {
                Ok(HostKeyRecord {
                    algorithm: row.get(0)?,
                    key: row.get(1)?,
                    fingerprint: row.get(2)?,
                })
            },
        )
        .optional()
        .map_err(|_| CommandError::storage(operation))?;
    let credential_ref = connection
        .query_row(
            "SELECT credential_ref FROM servers WHERE server_id=?1",
            [&request.server_id],
            |row| row.get(0),
        )
        .map_err(|_| CommandError::storage(operation))?;
    connection
        .commit()
        .map_err(|_| CommandError::storage(operation))?;
    Ok(StoredConnection {
        server,
        trust,
        credential_ref,
    })
}
pub fn state(database: &Path, request: &ConnectionRequest) -> CommandResult<SshConnectionState> {
    let stored = load(database, request, "getSshConnectionState")?;
    Ok(SshConnectionState {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: stored.server.server_id,
        record_version: stored.server.record_version,
        fingerprint: stored.trust.as_ref().map(|trust| trust.fingerprint.clone()),
        algorithm: stored.trust.map(|trust| trust.algorithm),
        credential_configured: stored.credential_ref.is_some(),
        cleanup_pending: pending_count(database)?,
    })
}
pub fn pending_count(database: &Path) -> CommandResult<u64> {
    persistence::open(database)
        .and_then(|connection| {
            connection.query_row("SELECT count(*) FROM credential_cleanup", [], |row| {
                row.get(0)
            })
        })
        .map_err(|_| CommandError::storage("credentialCleanup"))
}
pub fn add_challenge(
    database: &Path,
    request: &ConnectionRequest,
    key: HostKeyRecord,
) -> CommandResult<FingerprintChallenge> {
    let operation = "scanServerFingerprint";
    let mut connection =
        persistence::open(database).map_err(|_| CommandError::storage(operation))?;
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CommandError::storage(operation))?;
    checked(&tx, request, operation)?;
    let previous: Option<String> = tx
        .query_row(
            "SELECT fingerprint FROM ssh_trust WHERE server_id=?1",
            [&request.server_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| CommandError::storage(operation))?;
    let id = Uuid::new_v4().to_string();
    let expires_at = now()? + 300;
    tx.execute(
        "DELETE FROM ssh_challenges WHERE server_id=?1 OR expires_at < unixepoch()",
        [&request.server_id],
    )
    .map_err(|_| CommandError::storage(operation))?;
    tx.execute("INSERT INTO ssh_challenges(challenge_id,server_id,record_version,algorithm,host_key,fingerprint,expires_at) VALUES (?1,?2,?3,?4,?5,?6,?7)",rusqlite::params![id,request.server_id,request.expected_record_version,key.algorithm,key.key,key.fingerprint,expires_at]).map_err(|_|CommandError::storage(operation))?;
    tx.commit().map_err(|_| CommandError::storage(operation))?;
    Ok(FingerprintChallenge {
        schema_version: 1,
        request_id: request.request_id.clone(),
        challenge_id: id,
        server_id: request.server_id.clone(),
        record_version: request.expected_record_version,
        algorithm: key.algorithm,
        fingerprint: key.fingerprint,
        previous_fingerprint: previous,
        expires_at_unix_seconds: expires_at,
    })
}
pub fn challenge(
    database: &Path,
    request: &ConnectionRequest,
    id: &str,
    fingerprint: &str,
) -> CommandResult<StoredChallenge> {
    let operation = "confirmServerFingerprint";
    crate::server_input::validate_id(id, operation, "challengeId")?;
    let stored = load(database, request, operation)?;
    let connection = persistence::open(database).map_err(|_| CommandError::storage(operation))?;
    let row:Option<(u64,u64,HostKeyRecord)>=connection.query_row("SELECT record_version,expires_at,algorithm,host_key,fingerprint FROM ssh_challenges WHERE challenge_id=?1 AND server_id=?2",rusqlite::params![id,request.server_id],|row|Ok((row.get(0)?,row.get(1)?,HostKeyRecord{algorithm:row.get(2)?,key:row.get(3)?,fingerprint:row.get(4)?}))).optional().map_err(|_|CommandError::storage(operation))?;
    match row {
        Some((version, expires, key))
            if version == request.expected_record_version
                && expires > now()?
                && key.fingerprint == fingerprint =>
        {
            Ok(StoredChallenge {
                connection: stored,
                key,
            })
        }
        _ => Err(CommandError::new(
            "SSH_CHALLENGE_STALE",
            "trust",
            operation,
            "指纹确认已过期或连接信息已变更",
            "重新扫描并核对指纹",
        )),
    }
}
pub fn commit_trust(
    database: &Path,
    request: &ConnectionRequest,
    id: &str,
    key: &HostKeyRecord,
) -> CommandResult<SshConnectionState> {
    let _guard = mutation_guard(database)?;
    challenge(database, request, id, &key.fingerprint)?;
    let operation = "confirmServerFingerprint";
    let mut connection =
        persistence::open(database).map_err(|_| CommandError::storage(operation))?;
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CommandError::storage(operation))?;
    checked(&tx, request, operation)?;
    let valid:u64=tx.query_row("SELECT count(*) FROM ssh_challenges WHERE challenge_id=?1 AND server_id=?2 AND record_version=?3 AND fingerprint=?4 AND host_key=?5 AND expires_at > unixepoch()",rusqlite::params![id,request.server_id,request.expected_record_version,key.fingerprint,key.key],|row|row.get(0)).map_err(|_|CommandError::storage(operation))?;
    if valid != 1 {
        return Err(CommandError::new(
            "SSH_CHALLENGE_STALE",
            "trust",
            operation,
            "指纹确认已失效",
            "重新扫描并确认",
        ));
    }
    let previous_key: Option<Vec<u8>> = tx
        .query_row(
            "SELECT host_key FROM ssh_trust WHERE server_id=?1",
            [&request.server_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| CommandError::storage(operation))?;
    let identity_changed = previous_key
        .as_deref()
        .is_some_and(|previous| previous != key.key.as_slice());
    tx.execute("INSERT INTO ssh_trust(server_id,algorithm,host_key,fingerprint) VALUES (?1,?2,?3,?4) ON CONFLICT(server_id) DO UPDATE SET algorithm=excluded.algorithm,host_key=excluded.host_key,fingerprint=excluded.fingerprint",rusqlite::params![request.server_id,key.algorithm,key.key,key.fingerprint]).map_err(|_|CommandError::storage(operation))?;
    tx.execute(
        "UPDATE servers SET record_version=record_version+1,host_id=CASE WHEN ?2 THEN NULL ELSE host_id END WHERE server_id=?1",
        rusqlite::params![request.server_id, identity_changed],
    )
    .map_err(|_| CommandError::storage(operation))?;
    tx.execute(
        "DELETE FROM ssh_challenges WHERE server_id=?1",
        [&request.server_id],
    )
    .map_err(|_| CommandError::storage(operation))?;
    tx.commit().map_err(|_| CommandError::storage(operation))?;
    let mut fresh = request.clone();
    fresh.expected_record_version += 1;
    state(database, &fresh)
}
pub fn retire_credential(
    connection: &Connection,
    server_id: &str,
    operation: &'static str,
) -> CommandResult<()> {
    connection.execute("INSERT OR IGNORE INTO credential_cleanup(credential_ref) SELECT credential_ref FROM servers WHERE server_id=?1 AND credential_ref IS NOT NULL",[server_id]).map_err(|_|CommandError::storage(operation))?;
    Ok(())
}
pub fn stage_credential(database: &Path, reference: &str) -> CommandResult<()> {
    persistence::open(database)
        .and_then(|connection| {
            connection.execute(
                "INSERT INTO credential_cleanup(credential_ref) VALUES (?1)",
                [reference],
            )
        })
        .map_err(|_| CommandError::storage("saveServerCredentials"))?;
    Ok(())
}
pub fn attach_credential(
    database: &Path,
    request: &ConnectionRequest,
    reference: &str,
    operation_id: &str,
) -> CommandResult<()> {
    let operation = "saveServerCredentials";
    let mut connection =
        persistence::open(database).map_err(|_| CommandError::storage(operation))?;
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CommandError::storage(operation))?;
    checked(&tx, request, operation)?;
    retire_credential(&tx, &request.server_id, operation)?;
    tx.execute(
        "UPDATE servers SET credential_ref=?1,record_version=record_version+1 WHERE server_id=?2",
        rusqlite::params![reference, request.server_id],
    )
    .map_err(|_| CommandError::storage(operation))?;
    tx.execute(
        "DELETE FROM credential_cleanup WHERE credential_ref=?1",
        [reference],
    )
    .map_err(|_| CommandError::storage(operation))?;
    tx.execute("INSERT INTO credential_receipts(operation_id,server_id,credential_ref,base_version) VALUES (?1,?2,?3,?4)",rusqlite::params![operation_id,request.server_id,reference,request.expected_record_version]).map_err(|_|CommandError::storage(operation))?;
    tx.commit().map_err(|_| CommandError::storage(operation))
}
pub fn credential_receipt(
    database: &Path,
    operation_id: &str,
) -> CommandResult<Option<(String, String, u64)>> {
    persistence::open(database).and_then(|connection|connection.query_row("SELECT server_id,credential_ref,base_version FROM credential_receipts WHERE operation_id=?1",[operation_id],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?))).optional()).map_err(|_|CommandError::storage("saveServerCredentials"))
}
pub fn cleanup_candidates(database: &Path) -> CommandResult<Vec<String>> {
    let connection =
        persistence::open(database).map_err(|_| CommandError::storage("credentialCleanup"))?;
    let mut statement=connection.prepare("SELECT credential_ref FROM credential_cleanup WHERE credential_ref NOT IN (SELECT credential_ref FROM servers WHERE credential_ref IS NOT NULL)").map_err(|_|CommandError::storage("credentialCleanup"))?;
    let result = statement
        .query_map([], |row| row.get(0))
        .map_err(|_| CommandError::storage("credentialCleanup"))?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|_| CommandError::storage("credentialCleanup"));
    result
}
pub fn complete_cleanup(database: &Path, reference: &str) -> CommandResult<()> {
    persistence::open(database)
        .and_then(|connection| {
            connection.execute(
                "DELETE FROM credential_cleanup WHERE credential_ref=?1",
                [reference],
            )
        })
        .map_err(|_| CommandError::storage("credentialCleanup"))?;
    Ok(())
}
pub fn bind_host(
    database: &Path,
    request: &ConnectionRequest,
    host_id: Option<&str>,
) -> CommandResult<()> {
    let operation = "connectServer";
    let mut connection =
        persistence::open(database).map_err(|_| CommandError::storage(operation))?;
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CommandError::storage(operation))?;
    let server = checked(&tx, request, operation)?;
    if server
        .host_id
        .as_deref()
        .is_some_and(|expected| Some(expected) != host_id)
    {
        return Err(CommandError::new(
            "HOST_IDENTITY_MISMATCH",
            "identity",
            operation,
            "远端管理目录身份与原绑定不一致",
            "检查远端管理目录，不要以新身份覆盖原绑定",
        ));
    }
    tx.execute(
        "UPDATE servers SET host_id=?1 WHERE server_id=?2",
        rusqlite::params![host_id, request.server_id],
    )
    .map_err(|_| CommandError::storage(operation))?;
    tx.commit().map_err(|_| CommandError::storage(operation))
}

pub fn record_cleanup_failure(database: &Path, reference: &str, code: &str) -> CommandResult<()> {
    persistence::open(database)
        .and_then(|connection| {
            connection.execute(
                "UPDATE credential_cleanup SET last_error_code=?1 WHERE credential_ref=?2",
                rusqlite::params![code, reference],
            )
        })
        .map_err(|_| CommandError::storage("credentialCleanup"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::{AuthKind, SaveServerRequest, ServerInput};
    type TestResult = Result<(), Box<dyn std::error::Error>>;
    struct Database {
        path: std::path::PathBuf,
        request: ConnectionRequest,
    }
    impl Database {
        fn new() -> Result<Self, Box<dyn std::error::Error>> {
            let path = std::env::temp_dir().join(format!("hd-ssh-{}.sqlite3", Uuid::new_v4()));
            persistence::initialize(&path)?;
            let server = persistence::save_server(
                &path,
                SaveServerRequest {
                    schema_version: 1,
                    request_id: Uuid::new_v4().to_string(),
                    operation_id: Uuid::new_v4().to_string(),
                    server_id: None,
                    expected_record_version: None,
                    input: ServerInput {
                        display_name: "test".into(),
                        host: "127.0.0.1".into(),
                        ssh_port: 22,
                        username: "test".into(),
                        auth_kind: AuthKind::Key,
                    },
                },
            )?;
            Ok(Self {
                path,
                request: ConnectionRequest {
                    schema_version: 1,
                    request_id: Uuid::new_v4().to_string(),
                    server_id: server.server_id,
                    expected_record_version: 1,
                },
            })
        }
    }
    impl Drop for Database {
        fn drop(&mut self) {
            for path in [&self.path, &self.path.with_extension("credentials.lock")] {
                if let Err(error) = std::fs::remove_file(path) {
                    if error.kind() != std::io::ErrorKind::NotFound {
                        eprintln!("test database cleanup failed: {error}");
                    }
                }
            }
        }
    }
    fn key() -> HostKeyRecord {
        HostKeyRecord {
            algorithm: "ED25519".into(),
            key: b"fixture-public-key".to_vec(),
            fingerprint: crate::ssh::fingerprint(b"fixture-public-key"),
        }
    }
    #[test]
    fn scanning_is_not_trust_and_confirmation_is_single_use() -> TestResult {
        let database = Database::new()?;
        let request = &database.request;
        let preview = add_challenge(&database.path, request, key())?;
        assert!(state(&database.path, request)?.fingerprint.is_none());
        let result = commit_trust(&database.path, request, &preview.challenge_id, &key())?;
        assert_eq!(result.record_version, 2);
        assert!(result.fingerprint.is_some());
        assert!(challenge(
            &database.path,
            request,
            &preview.challenge_id,
            &preview.fingerprint
        )
        .is_err());
        Ok(())
    }
    #[test]
    fn challenge_rejects_expiry_foreign_targets_and_different_fingerprint() -> TestResult {
        let database = Database::new()?;
        let other = Database::new()?;
        let preview = add_challenge(&database.path, &database.request, key())?;
        assert!(challenge(
            &database.path,
            &database.request,
            &preview.challenge_id,
            "SHA256:wrong"
        )
        .is_err());
        assert!(challenge(
            &other.path,
            &other.request,
            &preview.challenge_id,
            &preview.fingerprint
        )
        .is_err());
        persistence::open(&database.path)?.execute("UPDATE ssh_challenges SET expires_at=0", [])?;
        assert!(commit_trust(
            &database.path,
            &database.request,
            &preview.challenge_id,
            &key()
        )
        .is_err());
        Ok(())
    }
    #[test]
    fn credentials_are_referenced_atomically_and_deleted_connections_queue_cleanup() -> TestResult {
        let database = Database::new()?;
        let reference = Uuid::new_v4().to_string();
        stage_credential(&database.path, &reference)?;
        assert_eq!(pending_count(&database.path)?, 1);
        attach_credential(
            &database.path,
            &database.request,
            &reference,
            &Uuid::new_v4().to_string(),
        )?;
        assert_eq!(pending_count(&database.path)?, 0);
        let request = ConnectionRequest {
            expected_record_version: 2,
            ..database.request.clone()
        };
        assert_eq!(
            load(&database.path, &request, "test")?
                .credential_ref
                .as_deref(),
            Some(reference.as_str())
        );
        persistence::delete_server(
            &database.path,
            crate::dto::DeleteServerRequest {
                schema_version: 1,
                request_id: Uuid::new_v4().to_string(),
                operation_id: Uuid::new_v4().to_string(),
                server_id: request.server_id,
                expected_record_version: 2,
            },
        )?;
        assert_eq!(cleanup_candidates(&database.path)?, vec![reference]);
        Ok(())
    }
    #[test]
    fn stale_credential_attach_keeps_candidate_for_safe_cleanup() -> TestResult {
        let database = Database::new()?;
        let reference = Uuid::new_v4().to_string();
        stage_credential(&database.path, &reference)?;
        let stale = ConnectionRequest {
            expected_record_version: 2,
            ..database.request.clone()
        };
        assert!(attach_credential(
            &database.path,
            &stale,
            &reference,
            &Uuid::new_v4().to_string()
        )
        .is_err());
        assert_eq!(pending_count(&database.path)?, 1);
        assert!(!state(&database.path, &database.request)?.credential_configured);
        Ok(())
    }
    #[test]
    fn local_file_lock_waits_for_parallel_credential_mutation() -> TestResult {
        let database = Database::new()?;
        let guard = mutation_guard(&database.path)?;
        let path = database.path.clone();
        let waiter = std::thread::spawn(move || mutation_guard(&path).map(|_| ()));
        std::thread::sleep(std::time::Duration::from_millis(50));
        drop(guard);
        assert!(waiter.join().expect("lock waiter thread").is_ok());
        Ok(())
    }
    #[test]
    fn host_binding_never_overwrites_existing_identity_or_treats_missing_as_same() -> TestResult {
        let database = Database::new()?;
        bind_host(
            &database.path,
            &database.request,
            Some("11111111-1111-4111-8111-111111111111"),
        )?;
        assert!(bind_host(&database.path, &database.request, None).is_err());
        assert!(bind_host(
            &database.path,
            &database.request,
            Some("22222222-2222-4222-8222-222222222222")
        )
        .is_err());
        assert_eq!(
            load(&database.path, &database.request, "test")?
                .server
                .host_id
                .as_deref(),
            Some("11111111-1111-4111-8111-111111111111")
        );
        Ok(())
    }

    #[test]
    fn changed_trusted_key_clears_host_binding_but_preserves_credentials() -> TestResult {
        let database = Database::new()?;
        let mut request = database.request.clone();
        let first = add_challenge(&database.path, &request, key())?;
        commit_trust(&database.path, &request, &first.challenge_id, &key())?;
        request.expected_record_version = 2;
        let reference = Uuid::new_v4().to_string();
        stage_credential(&database.path, &reference)?;
        attach_credential(
            &database.path,
            &request,
            &reference,
            &Uuid::new_v4().to_string(),
        )?;
        request.expected_record_version = 3;
        bind_host(
            &database.path,
            &request,
            Some("11111111-1111-4111-8111-111111111111"),
        )?;
        let same = add_challenge(&database.path, &request, key())?;
        commit_trust(&database.path, &request, &same.challenge_id, &key())?;
        request.expected_record_version = 4;
        let unchanged = load(&database.path, &request, "test")?;
        assert!(unchanged.server.host_id.is_some());
        assert!(unchanged.credential_ref.is_some());
        let different = HostKeyRecord {
            algorithm: "ED25519".into(),
            key: b"replacement-key".to_vec(),
            fingerprint: crate::ssh::fingerprint(b"replacement-key"),
        };
        let preview = add_challenge(
            &database.path,
            &request,
            HostKeyRecord {
                algorithm: different.algorithm.clone(),
                key: different.key.clone(),
                fingerprint: different.fingerprint.clone(),
            },
        )?;
        commit_trust(&database.path, &request, &preview.challenge_id, &different)?;
        request.expected_record_version = 5;
        let replaced = load(&database.path, &request, "test")?;
        assert!(replaced.server.host_id.is_none());
        assert_eq!(replaced.credential_ref.as_deref(), Some(reference.as_str()));
        assert_eq!(pending_count(&database.path)?, 0);
        Ok(())
    }
}
