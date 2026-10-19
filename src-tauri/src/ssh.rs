use crate::{
    dto::{CommandError, CommandResult, ServerSummary},
    secrets,
    ssh_dto::{
        ConfirmFingerprintRequest, ConnectionRequest, CredentialInput, FingerprintChallenge,
        HostConnection, ManagementState,
    },
    ssh_state::{self, HostKeyRecord},
};
use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use ssh2::{HostKeyType, Session};
use std::{
    io::Read,
    net::{TcpStream, ToSocketAddrs},
    path::Path,
    time::{Duration, Instant},
};

fn network_error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "connection",
        "ssh",
        message,
        "检查地址、端口、网络与 SSH 服务后重试",
    )
}

fn handshake(server: &ServerSummary) -> CommandResult<(Session, HostKeyRecord)> {
    let addresses = (server.host.as_str(), server.ssh_port)
        .to_socket_addrs()
        .map_err(|_| network_error("SSH_DNS_FAILED", "无法解析主机地址"))?;
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut stream = None;
    for address in addresses.take(8) {
        let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
            break;
        };
        if let Ok(candidate) =
            TcpStream::connect_timeout(&address, remaining.min(Duration::from_secs(3)))
        {
            stream = Some(candidate);
            break;
        }
    }
    let stream = stream.ok_or_else(|| network_error("SSH_UNREACHABLE", "无法连接 SSH 端口"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .map_err(|_| network_error("SSH_SOCKET_FAILED", "无法设置 SSH 超时"))?;
    stream
        .set_write_timeout(Some(Duration::from_secs(10)))
        .map_err(|_| network_error("SSH_SOCKET_FAILED", "无法设置 SSH 超时"))?;
    let mut session =
        Session::new().map_err(|_| network_error("SSH_SESSION_FAILED", "无法创建 SSH 会话"))?;
    session.set_timeout(10_000);
    session.set_tcp_stream(stream);
    session
        .handshake()
        .map_err(|_| network_error("SSH_HANDSHAKE_FAILED", "SSH 握手失败或超时"))?;
    let (bytes, kind) = session
        .host_key()
        .ok_or_else(|| network_error("SSH_HOST_KEY_MISSING", "主机未提供有效 SSH 公钥"))?;
    let algorithm = match kind {
        HostKeyType::Rsa => "RSA",
        HostKeyType::Dss => "DSA",
        HostKeyType::Ecdsa256 => "ECDSA-256",
        HostKeyType::Ecdsa384 => "ECDSA-384",
        HostKeyType::Ecdsa521 => "ECDSA-521",
        HostKeyType::Ed25519 => "ED25519",
        HostKeyType::Unknown => {
            return Err(network_error(
                "SSH_KEY_UNSUPPORTED",
                "不支持此 SSH 主机公钥类型",
            ))
        }
    };
    let key = HostKeyRecord {
        algorithm: algorithm.into(),
        fingerprint: fingerprint(bytes),
        key: bytes.to_vec(),
    };
    Ok((session, key))
}
pub fn fingerprint(bytes: &[u8]) -> String {
    format!("SHA256:{}", STANDARD_NO_PAD.encode(Sha256::digest(bytes)))
}

pub fn require_trust(
    expected: Option<&HostKeyRecord>,
    observed: &HostKeyRecord,
) -> CommandResult<()> {
    match expected {
        Some(expected)
            if expected.key == observed.key && expected.algorithm == observed.algorithm =>
        {
            Ok(())
        }
        Some(_) => Err(CommandError::new(
            "SSH_FINGERPRINT_CHANGED",
            "trust",
            "connectServer",
            "SSH 指纹发生变化，未发送凭据",
            "在主机控制台核实新旧指纹，再重新扫描确认",
        )),
        None => Err(CommandError::new(
            "SSH_TRUST_REQUIRED",
            "trust",
            "connectServer",
            "尚未确认 SSH 指纹，未发送凭据",
            "先扫描指纹并与主机控制台核对",
        )),
    }
}
pub fn scan(database: &Path, request: ConnectionRequest) -> CommandResult<FingerprintChallenge> {
    let stored = ssh_state::load(database, &request, "scanServerFingerprint")?;
    let (_session, key) = handshake(&stored.server)?;
    ssh_state::add_challenge(database, &request, key)
}
pub fn confirm(
    database: &Path,
    request: ConfirmFingerprintRequest,
) -> CommandResult<crate::ssh_dto::SshConnectionState> {
    let connection = request.connection();
    let challenge = ssh_state::challenge(
        database,
        &connection,
        &request.challenge_id,
        &request.fingerprint,
    )?;
    let (_session, observed) = handshake(&challenge.connection.server)?;
    require_trust(Some(&challenge.key), &observed)?;
    ssh_state::commit_trust(database, &connection, &request.challenge_id, &observed)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct HostIdentity {
    schema_version: u32,
    host_id: String,
}
pub fn parse_host_identity(bytes: &[u8]) -> CommandResult<String> {
    let identity: HostIdentity = serde_json::from_slice(bytes).map_err(|_| {
        CommandError::new(
            "HOST_IDENTITY_INVALID",
            "identity",
            "connectServer",
            "远端 host.json 格式无效",
            "检查管理目录身份文件，禁止自动覆盖",
        )
    })?;
    if identity.schema_version != 1 {
        return Err(CommandError::new(
            "SCHEMA_UNSUPPORTED",
            "identity",
            "connectServer",
            "远端管理目录版本不受支持",
            "使用匹配版本的客户端核对管理目录",
        ));
    }
    crate::server_input::validate_id(&identity.host_id, "connectServer", "hostId").map_err(
        |_| {
            CommandError::new(
                "HOST_IDENTITY_INVALID",
                "identity",
                "connectServer",
                "远端 hostId 格式无效",
                "核对管理目录身份文件，不自动生成替代身份",
            )
        },
    )?;
    Ok(identity.host_id)
}
fn read_host_identity(session: &Session) -> CommandResult<Option<String>> {
    let sftp = session
        .sftp()
        .map_err(|_| network_error("SSH_SFTP_FAILED", "登录通过，但无法启动 SFTP 身份检查"))?;
    let path = Path::new("/var/lib/famine-hub/host.json");
    let stat = match sftp.lstat(path) {
        Ok(stat) => stat,
        Err(error) if error.code() == ssh2::ErrorCode::SFTP(2) => return Ok(None),
        Err(_) => {
            return Err(CommandError::new(
                "HOST_IDENTITY_READ_FAILED",
                "identity",
                "connectServer",
                "无法读取远端管理目录身份",
                "检查管理目录读取权限，不将权限错误视为未初始化",
            ))
        }
    };
    if !stat.is_file() || stat.size.is_none_or(|size| size > 65536) {
        return Err(CommandError::new(
            "HOST_IDENTITY_INVALID",
            "identity",
            "connectServer",
            "远端身份文件类型或大小不合法",
            "检查 host.json，不使用符号链接或异常文件",
        ));
    }
    let mut bytes = Vec::new();
    sftp.open(path)
        .and_then(|file| {
            file.take(65537)
                .read_to_end(&mut bytes)
                .map_err(|_| ssh2::Error::from_errno(ssh2::ErrorCode::SFTP(4)))
        })
        .map_err(|_| network_error("HOST_IDENTITY_READ_FAILED", "无法完成远端身份文件读取"))?;
    if bytes.len() > 65536 {
        return Err(network_error("HOST_IDENTITY_INVALID", "远端身份文件过大"));
    }
    parse_host_identity(&bytes).map(Some)
}
fn authenticate(
    session: &Session,
    server: &ServerSummary,
    trusted: Option<&HostKeyRecord>,
    observed: &HostKeyRecord,
    credential: &CredentialInput,
) -> CommandResult<()> {
    require_trust(trusted, observed)?;
    if credential.auth_kind() != server.auth_kind {
        return Err(network_error(
            "SSH_AUTH_KIND_MISMATCH",
            "保存的凭据与认证方式不一致",
        ));
    }
    let authenticated = match credential {
        CredentialInput::Password { password } => {
            session.userauth_password(&server.username, password)
        }
        CredentialInput::PrivateKey {
            private_key,
            passphrase,
        } => session.userauth_pubkey_memory(
            &server.username,
            None,
            private_key,
            passphrase.as_deref(),
        ),
    };
    if authenticated.is_err() || !session.authenticated() {
        return Err(CommandError::new(
            "SSH_AUTH_FAILED",
            "connection",
            "connectServer",
            "SSH 认证失败",
            "核对用户名、密码或私钥及口令，并确认主机允许此认证方式",
        ));
    }
    Ok(())
}
pub(crate) fn with_verified_session<T>(
    database: &Path,
    request: &ConnectionRequest,
    work: impl FnOnce(&Session, Option<String>) -> CommandResult<T>,
) -> CommandResult<T> {
    let stored = ssh_state::load(database, request, "connectServer")?;
    if stored.trust.is_none() {
        return Err(CommandError::new(
            "SSH_TRUST_REQUIRED",
            "trust",
            "connectServer",
            "请先确认 SSH 指纹",
            "扫描并核对主机指纹",
        ));
    }
    let (session, observed) = handshake(&stored.server)?;
    require_trust(stored.trust.as_ref(), &observed)?;
    // Recheck the connection after handshake and before reading or transmitting a credential.
    let _guard = ssh_state::mutation_guard(database)?;
    let current = ssh_state::load(database, request, "connectServer")?;
    require_trust(current.trust.as_ref(), &observed)?;
    let reference = current.credential_ref.ok_or_else(|| {
        CommandError::new(
            "SECRET_REQUIRED",
            "security",
            "connectServer",
            "尚未保存 SSH 凭据",
            "在系统凭据库中保存密码或私钥后重试",
        )
    })?;
    let credential = secrets::read(&reference)?;
    authenticate(
        &session,
        &current.server,
        current.trust.as_ref(),
        &observed,
        &credential,
    )?;
    let host_id = read_host_identity(&session)?;
    ssh_state::bind_host(database, request, host_id.as_deref())?;
    work(&session, host_id)
}
pub fn connect(database: &Path, request: ConnectionRequest) -> CommandResult<HostConnection> {
    with_verified_session(database, &request, |_session, host_id| {
        Ok(HostConnection {
            schema_version: 1,
            request_id: request.request_id.clone(),
            server_id: request.server_id.clone(),
            record_version: request.expected_record_version,
            management_state: if host_id.is_some() {
                ManagementState::Ready
            } else {
                ManagementState::Uninitialized
            },
            host_id,
            verified_at_unix_seconds: ssh_state::now()?,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key(value: &[u8]) -> HostKeyRecord {
        HostKeyRecord {
            algorithm: "ED25519".into(),
            key: value.to_vec(),
            fingerprint: fingerprint(value),
        }
    }
    #[test]
    fn never_allows_untrusted_or_changed_keys() {
        assert!(
            matches!(require_trust(None,&key(b"a")),Err(error) if error.code=="SSH_TRUST_REQUIRED")
        );
        assert!(
            matches!(require_trust(Some(&key(b"a")),&key(b"b")),Err(error) if error.code=="SSH_FINGERPRINT_CHANGED")
        );
        assert!(require_trust(Some(&key(b"a")), &key(b"a")).is_ok());
    }
    #[test]
    fn fingerprint_is_sha256_without_base64_padding() {
        assert_eq!(
            fingerprint(b"abc"),
            "SHA256:ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0"
        );
    }
    #[test]
    fn validates_remote_identity_without_generating_a_replacement() {
        assert!(parse_host_identity(
            br#"{"schemaVersion":1,"hostId":"11111111-1111-4111-8111-111111111111"}"#
        )
        .is_ok());
        assert!(parse_host_identity(
            br#"{"schemaVersion":2,"hostId":"11111111-1111-4111-8111-111111111111"}"#
        )
        .is_err());
        assert!(parse_host_identity(br#"{"schemaVersion":1,"hostId":"../default"}"#).is_err());
    }

    #[test]
    #[ignore = "run scripts/test-local-ssh.py with an isolated OpenSSH daemon"]
    fn local_ssh_fixture_authenticates_only_after_trust() -> Result<(), Box<dyn std::error::Error>>
    {
        let port: u16 = std::env::var("HUNGER_SSH_TEST_PORT")?.parse()?;
        let username = std::env::var("HUNGER_SSH_TEST_USER")?;
        let key_path = std::env::var("HUNGER_SSH_TEST_KEY")?;
        let server = ServerSummary {
            server_id: uuid::Uuid::new_v4().to_string(),
            display_name: "isolated test".into(),
            host: "127.0.0.1".into(),
            ssh_port: port,
            username,
            auth_kind: crate::dto::AuthKind::Key,
            host_id: None,
            connection_state: crate::dto::ConnectionState::Disconnected,
            record_version: 1,
        };
        let (session, observed) = handshake(&server)?;
        let credential = CredentialInput::PrivateKey {
            private_key: std::fs::read_to_string(key_path)?,
            passphrase: None,
        };
        assert!(
            matches!(authenticate(&session,&server,None,&observed,&credential),Err(error) if error.code=="SSH_TRUST_REQUIRED")
        );
        assert!(!session.authenticated());
        assert!(
            matches!(authenticate(&session,&server,Some(&key(b"incorrect-trust")),&observed,&credential),Err(error) if error.code=="SSH_FINGERPRINT_CHANGED")
        );
        assert!(!session.authenticated());
        authenticate(&session, &server, Some(&observed), &observed, &credential)?;
        assert!(session.authenticated());
        read_host_identity(&session)?;
        let request = ConnectionRequest {
            schema_version: 1,
            request_id: uuid::Uuid::new_v4().to_string(),
            server_id: server.server_id.clone(),
            expected_record_version: 1,
        };
        let environment = crate::environment::from_output(
            &crate::environment::execute(&session)?,
            &request,
            None,
        )?;
        assert!(environment.kernel.is_some());
        assert_eq!(environment.server_id, server.server_id);
        assert!(!environment.deployment_verified);
        let registry_root = std::path::PathBuf::from(std::env::var("HUNGER_REGISTRY_TEST_ROOT")?);
        crate::registry::tests::exercise_sftp_fixtures(&session, &registry_root, &request)?;
        crate::host_initialization::tests::exercise_ssh(&session, &registry_root)?;
        Ok(())
    }
}
