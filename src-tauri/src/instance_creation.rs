use crate::{
    dto::{CommandError, CommandResult},
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use ssh2::{ExtendedData, Session};
use std::{
    collections::HashSet,
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "previewInstanceCreate";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-create-preview.py");
const ROOT: &str = "/var/lib/famine-hub";

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HostTarget {
    pub server_id: String,
    pub host_id: String,
}
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CreateInput {
    pub display_name: String,
    pub autostart: bool,
    pub start_after_create: bool,
    pub port_policy: String,
    pub caves_enabled: bool,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreviewRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub expected_registry_revision: u64,
    pub input: CreateInput,
}
impl PreviewRequest {
    fn connection(&self) -> ConnectionRequest {
        ConnectionRequest {
            schema_version: self.schema_version,
            request_id: self.request_id.clone(),
            server_id: self.target.server_id.clone(),
            expected_record_version: self.expected_record_version,
        }
    }
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PortPlan {
    pub shard: String,
    pub purpose: String,
    pub protocol: String,
    pub bind_address: String,
    pub host_port: u16,
    pub container_port: u16,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Probe {
    schema_version: u32,
    host_id: String,
    registry_revision: u64,
    captured_at_unix_seconds: u64,
    ports: Vec<PortPlan>,
    blockers: Vec<String>,
    available_bytes: u64,
    docker_version: Option<String>,
    compose_version: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewResponse {
    schema_version: u32,
    request_id: String,
    target: HostTarget,
    record_version: u64,
    registry_revision: u64,
    preview_id: String,
    captured_at_unix_seconds: u64,
    expires_at_unix_seconds: u64,
    input: CreateInput,
    proposed_instance_id: String,
    compose_project: String,
    storage_path: String,
    ports: Vec<PortPlan>,
    blockers: Vec<String>,
    available_bytes: u64,
    docker_version: Option<String>,
    compose_version: Option<String>,
    allowed: bool,
    port_reservation: &'static str,
}
fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "preflight",
        OP,
        message,
        "重新读取专服列表与主机环境后重试",
    )
}
fn normalize(request: &mut PreviewRequest) -> CommandResult<()> {
    request.connection().validate(OP)?;
    crate::server_input::validate_id(&request.target.host_id, OP, "hostId")?;
    if request.expected_registry_revision > crate::server_input::MAX_REVISION {
        return Err(CommandError::invalid(
            OP,
            "registryRevision",
            "注册表版本无效",
        ));
    }
    request.input.display_name = request.input.display_name.trim().into();
    let input = &request.input;
    if input.display_name.is_empty()
        || input.display_name.chars().count() > 64
        || input.display_name.chars().any(char::is_control)
    {
        return Err(CommandError::invalid(
            OP,
            "displayName",
            "名称需为 1–64 个字符，且不能含控制字符",
        ));
    }
    if !input.autostart && input.start_after_create {
        return Err(CommandError::invalid(
            OP,
            "startAfterCreate",
            "未自动启动时不能设置创建后启动",
        ));
    }
    if input.port_policy != "auto" {
        return Err(CommandError::invalid(OP, "portPolicy", "端口策略无效"));
    }
    Ok(())
}
fn decode(output: &[u8]) -> CommandResult<Probe> {
    let invalid = || error("CREATE_PROTOCOL_INVALID", "创建预览响应格式无效");
    let envelope: Value = serde_json::from_slice(output).map_err(|_| invalid())?;
    if envelope.get("ok") == Some(&Value::Bool(true)) {
        return serde_json::from_value(envelope.get("data").cloned().ok_or_else(invalid)?)
            .map_err(|_| invalid());
    }
    if envelope.get("ok") != Some(&Value::Bool(false)) {
        return Err(invalid());
    }
    let (code, message) = match envelope.get("code").and_then(Value::as_str) {
        Some("HOST_IDENTITY_MISMATCH") => ("HOST_IDENTITY_MISMATCH", "创建目标主机身份不一致"),
        Some("REGISTRY_CHANGED" | "CREATE_METADATA_CHANGED") => {
            ("REGISTRY_CHANGED", "注册表或配置发生变化，请刷新后重新预览")
        }
        Some("CREATE_METADATA_UNSAFE") => {
            ("CREATE_METADATA_UNSAFE", "管理目录或文件类型、权限不安全")
        }
        Some("CREATE_REGISTRY_INVALID") => ("CREATE_REGISTRY_INVALID", "创建预览所需注册表无效"),
        Some("CREATE_MANIFEST_INVALID") => ("CREATE_MANIFEST_INVALID", "已有专服配置或身份无效"),
        Some("CREATE_PORT_STATE_INVALID") => ("CREATE_PORT_STATE_INVALID", "已有端口预留数据无效"),
        Some("CREATE_PROBE_LIMIT") => ("CREATE_PROBE_LIMIT", "主机检查数据超过容量限制"),
        Some("CREATE_SOCKET_INVALID") => ("CREATE_SOCKET_INVALID", "主机端口占用数据无效"),
        Some("CREATE_DOCKER_RESPONSE_INVALID") => {
            ("CREATE_DOCKER_RESPONSE_INVALID", "Docker 端口检查响应无效")
        }
        _ => ("CREATE_PROBE_FAILED", "无法完成创建预览检查"),
    };
    Err(error(code, message))
}
fn validate_probe(probe: &Probe, request: &PreviewRequest) -> CommandResult<()> {
    const BLOCKERS: [&str; 8] = [
        "PORT_RESERVATIONS_MISSING",
        "EXISTING_PORT_PLAN_INCOMPLETE",
        "PORT_EVIDENCE_UNAVAILABLE",
        "DOCKER_UNAVAILABLE",
        "DOCKER_PLATFORM_UNSUPPORTED",
        "COMPOSE_UNAVAILABLE",
        "MANAGEMENT_ROOT_UNWRITABLE",
        "PORT_PLAN_EXHAUSTED",
    ];
    let mut seen = HashSet::new();
    let mut numbers = HashSet::new();
    let ports_valid = probe.ports.iter().all(|port| {
        let range = match port.purpose.as_str() {
            "game" => 11000..=19999,
            "steamAuth" => 8766..=9765,
            "steamMaster" => 27016..=28015,
            _ => 0..=0,
        };
        ["master", "caves"].contains(&port.shard.as_str())
            && (request.input.caves_enabled || port.shard != "caves")
            && port.protocol == "udp"
            && port.bind_address == "0.0.0.0"
            && port.host_port > 0
            && range.contains(&port.host_port)
            && port.container_port == port.host_port
            && seen.insert((&port.shard, &port.purpose))
            && numbers.insert(port.host_port)
    });
    let version_valid = |value: &Option<String>| {
        value.as_ref().is_none_or(|version| {
            !version.is_empty()
                && version.len() <= 64
                && version
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b".+_-".contains(&byte))
        })
    };
    if probe.schema_version != 1
        || probe.host_id != request.target.host_id
        || probe.registry_revision != request.expected_registry_revision
        || probe.captured_at_unix_seconds == 0
        || probe.captured_at_unix_seconds > crate::server_input::MAX_REVISION
        || probe.available_bytes > crate::server_input::MAX_REVISION
        || !ports_valid
        || !(probe.ports.is_empty()
            || probe.ports.len() == if request.input.caves_enabled { 6 } else { 3 })
        || (probe.ports.is_empty() && probe.blockers.is_empty())
        || probe.blockers.len() > BLOCKERS.len()
        || probe
            .blockers
            .iter()
            .any(|item| !BLOCKERS.contains(&item.as_str()))
        || !version_valid(&probe.docker_version)
        || !version_valid(&probe.compose_version)
    {
        return Err(error(
            "CREATE_PROTOCOL_INVALID",
            "创建预览身份、端口或阻断项无效",
        ));
    }
    Ok(())
}
fn execute(session: &Session, request: &PreviewRequest) -> CommandResult<Probe> {
    let input=STANDARD.encode(serde_json::to_vec(&json!({"hostId":request.target.host_id,"expectedRegistryRevision":request.expected_registry_revision,"input":request.input})).map_err(|_|error("CREATE_PROTOCOL_INVALID","无法编码创建预览请求"))?);
    let source = format!("{SCRIPT}\nmain(json.loads(base64.b64decode('{input}')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("CREATE_CHANNEL_FAILED", "无法创建预览检查通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("CREATE_CHANNEL_FAILED", "无法配置预览检查通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法执行创建预览检查"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法提交创建预览检查"))?;
    channel
        .send_eof()
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法启动创建预览检查"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(45);
    let mut output = Vec::new();
    let mut buffer = [0; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("CREATE_PROBE_TIMEOUT", "创建预览检查超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= 32768 => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => return Err(error("CREATE_PROBE_FAILED", "无法读取创建预览结果")),
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("CREATE_PROBE_FAILED", "创建预览检查未能正常结束"))?;
    if channel
        .exit_status()
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法核对创建预览结果"))?
        != 0
    {
        return Err(error("CREATE_PROBE_FAILED", "创建预览脚本执行失败"));
    }
    decode(&output)
}
pub fn preview(database: &Path, mut request: PreviewRequest) -> CommandResult<PreviewResponse> {
    normalize(&mut request)?;
    crate::ssh::with_verified_session(database, &request.connection(), |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "创建目标主机身份不一致"));
        }
        let probe = execute(session, &request)?;
        validate_probe(&probe, &request)?;
        let candidate = uuid::Uuid::new_v4().to_string();
        let now = crate::ssh_state::now()?;
        Ok(PreviewResponse {
            schema_version: 1,
            request_id: request.request_id.clone(),
            target: request.target.clone(),
            record_version: request.expected_record_version,
            registry_revision: probe.registry_revision,
            preview_id: uuid::Uuid::new_v4().to_string(),
            captured_at_unix_seconds: now,
            expires_at_unix_seconds: now + 600,
            input: request.input.clone(),
            compose_project: format!("fh_{}", candidate.replace('-', "")),
            storage_path: format!("{ROOT}/instances/{candidate}"),
            proposed_instance_id: candidate,
            ports: probe.ports,
            blockers: probe.blockers,
            available_bytes: probe.available_bytes,
            docker_version: probe.docker_version,
            compose_version: probe.compose_version,
            allowed: false,
            port_reservation: "unreserved",
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request_value() -> Value {
        json!({"schemaVersion":1,"requestId":uuid::Uuid::new_v4().to_string(),"target":{"serverId":uuid::Uuid::new_v4().to_string(),"hostId":"11111111-1111-4111-8111-111111111111"},"expectedRecordVersion":3,"expectedRegistryRevision":4,"input":{"displayName":" 生存一服 ","autostart":true,"startAfterCreate":true,"portPolicy":"auto","cavesEnabled":false}})
    }
    #[test]
    fn request_rejects_credentials_paths_and_invalid_parameters() {
        let value = request_value();
        let mut parsed: PreviewRequest = serde_json::from_value(value.clone()).expect("request");
        assert!(normalize(&mut parsed).is_ok());
        assert_eq!(parsed.input.display_name, "生存一服");
        for field in ["token", "password", "path"] {
            let mut unsafe_value = value.clone();
            unsafe_value["input"][field] = "secret".into();
            assert!(serde_json::from_value::<PreviewRequest>(unsafe_value).is_err());
        }
        for change in [json!({"displayName":"a\nb"}), json!({"cavesEnabled":"yes"})] {
            let mut bad = value.clone();
            let (key, changed) = change
                .as_object()
                .expect("object")
                .iter()
                .next()
                .expect("change");
            bad["input"][key] = changed.clone();
            if key == "cavesEnabled" {
                assert!(serde_json::from_value::<PreviewRequest>(bad).is_err());
            } else {
                let mut parsed: PreviewRequest =
                    serde_json::from_value(bad).expect("typed request");
                assert!(normalize(&mut parsed).is_err());
            }
        }
    }
    #[test]
    fn probe_requires_target_full_ports_and_deployment_gate() {
        let mut request: PreviewRequest = serde_json::from_value(request_value()).expect("request");
        normalize(&mut request).expect("normalized");
        let mut value = json!({"ok":true,"data":{"schemaVersion":1,"hostId":request.target.host_id,"registryRevision":4,"capturedAtUnixSeconds":1700000000,"ports":[
            {"shard":"master","purpose":"game","protocol":"udp","bindAddress":"0.0.0.0","hostPort":11000,"containerPort":11000},
            {"shard":"master","purpose":"steamAuth","protocol":"udp","bindAddress":"0.0.0.0","hostPort":8766,"containerPort":8766},
            {"shard":"master","purpose":"steamMaster","protocol":"udp","bindAddress":"0.0.0.0","hostPort":27016,"containerPort":27016}],"blockers":[],"availableBytes":8000000000u64,"dockerVersion":"29.0.0","composeVersion":"2.40.0"}});
        let probe = decode(&serde_json::to_vec(&value).expect("json")).expect("probe");
        assert!(validate_probe(&probe, &request).is_ok());
        value["data"]["ports"][0]["containerPort"] = 11001.into();
        let probe = decode(&serde_json::to_vec(&value).expect("json")).expect("probe");
        assert!(validate_probe(&probe, &request).is_err());
        value["data"]["ports"] = json!([]);
        value["data"]["blockers"] = json!([]);
        let probe = decode(&serde_json::to_vec(&value).expect("json")).expect("probe");
        assert!(validate_probe(&probe, &request).is_err());
        assert!(decode(b"{\"ok\":true,\"data\":null}").is_err());
        assert!(decode(b"banner\n{\"ok\":true,\"data\":{}}").is_err());
        let failure = decode(b"{\"ok\":false,\"code\":\"REGISTRY_CHANGED\"}").expect_err("changed");
        assert_eq!(failure.code, "REGISTRY_CHANGED");
    }
}
