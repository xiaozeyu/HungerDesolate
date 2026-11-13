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

const OP_CHECK: &str = "checkDstUpdates";
const OP_PLAN: &str = "previewInstanceUpdate";
const OP_UPDATE: &str = "runInstanceUpdate";
const CHECK_SCRIPT: &str = include_str!("../../remote-scripts/probes/dst-update-check.py");
const PLAN_SCRIPT: &str = include_str!("../../remote-scripts/actions/update-plan.py");
const UPDATE_SCRIPT: &str = include_str!("../../remote-scripts/actions/update-task.py");
const RESTORE_SCRIPT: &str = include_str!("../../remote-scripts/actions/restore-task.py");
const ARCHIVE_SCRIPT: &str = include_str!("../../remote-scripts/actions/backup-archive.py");
const RELEASE_SCRIPT: &str = include_str!("../../remote-scripts/actions/instance-release.py");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HostUpdateRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub host_id: String,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HostTarget {
    pub server_id: String,
    pub host_id: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CandidateRelease {
    pub release_id: String,
    pub build: String,
    pub image_digest: String,
    pub runtime_verified: bool,
    pub created_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LatestBuild {
    pub build: String,
    pub checked_at: String,
    pub source: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CheckError {
    pub code: String,
    pub message: String,
    pub at: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HostDstUpdateStatus {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub latest: Option<LatestBuild>,
    pub candidate_releases: Vec<CandidateRelease>,
    pub last_check_error: Option<CheckError>,
    pub checking: bool,
    pub stage: Option<String>,
    pub started_at: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreviewRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub target_release_id: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CurrentInstance {
    pub release_id: Option<String>,
    pub build: Option<String>,
    pub image_digest: Option<String>,
    pub runtime_status: String,
    pub running_shards: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TargetRelease {
    pub release_id: String,
    pub build: Option<String>,
    pub image_digest: Option<String>,
    pub runtime_verified: bool,
    pub created_at: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackupPlan {
    pub required: bool,
    pub capture_mode: String,
    pub scope: String,
    pub estimated_bytes: u64,
    pub available_bytes: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RollbackPlan {
    pub available: bool,
    pub reason: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdatePreview {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub plan_id: String,
    pub operation: String,
    pub base_revision: u64,
    pub current: CurrentInstance,
    pub target_release: TargetRelease,
    pub shards: Vec<String>,
    pub stop_required: bool,
    pub backup: BackupPlan,
    pub rollback: RollbackPlan,
    pub warnings: Vec<String>,
    pub blockers: Vec<String>,
    pub allowed: bool,
    pub expires_at: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RunRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub plan_id: String,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskStatusRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskListRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteStatus {
    latest: Option<LatestBuild>,
    candidate_releases: Vec<CandidateRelease>,
    last_check_error: Option<CheckError>,
    checking: bool,
    stage: Option<String>,
    started_at: Option<String>,
}

fn error(code: &'static str, operation: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "release", operation, message, "刷新主机状态后重试")
}

fn work_error(
    code: &'static str,
    operation: &'static str,
    message: &'static str,
) -> Box<CommandError> {
    CommandError::new(
        code,
        "update",
        operation,
        message,
        "核对任务阶段和保护点后再操作",
    )
}

fn validate_host(request: &HostUpdateRequest) -> CommandResult<()> {
    if request.schema_version != 1
        || request.expected_record_version == 0
        || request.expected_record_version > crate::server_input::MAX_REVISION
    {
        return Err(error(
            "DST_UPDATE_INPUT_INVALID",
            OP_CHECK,
            "更新检查请求无效",
        ));
    }
    crate::server_input::validate_id(&request.request_id, OP_CHECK, "requestId")?;
    crate::server_input::validate_id(&request.server_id, OP_CHECK, "serverId")?;
    crate::server_input::validate_id(&request.host_id, OP_CHECK, "hostId")?;
    Ok(())
}

fn validate_target(
    request_id: &str,
    target: &InstanceTarget,
    operation: &'static str,
) -> CommandResult<()> {
    crate::server_input::validate_id(request_id, operation, "requestId")?;
    crate::server_input::validate_id(&target.server_id, operation, "serverId")?;
    crate::server_input::validate_id(&target.host_id, operation, "hostId")?;
    crate::server_input::validate_id(&target.instance_id, operation, "instanceId")?;
    Ok(())
}

fn connection(request_id: &str, server_id: &str, record_version: u64) -> ConnectionRequest {
    ConnectionRequest {
        schema_version: 1,
        request_id: request_id.to_string(),
        server_id: server_id.to_string(),
        expected_record_version: record_version,
    }
}

fn invoke(session: &Session, program: String, timeout: Duration) -> CommandResult<Value> {
    let mut channel = session
        .channel_session()
        .map_err(|_| work_error("UPDATE_CHANNEL_FAILED", OP_UPDATE, "无法建立更新通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| work_error("UPDATE_CHANNEL_FAILED", OP_UPDATE, "无法配置更新通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        work_error(
            "UPDATE_RUNTIME_REQUIRED",
            OP_UPDATE,
            "远端需要 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(program.as_bytes())
        .map_err(|_| work_error("UPDATE_CHANNEL_FAILED", OP_UPDATE, "无法提交更新请求"))?;
    channel
        .send_eof()
        .map_err(|_| work_error("UPDATE_CHANNEL_FAILED", OP_UPDATE, "无法结束更新请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + timeout;
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            session.set_blocking(true);
            return Err(work_error("UPDATE_TIMEOUT", OP_UPDATE, "更新请求超时"));
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
                session.set_blocking(true);
                return Err(work_error(
                    "UPDATE_CHANNEL_FAILED",
                    OP_UPDATE,
                    "无法读取更新响应",
                ));
            }
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| work_error("UPDATE_CHANNEL_FAILED", OP_UPDATE, "更新通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| work_error("UPDATE_PROTOCOL_INVALID", OP_UPDATE, "更新响应不是 JSON"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        let code = envelope
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("UPDATE_FAILED");
        return Err(map_error(code, envelope.get("details")));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| work_error("UPDATE_PROTOCOL_INVALID", OP_UPDATE, "更新响应缺少数据"))
}

fn map_error(code: &str, details: Option<&Value>) -> Box<CommandError> {
    let (static_code, message) = match code {
        "HOST_IDENTITY_MISMATCH" => ("HOST_IDENTITY_MISMATCH", "主机身份不一致"),
        "DST_UPDATE_CHECK_FAILED" => (
            "DST_UPDATE_CHECK_FAILED",
            "无法从 SteamCMD 获取最新 DST build",
        ),
        "DST_UPDATE_CHECK_TIMEOUT" => (
            "DST_UPDATE_CHECK_TIMEOUT",
            "SteamCMD 查询超时，请检查主机网络后重试",
        ),
        "DST_UPDATE_CHECK_DOCKER_INCOMPATIBLE" => (
            "DST_UPDATE_CHECK_DOCKER_INCOMPATIBLE",
            "Docker Engine 29.4.2 会阻断 SteamCMD，请升级到 29.4.3 或更新版本",
        ),
        "DST_UPDATE_CHECK_INVALID" => ("DST_UPDATE_CHECK_INVALID", "SteamCMD 返回的 buildid 无效"),
        "DST_UPDATE_CHECK_UNAVAILABLE" => (
            "DST_UPDATE_CHECK_UNAVAILABLE",
            "没有可用于检查的已验证 Release 镜像",
        ),
        "DST_UPDATE_CACHE_INVALID" => ("DST_UPDATE_CACHE_INVALID", "更新检查缓存无效"),
        "DST_UPDATE_INPUT_INVALID" | "UPDATE_PLAN_INPUT_INVALID" | "UPDATE_TASK_INPUT_INVALID" => {
            ("DST_UPDATE_INPUT_INVALID", "更新请求参数无效")
        }
        "UPDATE_REVISION_CONFLICT" => ("UPDATE_REVISION_CONFLICT", "专服配置已变化，请刷新后重试"),
        "INSTANCE_BUSY" => ("INSTANCE_BUSY", "专服正由其他维护操作占用"),
        "UPDATE_TARGET_NOT_APPLICABLE" => ("UPDATE_TARGET_NOT_APPLICABLE", "专服当前不可切换"),
        "UPDATE_PLAN_STALE" => ("UPDATE_PLAN_STALE", "预览已过期，请重新预览"),
        "UPDATE_TARGET_RELEASE_NOT_FOUND" => {
            ("UPDATE_TARGET_RELEASE_NOT_FOUND", "目标 Release 不存在")
        }
        "UPDATE_TARGET_RELEASE_NOT_VERIFIED" => (
            "UPDATE_TARGET_RELEASE_NOT_VERIFIED",
            "目标 Release 未通过验证",
        ),
        "UPDATE_TARGET_RELEASE_IMAGE_MISSING" => (
            "UPDATE_TARGET_RELEASE_IMAGE_MISSING",
            "目标 Release 镜像不可用",
        ),
        "UPDATE_SAME_RELEASE" => ("UPDATE_SAME_RELEASE", "目标 Release 与当前版本相同"),
        "UPDATE_TARGET_BUILD_OLDER" => ("UPDATE_TARGET_BUILD_OLDER", "不支持降级到更低 build"),
        "UPDATE_TARGET_BUILD_UNKNOWN" => ("UPDATE_TARGET_BUILD_UNKNOWN", "无法比较目标 build"),
        "UPDATE_RUNTIME_UNKNOWN" | "UPDATE_RUNTIME_DEGRADED" => {
            ("UPDATE_RUNTIME_INVALID", "专服运行状态不稳定，先对账或修复")
        }
        "UPDATE_SPACE_INSUFFICIENT" => ("UPDATE_SPACE_INSUFFICIENT", "磁盘空间不足"),
        "DST_UPDATE_BLOCKED" => ("DST_UPDATE_BLOCKED", "切换预览被阻断"),
        "UPDATE_STOP_FAILED" => ("UPDATE_STOP_FAILED", "停止分片失败"),
        "UPDATE_VERIFY_FAILED" => ("UPDATE_VERIFY_FAILED", "切换后运行验证失败"),
        "UPDATE_ISOLATION_CHANGED" => ("UPDATE_ISOLATION_CHANGED", "其他专服文件发生变化"),
        "UPDATE_TASK_INTERRUPTED" => ("UPDATE_TASK_INTERRUPTED", "更新任务中断"),
        "UPDATE_CANCELLED" => ("UPDATE_CANCELLED", "更新已取消"),
        "UPDATE_CONTROLLER_START_FAILED" => {
            ("UPDATE_CONTROLLER_START_FAILED", "无法启动更新 worker")
        }
        _ => ("UPDATE_FAILED", "DST 切换失败"),
    };
    let mut value = CommandError::new(
        static_code,
        "update",
        OP_UPDATE,
        message,
        "核对任务阶段和保护点后再操作",
    );
    value.details = details.cloned().unwrap_or(Value::Null);
    value
}

fn host_program(action: &str, request_id: &str, server_id: &str, host_id: &str) -> String {
    let script = STANDARD.encode(CHECK_SCRIPT.as_bytes());
    let request = STANDARD.encode(
        serde_json::to_vec(
            &json!({"schemaVersion":1,"action":action,"requestId":request_id,
            "serverId":server_id,"hostId":host_id}),
        )
        .expect("request serialization"),
    );
    format!("import base64,json\nnamespace={{'__name__':'dst_update_check'}}\nexec(compile(base64.b64decode('{script}'),'<update-check>','exec'),namespace)\nnamespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')))\n")
}

fn status_from(
    value: Value,
    request_id: &str,
    host_id: &str,
) -> CommandResult<HostDstUpdateStatus> {
    let remote: RemoteStatus = serde_json::from_value(value).map_err(|_| {
        error(
            "DST_UPDATE_PROTOCOL_INVALID",
            OP_CHECK,
            "更新检查响应字段无效",
        )
    })?;
    Ok(HostDstUpdateStatus {
        schema_version: 1,
        request_id: request_id.to_string(),
        target: HostTarget {
            server_id: String::new(),
            host_id: host_id.to_string(),
        },
        latest: remote.latest,
        candidate_releases: remote.candidate_releases,
        last_check_error: remote.last_check_error,
        checking: remote.checking,
        stage: remote.stage,
        started_at: remote.started_at,
    })
}

pub fn get_status(
    database: &Path,
    request: HostUpdateRequest,
) -> CommandResult<HostDstUpdateStatus> {
    validate_host(&request)?;
    let connection = connection(
        &request.request_id,
        &request.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_CHECK,
                "更新检查主机身份不一致",
            ));
        }
        let value = invoke(
            session,
            host_program(
                "status",
                &request.request_id,
                &request.server_id,
                &request.host_id,
            ),
            Duration::from_secs(30),
        )?;
        let mut status = status_from(value, &request.request_id, &request.host_id)?;
        status.target.server_id = request.server_id.clone();
        Ok(status)
    })
}

pub fn check(database: &Path, request: HostUpdateRequest) -> CommandResult<HostDstUpdateStatus> {
    validate_host(&request)?;
    let connection = connection(
        &request.request_id,
        &request.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_CHECK,
                "更新检查主机身份不一致",
            ));
        }
        let value = invoke(
            session,
            host_program(
                "check",
                &request.request_id,
                &request.server_id,
                &request.host_id,
            ),
            Duration::from_secs(720),
        )?;
        let mut status = status_from(value, &request.request_id, &request.host_id)?;
        status.target.server_id = request.server_id.clone();
        Ok(status)
    })
}

fn preview_program(request: &PreviewRequest) -> String {
    let script = STANDARD.encode(PLAN_SCRIPT.as_bytes());
    let payload = STANDARD.encode(
        serde_json::to_vec(&json!({"schemaVersion":1,"requestId":request.request_id,
            "target":request.target,"expectedRevision":request.expected_revision,
            "targetReleaseId":request.target_release_id}))
        .expect("request serialization"),
    );
    format!("import base64,json\nnamespace={{'__name__':'update_plan'}}\nexec(compile(base64.b64decode('{script}'),'<update-plan>','exec'),namespace)\nnamespace['main'](json.loads(base64.b64decode('{payload}').decode('utf-8')))\n")
}

pub fn preview(database: &Path, request: PreviewRequest) -> CommandResult<UpdatePreview> {
    if request.schema_version != 1
        || request.expected_record_version == 0
        || request.expected_record_version > crate::server_input::MAX_REVISION
        || request.expected_revision > crate::server_input::MAX_REVISION
    {
        return Err(error(
            "DST_UPDATE_INPUT_INVALID",
            OP_PLAN,
            "更新预览请求无效",
        ));
    }
    validate_target(&request.request_id, &request.target, OP_PLAN)?;
    crate::server_input::validate_id(&request.target_release_id, OP_PLAN, "targetReleaseId")?;
    let connection = connection(
        &request.request_id,
        &request.target.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_PLAN,
                "更新预览主机身份不一致",
            ));
        }
        let value = invoke(session, preview_program(&request), Duration::from_secs(60))?;
        let preview: UpdatePreview = serde_json::from_value(value)
            .map_err(|_| error("DST_UPDATE_PROTOCOL_INVALID", OP_PLAN, "更新预览字段无效"))?;
        if preview.schema_version != 1
            || preview.request_id != request.request_id
            || preview.target.server_id != request.target.server_id
            || preview.target.host_id != request.target.host_id
            || preview.target.instance_id != request.target.instance_id
            || preview.plan_id.is_empty()
            || preview.operation != "instanceUpdate"
            || preview.base_revision != request.expected_revision
            || preview.target_release.release_id != request.target_release_id
            || preview.allowed != preview.blockers.is_empty()
        {
            return Err(error(
                "DST_UPDATE_PROTOCOL_INVALID",
                OP_PLAN,
                "更新预览身份或字段无效",
            ));
        }
        Ok(preview)
    })
}

fn task_program(request: &Value, include_sources: bool) -> String {
    let update_source = STANDARD.encode(UPDATE_SCRIPT.as_bytes());
    let request_source =
        STANDARD.encode(serde_json::to_vec(request).expect("request serialization"));
    let mut globals = format!("UPDATE_SOURCE=base64.b64decode('{update_source}').decode('utf-8')");
    if include_sources {
        for (name, source) in [
            ("RESTORE_SOURCE", RESTORE_SCRIPT),
            ("ARCHIVE_SOURCE", ARCHIVE_SCRIPT),
            ("RELEASE_SOURCE", RELEASE_SCRIPT),
        ] {
            let encoded = STANDARD.encode(source.as_bytes());
            globals.push_str(&format!(
                "\n{name}=base64.b64decode('{encoded}').decode('utf-8')"
            ));
        }
    }
    let include = if include_sources { "True" } else { "False" };
    format!("import base64,json\n{globals}\nnamespace={{'__name__':'update_task','UPDATE_SOURCE':UPDATE_SOURCE}}\nif {include}:\n    namespace.update({{'RESTORE_SOURCE':RESTORE_SOURCE,'ARCHIVE_SOURCE':ARCHIVE_SOURCE,'RELEASE_SOURCE':RELEASE_SOURCE}})\nexec(compile(UPDATE_SOURCE,'<update-task>','exec'),namespace)\nnamespace['main'](json.loads(base64.b64decode('{request_source}').decode('utf-8')))\n")
}

fn task_response(
    remote: Value,
    request_id: &str,
    target: &InstanceTarget,
    operation_id: &str,
) -> CommandResult<Value> {
    if remote.get("taskId").and_then(Value::as_str) != Some(operation_id)
        || remote.get("operationId").and_then(Value::as_str) != Some(operation_id)
        || remote.get("operation").and_then(Value::as_str) != Some("instanceUpdate")
        || remote
            .get("target")
            .and_then(|value| value.get("hostId"))
            .and_then(Value::as_str)
            != Some(target.host_id.as_str())
        || remote
            .get("target")
            .and_then(|value| value.get("instanceId"))
            .and_then(Value::as_str)
            != Some(target.instance_id.as_str())
        || remote.get("status").and_then(Value::as_str).is_none()
        || remote.get("stage").and_then(Value::as_str).is_none()
        || remote.get("sequence").and_then(Value::as_u64).unwrap_or(0) == 0
    {
        return Err(work_error(
            "UPDATE_PROTOCOL_INVALID",
            OP_UPDATE,
            "更新任务响应身份或字段无效",
        ));
    }
    Ok(json!({"schemaVersion":1,"requestId":request_id,"target":target,"task":remote}))
}

pub fn run(database: &Path, request: RunRequest) -> CommandResult<Value> {
    if request.schema_version != 1
        || request.expected_record_version == 0
        || request.expected_record_version > crate::server_input::MAX_REVISION
        || request.expected_revision > crate::server_input::MAX_REVISION
    {
        return Err(error(
            "UPDATE_TASK_INPUT_INVALID",
            OP_UPDATE,
            "更新任务请求无效",
        ));
    }
    validate_target(&request.request_id, &request.target, OP_UPDATE)?;
    crate::server_input::validate_id(&request.plan_id, OP_UPDATE, "planId")?;
    crate::server_input::validate_id(&request.operation_id, OP_UPDATE, "operationId")?;
    let connection = connection(
        &request.request_id,
        &request.target.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_UPDATE,
                "更新任务主机身份不一致",
            ));
        }
        let payload = json!({"schemaVersion":1,"action":"updateStart","requestId":request.request_id,
            "operationId":request.operation_id,"hostId":request.target.host_id,
            "instanceId":request.target.instance_id,"expectedRevision":request.expected_revision,
            "planId":request.plan_id});
        let program = task_program(&payload, true);
        let remote = invoke(session, program, Duration::from_secs(60))?;
        task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn task_status(database: &Path, request: TaskStatusRequest) -> CommandResult<Value> {
    if request.schema_version != 1 || request.expected_record_version == 0 {
        return Err(error(
            "UPDATE_TASK_INPUT_INVALID",
            OP_UPDATE,
            "更新任务查询无效",
        ));
    }
    validate_target(&request.request_id, &request.target, OP_UPDATE)?;
    crate::server_input::validate_id(&request.operation_id, OP_UPDATE, "operationId")?;
    let connection = connection(
        &request.request_id,
        &request.target.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_UPDATE,
                "更新任务主机身份不一致",
            ));
        }
        let payload = json!({"schemaVersion":1,"action":"updateStatus","requestId":request.request_id,
            "operationId":request.operation_id,"hostId":request.target.host_id,
            "instanceId":request.target.instance_id});
        let remote = invoke(
            session,
            task_program(&payload, false),
            Duration::from_secs(30),
        )?;
        task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn task_cancel(database: &Path, request: TaskStatusRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error(
            "UPDATE_TASK_INPUT_INVALID",
            OP_UPDATE,
            "更新任务取消无效",
        ));
    }
    validate_target(&request.request_id, &request.target, OP_UPDATE)?;
    crate::server_input::validate_id(&request.operation_id, OP_UPDATE, "operationId")?;
    let connection = connection(
        &request.request_id,
        &request.target.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_UPDATE,
                "更新任务主机身份不一致",
            ));
        }
        let payload = json!({"schemaVersion":1,"action":"updateCancel","requestId":request.request_id,
            "operationId":request.operation_id,"hostId":request.target.host_id,
            "instanceId":request.target.instance_id});
        let remote = invoke(
            session,
            task_program(&payload, false),
            Duration::from_secs(30),
        )?;
        task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn task_list(database: &Path, request: TaskListRequest) -> CommandResult<Vec<Value>> {
    if request.schema_version != 1 || request.expected_record_version == 0 {
        return Err(error(
            "UPDATE_TASK_INPUT_INVALID",
            OP_UPDATE,
            "更新任务列表无效",
        ));
    }
    validate_target(&request.request_id, &request.target, OP_UPDATE)?;
    let connection = connection(
        &request.request_id,
        &request.target.server_id,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                OP_UPDATE,
                "更新任务主机身份不一致",
            ));
        }
        let payload = json!({"schemaVersion":1,"action":"updateList","requestId":request.request_id,
            "hostId":request.target.host_id,"instanceId":request.target.instance_id});
        let remote = invoke(
            session,
            task_program(&payload, false),
            Duration::from_secs(30),
        )?;
        let values = remote.as_array().ok_or_else(|| {
            work_error("UPDATE_PROTOCOL_INVALID", OP_UPDATE, "更新任务列表字段无效")
        })?;
        let mut result = Vec::new();
        for value in values {
            let task_id = value
                .get("taskId")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let task = task_response(value.clone(), &request.request_id, &request.target, task_id)?;
            result.push(task);
        }
        Ok(result)
    })
}

#[cfg(test)]
mod tests {
    use super::map_error;
    use serde_json::json;

    #[test]
    fn blocked_preview_keeps_structured_blockers() {
        let details = json!({"blockers": ["UPDATE_RUNTIME_UNKNOWN"]});
        let error = map_error("DST_UPDATE_BLOCKED", Some(&details));
        assert_eq!(error.code, "DST_UPDATE_BLOCKED");
        assert_eq!(error.details["blockers"][0], "UPDATE_RUNTIME_UNKNOWN");
    }

    #[test]
    fn unknown_update_codes_are_reported_as_update_failed() {
        let error = map_error("SOMETHING_NEW", None);
        assert_eq!(error.code, "UPDATE_FAILED");
        assert_eq!(error.category, "update");
    }

    #[test]
    fn status_parses_checking_stage_and_started_at() {
        let value = json!({
            "latest": null,
            "candidateReleases": [],
            "lastCheckError": null,
            "checking": true,
            "stage": "steamcmd",
            "startedAt": "2026-11-13T00:00:00Z"
        });
        let status = super::status_from(value, "request", "host").unwrap();
        assert!(status.checking);
        assert_eq!(status.stage.as_deref(), Some("steamcmd"));
        assert_eq!(status.started_at.as_deref(), Some("2026-11-13T00:00:00Z"));
    }
}
