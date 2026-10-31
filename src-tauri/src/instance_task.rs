use crate::{
    dto::{CommandError, CommandResult},
    instance_creation::{CreateInput, HostTarget, PortPlan},
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

const OP: &str = "createInstance";
const ADMISSION: &str = include_str!("../../remote-scripts/actions/instance-create-control.py");
const TASK: &str = include_str!("../../remote-scripts/actions/instance-create-task.py");
const WORKER: &str = include_str!("../../remote-scripts/actions/instance-create-worker.py");
const RELEASE: &str = include_str!("../../remote-scripts/actions/instance-release.py");
const DOCKER: &str = include_str!("../../remote-scripts/actions/instance-docker.py");
const MAX_OUTPUT: usize = 256 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StartRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub expected_registry_revision: u64,
    pub operation_id: String,
    pub input: CreateInput,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AdmissionInput<'a> {
    display_name: &'a str,
    autostart: bool,
    start_after_create: bool,
    port_policy: &'a str,
    caves_enabled: bool,
    release_id: &'a str,
    release_catalog_digest: &'a str,
    expected_ports: &'a [PortPlan],
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StatusRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteTarget {
    host_id: String,
    instance_id: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskJournalEntry {
    pub sequence: u64,
    pub status: String,
    pub stage: String,
    pub at: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteTask {
    schema_version: u32,
    task_id: String,
    operation_id: String,
    target: RemoteTarget,
    operation: String,
    status: String,
    stage: String,
    last_completed_stage: Option<String>,
    progress: Value,
    sequence: u64,
    created_at: String,
    started_at: Option<String>,
    finished_at: Option<String>,
    cancel_allowed: bool,
    observation: String,
    result: Option<Value>,
    error: Option<Value>,
    #[serde(default)]
    journal: Vec<TaskJournalEntry>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskSnapshot {
    schema_version: u32,
    task_id: String,
    operation_id: String,
    target: crate::instance_dto::InstanceTarget,
    operation: String,
    status: String,
    stage: String,
    last_completed_stage: Option<String>,
    progress: Value,
    sequence: u64,
    created_at: String,
    started_at: Option<String>,
    finished_at: Option<String>,
    cancel_allowed: bool,
    observation: String,
    result: Option<Value>,
    error: Option<Value>,
    journal: Vec<TaskJournalEntry>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskResponse {
    schema_version: u32,
    request_id: String,
    server_id: String,
    record_version: u64,
    task: Option<TaskSnapshot>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "instanceCreate",
        OP,
        message,
        "刷新专服列表并核对创建任务现场",
    )
}

fn valid_uuid(value: &str) -> bool {
    uuid::Uuid::try_parse(value)
        .is_ok_and(|id| id.get_version_num() == 4 && id.to_string() == value)
}

fn validate_start(request: &StartRequest) -> CommandResult<()> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "创建任务请求版本不受支持"));
    }
    crate::server_input::validate_id(&request.target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&request.target.host_id, OP, "hostId")?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    if request.expected_registry_revision > crate::server_input::MAX_REVISION
        || request.expected_record_version > crate::server_input::MAX_REVISION
        || request.input.display_name.trim().is_empty()
        || request.input.display_name.chars().count() > 64
        || request.input.display_name.chars().any(char::is_control)
        || request.input.port_policy != "auto"
        || (!request.input.autostart && request.input.start_after_create)
        || !valid_uuid(&request.input.release_id)
        || !request.input.release_catalog_digest.starts_with("sha256:")
    {
        return Err(error("CREATE_INPUT_INVALID", "创建容器请求参数无效"));
    }
    Ok(())
}

fn validate_status(request: &StatusRequest) -> CommandResult<()> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "创建任务查询版本不受支持"));
    }
    crate::server_input::validate_id(&request.target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&request.target.host_id, OP, "hostId")?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    Ok(())
}

fn decode(output: &[u8]) -> CommandResult<Option<RemoteTask>> {
    let envelope: Value = serde_json::from_slice(output)
        .map_err(|_| error("CREATE_TASK_PROTOCOL_INVALID", "创建任务响应格式无效"))?;
    match envelope.get("ok").and_then(Value::as_bool) {
        Some(true) => serde_json::from_value(
            envelope
                .get("data")
                .cloned()
                .ok_or_else(|| error("CREATE_TASK_PROTOCOL_INVALID", "创建任务响应缺少数据"))?,
        )
        .map_err(|_| error("CREATE_TASK_PROTOCOL_INVALID", "创建任务数据格式无效")),
        Some(false) => {
            let code = envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("CREATE_TASK_FAILED");
            Err(remote_error(code))
        }
        None => Err(error(
            "CREATE_TASK_PROTOCOL_INVALID",
            "创建任务响应包络无效",
        )),
    }
}

fn remote_error(code: &str) -> Box<CommandError> {
    let mut result = match code {
        "CREATE_RELEASE_REQUIRED" => error("CREATE_RELEASE_REQUIRED", "没有已验证的 DST release"),
        "CREATE_DOCKER_RUNTIME_REQUIRED" => error(
            "CREATE_DOCKER_RUNTIME_REQUIRED",
            "Docker runtime 尚未通过验证",
        ),
        "CREATE_CONTAINER_VERIFICATION_REQUIRED" => error(
            "CREATE_CONTAINER_VERIFICATION_REQUIRED",
            "容器已创建但仍待验证",
        ),
        "OPERATION_ID_REUSED" => error("OPERATION_ID_REUSED", "操作编号已用于不同的创建请求"),
        "INSTANCE_CREATE_BUSY" => error("INSTANCE_CREATE_BUSY", "已有创建任务正在运行"),
        "REGISTRY_CHANGED" => error("REGISTRY_CHANGED", "注册表已变化，请刷新后重试"),
        "RELEASE_CATALOG_CHANGED" => error(
            "RELEASE_CATALOG_CHANGED",
            "Release 列表已变化，请刷新 Release 后重试",
        ),
        "CREATE_PORT_STATE_INVALID" => error(
            "CREATE_PORT_STATE_INVALID",
            "端口预留记录无效，请核对实例注册表",
        ),
        "CREATE_INPUT_INVALID" | "CREATE_TASK_INPUT_INVALID" => error(
            "CREATE_INPUT_INVALID",
            "创建请求字段无效，请重新打开新建窗口",
        ),
        "CREATE_PATH_UNSAFE" | "CREATE_TASK_PATH_UNSAFE" | "CREATE_FILE_INVALID" => error(
            "CREATE_TASK_PATH_UNSAFE",
            "创建任务目录或管理文件权限不符合要求，未继续创建",
        ),
        "REGISTRY_BUSY" => error("REGISTRY_BUSY", "其他实例操作占用注册锁，请稍后重试"),
        "CREATE_REGISTRY_INVALID" => error("CREATE_REGISTRY_INVALID", "实例注册表状态无效"),
        "CREATE_OPERATION_STATE_INVALID" => error(
            "CREATE_OPERATION_STATE_INVALID",
            "实例操作记录无效，请核对注册表",
        ),
        "PLAN_STALE" => error("PLAN_STALE", "创建计划已失效，请重试"),
        "ROOT_REQUIRED" => error("ROOT_REQUIRED", "远端创建任务要求 root Docker 环境"),
        "CREATE_TASK_STATE_INVALID" => error("CREATE_TASK_STATE_INVALID", "创建任务状态文件无效"),
        _ => error("CREATE_TASK_FAILED", "创建容器请求失败，请查看远端错误码"),
    };
    if !code.is_empty()
        && code.len() <= 96
        && code
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    {
        result.details = json!({"remoteCode": code});
    }
    result
}

fn task_snapshot(remote: RemoteTask, server_id: &str) -> CommandResult<TaskSnapshot> {
    if remote.schema_version != 1
        || !valid_uuid(&remote.task_id)
        || remote.task_id != remote.operation_id
        || remote.operation != "instanceCreate"
        || !valid_uuid(&remote.target.host_id)
        || !valid_uuid(&remote.target.instance_id)
        || remote.sequence == 0
        || remote.journal.len() > 100
        || remote.journal.iter().any(|entry| {
            entry.sequence == 0
                || ![
                    "queued",
                    "running",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "interrupted",
                ]
                .contains(&entry.status.as_str())
                || entry.stage.len() > 64
                || entry.at.len() > 64
        })
    {
        return Err(error(
            "CREATE_TASK_PROTOCOL_INVALID",
            "创建任务身份或序号无效",
        ));
    }
    Ok(TaskSnapshot {
        schema_version: remote.schema_version,
        task_id: remote.task_id,
        operation_id: remote.operation_id,
        target: crate::instance_dto::InstanceTarget {
            server_id: server_id.to_string(),
            host_id: remote.target.host_id,
            instance_id: remote.target.instance_id,
        },
        operation: remote.operation,
        status: remote.status,
        stage: remote.stage,
        last_completed_stage: remote.last_completed_stage,
        progress: remote.progress,
        sequence: remote.sequence,
        created_at: remote.created_at,
        started_at: remote.started_at,
        finished_at: remote.finished_at,
        cancel_allowed: remote.cancel_allowed,
        observation: remote.observation,
        result: remote.result,
        error: remote.error,
        journal: remote.journal,
    })
}

fn execute(session: &Session, request: &Value) -> CommandResult<Option<RemoteTask>> {
    let request_source = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("CREATE_TASK_PROTOCOL_INVALID", "创建任务请求无法编码"))?,
    );
    let py = |value: &str| {
        serde_json::to_string(value)
            .map_err(|_| error("CREATE_TASK_PROTOCOL_INVALID", "远端脚本无法编码"))
    };
    let source = format!(
        "import base64\n{ADMISSION}\n{TASK}\nWORKER_SOURCE={worker}\nRUNTIME_SOURCE={runtime}\nRELEASE_SOURCE={release}\nDOCKER_SOURCE={docker}\nmain(json.loads(base64.b64decode('{request_source}').decode('utf-8')))\n",
        worker = py(WORKER)?, runtime = py(TASK)?, release = py(RELEASE)?, docker = py(DOCKER)?
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("CREATE_TASK_CHANNEL_FAILED", "无法创建创建任务通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("CREATE_TASK_CHANNEL_FAILED", "无法配置创建任务通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("CREATE_TASK_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("CREATE_TASK_CHANNEL_FAILED", "无法提交创建任务"))?;
    channel
        .send_eof()
        .map_err(|_| error("CREATE_TASK_CHANNEL_FAILED", "无法结束创建任务请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("CREATE_TASK_TIMEOUT", "创建任务请求超时"));
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
            _ => return Err(error("CREATE_TASK_CHANNEL_FAILED", "无法读取创建任务结果")),
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("CREATE_TASK_CHANNEL_FAILED", "创建任务通道未正常结束"))?;
    if channel
        .exit_status()
        .map_err(|_| error("CREATE_TASK_CHANNEL_FAILED", "无法确认创建任务结果"))?
        != 0
    {
        return Err(error("CREATE_TASK_FAILED", "远端创建任务脚本执行失败"));
    }
    decode(&output)
}

pub fn start(database: &Path, request: StartRequest) -> CommandResult<TaskResponse> {
    validate_start(&request)?;
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "创建目标主机身份不一致"));
        }
        let check = crate::instance_creation::check(
            session,
            &request.target.host_id,
            request.expected_registry_revision,
            &request.input,
        )?;
        if !check.blockers.is_empty() {
            return Err(crate::instance_creation::blocked(&check.blockers));
        }
        let input = serde_json::to_value(AdmissionInput {
            display_name: &request.input.display_name,
            autostart: request.input.autostart,
            start_after_create: request.input.start_after_create,
            port_policy: &request.input.port_policy,
            caves_enabled: request.input.caves_enabled,
            release_id: &request.input.release_id,
            release_catalog_digest: &request.input.release_catalog_digest,
            expected_ports: &check.ports,
        })
        .map_err(|_| error("CREATE_TASK_PROTOCOL_INVALID", "创建容器请求无法编码"))?;
        let operation_id = &request.operation_id;
        let task = execute(session, &json!({"action":"start","schemaVersion":1,"operationId":operation_id,"planId":operation_id,"hostId":&request.target.host_id,"expectedRegistryRevision":request.expected_registry_revision,"input":input}))?
            .ok_or_else(|| error("CREATE_TASK_PROTOCOL_INVALID", "创建任务响应为空"))?;
        Ok(TaskResponse {
            schema_version: 1,
            request_id: request.request_id,
            server_id: request.target.server_id,
            record_version: request.expected_record_version,
            task: Some(task_snapshot(task, &connection.server_id)?),
        })
    })
}

pub fn status(database: &Path, request: StatusRequest) -> CommandResult<TaskResponse> {
    validate_status(&request)?;
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "创建任务主机身份不一致"));
        }
        let task = execute(
            session,
            &json!({"action":"status","taskId":request.operation_id}),
        )?;
        let task = task
            .map(|value| task_snapshot(value, &connection.server_id))
            .transpose()?;
        Ok(TaskResponse {
            schema_version: 1,
            request_id: request.request_id,
            server_id: request.target.server_id,
            record_version: request.expected_record_version,
            task,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creation_admission_does_not_hide_unknown_remote_codes() {
        let failure = decode(br#"{"ok":false,"code":"CREATE_NEW_FAILURE"}"#)
            .expect_err("unknown creation error must be preserved");
        assert_eq!(failure.code, "CREATE_TASK_FAILED");
        assert_eq!(failure.details["remoteCode"], "CREATE_NEW_FAILURE");
        let unsafe_failure = remote_error("private token /path");
        assert!(unsafe_failure.details.get("remoteCode").is_none());
    }

    #[test]
    fn release_changes_have_a_specific_recovery_message() {
        let failure = remote_error("RELEASE_CATALOG_CHANGED");
        assert_eq!(failure.code, "RELEASE_CATALOG_CHANGED");
        assert!(failure.message.contains("刷新 Release"));
    }

    #[test]
    fn admission_input_carries_the_checked_port_plan() {
        let release_id = "22222222-2222-4222-8222-222222222222";
        let input = CreateInput {
            display_name: "生存一服".into(),
            autostart: true,
            start_after_create: false,
            port_policy: "auto".into(),
            caves_enabled: false,
            release_id: release_id.into(),
            release_catalog_digest: format!("sha256:{}", "a".repeat(64)),
        };
        let ports = vec![
            PortPlan {
                shard: "master".into(),
                purpose: "game".into(),
                protocol: "udp".into(),
                bind_address: "0.0.0.0".into(),
                host_port: 11000,
                container_port: 11000,
            },
            PortPlan {
                shard: "master".into(),
                purpose: "steamAuth".into(),
                protocol: "udp".into(),
                bind_address: "0.0.0.0".into(),
                host_port: 8766,
                container_port: 8766,
            },
            PortPlan {
                shard: "master".into(),
                purpose: "steamMaster".into(),
                protocol: "udp".into(),
                bind_address: "0.0.0.0".into(),
                host_port: 27016,
                container_port: 27016,
            },
        ];
        let value = serde_json::to_value(AdmissionInput {
            display_name: &input.display_name,
            autostart: input.autostart,
            start_after_create: input.start_after_create,
            port_policy: &input.port_policy,
            caves_enabled: input.caves_enabled,
            release_id: &input.release_id,
            release_catalog_digest: &input.release_catalog_digest,
            expected_ports: &ports,
        })
        .expect("admission input");
        assert_eq!(value.as_object().expect("object").len(), 8);
        assert_eq!(value["expectedPorts"][0]["hostPort"], 11000);
        assert_eq!(value["expectedPorts"][2]["purpose"], "steamMaster");
        assert_eq!(value["releaseId"], release_id);
    }

    #[test]
    fn bundled_docker_script_uses_create_without_no_start() {
        assert!(DOCKER.contains("\"-f\", compose_path, \"create\"]"));
        assert!(!DOCKER.contains("--no-start"));
        assert!(WORKER.contains("docker_module[\"create_containers\"]"));
    }
}
