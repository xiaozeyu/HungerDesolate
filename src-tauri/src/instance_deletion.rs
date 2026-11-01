use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::InstanceTarget,
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

const OP: &str = "deleteInstance";
const SCRIPT: &str = include_str!("../../remote-scripts/actions/instance-delete.py");
const MAX_OUTPUT: usize = 128 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreviewRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_registry_revision: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DeleteRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_registry_revision: u64,
    pub plan_id: String,
    pub operation_id: String,
    pub typed_name: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ContainerResource {
    id: String,
    role: String,
    status: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NetworkResource {
    id: String,
    name: String,
    endpoint_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemotePreview {
    host_id: String,
    instance_id: String,
    registry_revision: u64,
    display_name: String,
    lifecycle: String,
    containers: Vec<ContainerResource>,
    networks: Vec<NetworkResource>,
    data_present: bool,
    world_data: bool,
    archived: bool,
    blockers: Vec<String>,
    allowed: bool,
    plan_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteDeleteResult {
    host_id: String,
    instance_id: String,
    registry_revision: u64,
    released_reservations: u64,
    removed: bool,
    retired: bool,
    removed_containers: u64,
    removed_networks: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub record_version: u64,
    pub registry_revision: u64,
    pub display_name: String,
    pub lifecycle: String,
    pub containers: Vec<ContainerResource>,
    pub networks: Vec<NetworkResource>,
    pub data_present: bool,
    pub world_data: bool,
    pub archived: bool,
    pub blockers: Vec<String>,
    pub allowed: bool,
    pub plan_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DeleteResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub record_version: u64,
    pub registry_revision: u64,
    pub released_reservations: u64,
    pub removed: bool,
    pub retired: bool,
    pub removed_containers: u64,
    pub removed_networks: u64,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "instance_deletion",
        OP,
        message,
        "刷新专服列表并重新预览；运行中或归属不匹配的 Docker 资源不会被删除",
    )
}

fn remote_error(envelope: &Value) -> Box<CommandError> {
    let code = envelope
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let (stable, message) = match code {
        "INSTANCE_NOT_FOUND" => ("INSTANCE_NOT_FOUND", "专服记录已不存在，请刷新列表"),
        "REGISTRY_CHANGED" => ("REGISTRY_CHANGED", "专服列表已变化，请重新预览删除影响"),
        "INSTANCE_CONFIRMATION_MISMATCH" => {
            ("INSTANCE_CONFIRMATION_MISMATCH", "确认名称与专服记录不一致")
        }
        "INSTANCE_DELETE_PLAN_STALE" => (
            "INSTANCE_DELETE_PLAN_STALE",
            "Docker 资源或实例数据已变化，请重新预览",
        ),
        "INSTANCE_DELETE_NOT_ALLOWED" => ("INSTANCE_DELETE_NOT_ALLOWED", "当前专服状态不允许删除"),
        "INSTANCE_DELETE_STATE_INVALID" => (
            "INSTANCE_DELETE_STATE_INVALID",
            "远端注册表、任务或 Docker 状态文件无效，未继续删除",
        ),
        "INSTANCE_DELETE_INPUT_INVALID" => (
            "INSTANCE_DELETE_INPUT_INVALID",
            "删除预览参数无效，请刷新列表后重试",
        ),
        "INSTANCE_DELETE_ACTION_INVALID" => (
            "INSTANCE_DELETE_ACTION_INVALID",
            "删除操作类型不匹配，请更新桌面应用",
        ),
        "INSTANCE_DELETE_BUSY" => (
            "INSTANCE_DELETE_BUSY",
            "专服正在执行其他任务，请等待任务结束后重试",
        ),
        "INSTANCE_DELETE_ACTIVE_TASK" | "ACTIVE_TASK" => {
            ("INSTANCE_DELETE_ACTIVE_TASK", "专服仍有活动任务，不能删除")
        }
        "CONTAINER_RUNNING" => (
            "INSTANCE_DELETE_CONTAINER_RUNNING",
            "实例仍有运行中的容器，请先安全停止",
        ),
        "TASK_STATUS_MISSING" => (
            "INSTANCE_DELETE_TASK_UNKNOWN",
            "创建任务状态缺失，现场待核实",
        ),
        "INSTANCE_DELETE_RESOURCE_IDENTITY_INVALID" => (
            "INSTANCE_DELETE_RESOURCE_IDENTITY_INVALID",
            "Docker 资源归属无法确认，未删除资源",
        ),
        "INSTANCE_DELETE_DATA_UNSAFE" => (
            "INSTANCE_DELETE_DATA_UNSAFE",
            "实例目录包含不安全路径或权限，未移动数据",
        ),
        "INSTANCE_DELETE_DATA_LIMIT" => (
            "INSTANCE_DELETE_DATA_LIMIT",
            "实例目录项目过多，不能安全生成删除预览",
        ),
        "INSTANCE_DELETE_DATA_CONFLICT" => (
            "INSTANCE_DELETE_DATA_CONFLICT",
            "归档目录已存在，未覆盖已有数据",
        ),
        "INSTANCE_DELETE_RESOURCES_REMAIN" => (
            "INSTANCE_DELETE_RESOURCES_REMAIN",
            "仍有 Docker 资源关联实例，未释放端口",
        ),
        "INSTANCE_DELETE_DOCKER_UNAVAILABLE" => (
            "INSTANCE_DELETE_DOCKER_UNAVAILABLE",
            "无法核对主机 Docker 资源",
        ),
        "INSTANCE_DELETE_TASK_STATE_UNKNOWN" => (
            "INSTANCE_DELETE_TASK_STATE_UNKNOWN",
            "无法确认任务是否仍在运行",
        ),
        "OPERATION_ID_REUSED" => ("OPERATION_ID_REUSED", "操作编号已用于不同的删除请求"),
        "ROOT_REQUIRED" => ("ROOT_REQUIRED", "删除实例资源需要 root Docker 环境"),
        "HOST_IDENTITY_MISMATCH" => ("HOST_IDENTITY_MISMATCH", "目标主机身份不一致"),
        _ => (
            "INSTANCE_DELETE_FAILED",
            "删除未完成，实例数据仍保留；刷新后重新核对现场",
        ),
    };
    let mut result = error(stable, message);
    if code.len() <= 96
        && code
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    {
        result.details = json!({"remoteCode":code});
    }
    if stable == "INSTANCE_DELETE_FAILED" {
        let details = envelope.get("details").and_then(Value::as_object);
        let exception = details
            .and_then(|value| value.get("exception"))
            .and_then(Value::as_str)
            .filter(|value| {
                [
                    "OSError",
                    "FileNotFoundError",
                    "PermissionError",
                    "NotADirectoryError",
                    "IsADirectoryError",
                    "ValueError",
                    "TypeError",
                    "KeyError",
                    "AttributeError",
                ]
                .contains(value)
            });
        let line = details
            .and_then(|value| value.get("line"))
            .and_then(Value::as_u64)
            .filter(|value| *value > 0 && *value <= 10_000);
        let errno = details
            .and_then(|value| value.get("errno"))
            .and_then(Value::as_i64)
            .filter(|value| (1..=4095).contains(value));
        result.details =
            json!({"remoteCode":code,"pythonException":exception,"pythonLine":line,"errno":errno});
    }
    result
}

fn execute(session: &Session, request: &Value) -> CommandResult<Value> {
    let encoded = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("INSTANCE_DELETE_PROTOCOL_INVALID", "删除请求编码失败"))?,
    );
    let source = format!(
        "import base64\n{SCRIPT}\nmain(json.loads(base64.b64decode('{encoded}').decode('utf-8')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("INSTANCE_DELETE_CHANNEL_FAILED", "无法创建删除任务通道"))?;
    channel
        .handle_extended_data(ExtendedData::Normal)
        .map_err(|_| error("INSTANCE_DELETE_CHANNEL_FAILED", "无法配置删除任务通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "INSTANCE_DELETE_RUNTIME_REQUIRED",
            "远端需要 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("INSTANCE_DELETE_CHANNEL_FAILED", "无法提交删除请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("INSTANCE_DELETE_CHANNEL_FAILED", "无法结束删除请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(60);
    let mut output = Vec::new();
    let mut stderr = Vec::new();
    let mut stderr_stream = channel.stderr();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error(
                "INSTANCE_DELETE_TIMEOUT",
                "删除资源核对超时，现场保留",
            ));
        }
        match channel.read(&mut buffer) {
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= MAX_OUTPUT => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => return Err(error("INSTANCE_DELETE_CHANNEL_FAILED", "无法读取删除结果")),
        }
        match stderr_stream.read(&mut buffer) {
            Ok(count) if count > 0 && stderr.len() + count <= 8 * 1024 => {
                stderr.extend_from_slice(&buffer[..count])
            }
            Ok(_) => {}
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(_) => return Err(error("INSTANCE_DELETE_CHANNEL_FAILED", "无法读取删除诊断")),
        }
        if channel.eof() {
            break;
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("INSTANCE_DELETE_CHANNEL_FAILED", "删除通道未正常结束"))?;
    if output.is_empty() {
        let text = String::from_utf8_lossy(&stderr).to_ascii_lowercase();
        let code = if text.contains("syntaxerror") || text.contains("indentationerror") {
            "INSTANCE_DELETE_SCRIPT_INVALID"
        } else if text.contains("permission denied") {
            "INSTANCE_DELETE_PERMISSION_DENIED"
        } else {
            "INSTANCE_DELETE_SCRIPT_FAILED"
        };
        return Err(error(code, "远端删除脚本未返回结果，现场保留"));
    }
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("INSTANCE_DELETE_PROTOCOL_INVALID", "远端删除响应格式无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(remote_error(&envelope));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("INSTANCE_DELETE_PROTOCOL_INVALID", "远端删除响应缺少数据"))
}

fn validate_target(
    schema_version: u32,
    request_id: &str,
    target: &InstanceTarget,
    expected_record_version: u64,
) -> CommandResult<ConnectionRequest> {
    if schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "删除请求版本不受支持"));
    }
    crate::server_input::validate_id(request_id, OP, "requestId")?;
    crate::server_input::validate_id(&target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&target.host_id, OP, "hostId")?;
    crate::server_input::validate_id(&target.instance_id, OP, "instanceId")?;
    if expected_record_version > crate::server_input::MAX_REVISION {
        return Err(error("INSTANCE_DELETE_INPUT_INVALID", "连接版本无效"));
    }
    Ok(ConnectionRequest {
        schema_version,
        request_id: request_id.to_string(),
        server_id: target.server_id.clone(),
        expected_record_version,
    })
}

fn preview_payload(request: &PreviewRequest) -> Value {
    json!({"schemaVersion":1,"action":"preview","hostId":request.target.host_id,
        "instanceId":request.target.instance_id,"expectedRegistryRevision":request.expected_registry_revision})
}

fn delete_payload(request: &DeleteRequest) -> Value {
    json!({"schemaVersion":1,"action":"delete","hostId":request.target.host_id,
        "instanceId":request.target.instance_id,"expectedRegistryRevision":request.expected_registry_revision,
        "planId":request.plan_id,"operationId":request.operation_id,"typedName":request.typed_name})
}

pub fn preview(database: &Path, request: PreviewRequest) -> CommandResult<PreviewResponse> {
    let connection = validate_target(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "删除目标主机身份不一致"));
        }
        let data = execute(session, &preview_payload(&request))?;
        let remote: RemotePreview = serde_json::from_value(data)
            .map_err(|_| error("INSTANCE_DELETE_PROTOCOL_INVALID", "删除预览字段无效"))?;
        if remote.host_id != request.target.host_id
            || remote.instance_id != request.target.instance_id
            || remote.registry_revision != request.expected_registry_revision
        {
            return Err(error(
                "INSTANCE_DELETE_PROTOCOL_INVALID",
                "删除预览身份不匹配",
            ));
        }
        Ok(PreviewResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            record_version: request.expected_record_version,
            registry_revision: remote.registry_revision,
            display_name: remote.display_name,
            lifecycle: remote.lifecycle,
            containers: remote.containers,
            networks: remote.networks,
            data_present: remote.data_present,
            archived: remote.archived,
            blockers: remote.blockers,
            world_data: remote.world_data,
            allowed: remote.allowed,
            plan_id: remote.plan_id,
        })
    })
}

pub fn delete(database: &Path, request: DeleteRequest) -> CommandResult<DeleteResponse> {
    let connection = validate_target(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    if request.plan_id.len() != 64
        || !request.plan_id.bytes().all(|byte| byte.is_ascii_hexdigit())
        || request.typed_name.trim().is_empty()
        || request.typed_name.chars().count() > 128
    {
        return Err(error("INSTANCE_DELETE_INPUT_INVALID", "删除确认请求无效"));
    }
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "删除目标主机身份不一致"));
        }
        let data = execute(session, &delete_payload(&request))?;
        let remote: RemoteDeleteResult = serde_json::from_value(data)
            .map_err(|_| error("INSTANCE_DELETE_PROTOCOL_INVALID", "删除结果字段无效"))?;
        if remote.host_id != request.target.host_id
            || remote.instance_id != request.target.instance_id
            || !remote.removed
        {
            return Err(error(
                "INSTANCE_DELETE_PROTOCOL_INVALID",
                "删除结果身份不匹配",
            ));
        }
        Ok(DeleteResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            record_version: request.expected_record_version,
            registry_revision: remote.registry_revision,
            released_reservations: remote.released_reservations,
            removed: remote.removed,
            retired: remote.retired,
            removed_containers: remote.removed_containers,
            removed_networks: remote.removed_networks,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::{delete_payload, preview_payload, remote_error, DeleteRequest, PreviewRequest};
    use crate::instance_dto::InstanceTarget;

    fn target() -> InstanceTarget {
        InstanceTarget {
            server_id: "11111111-1111-4111-8111-111111111111".into(),
            host_id: "22222222-2222-4222-8222-222222222222".into(),
            instance_id: "33333333-3333-4333-8333-333333333333".into(),
        }
    }

    #[test]
    fn remote_preview_and_delete_requests_include_schema_version() {
        let preview = PreviewRequest {
            schema_version: 1,
            request_id: "44444444-4444-4444-8444-444444444444".into(),
            target: target(),
            expected_record_version: 1,
            expected_registry_revision: 2,
        };
        assert_eq!(preview_payload(&preview)["schemaVersion"], 1);
        assert_eq!(preview_payload(&preview)["action"], "preview");

        let deletion = DeleteRequest {
            schema_version: 1,
            request_id: "55555555-5555-4555-8555-555555555555".into(),
            target: target(),
            expected_record_version: 1,
            expected_registry_revision: 2,
            plan_id: "a".repeat(64),
            operation_id: "66666666-6666-4666-8666-666666666666".into(),
            typed_name: "测试服".into(),
        };
        assert_eq!(delete_payload(&deletion)["schemaVersion"], 1);
        assert_eq!(delete_payload(&deletion)["action"], "delete");
    }

    #[test]
    fn unmapped_remote_codes_are_preserved_for_delete_diagnostics() {
        let error = remote_error(&serde_json::json!({"code":"INSTANCE_DELETE_STATE_INVALID"}));
        assert_eq!(error.code, "INSTANCE_DELETE_STATE_INVALID");
        assert_eq!(error.details["remoteCode"], "INSTANCE_DELETE_STATE_INVALID");
    }

    #[test]
    fn generic_delete_failures_keep_only_allowlisted_diagnostics() {
        let error = remote_error(&serde_json::json!({
            "code":"INSTANCE_DELETE_FAILED",
            "details":{"exception":"PermissionError","line":310,"errno":13,"path":"/private/path"}
        }));
        assert_eq!(error.details["pythonException"], "PermissionError");
        assert_eq!(error.details["pythonLine"], 310);
        assert_eq!(error.details["errno"], 13);
        assert!(error.details.get("path").is_none());
    }
}
