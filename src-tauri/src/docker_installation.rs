use crate::{
    dto::{CommandError, CommandResult},
    ssh_dto::ConnectionRequest,
    ssh_state,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::{json, Value};
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    time::{Duration, Instant},
};

const OP: &str = "previewDockerInstallation";
const PREFLIGHT: &str = include_str!("../../remote-scripts/probes/docker-install-preview.py");
const CONTROL: &str = include_str!("../../remote-scripts/actions/docker-install-control.py");
const WORKER: &str = include_str!("../../remote-scripts/actions/docker-install-worker.py");
const MAX_OUTPUT: usize = 16384;
const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DockerInstallationPreview {
    pub status: String,
    pub blockers: Vec<String>,
    pub snapshot_digest: String,
    pub expires_at_unix_seconds: u64,
    pub distribution: Distribution,
    pub host_id: Option<String>,
    pub uid: u32,
    pub root_available_bytes: u64,
    pub source_state: String,
    pub repository_reachable: bool,
    pub apt_running: bool,
    pub dpkg_clean: bool,
    pub installed_docker_packages: std::collections::BTreeMap<String, String>,
    pub packages: Vec<String>,
    pub repository: String,
    pub source_file: String,
    pub key_file: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Distribution {
    pub id: Option<String>,
    pub version: Option<String>,
    pub codename: Option<String>,
    pub architecture: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewResponse {
    schema_version: u32,
    request_id: String,
    server_id: String,
    record_version: u64,
    pub plan_id: Option<String>,
    pub operation_id: Option<String>,
    preview: DockerInstallationPreview,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StartRequest {
    #[serde(flatten)]
    pub connection: ConnectionRequest,
    pub plan_id: String,
    pub operation_id: String,
    pub snapshot_digest: String,
    pub expires_at_unix_seconds: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StatusRequest {
    #[serde(flatten)]
    pub connection: ConnectionRequest,
    pub operation_id: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DockerInstallTask {
    pub schema_version: u32,
    pub task_id: String,
    pub host_id: String,
    pub sequence: u64,
    pub status: String,
    pub phase: String,
    pub message_code: Option<String>,
    pub started_at_unix_seconds: u64,
    pub updated_at_unix_seconds: u64,
    pub installed_versions: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskResponse {
    schema_version: u32,
    request_id: String,
    server_id: String,
    host_id: String,
    record_version: u64,
    task: Option<DockerInstallTask>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "installation",
        OP,
        message,
        "刷新主机环境后重试；不会自动修改软件包或服务",
    )
}
fn valid_host_id(value: &str) -> bool {
    uuid::Uuid::try_parse(value)
        .is_ok_and(|id| id.get_version_num() == 4 && id.to_string() == value)
}

fn repository_http_blocker(code: &str) -> bool {
    code.strip_prefix("DOCKER_REPOSITORY_HTTP_")
        .is_some_and(|status| {
            status.len() == 3
                && status.bytes().all(|byte| byte.is_ascii_digit())
                && status
                    .parse::<u16>()
                    .is_ok_and(|value| (100..=599).contains(&value))
        })
}

fn validate(preview: &DockerInstallationPreview) -> CommandResult<()> {
    const PACKAGES: [&str; 5] = [
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
    ];
    const BLOCKERS: [&str; 33] = [
        "UNSUPPORTED_DISTRIBUTION",
        "UNSUPPORTED_ARCHITECTURE",
        "ROOT_REQUIRED",
        "SYSTEMD_REQUIRED",
        "APT_REQUIRED",
        "CONFLICTING_PACKAGES",
        "DOCKER_ALREADY_INSTALLED",
        "HOST_NOT_INITIALIZED",
        "MANAGEMENT_ROOT_UNSAFE",
        "BOOTSTRAP_PATH_UNSAFE",
        "BOOTSTRAP_OWNER_OR_MODE_INVALID",
        "DOCKER_SOURCE_UNSAFE",
        "DOCKER_SOURCE_CONFLICT",
        "LEGACY_DOCKER_SOURCE_PRESENT",
        "DOCKER_KEY_UNSAFE",
        "DOCKER_KEY_CONFLICT",
        "INSUFFICIENT_DISK_SPACE",
        "DOCKER_REPOSITORY_UNREACHABLE",
        "DOCKER_REPOSITORY_TIMEOUT",
        "DOCKER_REPOSITORY_DNS_FAILED",
        "DOCKER_REPOSITORY_TLS_CERT_INVALID",
        "DOCKER_REPOSITORY_TLS_FAILED",
        "DOCKER_REPOSITORY_CONNECTION_FAILED",
        "DOCKER_REPOSITORY_RESPONSE_INVALID",
        "INSTALL_TASK_PATH_UNSAFE",
        "INSTALL_ALREADY_RUNNING",
        "HOST_IDENTITY_MISSING",
        "HOST_IDENTITY_INVALID",
        "APT_BUSY",
        "DPKG_STATE_INCOMPLETE",
        "APT_SOURCE_DIRECTORY_UNSAFE",
        "APT_SOURCE_DIRECTORY_TOO_LARGE",
        "APT_SOURCE_DIRECTORY_UNREADABLE",
    ];
    let now = ssh_state::now()?;
    if !["ready", "blocked"].contains(&preview.status.as_str())
        || preview.snapshot_digest.len() != 64
        || !preview
            .snapshot_digest
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
        || preview.expires_at_unix_seconds <= now
        || preview.expires_at_unix_seconds > now.saturating_add(600)
        || preview.root_available_bytes > MAX_SAFE_INTEGER
        || !["missing", "matching"].contains(&preview.source_state.as_str())
        || preview
            .packages
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>()
            != PACKAGES
        || preview.repository != "https://download.docker.com/linux/ubuntu"
        || preview.source_file != "/etc/apt/sources.list.d/docker.sources"
        || preview.key_file != "/etc/apt/keyrings/docker.asc"
        || preview.blockers.iter().any(|blocker| {
            !BLOCKERS.contains(&blocker.as_str()) && !repository_http_blocker(blocker)
        })
        || preview
            .installed_docker_packages
            .keys()
            .any(|package| !PACKAGES.contains(&package.as_str()))
        || preview
            .installed_docker_packages
            .values()
            .any(|version| version.is_empty() || version.len() > 128)
        || (preview.status == "ready"
            && (!preview.blockers.is_empty()
                || preview.distribution.id.as_deref() != Some("ubuntu")
                || preview.distribution.version.as_deref() != Some("26.04")
                || preview.distribution.codename.as_deref() != Some("resolute")
                || !preview.host_id.as_deref().is_some_and(valid_host_id)
                || !matches!(
                    preview.distribution.architecture.as_deref(),
                    Some("x86_64" | "amd64")
                )
                || preview.uid != 0
                || !preview.repository_reachable
                || preview.apt_running
                || !preview.dpkg_clean))
        || (preview.status == "blocked" && preview.blockers.is_empty())
    {
        return Err(error(
            "DOCKER_INSTALL_PROTOCOL_INVALID",
            "Docker 安装预检计划无效",
        ));
    }
    Ok(())
}

// Unwrap the controller envelope once, then decode directly to the command's DTO.
fn decode_response<T: DeserializeOwned>(output: &[u8], action: &str) -> CommandResult<T> {
    let invalid = || {
        error(
            "DOCKER_INSTALL_PROTOCOL_INVALID",
            "Docker 控制器响应格式无效",
        )
    };
    let envelope: Value = serde_json::from_slice(output).map_err(|_| invalid())?;
    match envelope.get("ok").and_then(Value::as_bool) {
        Some(true) => {
            let data = envelope.get("data").ok_or_else(|| {
                error(
                    "DOCKER_INSTALL_PROTOCOL_INVALID",
                    match action {
                        "status" => "Docker 安装状态响应缺少数据",
                        "start" => "Docker 安装任务响应缺少数据",
                        _ => "Docker 安装预检结果缺少数据",
                    },
                )
            })?;
            serde_json::from_value(data.clone()).map_err(|_| invalid())
        }
        Some(false) => {
            let code = envelope
                .get("code")
                .and_then(Value::as_str)
                .ok_or_else(invalid)?;
            Err(remote_error(code))
        }
        None => Err(invalid()),
    }
}

fn execute<T: DeserializeOwned>(session: &Session, request: &Value) -> CommandResult<T> {
    let mut channel = session
        .channel_session()
        .map_err(|_| error("DOCKER_INSTALL_CHANNEL_FAILED", "无法创建安装预检通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("DOCKER_INSTALL_CHANNEL_FAILED", "无法配置安装预检通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "DOCKER_INSTALL_RUNTIME_REQUIRED",
            "需要 /usr/bin/python3 执行安装预检",
        )
    })?;
    let action = request.get("action").and_then(Value::as_str).unwrap_or("");
    let needs_preflight = action != "status";
    let needs_worker = action == "start";
    let preflight_source = if needs_preflight {
        serde_json::to_string(PREFLIGHT)
            .map_err(|_| error("DOCKER_INSTALL_PROTOCOL_INVALID", "预检脚本无法编码"))?
    } else {
        "None".into()
    };
    let worker_source = if needs_worker {
        serde_json::to_string(WORKER)
            .map_err(|_| error("DOCKER_INSTALL_PROTOCOL_INVALID", "安装任务无法编码"))?
    } else {
        "None".into()
    };
    let request_source = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("DOCKER_INSTALL_PROTOCOL_INVALID", "安装请求无法编码"))?,
    );
    let source = format!(
        "{}{}\n{CONTROL}\nPREFLIGHT_SOURCE={preflight_source}\nWORKER_SOURCE={worker_source}\nmain(json.loads(base64.b64decode('{request_source}').decode('utf-8')))\n",
        if needs_preflight {PREFLIGHT} else {""}, if needs_preflight {"\n"} else {""}
    );
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("DOCKER_INSTALL_PREFLIGHT_FAILED", "无法提交安装预检"))?;
    channel
        .send_eof()
        .map_err(|_| error("DOCKER_INSTALL_PREFLIGHT_FAILED", "无法启动安装预检"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut output = Vec::new();
    let mut buffer = [0u8; 2048];
    loop {
        if Instant::now() >= deadline {
            return Err(error(
                "DOCKER_INSTALL_PREFLIGHT_TIMEOUT",
                "Docker 安装预检超时",
            ));
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
            _ => {
                return Err(error(
                    "DOCKER_INSTALL_PREFLIGHT_FAILED",
                    "无法读取 Docker 安装预检结果",
                ))
            }
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("DOCKER_INSTALL_PREFLIGHT_FAILED", "安装预检未能正常结束"))?;
    if channel
        .exit_status()
        .map_err(|_| error("DOCKER_INSTALL_PREFLIGHT_FAILED", "无法确认安装预检结果"))?
        != 0
    {
        return Err(error(
            "DOCKER_INSTALL_PREFLIGHT_FAILED",
            "Docker 安装预检执行失败",
        ));
    }
    decode_response(&output, action)
}

pub fn preview(
    database: &std::path::Path,
    request: ConnectionRequest,
) -> CommandResult<PreviewResponse> {
    crate::ssh::with_verified_session(database, &request, |session, host_id| {
        let host_id =
            host_id.ok_or_else(|| error("HOST_UNINITIALIZED", "请先初始化主机管理目录"))?;
        let preview: DockerInstallationPreview = execute(session, &json!({"action":"preview"}))?;
        validate(&preview)?;
        if preview.host_id.as_deref() != Some(host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "安装预检主机身份与当前目标不一致",
            ));
        }
        let (plan_id, operation_id) = if preview.status == "ready" {
            (
                Some(uuid::Uuid::new_v4().to_string()),
                Some(uuid::Uuid::new_v4().to_string()),
            )
        } else {
            (None, None)
        };
        Ok(PreviewResponse {
            schema_version: 1,
            request_id: request.request_id.clone(),
            server_id: request.server_id.clone(),
            record_version: request.expected_record_version,
            plan_id,
            operation_id,
            preview,
        })
    })
}

fn remote_error(code: &str) -> Box<CommandError> {
    match code {
        "PLAN_EXPIRED" => error("PLAN_EXPIRED", "安装预检已过期，请重新检查"),
        "INSTALL_HOST_CHANGED" => error(
            "INSTALL_HOST_CHANGED",
            "安装目标主机身份发生变化，请重新检查",
        ),
        "INSTALL_SNAPSHOT_CHANGED" => error(
            "INSTALL_SNAPSHOT_CHANGED",
            "主机安装状态与预检快照不同，请重新检查",
        ),
        "INSTALL_PREFLIGHT_BLOCKED" => error(
            "INSTALL_PREFLIGHT_BLOCKED",
            "安装条件已不满足，请重新检查阻断项",
        ),
        "PLAN_STALE" => error("PLAN_STALE", "安装预检已过期或主机状态变化，请重新检查"),
        "INSTALL_ALREADY_RUNNING" => {
            error("INSTALL_ALREADY_RUNNING", "已有 Docker 安装任务正在运行")
        }
        "ROOT_REQUIRED" => error("ROOT_REQUIRED", "Docker 安装任务要求 root SSH 账户"),
        "OPERATION_ID_REUSED" => error("OPERATION_ID_REUSED", "操作编号已用于不同的安装计划"),
        "INSTALL_TASK_PATH_UNSAFE" => error(
            "INSTALL_TASK_PATH_UNSAFE",
            "远端安装任务目录权限或类型不安全",
        ),
        "INSTALL_CONTROLLER_START_FAILED" => error(
            "INSTALL_CONTROLLER_START_FAILED",
            "未能启动持久 Docker 安装任务",
        ),
        "INPUT_INVALID" => error(
            "DOCKER_INSTALL_INPUT_INVALID",
            "Docker 安装任务请求参数无效",
        ),
        "INSTALL_TASK_ID_INVALID" => {
            error("INSTALL_TASK_ID_INVALID", "远端 Docker 安装任务编号无效")
        }
        "INSTALL_TASK_STATE_INVALID" => error(
            "INSTALL_TASK_STATE_INVALID",
            "远端 Docker 安装任务状态文件无效",
        ),
        "INSTALL_CONTROLLER_START_TIMEOUT" => error(
            "INSTALL_CONTROLLER_START_TIMEOUT",
            "启动持久 Docker 安装任务超时",
        ),
        "ACTION_INVALID" => error("DOCKER_INSTALL_ACTION_INVALID", "Docker 安装控制器动作无效"),
        "INSTALL_FAILED" => error("INSTALL_FAILED", "Docker 安装脚本执行异常"),
        _ => error(
            "DOCKER_INSTALL_TASK_FAILED",
            "Docker 安装任务无法接纳或对账",
        ),
    }
}

pub fn start(database: &std::path::Path, request: StartRequest) -> CommandResult<TaskResponse> {
    crate::server_input::validate_id(&request.plan_id, "startDockerInstallation", "planId")?;
    crate::server_input::validate_id(
        &request.operation_id,
        "startDockerInstallation",
        "operationId",
    )?;
    if request.snapshot_digest.len() != 64
        || !request
            .snapshot_digest
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(CommandError::invalid(
            "startDockerInstallation",
            "snapshotDigest",
            "安装预检摘要无效",
        ));
    }
    crate::ssh::with_verified_session(database, &request.connection, |session, host_id| {
        let host_id =
            host_id.ok_or_else(|| error("HOST_UNINITIALIZED", "请先初始化主机管理目录"))?;
        let task: DockerInstallTask = execute(
            session,
            &json!({"action":"start","planId":request.plan_id,"operationId":request.operation_id,"hostId":host_id.clone(),"snapshotDigest":request.snapshot_digest,"expiresAtUnixSeconds":request.expires_at_unix_seconds}),
        )?;
        if task.task_id != request.operation_id
            || task.host_id != host_id
            || !["queued", "running", "succeeded", "failed", "interrupted"]
                .contains(&task.status.as_str())
        {
            return Err(error(
                "DOCKER_INSTALL_PROTOCOL_INVALID",
                "Docker 安装任务身份或状态不匹配",
            ));
        }
        Ok(TaskResponse {
            schema_version: 1,
            request_id: request.connection.request_id.clone(),
            server_id: request.connection.server_id.clone(),
            host_id,
            record_version: request.connection.expected_record_version,
            task: Some(task),
        })
    })
}

pub fn status(database: &std::path::Path, request: StatusRequest) -> CommandResult<TaskResponse> {
    if let Some(operation_id) = &request.operation_id {
        crate::server_input::validate_id(
            operation_id,
            "getDockerInstallationStatus",
            "operationId",
        )?;
    }
    crate::ssh::with_verified_session(database, &request.connection, |session, host_id| {
        let host_id =
            host_id.ok_or_else(|| error("HOST_UNINITIALIZED", "请先初始化主机管理目录"))?;
        let task: Option<DockerInstallTask> = execute(
            session,
            &json!({"action":"status", "taskId":request.operation_id}),
        )?;
        if task.as_ref().is_some_and(|task| task.host_id != host_id) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "Docker 安装任务归属与当前主机不一致",
            ));
        }
        if task.as_ref().is_some_and(|task| {
            request
                .operation_id
                .as_ref()
                .is_some_and(|id| &task.task_id != id)
        }) {
            return Err(error(
                "DOCKER_INSTALL_PROTOCOL_INVALID",
                "Docker 安装任务编号与查询不一致",
            ));
        }
        Ok(TaskResponse {
            schema_version: 1,
            request_id: request.connection.request_id.clone(),
            server_id: request.connection.server_id.clone(),
            host_id,
            record_version: request.connection.expected_record_version,
            task,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // Run the bundled controller against temporary task files. No SSH, apt or systemd writes.
    fn controller_status(state: Value) -> Vec<u8> {
        let encoded = STANDARD.encode(serde_json::to_vec(&state).expect("encode fixture"));
        let source = format!(
            r#"{CONTROL}
import tempfile
from unittest.mock import patch
from types import SimpleNamespace
fixture = json.loads(base64.b64decode('{encoded}'))
with tempfile.TemporaryDirectory() as folder:
    BOOTSTRAP = Path(folder)
    TASKS = BOOTSTRAP / 'docker-install'
    if fixture is not None:
        TASKS.mkdir(mode=0o700)
        directory = TASKS / '11111111-1111-4111-8111-111111111111'
        directory.mkdir(mode=0o700)
        write_new(directory / 'status.json', json.dumps(fixture).encode())
    with patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='active')):
        main({{'action': 'status'}})
"#
        );
        let mut child = std::process::Command::new("python3")
            .args(["-I", "-"])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .expect("Python is required for controller protocol tests");
        child
            .stdin
            .take()
            .expect("stdin")
            .write_all(source.as_bytes())
            .expect("write controller");
        let output = child.wait_with_output().expect("controller exit");
        assert!(output.status.success());
        output.stdout
    }

    fn task_fixture(status: &str) -> Value {
        json!({
            "schemaVersion":1,"taskId":"11111111-1111-4111-8111-111111111111",
            "hostId":"33333333-3333-4333-8333-333333333333","sequence":1,
            "status":status,"phase":"accepted","messageCode":null,
            "startedAtUnixSeconds":1700000000,"updatedAtUnixSeconds":1700000000,
            "installedVersions":{}
        })
    }

    #[test]
    fn controller_without_tasks_decodes_to_empty_status() {
        let bytes = controller_status(Value::Null);
        let task: Option<DockerInstallTask> =
            decode_response(&bytes, "status").expect("empty status");
        assert!(task.is_none());
        assert!(decode_response::<DockerInstallTask>(&bytes, "start").is_err());
        assert!(decode_response::<DockerInstallationPreview>(&bytes, "preview").is_err());
    }

    #[test]
    fn controller_existing_tasks_decode_without_a_second_envelope() {
        for status in ["queued", "running", "succeeded", "failed", "interrupted"] {
            let bytes = controller_status(task_fixture(status));
            let task: Option<DockerInstallTask> =
                decode_response(&bytes, "status").expect("task status");
            assert_eq!(task.expect("existing task").status, status);
            let task: DockerInstallTask =
                decode_response(&bytes, "start").expect("task admission uses same envelope");
            assert_eq!(task.status, status);
        }
    }

    #[test]
    fn controller_errors_reach_the_error_mapping() {
        let bytes = controller_status(json!({"taskId":"wrong","status":"running"}));
        let failure = decode_response::<Option<DockerInstallTask>>(&bytes, "status")
            .expect_err("invalid task");
        assert_eq!(failure.code, "INSTALL_TASK_STATE_INVALID");
        for code in [
            "PLAN_STALE",
            "INSTALL_CONTROLLER_START_TIMEOUT",
            "ROOT_REQUIRED",
        ] {
            let bytes = serde_json::to_vec(&json!({"ok":false,"code":code})).expect("envelope");
            assert_eq!(
                decode_response::<DockerInstallTask>(&bytes, "start")
                    .expect_err("rejected")
                    .code,
                code
            );
        }
    }

    #[test]
    fn malformed_envelopes_remain_protocol_errors() {
        for bytes in [
            b"null".as_slice(),
            b"{}",
            b"{\"ok\":true}",
            b"{\"ok\":false}",
            b"{\"ok\":\"true\",\"data\":null}",
            b"banner\n{\"ok\":true,\"data\":null}",
            b"{\"ok\":true,\"data\":42}",
        ] {
            assert_eq!(
                decode_response::<Option<DockerInstallTask>>(bytes, "status")
                    .expect_err("bad response")
                    .code,
                "DOCKER_INSTALL_PROTOCOL_INVALID"
            );
        }
    }

    #[test]
    fn install_preview_rejects_source_tampering_and_unknown_status() {
        let expires = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_secs() + 300)
            .unwrap_or(0);
        let mut value = json!({
            "status":"ready","blockers":[],"snapshotDigest":"a".repeat(64),
            "expiresAtUnixSeconds":expires,
            "distribution":{"id":"ubuntu","version":"26.04","codename":"resolute","architecture":"x86_64"},
            "hostId":"33333333-3333-4333-8333-333333333333",
            "uid":0,"rootAvailableBytes":8_000_000_000u64,"sourceState":"missing",
            "repositoryReachable":true,
            "installedDockerPackages":{},
            "aptRunning":false,"dpkgClean":true,
            "packages":["docker-ce","docker-ce-cli","containerd.io","docker-buildx-plugin","docker-compose-plugin"],
            "repository":"https://download.docker.com/linux/ubuntu",
            "sourceFile":"/etc/apt/sources.list.d/docker.sources","keyFile":"/etc/apt/keyrings/docker.asc"
        });
        let preview = serde_json::from_value::<DockerInstallationPreview>(value.clone())
            .expect("valid preview DTO");
        assert!(validate(&preview).is_ok());
        let serialized = serde_json::to_value(&preview).expect("frontend preview");
        assert_eq!(serialized["aptRunning"], false);
        assert_eq!(serialized["dpkgClean"], true);
        for blocker in [
            "DOCKER_REPOSITORY_TIMEOUT",
            "DOCKER_REPOSITORY_DNS_FAILED",
            "DOCKER_REPOSITORY_TLS_CERT_INVALID",
            "DOCKER_REPOSITORY_TLS_FAILED",
            "DOCKER_REPOSITORY_CONNECTION_FAILED",
            "DOCKER_REPOSITORY_RESPONSE_INVALID",
            "DOCKER_REPOSITORY_HTTP_403",
            "DOCKER_REPOSITORY_HTTP_503",
        ] {
            let mut blocked = value.clone();
            blocked["status"] = "blocked".into();
            blocked["repositoryReachable"] = false.into();
            blocked["blockers"] = json!([blocker]);
            let preview: DockerInstallationPreview =
                serde_json::from_value(blocked).expect("blocked preview");
            assert!(validate(&preview).is_ok(), "{blocker}");
        }
        for blocker in [
            "DOCKER_REPOSITORY_HTTP_999",
            "DOCKER_REPOSITORY_HTTP_403 secret",
            "DOCKER_REPOSITORY_HTTP_200\n",
        ] {
            assert!(!repository_http_blocker(blocker));
        }
        value["repository"] = "https://example.invalid".into();
        let parsed =
            serde_json::from_value::<DockerInstallationPreview>(value).expect("valid preview dto");
        assert!(validate(&parsed).is_err());
    }

    #[test]
    fn start_request_rejects_arbitrary_remote_paths() {
        let request = json!({
            "schemaVersion":1,"requestId":uuid::Uuid::new_v4().to_string(),
            "serverId":uuid::Uuid::new_v4().to_string(),"expectedRecordVersion":1,
            "planId":uuid::Uuid::new_v4().to_string(),"operationId":uuid::Uuid::new_v4().to_string(),
            "snapshotDigest":"a".repeat(64),"expiresAtUnixSeconds":1800000000
        });
        assert!(serde_json::from_value::<StartRequest>(request.clone()).is_ok());
        let mut unsafe_request = request;
        unsafe_request["remotePath"] = "/etc/apt".into();
        assert!(serde_json::from_value::<StartRequest>(unsafe_request).is_err());
    }

    #[test]
    fn status_request_supports_optional_operation_and_rejects_paths() {
        let mut request = json!({"schemaVersion":1,"requestId":uuid::Uuid::new_v4().to_string(),"serverId":uuid::Uuid::new_v4().to_string(),"expectedRecordVersion":1});
        let parsed: StatusRequest =
            serde_json::from_value(request.clone()).expect("latest task request");
        assert!(parsed.operation_id.is_none());
        let id = uuid::Uuid::new_v4().to_string();
        request["operationId"] = id.clone().into();
        let parsed: StatusRequest =
            serde_json::from_value(request.clone()).expect("exact task request");
        assert_eq!(parsed.operation_id.as_deref(), Some(id.as_str()));
        request["remotePath"] = "/etc/apt".into();
        assert!(serde_json::from_value::<StatusRequest>(request).is_err());
        for code in [
            "PLAN_EXPIRED",
            "INSTALL_HOST_CHANGED",
            "INSTALL_PREFLIGHT_BLOCKED",
            "INSTALL_SNAPSHOT_CHANGED",
        ] {
            assert_eq!(remote_error(code).code, code);
        }
    }

    #[test]
    fn remote_task_errors_keep_known_admission_causes() {
        let timeout = remote_error("INSTALL_CONTROLLER_START_TIMEOUT");
        assert_eq!(timeout.code, "INSTALL_CONTROLLER_START_TIMEOUT");
        assert_eq!(timeout.message, "启动持久 Docker 安装任务超时");

        let invalid_state = remote_error("INSTALL_TASK_STATE_INVALID");
        assert_eq!(invalid_state.code, "INSTALL_TASK_STATE_INVALID");
        assert_eq!(invalid_state.message, "远端 Docker 安装任务状态文件无效");

        let unknown = remote_error("unknown-controller-code");
        assert_eq!(unknown.code, "DOCKER_INSTALL_TASK_FAILED");
    }
}
