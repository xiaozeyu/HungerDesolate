use crate::{
    dto::{CommandError, CommandResult},
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

const OP: &str = "manageBuildCache";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/build-cache.py");
const MAX_OUTPUT: usize = 256 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub schema_version: u32,
    pub request_id: String,
    pub action: String,
    pub server_id: String,
    pub host_id: String,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DockerCache {
    pub size: Option<String>,
    pub reclaimable: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub host_id: String,
    pub record_version: Option<u64>,
    pub captured_at: String,
    pub context_bytes: u64,
    pub build_active: bool,
    pub docker_available: bool,
    pub docker_cache: DockerCache,
    pub allowed: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CleanupResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub host_id: String,
    pub record_version: Option<u64>,
    pub captured_at: String,
    pub context_bytes: u64,
    pub docker_output: String,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "release", OP, message, "查看构建状态后重试")
}

fn validate(request: &Request) -> CommandResult<()> {
    if request.schema_version != 1 || !matches!(request.action.as_str(), "preview" | "cleanup") {
        return Err(error("BUILD_CACHE_INPUT_INVALID", "构建缓存操作无效"));
    }
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    crate::server_input::validate_id(&request.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&request.host_id, OP, "hostId")?;
    if request.expected_record_version == 0
        || request.expected_record_version > crate::server_input::MAX_REVISION
    {
        return Err(error("BUILD_CACHE_INPUT_INVALID", "主机连接版本无效"));
    }
    Ok(())
}

fn invoke(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("BUILD_CACHE_PROTOCOL_INVALID", "构建缓存请求编码失败"))?,
    );
    let script = STANDARD.encode(SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nnamespace={{'__name__':'build_cache'}}\nexec(compile(base64.b64decode('{script}'),'<build-cache>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'schemaVersion':1,'ok':False,'code':'BUILD_CACHE_FAILED'}},separators=(',',':')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("BUILD_CACHE_CHANNEL_FAILED", "无法建立构建缓存通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("BUILD_CACHE_CHANNEL_FAILED", "无法配置构建缓存通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("BUILD_CACHE_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("BUILD_CACHE_CHANNEL_FAILED", "无法提交构建缓存请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("BUILD_CACHE_CHANNEL_FAILED", "无法结束构建缓存请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(360);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("BUILD_CACHE_TIMEOUT", "清理构建缓存超时"));
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
            _ => return Err(error("BUILD_CACHE_CHANNEL_FAILED", "无法读取构建缓存响应")),
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("BUILD_CACHE_CHANNEL_FAILED", "构建缓存通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("BUILD_CACHE_PROTOCOL_INVALID", "构建缓存响应不是 JSON"))?;
    if envelope.get("ok") == Some(&Value::Bool(false)) {
        return Err(
            match envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("BUILD_CACHE_FAILED")
            {
                "HOST_IDENTITY_MISMATCH" => error("HOST_IDENTITY_MISMATCH", "主机身份不一致"),
                "BUILD_CACHE_BUSY" => {
                    error("BUILD_CACHE_BUSY", "主机正在构建镜像，暂时不能清理缓存")
                }
                "BUILD_CACHE_DOCKER_UNAVAILABLE" => {
                    error("BUILD_CACHE_DOCKER_UNAVAILABLE", "Docker 不可用")
                }
                "BUILD_CACHE_DOCKER_FAILED" => {
                    error("BUILD_CACHE_DOCKER_FAILED", "Docker 构建缓存清理失败")
                }
                _ => error("BUILD_CACHE_FAILED", "构建缓存操作失败"),
            },
        );
    }
    Ok(envelope)
}

pub fn manage(database: &Path, request: Request) -> CommandResult<Value> {
    validate(&request)?;
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "构建缓存目标主机身份不一致",
            ));
        }
        let value = invoke(
            session,
            &serde_json::json!({
                "schemaVersion": 1,
                "requestId": request.request_id,
                "action": request.action,
                "serverId": request.server_id,
                "hostId": request.host_id,
                "expectedRecordVersion": request.expected_record_version,
            }),
        )?;
        if value.get("requestId").and_then(Value::as_str) != Some(request.request_id.as_str())
            || value.get("hostId").and_then(Value::as_str) != Some(request.host_id.as_str())
            || value.get("serverId").and_then(Value::as_str) != Some(request.server_id.as_str())
        {
            return Err(error(
                "BUILD_CACHE_PROTOCOL_INVALID",
                "构建缓存响应身份无效",
            ));
        }
        if request.action == "preview" {
            serde_json::from_value::<PreviewResponse>(value)
                .map(|value| serde_json::to_value(value).unwrap_or(Value::Null))
                .map_err(|_| error("BUILD_CACHE_PROTOCOL_INVALID", "构建缓存预览字段无效"))
        } else {
            serde_json::from_value::<CleanupResponse>(value)
                .map(|value| serde_json::to_value(value).unwrap_or(Value::Null))
                .map_err(|_| error("BUILD_CACHE_PROTOCOL_INVALID", "构建缓存清理字段无效"))
        }
    })
}
