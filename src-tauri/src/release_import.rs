use crate::{
    dto::{CommandError, CommandResult},
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "prepareInstanceRelease";
const PREPARE: &str = include_str!("../../remote-scripts/probes/instance-release-prepare.py");
const IMPORT: &str = include_str!("../../remote-scripts/actions/instance-release-import.py");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrepareRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub expected_record_version: u64,
    pub release_id: String,
    pub catalog_digest: String,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ImportRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub expected_record_version: u64,
    pub release_id: String,
    pub catalog_digest: String,
    pub operation_id: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PrepareResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub record_version: u64,
    pub release_id: String,
    pub catalog_digest: Option<String>,
    pub status: String,
    pub blockers: Vec<String>,
    pub artifact_path: Option<String>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub task: Option<Value>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "release",
        OP,
        message,
        "核对 release staging 和 catalog 后重试",
    )
}
fn valid_id(value: &str) -> bool {
    uuid::Uuid::try_parse(value)
        .is_ok_and(|id| id.get_version_num() == 4 && id.to_string() == value)
}
fn validate_target(target: &crate::instance_creation::HostTarget) -> CommandResult<()> {
    crate::server_input::validate_id(&target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&target.host_id, OP, "hostId")?;
    Ok(())
}

fn execute(session: &Session, script: &str, request: &Value) -> CommandResult<Value> {
    let encoded = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("CREATE_RELEASE_PROTOCOL_INVALID", "release 请求编码失败"))?,
    );
    let source=format!("import base64\n{script}\nmain(json.loads(base64.b64decode('{encoded}').decode('utf-8')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法创建 release 通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法配置 release 通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "CREATE_RELEASE_RUNTIME_REQUIRED",
            "远端需要 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法提交 release 请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法结束 release 请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("CREATE_RELEASE_TIMEOUT", "release 操作超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= 256 * 1024 => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => {
                return Err(error(
                    "CREATE_RELEASE_CHANNEL_FAILED",
                    "无法读取 release 结果",
                ))
            }
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "release 通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("CREATE_RELEASE_PROTOCOL_INVALID", "release 响应无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(error(
            "CREATE_RELEASE_TASK_FAILED",
            "release 准备或导入失败",
        ));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("CREATE_RELEASE_PROTOCOL_INVALID", "release 响应缺少数据"))
}

pub fn preview(database: &Path, request: PrepareRequest) -> CommandResult<PrepareResponse> {
    validate_target(&request.target)?;
    if request.schema_version != 1 || !valid_id(&request.release_id) {
        return Err(error(
            "CREATE_RELEASE_INPUT_INVALID",
            "release 预览请求无效",
        ));
    }
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "release 目标主机不一致"));
        }
        let data = execute(
            session,
            PREPARE,
            &json!({"hostId":request.target.host_id,"releaseId":request.release_id,"catalogDigest":request.catalog_digest}),
        )?;
        Ok(PrepareResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            record_version: request.expected_record_version,
            release_id: data
                .get("releaseId")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            catalog_digest: data
                .get("catalogDigest")
                .and_then(Value::as_str)
                .map(str::to_string),
            status: data
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("blocked")
                .to_string(),
            blockers: data
                .get("blockers")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default(),
            artifact_path: data
                .get("artifactPath")
                .and_then(Value::as_str)
                .map(str::to_string),
        })
    })
}
pub fn import(database: &Path, request: ImportRequest) -> CommandResult<ImportResponse> {
    validate_target(&request.target)?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "release 导入目标不一致"));
        }
        let data = execute(
            session,
            IMPORT,
            &json!({"action":"start","hostId":request.target.host_id,"releaseId":request.release_id,"catalogDigest":request.catalog_digest,"operationId":request.operation_id}),
        )?;
        Ok(ImportResponse {
            schema_version: 1,
            request_id: request.request_id,
            server_id: request.target.server_id,
            record_version: request.expected_record_version,
            task: Some(data),
        })
    })
}
