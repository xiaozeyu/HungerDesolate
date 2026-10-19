use crate::dto::{CommandError, CommandResult, ServerInput};
use std::net::IpAddr;
use uuid::Uuid;

pub const MAX_REVISION: u64 = 9_007_199_254_740_991;

pub fn validate_envelope(
    schema: u32,
    request_id: &str,
    operation_id: &str,
    operation: &'static str,
) -> CommandResult<()> {
    if schema != 1 {
        return Err(CommandError::new(
            "SCHEMA_UNSUPPORTED",
            "validation",
            operation,
            "不支持此请求版本",
            "更新应用后重试",
        ));
    }
    validate_id(request_id, operation, "requestId")?;
    validate_id(operation_id, operation, "operationId")
}

pub fn validate_id(id: &str, operation: &'static str, field: &'static str) -> CommandResult<()> {
    if !Uuid::try_parse(id)
        .is_ok_and(|value| value.to_string() == id && value.get_version_num() == 4)
    {
        return Err(CommandError::invalid(
            operation,
            field,
            "连接与请求标识必须是规范 UUID v4",
        ));
    }
    Ok(())
}

pub fn normalize(mut input: ServerInput) -> CommandResult<ServerInput> {
    let operation = "saveServer";
    input.display_name = input.display_name.trim().to_string();
    input.host = input.host.trim().to_lowercase();
    input.username = input.username.trim().to_string();
    if input.display_name.is_empty()
        || input.display_name.chars().count() > 64
        || input.display_name.chars().any(char::is_control)
    {
        return Err(CommandError::invalid(
            operation,
            "displayName",
            "名称需为 1–64 个字符，且不能含控制字符",
        ));
    }
    let ip = input.host.parse::<IpAddr>().is_ok();
    let dns = input.host.len() <= 253
        && input.host.split('.').all(|part| {
            !part.is_empty()
                && part.len() <= 63
                && !part.starts_with('-')
                && !part.ends_with('-')
                && part.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
        });
    if !ip && (!dns || input.host.bytes().all(|c| c.is_ascii_digit() || c == b'.')) {
        return Err(CommandError::invalid(
            operation,
            "host",
            "请输入 IP 或主机名，不含协议、端口或路径",
        ));
    }
    if input.ssh_port == 0 {
        return Err(CommandError::invalid(
            operation,
            "sshPort",
            "SSH 端口需为 1–65535",
        ));
    }
    if input.username.is_empty()
        || input.username.len() > 64
        || !input
            .username
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_-.@".contains(&c))
        || input.username.starts_with('-')
    {
        return Err(CommandError::invalid(
            operation,
            "username",
            "用户名需为 1–64 个字母、数字或 _-.@，且不能以 - 开头",
        ));
    }
    Ok(input)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::AuthKind;
    fn input(host: &str) -> ServerInput {
        ServerInput {
            display_name: "主机".into(),
            host: host.into(),
            ssh_port: 22,
            username: "dst-admin".into(),
            auth_kind: AuthKind::Key,
        }
    }
    #[test]
    fn validates_hosts_without_accepting_urls_or_shell_text() {
        for host in ["localhost", "EXAMPLE.COM", "192.0.2.24", "2001:db8::1"] {
            assert!(normalize(input(host)).is_ok(), "{host}");
        }
        for host in [
            "",
            "ssh://host",
            "a:22",
            "host/path",
            "-bad",
            "x;echo",
            "999.1.2.3",
        ] {
            assert!(normalize(input(host)).is_err(), "{host}");
        }
    }
    #[test]
    fn rejects_secret_fields_before_persistence() {
        let value = serde_json::json!({"displayName":"a","host":"host","sshPort":22,"username":"u","authKind":"key","password":"must-not-persist"});
        assert!(serde_json::from_value::<ServerInput>(value).is_err());
    }
}
