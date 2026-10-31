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

const OP: &str = "hostMods";
const PROBE_SCRIPT: &str = include_str!("../../remote-scripts/probes/host-mods.py");
const DOWNLOAD_SCRIPT: &str = include_str!("../../remote-scripts/actions/host-mods-download.py");
const MAX_OUTPUT: usize = 4 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HostTarget {
    pub server_id: String,
    pub host_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ListRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DeleteRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub workshop_id: String,
    pub content_digest: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrepareRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
    pub workshop_ids: Vec<String>,
    #[serde(default)]
    pub source_handles: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskListRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsedBy {
    pub instance_id: String,
    pub display_name: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModItem {
    pub workshop_id: String,
    pub content_digest: String,
    pub source_handle: Option<String>,
    pub prepared_at: Option<String>,
    pub size_bytes: Option<u64>,
    pub file_count: Option<u64>,
    pub used_by: Vec<UsedBy>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ListResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub record_version: u64,
    pub items: Vec<ModItem>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeleteResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub record_version: u64,
    pub deleted: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub task: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskListResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub items: Vec<Value>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "mods", OP, message, "刷新主机 MOD 库后重试")
}

fn validate_target(
    target: &HostTarget,
    request_id: &str,
    record_version: u64,
) -> CommandResult<()> {
    crate::server_input::validate_id(request_id, OP, "requestId")?;
    crate::server_input::validate_id(&target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&target.host_id, OP, "hostId")?;
    if record_version == 0 || record_version > crate::server_input::MAX_REVISION {
        return Err(error("HOST_MOD_INPUT_INVALID", "主机连接版本无效"));
    }
    Ok(())
}

fn validate_task_target(
    target: &HostTarget,
    request_id: &str,
    record_version: u64,
    operation_id: &str,
) -> CommandResult<()> {
    validate_target(target, request_id, record_version)?;
    crate::server_input::validate_id(operation_id, OP, "operationId")
}

fn connection(target: &HostTarget, request_id: &str, record_version: u64) -> ConnectionRequest {
    ConnectionRequest {
        schema_version: 1,
        request_id: request_id.to_string(),
        server_id: target.server_id.clone(),
        expected_record_version: record_version,
    }
}

fn invoke_probe(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("HOST_MOD_PROTOCOL_INVALID", "主机 MOD 请求编码失败"))?,
    );
    let script = STANDARD.encode(PROBE_SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nnamespace={{'__name__':'host_mods_probe'}}\nexec(compile(base64.b64decode('{script}'),'<host-mods>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'ok':False,'code':'HOST_MOD_FAILED'}},separators=(',',':')))\n"
    );
    run_python(session, &source)
}

fn invoke_download(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(serde_json::to_vec(payload).map_err(|_| {
        error(
            "HOST_MOD_TASK_PROTOCOL_INVALID",
            "主机 MOD 任务请求编码失败",
        )
    })?);
    let script = STANDARD.encode(DOWNLOAD_SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nworker_source=base64.b64decode('{script}').decode('utf-8')\nnamespace={{'__name__':'host_mods_download'}}\nexec(compile(worker_source,'<host-mods-download>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')),worker_source)\nexcept Exception:\n    print(json.dumps({{'ok':False,'code':'HOST_MOD_TASK_CONTROLLER_FAILED'}},separators=(',',':')))\n"
    );
    run_python(session, &source)
}

fn run_python(session: &Session, source: &str) -> CommandResult<Value> {
    let mut channel = session
        .channel_session()
        .map_err(|_| error("HOST_MOD_CHANNEL_FAILED", "无法建立主机 MOD 通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("HOST_MOD_CHANNEL_FAILED", "无法配置主机 MOD 通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("HOST_MOD_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("HOST_MOD_CHANNEL_FAILED", "无法提交主机 MOD 请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("HOST_MOD_CHANNEL_FAILED", "无法结束主机 MOD 请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(60);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("HOST_MOD_QUERY_TIMEOUT", "主机 MOD 查询超时"));
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
            _ => return Err(error("HOST_MOD_CHANNEL_FAILED", "无法读取主机 MOD 响应")),
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("HOST_MOD_CHANNEL_FAILED", "主机 MOD 通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("HOST_MOD_PROTOCOL_INVALID", "主机 MOD 响应不是 JSON"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(
            match envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("HOST_MOD_FAILED")
            {
                "HOST_MOD_IN_USE" => error("HOST_MOD_IN_USE", "该 MOD 版本仍被实例使用，不能删除"),
                "HOST_MOD_FETCH_CAPABILITY_UNAVAILABLE" => error(
                    "HOST_MOD_FETCH_CAPABILITY_UNAVAILABLE",
                    "主机没有包含 SteamCMD Workshop 获取能力的已验证 Release",
                ),
                "HOST_MOD_RELEASE_IMAGE_MISSING" => error(
                    "HOST_MOD_RELEASE_IMAGE_MISSING",
                    "没有可用的本地 Release 镜像",
                ),
                "HOST_MOD_TASK_BUSY" | "HOST_MOD_TASK_LIMIT" => {
                    error("HOST_MOD_TASK_BUSY", "主机 MOD 准备任务已排队")
                }
                _ => error("HOST_MOD_FAILED", "主机 MOD 操作失败"),
            },
        );
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("HOST_MOD_PROTOCOL_INVALID", "主机 MOD 响应缺少数据"))
}

pub fn list(database: &Path, request: ListRequest) -> CommandResult<ListResponse> {
    validate_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
    )?;
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "主机 MOD 请求版本不受支持"));
    }
    let payload = serde_json::json!({"action":"list","schemaVersion":1,"requestId":request.request_id,
        "serverId":request.target.server_id,"hostId":request.target.host_id,
        "expectedRecordVersion":request.expected_record_version});
    with_verified_session(
        database,
        &connection(
            &request.target,
            &request.request_id,
            request.expected_record_version,
        ),
        |session, host_id| {
            if host_id.as_deref() != Some(request.target.host_id.as_str()) {
                return Err(error("HOST_IDENTITY_MISMATCH", "主机身份不一致"));
            }
            let value = invoke_probe(session, &payload)?;
            let remote: Value = serde_json::from_value(value)
                .map_err(|_| error("HOST_MOD_PROTOCOL_INVALID", "主机 MOD 查询响应无效"))?;
            if remote.get("requestId").and_then(Value::as_str) != Some(request.request_id.as_str())
                || remote.get("recordVersion").and_then(Value::as_u64)
                    != Some(request.expected_record_version)
                || remote
                    .get("target")
                    .and_then(|value| value.get("hostId"))
                    .and_then(Value::as_str)
                    != Some(request.target.host_id.as_str())
            {
                return Err(error(
                    "HOST_MOD_PROTOCOL_INVALID",
                    "主机 MOD 查询响应身份无效",
                ));
            }
            let raw_items = remote
                .get("items")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if raw_items.len() > 2000 {
                return Err(error("HOST_MOD_PROTOCOL_INVALID", "主机 MOD 列表过大"));
            }
            let mut items = Vec::with_capacity(raw_items.len());
            for item in raw_items {
                items.push(ModItem {
                    workshop_id: item
                        .get("workshopId")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    content_digest: item
                        .get("contentDigest")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    source_handle: item
                        .get("sourceHandle")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    prepared_at: item
                        .get("preparedAt")
                        .and_then(Value::as_str)
                        .map(str::to_string),
                    size_bytes: item.get("sizeBytes").and_then(Value::as_u64),
                    file_count: item.get("fileCount").and_then(Value::as_u64),
                    used_by: item
                        .get("usedBy")
                        .and_then(Value::as_array)
                        .map(|values| {
                            values
                                .iter()
                                .filter_map(|value| {
                                    Some(UsedBy {
                                        instance_id: value.get("instanceId")?.as_str()?.to_string(),
                                        display_name: value
                                            .get("displayName")
                                            .and_then(Value::as_str)
                                            .map(str::to_string),
                                    })
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                });
            }
            Ok(ListResponse {
                schema_version: 1,
                request_id: request.request_id,
                target: request.target,
                record_version: request.expected_record_version,
                items,
            })
        },
    )
}

pub fn delete(database: &Path, request: DeleteRequest) -> CommandResult<DeleteResponse> {
    validate_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
    )?;
    if request.schema_version != 1
        || request.workshop_id.is_empty()
        || request.workshop_id.len() > 20
        || !request
            .workshop_id
            .bytes()
            .all(|value| value.is_ascii_digit())
        || request.content_digest.len() != 64
        || !request
            .content_digest
            .bytes()
            .all(|value| value.is_ascii_hexdigit())
    {
        return Err(error("HOST_MOD_INPUT_INVALID", "主机 MOD 删除参数无效"));
    }
    let payload = serde_json::json!({"action":"delete","schemaVersion":1,"requestId":request.request_id,
        "serverId":request.target.server_id,"hostId":request.target.host_id,
        "expectedRecordVersion":request.expected_record_version,
        "workshopId":request.workshop_id,"contentDigest":request.content_digest});
    with_verified_session(
        database,
        &connection(
            &request.target,
            &request.request_id,
            request.expected_record_version,
        ),
        |session, host_id| {
            if host_id.as_deref() != Some(request.target.host_id.as_str()) {
                return Err(error("HOST_IDENTITY_MISMATCH", "主机身份不一致"));
            }
            let value = invoke_probe(session, &payload)?;
            Ok(DeleteResponse {
                schema_version: 1,
                request_id: request.request_id,
                target: request.target,
                record_version: request.expected_record_version,
                deleted: value.get("deleted").cloned().unwrap_or(Value::Null),
            })
        },
    )
}

pub fn prepare(database: &Path, request: PrepareRequest) -> CommandResult<TaskResponse> {
    validate_task_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
        &request.operation_id,
    )?;
    if request.schema_version != 1
        || request.workshop_ids.is_empty()
        || request.workshop_ids.len() > 50
        || request.workshop_ids.iter().any(|value| {
            value.is_empty() || value.len() > 20 || !value.bytes().all(|byte| byte.is_ascii_digit())
        })
        || request.source_handles.len() > request.workshop_ids.len()
    {
        return Err(error("HOST_MOD_INPUT_INVALID", "主机 MOD 准备参数无效"));
    }
    let payload = serde_json::json!({"action":"start","schemaVersion":1,"requestId":request.request_id,
        "serverId":request.target.server_id,"hostId":request.target.host_id,
        "expectedRecordVersion":request.expected_record_version,"operationId":request.operation_id,
        "workshopIds":request.workshop_ids,"sourceHandles":request.source_handles});
    with_verified_session(
        database,
        &connection(
            &request.target,
            &request.request_id,
            request.expected_record_version,
        ),
        |session, host_id| {
            if host_id.as_deref() != Some(request.target.host_id.as_str()) {
                return Err(error("HOST_IDENTITY_MISMATCH", "主机身份不一致"));
            }
            let value = invoke_download(session, &payload)?;
            let task = value.get("task").cloned().ok_or_else(|| {
                error(
                    "HOST_MOD_TASK_PROTOCOL_INVALID",
                    "主机 MOD 任务响应缺少任务",
                )
            })?;
            if task.get("operationId").and_then(Value::as_str)
                != Some(request.operation_id.as_str())
                || task.get("operation").and_then(Value::as_str) != Some("hostModPrepare")
            {
                return Err(error(
                    "HOST_MOD_TASK_PROTOCOL_INVALID",
                    "主机 MOD 任务编号或类型无效",
                ));
            }
            Ok(TaskResponse {
                schema_version: 1,
                request_id: request.request_id,
                target: request.target,
                task,
            })
        },
    )
}

pub fn task(database: &Path, request: TaskRequest) -> CommandResult<TaskResponse> {
    validate_task_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
        &request.operation_id,
    )?;
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "主机 MOD 请求版本不受支持"));
    }
    let payload = serde_json::json!({"action":"status","schemaVersion":1,"requestId":request.request_id,
        "serverId":request.target.server_id,"hostId":request.target.host_id,
        "expectedRecordVersion":request.expected_record_version,"operationId":request.operation_id});
    with_verified_session(
        database,
        &connection(
            &request.target,
            &request.request_id,
            request.expected_record_version,
        ),
        |session, host_id| {
            if host_id.as_deref() != Some(request.target.host_id.as_str()) {
                return Err(error("HOST_IDENTITY_MISMATCH", "主机身份不一致"));
            }
            let value = invoke_download(session, &payload)?;
            Ok(TaskResponse {
                schema_version: 1,
                request_id: request.request_id,
                target: request.target,
                task: value.get("task").cloned().unwrap_or(Value::Null),
            })
        },
    )
}

pub fn tasks(database: &Path, request: TaskListRequest) -> CommandResult<TaskListResponse> {
    validate_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
    )?;
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "主机 MOD 请求版本不受支持"));
    }
    let payload = serde_json::json!({"action":"list","schemaVersion":1,"requestId":request.request_id,
        "serverId":request.target.server_id,"hostId":request.target.host_id,
        "expectedRecordVersion":request.expected_record_version});
    with_verified_session(
        database,
        &connection(
            &request.target,
            &request.request_id,
            request.expected_record_version,
        ),
        |session, host_id| {
            if host_id.as_deref() != Some(request.target.host_id.as_str()) {
                return Err(error("HOST_IDENTITY_MISMATCH", "主机身份不一致"));
            }
            let value = invoke_download(session, &payload)?;
            Ok(TaskListResponse {
                schema_version: 1,
                request_id: request.request_id,
                target: request.target,
                items: value
                    .get("items")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default(),
            })
        },
    )
}
