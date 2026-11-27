use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::{InstanceTarget, RuntimeStatus},
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use ssh2::{ExtendedData, Session};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "startInstance";
const SCRIPT: &str = include_str!("../../remote-scripts/actions/instance-runtime-start.py");
const RELEASE_HELPERS: &str = include_str!("../../remote-scripts/actions/instance-release.py");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StartRequest {
    pub schema_version: u32,
    pub action: String,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteResult {
    schema_version: u32,
    request_id: String,
    operation_id: String,
    host_id: String,
    instance_id: String,
    revision: u64,
    status: String,
    verification_level: String,
    started_shards: Vec<String>,
    running_shards: Vec<String>,
    listeners: BTreeMap<String, Vec<u16>>,
    public_connectivity_verified: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StartResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub operation_id: String,
    pub target: InstanceTarget,
    pub record_version: u64,
    pub revision: u64,
    pub status: String,
    pub verification_level: String,
    pub started_shards: Vec<String>,
    pub running_shards: Vec<String>,
    pub listeners: BTreeMap<String, Vec<u16>>,
    pub public_connectivity_verified: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimePort {
    pub shard: String,
    pub purpose: String,
    pub protocol: String,
    pub container_port: u16,
    pub host_port: Option<u16>,
    pub bind_address: Option<String>,
    pub published: Option<bool>,
    pub listening: Option<bool>,
    pub required: bool,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeShard {
    pub shard: String,
    pub container_status: String,
    pub process_running: Option<bool>,
    pub ready: bool,
    pub installed_build: Option<String>,
    pub missing_ports: Vec<u16>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeSnapshot {
    pub host_id: String,
    pub instance_id: String,
    pub captured_at_unix_seconds: u64,
    pub captured_at: String,
    pub config_revision: u64,
    pub status: RuntimeStatus,
    pub verification_level: String,
    pub installed_build: Option<String>,
    pub build_source: String,
    pub release_id: Option<String>,
    pub max_players: Option<u16>,
    pub players: Option<u16>,
    pub game_mode: Option<String>,
    pub pvp: Option<bool>,
    pub desired_vs_deployed: String,
    pub shards: Vec<RuntimeShard>,
    pub ports: Vec<RuntimePort>,
    pub warnings: Vec<String>,
    pub public_connectivity_verified: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RemoteInspection {
    schema_version: u32,
    request_id: String,
    host_id: String,
    instances: Vec<RuntimeSnapshot>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "instance_runtime",
        OP,
        message,
        "查看错误原因，刷新运行状态后再重试",
    )
}

fn remote_error(envelope: &Value) -> Box<CommandError> {
    let code = envelope
        .get("code")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let (stable, message) = match code {
        "HOST_IDENTITY_MISMATCH" => ("HOST_IDENTITY_MISMATCH", "启动目标主机身份不一致"),
        "INSTANCE_IDENTITY_MISMATCH" => {
            ("INSTANCE_IDENTITY_MISMATCH", "专服身份与远端实例记录不一致")
        }
        "INSTANCE_NOT_FOUND" => ("INSTANCE_NOT_FOUND", "专服不存在或尚未完成创建"),
        "REVISION_CONFLICT" => ("REVISION_CONFLICT", "专服配置版本已变化，请刷新后重试"),
        "INSTANCE_START_CONFIG_NOT_APPLIED" => (
            "INSTANCE_START_CONFIG_NOT_APPLIED",
            "当前期望配置尚未应用，先应用世界配置",
        ),
        "INSTANCE_START_RUNTIME_NOT_READY" => (
            "INSTANCE_START_RUNTIME_NOT_READY",
            "容器已启动，但 DST 进程或分片 UDP 监听未在时限内就绪",
        ),
        "INSTANCE_START_CONTAINER_EXITED" => (
            "INSTANCE_START_CONTAINER_EXITED",
            "DST 分片已退出或正在反复重启，请检查该分片的启动日志",
        ),
        "INSTANCE_START_CONTAINER_MISSING" => (
            "INSTANCE_START_CONTAINER_MISSING",
            "缺少预期的 Master/Caves 容器",
        ),
        "INSTANCE_START_RESOURCE_IDENTITY_INVALID" => (
            "INSTANCE_START_RESOURCE_IDENTITY_INVALID",
            "容器身份标签与专服不匹配，未启动",
        ),
        "INSTANCE_START_DOCKER_UNAVAILABLE" => {
            ("INSTANCE_START_DOCKER_UNAVAILABLE", "远端 Docker 不可用")
        }
        "INSTANCE_START_DOCKER_RESPONSE_INVALID" => (
            "INSTANCE_START_DOCKER_RESPONSE_INVALID",
            "Docker 返回了无法识别的容器状态",
        ),
        "INSTANCE_START_STATE_INVALID"
        | "WORLD_CONFIG_STATE_INVALID"
        | "WORLD_SECRET_FILE_INVALID" => (
            "INSTANCE_START_STATE_INVALID",
            "实例运行配置或受保护文件状态无效",
        ),
        "INSTANCE_START_CONFIG_INVALID" => (
            "INSTANCE_START_CONFIG_INVALID",
            "Master/Caves server.ini 缺失、权限异常或端口配置无效",
        ),
        "WORLD_TOKEN_MISSING" => ("WORLD_TOKEN_MISSING", "Klei Token 缺失或格式无效"),
        "WORLD_RELEASE_MISSING" | "WORLD_RELEASE_INVALID" => (
            "WORLD_RELEASE_INVALID",
            "专服缺少通过基础验证的活动 DST Release",
        ),
        "INSTANCE_START_INPUT_INVALID" => (
            "INSTANCE_START_INPUT_INVALID",
            "运行操作请求字段无效，请刷新后重试",
        ),
        "INSTANCE_RESTART_NOT_RUNNING" => (
            "INSTANCE_RESTART_NOT_RUNNING",
            "只有全部分片运行时才能执行重启，请改用启动或停止",
        ),
        "INSTANCE_STOP_RUNTIME_NOT_STOPPED" => (
            "INSTANCE_STOP_RUNTIME_NOT_STOPPED",
            "Docker 未确认所有分片已停止，请刷新状态后重试",
        ),
        "WORLD_DEPLOYMENT_MISSING" => ("WORLD_DEPLOYMENT_MISSING", "专服容器尚未完成部署"),
        "ROOT_REQUIRED" => ("ROOT_REQUIRED", "远端需要 root 权限启动受管专服"),
        "INSTANCE_START_LOCK_FAILED" => ("INSTANCE_START_LOCK_FAILED", "无法取得专服运行锁"),
        "INSTANCE_START_COMPOSE_DRIFT" => (
            "INSTANCE_START_COMPOSE_DRIFT",
            "Compose 与受管模板不一致，未自动覆盖；请核对自定义改动",
        ),
        "INSTANCE_START_RUNTIME_PATH_INVALID" => (
            "INSTANCE_START_RUNTIME_PATH_INVALID",
            "无法为 DST 运行用户准备存档或 MOD 目录，请检查目录归属和权限",
        ),
        "INSTANCE_START_COMPOSE_FAILED" => (
            "INSTANCE_START_COMPOSE_FAILED",
            "Docker Compose 未能启动专服容器",
        ),
        "INSTANCE_START_TIMEOUT" => ("INSTANCE_START_TIMEOUT", "专服启动检查超时"),
        _ => (
            "INSTANCE_START_FAILED",
            "远端启动检查失败；请查看错误码和失败阶段",
        ),
    };
    let mut result = error(stable, message);
    let details = envelope.get("details").and_then(Value::as_object);
    let failure_stage = details
        .and_then(|value| value.get("failureStage"))
        .and_then(Value::as_str)
        .filter(|stage| {
            [
                "initialize",
                "validateRequest",
                "readHostState",
                "acquireInstanceLock",
                "validateManifest",
                "validateAppliedConfig",
                "validateActiveRelease",
                "validateToken",
                "validateShardConfig",
                "validateContainers",
                "repairRuntimeLayout",
                "stopServices",
                "startServices",
                "restartServices",
                "waitForRuntime",
            ]
            .contains(stage)
        });
    let failure_type = details
        .and_then(|value| value.get("failureType"))
        .and_then(Value::as_str)
        .filter(|kind| {
            [
                "OSError",
                "PermissionError",
                "FileNotFoundError",
                "NotADirectoryError",
                "ValueError",
                "TypeError",
                "KeyError",
                "AttributeError",
                "RuntimeError",
                "Error",
                "Exception",
            ]
            .contains(kind)
        });
    let safe_code = (1..=96).contains(&code.len())
        && code
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_');
    result.details = json!({
        "remoteCode": if safe_code { Some(code) } else { None },
        "failureStage": failure_stage,
        "failureType": failure_type,
    });
    if stable == "INSTANCE_START_RUNTIME_NOT_READY" {
        result.remote_changed = Some(true);
        result.recovery_steps =
            vec!["核对各分片的实际进程、已监听端口及缺少端口，再检查对应启动日志"];
        for key in [
            "masterMissingPorts",
            "masterObservedPorts",
            "cavesMissingPorts",
            "cavesObservedPorts",
        ] {
            if let Some(value) = details.and_then(|value| value.get(key)) {
                if value.as_array().is_some_and(|ports| {
                    ports.len() <= 64
                        && ports.iter().all(|port| {
                            port.as_u64()
                                .is_some_and(|port| (1..=65535).contains(&port))
                        })
                }) {
                    result.details[key] = value.clone();
                }
            }
        }
        for key in ["masterProcessRunning", "cavesProcessRunning"] {
            if let Some(value) = details
                .and_then(|value| value.get(key))
                .filter(|value| value.is_boolean())
            {
                result.details[key] = value.clone();
            }
        }
    }
    if stable == "INSTANCE_START_CONTAINER_EXITED" {
        result.remote_changed = Some(true);
        result.recovery_steps =
            vec!["查看对应 Master/Caves 容器最后一次启动的错误日志，修复退出原因后重试"];
        for (key, valid) in [
            (
                "shard",
                details
                    .and_then(|value| value.get("shard"))
                    .filter(|value| {
                        value
                            .as_str()
                            .is_some_and(|value| ["master", "caves"].contains(&value))
                    }),
            ),
            (
                "containerStatus",
                details
                    .and_then(|value| value.get("containerStatus"))
                    .filter(|value| {
                        value
                            .as_str()
                            .is_some_and(|value| ["restarting", "exited", "dead"].contains(&value))
                    }),
            ),
            (
                "exitCode",
                details
                    .and_then(|value| value.get("exitCode"))
                    .filter(|value| value.as_u64().is_some_and(|value| value <= 255)),
            ),
            (
                "restartCount",
                details
                    .and_then(|value| value.get("restartCount"))
                    .filter(|value| {
                        value
                            .as_u64()
                            .is_some_and(|value| value <= crate::server_input::MAX_REVISION)
                    }),
            ),
            (
                "oomKilled",
                details
                    .and_then(|value| value.get("oomKilled"))
                    .filter(|value| value.is_boolean()),
            ),
        ] {
            if let Some(value) = valid {
                result.details[key] = value.clone();
            }
        }
    }
    result
}

fn execute(session: &Session, request: &Value) -> CommandResult<Value> {
    let request_data = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("INSTANCE_START_PROTOCOL_INVALID", "启动请求编码失败"))?,
    );
    let script_data = STANDARD.encode(SCRIPT.as_bytes());
    let helpers_data = STANDARD.encode(RELEASE_HELPERS.as_bytes());
    let source = format!(
        "import base64,json\nRELEASE_HELPERS={{}}\nexec(compile(base64.b64decode('{helpers_data}'),'<instance-release>','exec'),RELEASE_HELPERS)\nexec(compile(base64.b64decode('{script_data}'),'<instance-runtime-start>','exec'))\nmain(json.loads(base64.b64decode('{request_data}').decode('utf-8')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("INSTANCE_START_CHANNEL_FAILED", "无法建立远端启动通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("INSTANCE_START_CHANNEL_FAILED", "无法配置远端启动通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "INSTANCE_START_PYTHON_REQUIRED",
            "远端需要 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("INSTANCE_START_CHANNEL_FAILED", "无法提交远端启动请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("INSTANCE_START_CHANNEL_FAILED", "无法结束远端启动请求"))?;
    session.set_blocking(false);
    let inspection = request.get("action").and_then(Value::as_str) == Some("inspect");
    let deadline = Instant::now() + Duration::from_secs(if inspection { 35 } else { 150 });
    let max_output = if inspection {
        2 * 1024 * 1024
    } else {
        64 * 1024
    };
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("INSTANCE_START_TIMEOUT", "专服启动检查超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= max_output => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => {
                return Err(error(
                    "INSTANCE_START_CHANNEL_FAILED",
                    "无法读取远端启动结果",
                ))
            }
        }
    }
    session.set_blocking(true);
    let timeout = session.timeout();
    session.set_timeout(1000);
    let closed = channel.wait_close();
    session.set_timeout(timeout);
    closed.map_err(|_| error("INSTANCE_START_CHANNEL_FAILED", "远端启动通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("INSTANCE_START_PROTOCOL_INVALID", "远端启动响应格式无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(remote_error(&envelope));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("INSTANCE_START_PROTOCOL_INVALID", "远端响应缺少结果"))
}

pub fn inspect_many(
    session: &Session,
    host_id: &str,
    request_id: &str,
    registry_revision: u64,
    ids: &[String],
) -> CommandResult<Vec<RuntimeSnapshot>> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let response: RemoteInspection = serde_json::from_value(execute(
        session,
        &json!({
            "schemaVersion": 1, "action": "inspect", "requestId": request_id, "hostId": host_id,
            "instanceIds": ids, "expectedRegistryRevision": registry_revision,
        }),
    )?)
    .map_err(|_| error("INSTANCE_RUNTIME_PROTOCOL_INVALID", "运行采集响应字段无效"))?;
    if response.schema_version != 1
        || response.request_id != request_id
        || response.host_id != host_id
        || response.instances.len() != ids.len()
    {
        return Err(error(
            "INSTANCE_RUNTIME_PROTOCOL_INVALID",
            "运行采集响应身份无效",
        ));
    }
    let mut seen = std::collections::BTreeSet::new();
    for snapshot in &response.instances {
        if snapshot.host_id != host_id
            || !ids.contains(&snapshot.instance_id)
            || !seen.insert(&snapshot.instance_id)
            || snapshot.captured_at_unix_seconds == 0
            || snapshot.captured_at.is_empty()
            || snapshot.captured_at.len() > 64
            || snapshot.config_revision > crate::server_input::MAX_REVISION
            || snapshot.public_connectivity_verified
            || snapshot.shards.len() > 2
            || snapshot.ports.len() > 7
            || snapshot.warnings.len() > 16
            || !["unverified", "processOnly", "localVerified"]
                .contains(&snapshot.verification_level.as_str())
            || !["unknown", "containerFile", "releaseMetadata"]
                .contains(&snapshot.build_source.as_str())
            || !["unknown", "pending", "inSync"].contains(&snapshot.desired_vs_deployed.as_str())
            || snapshot.installed_build.as_ref().is_some_and(|value| {
                value.is_empty() || value.len() > 64 || value.chars().any(char::is_control)
            })
            || snapshot
                .max_players
                .is_some_and(|value| value == 0 || value > 128)
            || snapshot.game_mode.as_ref().is_some_and(|value| {
                !["relaxed", "survival", "endless", "wilderness", "lightsout"]
                    .contains(&value.as_str())
            })
            || snapshot.ports.iter().any(|port| {
                !["master", "caves"].contains(&port.shard.as_str())
                    || !["game", "steamMaster", "steamAuth", "shard"]
                        .contains(&port.purpose.as_str())
                    || port.protocol != "udp"
                    || port.container_port == 0
                    || port.host_port == Some(0)
                    || (port.purpose == "shard"
                        && (port.host_port.is_some() || port.published != Some(false)))
                    || port
                        .bind_address
                        .as_ref()
                        .is_some_and(|value| value != "0.0.0.0")
            })
            || snapshot.shards.iter().any(|shard| {
                !["master", "caves"].contains(&shard.shard.as_str())
                    || ![
                        "created",
                        "running",
                        "paused",
                        "restarting",
                        "removing",
                        "exited",
                        "dead",
                    ]
                    .contains(&shard.container_status.as_str())
                    || shard.missing_ports.len() > 3
                    || shard.missing_ports.contains(&0)
                    || (shard.ready
                        && (shard.process_running != Some(true)
                            || shard.container_status != "running"
                            || !shard.missing_ports.is_empty()))
            })
        {
            return Err(error(
                "INSTANCE_RUNTIME_PROTOCOL_INVALID",
                "运行采集响应证据无效",
            ));
        }
        if snapshot.status == RuntimeStatus::Running
            && (snapshot.shards.is_empty()
                || snapshot.shards.iter().any(|shard| !shard.ready)
                || snapshot.verification_level != "localVerified")
        {
            return Err(error(
                "INSTANCE_RUNTIME_PROTOCOL_INVALID",
                "未就绪分片不能报告运行中",
            ));
        }
    }
    Ok(response.instances)
}

pub fn start(database: &Path, request: StartRequest) -> CommandResult<StartResponse> {
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    crate::server_input::validate_id(&request.target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&request.target.host_id, OP, "hostId")?;
    crate::server_input::validate_id(&request.target.instance_id, OP, "instanceId")?;
    if request.schema_version != 1
        || !["start", "stop", "restart"].contains(&request.action.as_str())
        || request.expected_revision > crate::server_input::MAX_REVISION
    {
        return Err(error("INSTANCE_START_INPUT_INVALID", "运行操作请求无效"));
    }
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "专服启动目标主机不一致"));
        }
        let remote: RemoteResult = serde_json::from_value(execute(
            session,
            &json!({
                "schemaVersion": 1,
                "action": request.action,
                "requestId": request.request_id,
                "operationId": request.operation_id,
                "hostId": request.target.host_id,
                "instanceId": request.target.instance_id,
                "expectedRevision": request.expected_revision,
            }),
        )?)
        .map_err(|_| error("INSTANCE_START_PROTOCOL_INVALID", "远端启动结果字段无效"))?;
        let response_identity_valid = remote.schema_version == 1
            && remote.host_id == request.target.host_id
            && remote.instance_id == request.target.instance_id
            && remote.revision == request.expected_revision
            && remote.request_id == request.request_id
            && remote.operation_id == request.operation_id
            && !remote.public_connectivity_verified;
        let expected_shards = if response_identity_valid && request.action == "stop" {
            remote.status == "stopped"
                && remote.running_shards.is_empty()
                && remote.listeners.is_empty()
        } else if response_identity_valid
            && remote.status == "running"
            && remote.verification_level == "localVerified"
            && !remote.running_shards.is_empty()
            && remote.running_shards.len() <= 2
            && remote.listeners.len() == remote.running_shards.len()
        {
            remote.running_shards.iter().all(|shard| {
                (shard == "master" || shard == "caves")
                    && remote.listeners.get(shard).is_some_and(|ports| {
                        ports.len() == 2
                            && ports.iter().all(|port| *port > 0)
                            && ports
                                .iter()
                                .collect::<std::collections::BTreeSet<_>>()
                                .len()
                                == ports.len()
                    })
            })
        } else {
            false
        };
        if !expected_shards
            || remote
                .started_shards
                .iter()
                .any(|shard| !remote.running_shards.contains(shard))
        {
            return Err(error(
                "INSTANCE_START_PROTOCOL_INVALID",
                "远端运行操作结果身份或状态证据无效",
            ));
        }
        Ok(StartResponse {
            schema_version: 1,
            request_id: request.request_id,
            operation_id: request.operation_id,
            target: request.target,
            record_version: connection.expected_record_version,
            revision: remote.revision,
            status: remote.status,
            verification_level: remote.verification_level,
            started_shards: remote.started_shards,
            running_shards: remote.running_shards,
            listeners: remote.listeners,
            public_connectivity_verified: remote.public_connectivity_verified,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::RemoteResult;

    #[test]
    fn start_result_keeps_public_connectivity_separate_from_local_readiness() {
        let result: RemoteResult = serde_json::from_value(serde_json::json!({
            "schemaVersion": 1,
            "requestId": "11111111-1111-4111-8111-111111111111",
            "operationId": "22222222-2222-4222-8222-222222222222",
            "hostId": "33333333-3333-4333-8333-333333333333",
            "instanceId": "44444444-4444-4444-8444-444444444444",
            "revision": 3,
            "status": "running",
            "verificationLevel": "localVerified",
            "startedShards": ["master", "caves"],
            "runningShards": ["master", "caves"],
            "listeners": {"master": [11000, 27016], "caves": [11001, 27017]},
            "publicConnectivityVerified": false
        }))
        .expect("well-shaped runtime result should deserialize");
        assert_eq!(result.running_shards.len(), 2);
        assert!(!result.public_connectivity_verified);
    }

    #[test]
    fn unknown_remote_start_code_is_preserved_with_allowlisted_failure_context() {
        let error = super::remote_error(&serde_json::json!({
            "code": "INSTANCE_START_NEW_FAILURE",
            "details": {"failureStage": "startServices", "failureType": "PermissionError",
                        "secret": "never expose"}
        }));
        assert_eq!(error.code, "INSTANCE_START_FAILED");
        assert_eq!(error.details["remoteCode"], "INSTANCE_START_NEW_FAILURE");
        assert_eq!(error.details["failureStage"], "startServices");
        assert_eq!(error.details["failureType"], "PermissionError");
        assert!(error.details.get("secret").is_none());
    }

    #[test]
    fn exited_shard_details_are_bounded_and_do_not_include_docker_error_text() {
        let error = super::remote_error(&serde_json::json!({
            "code": "INSTANCE_START_CONTAINER_EXITED",
            "details": {"failureStage": "waitForRuntime", "shard": "master", "exitCode": 1,
                "restartCount": 7, "oomKilled": false, "containerStatus": "restarting",
                "error": "private-token", "logs": "private-password"}
        }));
        assert_eq!(error.code, "INSTANCE_START_CONTAINER_EXITED");
        assert_eq!(error.details["shard"], "master");
        assert_eq!(error.details["exitCode"], 1);
        assert!(error.details.get("error").is_none());
        assert!(error.details.get("logs").is_none());
        assert_eq!(error.remote_changed, Some(true));
    }

    #[test]
    fn readiness_timeout_preserves_only_safe_process_and_listener_evidence() {
        let failure = super::remote_error(&serde_json::json!({
            "code": "INSTANCE_START_RUNTIME_NOT_READY",
            "details": {"failureStage": "waitForRuntime", "masterProcessRunning": true,
                "cavesProcessRunning": true, "masterMissingPorts": [11000],
                "cavesMissingPorts": [], "masterObservedPorts": [10888, 27016],
                "cavesObservedPorts": [11001, 27017], "token": "private"}
        }));
        assert_eq!(
            failure.details["masterMissingPorts"],
            serde_json::json!([11000])
        );
        assert_eq!(failure.details["cavesProcessRunning"], true);
        assert!(failure.details.get("token").is_none());
        assert_eq!(failure.remote_changed, Some(true));
    }
}
