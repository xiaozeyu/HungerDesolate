use crate::{
    dto::{CommandError, CommandResult},
    persistence,
    ssh_dto::{CleanupStatus, CredentialInput, SaveCredentialRequest, SshConnectionState},
    ssh_state,
};
use std::path::Path;
use zeroize::Zeroizing;

const SERVICE: &str = "app.hungerdesolate.hub.ssh";

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn entry(reference: &str) -> CommandResult<keyring::Entry> {
    crate::server_input::validate_id(reference, "credentialStore", "credentialRef")?;
    keyring::Entry::new(SERVICE, reference)
        .map_err(|_| credential_error("SECRET_STORE_UNAVAILABLE", "系统凭据库不可用"))
}
fn credential_error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "security",
        "credentialStore",
        message,
        "检查系统凭据库权限，必要时重新提供凭据",
    )
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub fn read(reference: &str) -> CommandResult<CredentialInput> {
    let secret = Zeroizing::new(
        entry(reference)?
            .get_password()
            .map_err(|_| credential_error("SECRET_READ_FAILED", "未能读取已保存凭据"))?,
    );
    serde_json::from_str(&secret)
        .map_err(|_| credential_error("SECRET_FORMAT_INVALID", "凭据格式不受支持，请重新配置"))
}
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
pub fn read(_reference: &str) -> CommandResult<CredentialInput> {
    Err(credential_error(
        "SECRET_STORE_UNSUPPORTED",
        "当前客户端平台不支持系统凭据库",
    ))
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn write(reference: &str, credential: &CredentialInput) -> CommandResult<()> {
    let secret = Zeroizing::new(
        serde_json::to_string(credential)
            .map_err(|_| credential_error("SECRET_FORMAT_INVALID", "凭据序列化失败"))?,
    );
    entry(reference)?
        .set_password(&secret)
        .map_err(|_| credential_error("SECRET_WRITE_FAILED", "系统凭据库拒绝保存凭据"))
}
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn write(_reference: &str, _credential: &CredentialInput) -> CommandResult<()> {
    Err(credential_error(
        "SECRET_STORE_UNSUPPORTED",
        "当前客户端平台不支持系统凭据库",
    ))
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn remove(reference: &str) -> CommandResult<()> {
    match entry(reference)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err(credential_error(
            "SECRET_DELETE_FAILED",
            "系统凭据库未能清理旧凭据",
        )),
    }
}
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn remove(_reference: &str) -> CommandResult<()> {
    Err(credential_error(
        "SECRET_STORE_UNSUPPORTED",
        "当前客户端平台不支持系统凭据库",
    ))
}

fn cleanup_unlocked(database: &Path) -> CommandResult<CleanupStatus> {
    let mut failed = 0;
    for reference in ssh_state::cleanup_candidates(database)? {
        match remove(&reference) {
            Ok(()) => ssh_state::complete_cleanup(database, &reference)?,
            Err(error) => {
                crate::ssh_state::record_cleanup_failure(database, &reference, error.code)?;
                failed += 1;
            }
        }
    }
    // Failed entries remain in the durable cleanup queue for an explicit retry.
    Ok(CleanupStatus {
        pending_count: ssh_state::pending_count(database)?,
        failed_count: failed,
    })
}
pub fn cleanup(database: &Path) -> CommandResult<CleanupStatus> {
    let _guard = ssh_state::mutation_guard(database)?;
    cleanup_unlocked(database)
}
pub fn status(database: &Path) -> CommandResult<CleanupStatus> {
    Ok(CleanupStatus {
        pending_count: ssh_state::pending_count(database)?,
        failed_count: 0,
    })
}

pub fn save(
    database: &Path,
    mut request: SaveCredentialRequest,
) -> CommandResult<SshConnectionState> {
    let operation = "saveServerCredentials";
    let connection_request = request.connection();
    connection_request.validate(operation)?;
    crate::server_input::validate_id(&request.operation_id, operation, "operationId")?;
    let credential = request.credential.resolve()?;
    let _guard = ssh_state::mutation_guard(database)?;
    let used_for_metadata: bool = persistence::open(database)
        .and_then(|connection| {
            connection.query_row(
                "SELECT EXISTS(SELECT 1 FROM local_operations WHERE operation_id=?1)",
                [&request.operation_id],
                |row| row.get(0),
            )
        })
        .map_err(|_| CommandError::storage(operation))?;
    if used_for_metadata {
        return Err(CommandError::new(
            "OPERATION_ID_REUSED",
            "conflict",
            operation,
            "操作标识已用于连接记录变更",
            "重新发起凭据操作",
        ));
    }
    if let Some((server_id, reference, base)) =
        ssh_state::credential_receipt(database, &request.operation_id)?
    {
        if server_id != request.server_id || base != request.expected_record_version {
            return Err(CommandError::new(
                "OPERATION_ID_REUSED",
                "conflict",
                operation,
                "操作标识已用于其他凭据输入",
                "刷新状态并重新编辑",
            ));
        }
        let connection =
            persistence::open(database).map_err(|_| CommandError::storage(operation))?;
        let current = persistence::get_server(&connection, &request.server_id, operation)?;
        let mut updated = connection_request.clone();
        updated.expected_record_version = current.record_version;
        let stored = ssh_state::load(database, &updated, operation)?;
        if stored.credential_ref.as_deref() != Some(&reference)
            || current.record_version != request.expected_record_version + 1
        {
            return Err(CommandError::new(
                "OPERATION_SUPERSEDED",
                "conflict",
                operation,
                "原凭据操作已完成，之后记录又被修改",
                "刷新核实，不重复覆盖当前凭据",
            ));
        }
        if read(&reference)? != credential {
            return Err(CommandError::new(
                "OPERATION_ID_REUSED",
                "conflict",
                operation,
                "同一操作标识不能保存不同凭据",
                "重新发起凭据替换",
            ));
        }
        return ssh_state::state(database, &updated);
    }
    let stored = ssh_state::load(database, &connection_request, operation)?;
    if stored.server.auth_kind != credential.auth_kind() {
        return Err(CommandError::invalid(
            operation,
            "credential",
            "凭据类型与连接认证方式不一致",
        ));
    }
    let reference = uuid::Uuid::new_v4().to_string();
    // Queue before keyring I/O: a crash can leave an orphan, but never an untracked secret.
    ssh_state::stage_credential(database, &reference)?;
    write(&reference, &credential)?;
    ssh_state::attach_credential(
        database,
        &connection_request,
        &reference,
        &request.operation_id,
    )?;
    cleanup_unlocked(database)?;
    let mut updated = connection_request;
    updated.expected_record_version += 1;
    ssh_state::state(database, &updated)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn credentials_are_typed_and_not_debug_dtos() {
        let input: CredentialInput =
            serde_json::from_value(serde_json::json!({"kind":"password","password":"test-secret"}))
                .expect("test password fixture must parse");
        assert!(input.validate().is_ok());
        assert!(serde_json::from_value::<CredentialInput>(
            serde_json::json!({"kind":"password","password":""})
        )
        .is_ok_and(|value| value.validate().is_err()));
        assert_eq!(SERVICE, "app.hungerdesolate.hub.ssh");
    }
}

#[cfg(all(test, any(target_os = "macos", target_os = "windows")))]
mod workflow_tests {
    use super::*;
    use keyring::credential::{Credential, CredentialApi, CredentialBuilderApi};
    use std::{
        any::Any,
        collections::{HashMap, HashSet},
        sync::{Arc, Mutex},
    };

    #[derive(Clone, Default)]
    struct MemoryStore {
        values: Arc<Mutex<HashMap<String, Vec<u8>>>>,
        deny_delete: Arc<Mutex<HashSet<String>>>,
    }
    struct MemoryCredential {
        store: MemoryStore,
        key: String,
    }
    fn lock_error() -> keyring::Error {
        keyring::Error::Invalid("test".into(), "test lock poisoned".into())
    }
    impl CredentialApi for MemoryCredential {
        fn set_secret(&self, value: &[u8]) -> keyring::Result<()> {
            self.store
                .values
                .lock()
                .map_err(|_| lock_error())?
                .insert(self.key.clone(), value.to_vec());
            Ok(())
        }
        fn get_secret(&self) -> keyring::Result<Vec<u8>> {
            self.store
                .values
                .lock()
                .map_err(|_| lock_error())?
                .get(&self.key)
                .cloned()
                .ok_or(keyring::Error::NoEntry)
        }
        fn delete_credential(&self) -> keyring::Result<()> {
            if self
                .store
                .deny_delete
                .lock()
                .map_err(|_| lock_error())?
                .contains(&self.key)
            {
                return Err(keyring::Error::Invalid("test".into(), "test denial".into()));
            }
            self.store
                .values
                .lock()
                .map_err(|_| lock_error())?
                .remove(&self.key)
                .map(|_| ())
                .ok_or(keyring::Error::NoEntry)
        }
        fn as_any(&self) -> &dyn Any {
            self
        }
    }
    impl CredentialBuilderApi for MemoryStore {
        fn build(
            &self,
            _target: Option<&str>,
            service: &str,
            user: &str,
        ) -> keyring::Result<Box<Credential>> {
            Ok(Box::new(MemoryCredential {
                store: self.clone(),
                key: format!("{service}:{user}"),
            }))
        }
        fn as_any(&self) -> &dyn Any {
            self
        }
    }
    #[test]
    fn production_credential_workflow_handles_retries_crash_candidates_and_cleanup_failures(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let memory = MemoryStore::default();
        // Only this test configures the vendor's test builder. Production uses the OS backend.
        keyring::set_default_credential_builder(Box::new(memory.clone()));
        let path =
            std::env::temp_dir().join(format!("hd-keyring-{}.sqlite3", uuid::Uuid::new_v4()));
        let result = (|| -> Result<(), Box<dyn std::error::Error>> {
            persistence::initialize(&path)?;
            let server = persistence::save_server(
                &path,
                crate::dto::SaveServerRequest {
                    schema_version: 1,
                    request_id: uuid::Uuid::new_v4().to_string(),
                    operation_id: uuid::Uuid::new_v4().to_string(),
                    server_id: None,
                    expected_record_version: None,
                    input: crate::dto::ServerInput {
                        display_name: "fixture".into(),
                        host: "localhost".into(),
                        ssh_port: 22,
                        username: "fixture".into(),
                        auth_kind: crate::dto::AuthKind::Password,
                    },
                },
            )?;
            let mut request = crate::ssh_dto::ConnectionRequest {
                schema_version: 1,
                request_id: uuid::Uuid::new_v4().to_string(),
                server_id: server.server_id.clone(),
                expected_record_version: 1,
            };
            let operation_id = uuid::Uuid::new_v4().to_string();
            let make_request = |password: &str, version: u64, op: &str| SaveCredentialRequest {
                schema_version: 1,
                request_id: uuid::Uuid::new_v4().to_string(),
                server_id: server.server_id.clone(),
                expected_record_version: version,
                operation_id: op.into(),
                credential: crate::credential_source::CredentialSource::Password {
                    password: password.into(),
                },
            };
            assert_eq!(
                save(&path, make_request("test-fixture-secret", 1, &operation_id))?.record_version,
                2
            );
            assert_eq!(
                save(&path, make_request("test-fixture-secret", 1, &operation_id))?.record_version,
                2
            );
            assert!(
                matches!(save(&path,make_request("different-fixture-secret",1,&operation_id)),Err(error) if error.code=="OPERATION_ID_REUSED")
            );
            request.expected_record_version = 2;
            let before_trust = ssh_state::state(&path, &request)?;
            assert!(before_trust.fingerprint.is_none());
            assert!(before_trust.credential_configured);
            assert!(
                matches!(crate::ssh::connect(&path,request.clone()),Err(error) if error.code=="SSH_TRUST_REQUIRED")
            );
            let hostkey = crate::ssh_state::HostKeyRecord {
                algorithm: "ED25519".into(),
                key: b"public-fixture".to_vec(),
                fingerprint: crate::ssh::fingerprint(b"public-fixture"),
            };
            let challenge = ssh_state::add_challenge(
                &path,
                &request,
                crate::ssh_state::HostKeyRecord {
                    algorithm: hostkey.algorithm.clone(),
                    key: hostkey.key.clone(),
                    fingerprint: hostkey.fingerprint.clone(),
                },
            )?;
            ssh_state::commit_trust(&path, &request, &challenge.challenge_id, &hostkey)?;
            request.expected_record_version = 3;
            let first = ssh_state::load(&path, &request, "test")?
                .credential_ref
                .ok_or("missing credential ref")?;
            assert!(
                read(&first)?
                    == CredentialInput::Password {
                        password: "test-fixture-secret".into()
                    }
            );
            let orphan = uuid::Uuid::new_v4().to_string();
            ssh_state::stage_credential(&path, &orphan)?;
            write(
                &orphan,
                &CredentialInput::Password {
                    password: "orphan-fixture-secret".into(),
                },
            )?;
            assert_eq!(cleanup(&path)?.pending_count, 0);
            assert!(read(&orphan).is_err());
            save(
                &path,
                make_request(
                    "replacement-fixture-secret",
                    3,
                    &uuid::Uuid::new_v4().to_string(),
                ),
            )?;
            request.expected_record_version = 4;
            assert!(read(&first).is_err());
            let reference = ssh_state::load(&path, &request, "test")?
                .credential_ref
                .ok_or("missing replacement")?;
            memory
                .deny_delete
                .lock()
                .map_err(|_| "test lock failed")?
                .insert(format!("{SERVICE}:{reference}"));
            persistence::delete_server(
                &path,
                crate::dto::DeleteServerRequest {
                    schema_version: 1,
                    request_id: uuid::Uuid::new_v4().to_string(),
                    operation_id: uuid::Uuid::new_v4().to_string(),
                    server_id: server.server_id,
                    expected_record_version: 4,
                },
            )?;
            let failed = cleanup(&path)?;
            assert_eq!(failed.pending_count, 1);
            assert_eq!(failed.failed_count, 1);
            memory
                .deny_delete
                .lock()
                .map_err(|_| "test lock failed")?
                .clear();
            assert_eq!(cleanup(&path)?.pending_count, 0);
            let disk = std::fs::read(&path)?;
            assert!(!disk
                .windows(b"fixture-secret".len())
                .any(|bytes| bytes == b"fixture-secret"));
            Ok(())
        })();
        let remove = std::fs::remove_file(&path);
        let lock = std::fs::remove_file(path.with_extension("credentials.lock"));
        result?;
        remove?;
        lock?;
        Ok(())
    }
}
