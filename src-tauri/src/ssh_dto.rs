use crate::dto::{AuthKind, CommandError, CommandResult};
use serde::{Deserialize, Serialize};
use zeroize::Zeroize;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ConnectionRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub expected_record_version: u64,
}
impl ConnectionRequest {
    pub fn validate(&self, operation: &'static str) -> CommandResult<()> {
        if self.schema_version != 1 {
            return Err(CommandError::new(
                "SCHEMA_UNSUPPORTED",
                "validation",
                operation,
                "请求版本不受支持",
                "更新应用后重试",
            ));
        }
        crate::server_input::validate_id(&self.request_id, operation, "requestId")?;
        crate::server_input::validate_id(&self.server_id, operation, "serverId")?;
        if !(1..crate::server_input::MAX_REVISION).contains(&self.expected_record_version) {
            return Err(CommandError::invalid(
                operation,
                "expectedRecordVersion",
                "连接记录版本无效",
            ));
        }
        Ok(())
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ConfirmFingerprintRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub expected_record_version: u64,
    pub challenge_id: String,
    pub fingerprint: String,
}
impl ConfirmFingerprintRequest {
    pub fn connection(&self) -> ConnectionRequest {
        ConnectionRequest {
            schema_version: self.schema_version,
            request_id: self.request_id.clone(),
            server_id: self.server_id.clone(),
            expected_record_version: self.expected_record_version,
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FingerprintChallenge {
    pub schema_version: u32,
    pub request_id: String,
    pub challenge_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub algorithm: String,
    pub fingerprint: String,
    pub previous_fingerprint: Option<String>,
    pub expires_at_unix_seconds: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SshConnectionState {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub fingerprint: Option<String>,
    pub algorithm: Option<String>,
    pub credential_configured: bool,
    pub cleanup_pending: u64,
}

#[derive(Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub enum CredentialInput {
    Password {
        password: String,
    },
    PrivateKey {
        #[serde(rename = "privateKey")]
        private_key: String,
        passphrase: Option<String>,
    },
}
impl CredentialInput {
    pub fn auth_kind(&self) -> AuthKind {
        match self {
            Self::Password { .. } => AuthKind::Password,
            Self::PrivateKey { .. } => AuthKind::Key,
        }
    }
    pub fn validate(&self) -> CommandResult<()> {
        let valid = match self {
            Self::Password { password } => {
                !password.is_empty() && password.len() <= 8192 && !password.contains('\0')
            }
            Self::PrivateKey {
                private_key,
                passphrase,
            } => {
                private_key.len() <= 65536
                    && private_key.starts_with("-----BEGIN ")
                    && private_key.contains("PRIVATE KEY-----")
                    && !private_key.contains('\0')
                    && passphrase
                        .as_ref()
                        .is_none_or(|value| value.len() <= 8192 && !value.contains('\0'))
            }
        };
        if !valid {
            return Err(CommandError::invalid(
                "saveServerCredentials",
                "credential",
                "凭据为空、格式不支持或长度超出限制",
            ));
        }
        Ok(())
    }
}
impl Drop for CredentialInput {
    fn drop(&mut self) {
        match self {
            Self::Password { password } => password.zeroize(),
            Self::PrivateKey {
                private_key,
                passphrase,
            } => {
                private_key.zeroize();
                if let Some(value) = passphrase {
                    value.zeroize();
                }
            }
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveCredentialRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub expected_record_version: u64,
    pub operation_id: String,
    pub credential: crate::credential_source::CredentialSource,
}
impl SaveCredentialRequest {
    pub fn connection(&self) -> ConnectionRequest {
        ConnectionRequest {
            schema_version: self.schema_version,
            request_id: self.request_id.clone(),
            server_id: self.server_id.clone(),
            expected_record_version: self.expected_record_version,
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupStatus {
    pub pending_count: u64,
    pub failed_count: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HostConnection {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub host_id: Option<String>,
    pub management_state: ManagementState,
    pub verified_at_unix_seconds: u64,
}
#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum ManagementState {
    Uninitialized,
    Ready,
}
