use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::InstanceTarget,
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "getInstanceDiagnostics";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-diagnostics.py");
const MAX_OUTPUT: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    #[serde(default = "default_log_limit")]
    pub log_limit: u32,
}

fn default_log_limit() -> u32 {
    200
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Shard {
    pub shard: String,
    pub container_id: Option<String>,
    pub status: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Health {
    pub runtime_status: String,
    pub config_revision: u64,
    pub world_revision: Option<u64>,
    pub mods_revision: Option<u64>,
    pub dst_build: Option<String>,
    pub dst_release_id: Option<String>,
    pub mods_release_id: Option<String>,
    pub docker_version: Option<String>,
    pub disk_available_bytes: Option<u64>,
    pub shards: Vec<Shard>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Logs {
    pub shard: String,
    pub lines: Vec<String>,
    pub truncated: bool,
    pub available: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FailedTask {
    pub task_id: String,
    pub operation: Option<String>,
    pub status: String,
    pub stage: Option<String>,
    pub error_code: Option<String>,
    #[serde(default)]
    pub recovery_steps: Vec<String>,
    pub created_at: Option<String>,
    pub finished_at: Option<String>,
    pub journal: Vec<Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    record_version: u64,
    captured_at: String,
    health: Health,
    logs: Vec<Logs>,
    tasks: Vec<FailedTask>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Response {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub record_version: u64,
    pub captured_at: String,
    pub health: Health,
    pub logs: Vec<Logs>,
    pub tasks: Vec<FailedTask>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "diagnostics", OP, message, "刷新诊断信息后重试")
}

fn validate(request: &Request) -> CommandResult<()> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "诊断请求版本不受支持"));
    }
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    for (field, value) in [
        ("serverId", &request.target.server_id),
        ("hostId", &request.target.host_id),
        ("instanceId", &request.target.instance_id),
    ] {
        crate::server_input::validate_id(value, OP, field)?;
    }
    if request.expected_record_version == 0
        || request.expected_record_version > crate::server_input::MAX_REVISION
    {
        return Err(error("DIAGNOSTICS_INPUT_INVALID", "主机连接版本无效"));
    }
    if request.log_limit == 0 || request.log_limit > 200 {
        return Err(error(
            "DIAGNOSTICS_INPUT_INVALID",
            "日志行数必须在 1 到 200 之间",
        ));
    }
    Ok(())
}

fn invoke(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("DIAGNOSTICS_PROTOCOL_INVALID", "诊断请求编码失败"))?,
    );
    let script = STANDARD.encode(SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nnamespace={{'__name__':'instance_diagnostics'}}\nexec(compile(base64.b64decode('{script}'),'<instance-diagnostics>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'schemaVersion':1,'ok':False,'code':'DIAGNOSTICS_FAILED'}},separators=(',',':')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("DIAGNOSTICS_CHANNEL_FAILED", "无法建立诊断通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("DIAGNOSTICS_CHANNEL_FAILED", "无法配置诊断通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("DIAGNOSTICS_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("DIAGNOSTICS_CHANNEL_FAILED", "无法提交诊断请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("DIAGNOSTICS_CHANNEL_FAILED", "无法结束诊断请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(45);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("DIAGNOSTICS_TIMEOUT", "诊断读取超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= MAX_OUTPUT => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => return Err(error("DIAGNOSTICS_CHANNEL_FAILED", "无法读取诊断响应")),
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("DIAGNOSTICS_CHANNEL_FAILED", "诊断通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("DIAGNOSTICS_PROTOCOL_INVALID", "诊断响应不是 JSON"))?;
    if envelope.get("ok") == Some(&Value::Bool(false)) {
        return Err(
            match envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("DIAGNOSTICS_FAILED")
            {
                "HOST_IDENTITY_MISMATCH" => error("HOST_IDENTITY_MISMATCH", "主机身份不一致"),
                "INSTANCE_NOT_FOUND" => error("INSTANCE_NOT_FOUND", "专服不存在"),
                "INSTANCE_IDENTITY_MISMATCH" => {
                    error("INSTANCE_IDENTITY_MISMATCH", "专服身份不一致")
                }
                _ => error("DIAGNOSTICS_FAILED", "无法读取专服诊断信息"),
            },
        );
    }
    Ok(envelope)
}

pub fn get(database: &Path, request: Request) -> CommandResult<Response> {
    validate(&request)?;
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "诊断目标主机身份不一致"));
        }
        let value = invoke(
            session,
            &serde_json::json!({
                "schemaVersion": 1,
                "requestId": request.request_id,
                "target": request.target,
                "expectedRecordVersion": request.expected_record_version,
                "logLimit": request.log_limit,
            }),
        )?;
        let remote: RemoteResponse = serde_json::from_value(value)
            .map_err(|_| error("DIAGNOSTICS_PROTOCOL_INVALID", "诊断响应字段无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.record_version != request.expected_record_version
            || remote.logs.len() > 2
            || remote.tasks.len() > 20
            || remote.logs.iter().any(|logs| logs.lines.len() > 200)
        {
            return Err(error(
                "DIAGNOSTICS_PROTOCOL_INVALID",
                "诊断响应身份或数据边界无效",
            ));
        }
        Ok(Response {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            record_version: remote.record_version,
            captured_at: remote.captured_at,
            health: remote.health,
            logs: remote.logs,
            tasks: remote.tasks,
        })
    })
}
