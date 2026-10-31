use crate::dto::{CommandError, CommandResult};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use ssh2::{ExtendedData, Session};
use std::{
    collections::HashSet,
    io::{Read, Write},
    time::{Duration, Instant},
};

const OP: &str = "createInstance";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-create-preview.py");

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
    pub release_id: String,
    pub release_catalog_digest: String,
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PortPlan {
    pub shard: String,
    pub purpose: String,
    pub protocol: String,
    pub bind_address: String,
    pub host_port: u16,
    pub container_port: u16,
}
#[derive(Debug)]
pub struct Check {
    pub ports: Vec<PortPlan>,
    pub blockers: Vec<String>,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Probe {
    schema_version: u32,
    host_id: String,
    registry_revision: u64,
    ports: Vec<PortPlan>,
    blockers: Vec<String>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "preflight",
        OP,
        message,
        "刷新专服列表与主机环境后重试",
    )
}
pub fn blocked(blockers: &[String]) -> Box<CommandError> {
    let mut result = error("CREATE_BLOCKED", "创建条件未满足");
    result.details = json!({"blockers": blockers});
    result
}
fn decode(output: &[u8]) -> CommandResult<Probe> {
    let invalid = || error("CREATE_PROTOCOL_INVALID", "创建检查响应格式无效");
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
            ("REGISTRY_CHANGED", "注册表或配置发生变化，请刷新后重试")
        }
        Some("CREATE_METADATA_UNSAFE") => {
            ("CREATE_METADATA_UNSAFE", "管理目录或文件类型、权限不安全")
        }
        Some("CREATE_REGISTRY_INVALID") => ("CREATE_REGISTRY_INVALID", "创建检查所需注册表无效"),
        Some("CREATE_MANIFEST_INVALID") => ("CREATE_MANIFEST_INVALID", "已有专服配置或身份无效"),
        Some("CREATE_PORT_STATE_INVALID") => ("CREATE_PORT_STATE_INVALID", "已有端口预留数据无效"),
        Some("CREATE_PROBE_LIMIT") => ("CREATE_PROBE_LIMIT", "主机检查数据超过容量限制"),
        Some("CREATE_SOCKET_INVALID") => ("CREATE_SOCKET_INVALID", "主机端口占用数据无效"),
        Some("CREATE_DOCKER_RESPONSE_INVALID") => {
            ("CREATE_DOCKER_RESPONSE_INVALID", "Docker 端口检查响应无效")
        }
        _ => ("CREATE_PROBE_FAILED", "无法完成创建检查"),
    };
    Err(error(code, message))
}
fn validate_probe(
    probe: &Probe,
    host_id: &str,
    expected_registry_revision: u64,
    caves_enabled: bool,
) -> CommandResult<()> {
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
            && (caves_enabled || port.shard != "caves")
            && port.protocol == "udp"
            && port.bind_address == "0.0.0.0"
            && port.host_port > 0
            && range.contains(&port.host_port)
            && port.container_port == port.host_port
            && seen.insert((&port.shard, &port.purpose))
            && numbers.insert(port.host_port)
    });
    if probe.schema_version != 1
        || probe.host_id != host_id
        || probe.registry_revision != expected_registry_revision
        || !ports_valid
        || !(probe.ports.is_empty() || probe.ports.len() == if caves_enabled { 6 } else { 3 })
        || (probe.ports.is_empty() && probe.blockers.is_empty())
        || probe.blockers.len() > BLOCKERS.len()
        || probe
            .blockers
            .iter()
            .any(|item| !BLOCKERS.contains(&item.as_str()))
    {
        return Err(error(
            "CREATE_PROTOCOL_INVALID",
            "创建检查身份、端口或阻断项无效",
        ));
    }
    Ok(())
}
fn execute(
    session: &Session,
    host_id: &str,
    expected_registry_revision: u64,
    input: &CreateInput,
) -> CommandResult<Probe> {
    let request = serde_json::to_vec(&json!({
        "hostId": host_id,
        "expectedRegistryRevision": expected_registry_revision,
        "input": input
    }))
    .map_err(|_| error("CREATE_PROTOCOL_INVALID", "无法编码创建检查请求"))?;
    let encoded = STANDARD.encode(request);
    let source = format!("{SCRIPT}\nmain(json.loads(base64.b64decode('{encoded}')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("CREATE_CHANNEL_FAILED", "无法创建创建检查通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("CREATE_CHANNEL_FAILED", "无法配置创建检查通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法执行创建检查"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法提交创建检查"))?;
    channel
        .send_eof()
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法启动创建检查"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(45);
    let mut output = Vec::new();
    let mut buffer = [0; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("CREATE_PROBE_TIMEOUT", "创建检查超时"));
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
            _ => return Err(error("CREATE_PROBE_FAILED", "无法读取创建检查结果")),
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("CREATE_PROBE_FAILED", "创建检查未能正常结束"))?;
    if channel
        .exit_status()
        .map_err(|_| error("CREATE_PROBE_FAILED", "无法核对创建检查结果"))?
        != 0
    {
        return Err(error("CREATE_PROBE_FAILED", "创建检查脚本执行失败"));
    }
    decode(&output)
}
pub fn check(
    session: &Session,
    host_id: &str,
    expected_registry_revision: u64,
    input: &CreateInput,
) -> CommandResult<Check> {
    let probe = execute(session, host_id, expected_registry_revision, input)?;
    validate_probe(
        &probe,
        host_id,
        expected_registry_revision,
        input.caves_enabled,
    )?;
    Ok(Check {
        ports: probe.ports,
        blockers: probe.blockers,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input_value() -> Value {
        json!({"displayName":" 生存一服 ","autostart":true,"startAfterCreate":false,"portPolicy":"auto","cavesEnabled":false,"releaseId":"22222222-2222-4222-8222-222222222222","releaseCatalogDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})
    }
    #[test]
    fn create_input_rejects_credentials_paths_and_unknown_fields() {
        let value = input_value();
        let parsed: CreateInput = serde_json::from_value(value).expect("input");
        assert_eq!(parsed.display_name, " 生存一服 ");
        for field in ["token", "password", "path"] {
            let mut unsafe_value = input_value();
            unsafe_value[field] = "secret".into();
            assert!(serde_json::from_value::<CreateInput>(unsafe_value).is_err());
        }
    }
    #[test]
    fn probe_requires_target_full_ports_and_deployment_gate() {
        let host_id = "11111111-1111-4111-8111-111111111111";
        let mut value = json!({"ok":true,"data":{"schemaVersion":1,"hostId":host_id,"registryRevision":4,"capturedAtUnixSeconds":1700000000,"ports":[
            {"shard":"master","purpose":"game","protocol":"udp","bindAddress":"0.0.0.0","hostPort":11000,"containerPort":11000},
            {"shard":"master","purpose":"steamAuth","protocol":"udp","bindAddress":"0.0.0.0","hostPort":8766,"containerPort":8766},
            {"shard":"master","purpose":"steamMaster","protocol":"udp","bindAddress":"0.0.0.0","hostPort":27016,"containerPort":27016}],"blockers":[],"availableBytes":8000000000u64,"dockerVersion":"29.0.0","composeVersion":"2.40.0"}});
        let probe = decode(&serde_json::to_vec(&value).expect("json")).expect("probe");
        assert!(validate_probe(&probe, host_id, 4, false).is_ok());
        assert!(validate_probe(&probe, host_id, 5, false).is_err());
        value["data"]["ports"][0]["containerPort"] = 11001.into();
        let probe = decode(&serde_json::to_vec(&value).expect("json")).expect("probe");
        assert!(validate_probe(&probe, host_id, 4, false).is_err());
        value["data"]["ports"] = json!([]);
        value["data"]["blockers"] = json!([]);
        let probe = decode(&serde_json::to_vec(&value).expect("json")).expect("probe");
        assert!(validate_probe(&probe, host_id, 4, false).is_err());
        assert!(decode(b"{\"ok\":true,\"data\":null}").is_err());
        assert!(decode(b"banner\n{\"ok\":true,\"data\":{}}").is_err());
        let failure = decode(b"{\"ok\":false,\"code\":\"REGISTRY_CHANGED\"}").expect_err("changed");
        assert_eq!(failure.code, "REGISTRY_CHANGED");
    }
    #[test]
    fn blockers_are_reported_with_whitelisted_codes() {
        let failure = blocked(&["PORT_PLAN_EXHAUSTED".into()]);
        assert_eq!(failure.code, "CREATE_BLOCKED");
        assert_eq!(failure.details["blockers"][0], "PORT_PLAN_EXHAUSTED");
        assert_eq!(failure.operation, "createInstance");
    }
}
