use crate::{
    dto::{CommandError, CommandResult},
    persistence,
    ssh_dto::ConnectionRequest,
    ssh_state,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "initializeHost";
const SCRIPT: &str = include_str!("../../remote-scripts/actions/initialize-host.py");
const ROOT: &str = "/var/lib/famine-hub";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InitializeRequest {
    #[serde(flatten)]
    pub connection: ConnectionRequest,
    pub plan_id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializationResponse {
    schema_version: u32,
    request_id: String,
    server_id: String,
    record_version: u64,
    pub plan: Option<InitializationPlan>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializationPlan {
    plan_id: String,
    operation_id: String,
    host_id: String,
    expires_at_unix_seconds: u64,
    status: String,
    management_root: &'static str,
    paths: Vec<String>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "initialization",
        OP,
        message,
        "核对主机权限与管理目录后重查原操作；不要删除或覆盖现有文件",
    )
}
fn remote_error(code: &str) -> Box<CommandError> {
    match code {
        "INITIALIZATION_PERMISSION_DENIED" => CommandError::new(
            "INITIALIZATION_PERMISSION_DENIED",
            "initialization",
            OP,
            "SSH 用户没有管理目录写入权限",
            "请管理员仅为缺失或空目录预授权固定路径，再重新检查；已有数据先核实归属",
        ),
        "INITIALIZATION_PATH_UNSAFE" => error(
            "INITIALIZATION_PATH_UNSAFE",
            "管理路径的所有者、权限或类型不安全",
        ),
        "INITIALIZATION_EXISTING_DATA" => error(
            "INITIALIZATION_EXISTING_DATA",
            "管理目录存在其他文件，已拒绝覆盖",
        ),
        "INITIALIZATION_IDENTITY_INVALID" => {
            error("INITIALIZATION_IDENTITY_INVALID", "现有身份或注册表无效")
        }
        "INITIALIZATION_BUSY" => error("INITIALIZATION_BUSY", "其他客户端正在初始化，请稍后核对"),
        "INITIALIZATION_LINUX_REQUIRED" => {
            error("INITIALIZATION_LINUX_REQUIRED", "初始化仅支持 Linux 主机")
        }
        "HOST_IDENTITY_MISMATCH" => error("HOST_IDENTITY_MISMATCH", "初始化回执与主机身份不一致"),
        "OPERATION_ID_REUSED" => error("OPERATION_ID_REUSED", "操作编号与原初始化输入不一致"),
        "PLAN_STALE" => error("PLAN_STALE", "初始化预览已过期或目录发生变化"),
        _ => error(
            "INITIALIZATION_FILE_INVALID",
            "管理文件不可读取、格式无效或路径类型不安全",
        ),
    }
}

pub(crate) fn execute(session: &Session, input: &Value) -> CommandResult<Value> {
    execute_script(session, input, SCRIPT)
}
fn execute_script(session: &Session, input: &Value, script: &str) -> CommandResult<Value> {
    let body = format!(
        "{script}\nmain(json.loads(base64.b64decode('{}')))\n",
        STANDARD.encode(input.to_string())
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("INITIALIZATION_CHANNEL_FAILED", "无法创建初始化通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("INITIALIZATION_CHANNEL_FAILED", "无法配置初始化通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "INITIALIZATION_RUNTIME_REQUIRED",
            "无法执行 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(body.as_bytes())
        .map_err(|_| uncertain())?;
    channel.send_eof().map_err(|_| uncertain())?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    let read = (|| {
        loop {
            if Instant::now() >= deadline {
                return Err(uncertain());
            }
            match channel.read(&mut buffer) {
                Ok(0) if channel.eof() => break,
                Ok(0) => std::thread::sleep(Duration::from_millis(20)),
                Ok(count) if output.len() + count <= 16384 => {
                    output.extend_from_slice(&buffer[..count])
                }
                Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                _ => return Err(uncertain()),
            }
        }
        Ok(())
    })();
    session.set_blocking(true);
    session.set_timeout(1000);
    read?;
    channel.wait_close().map_err(|_| uncertain())?;
    if channel.exit_status().map_err(|_| uncertain())? != 0 {
        return Err(error(
            "INITIALIZATION_RUNTIME_REQUIRED",
            "初始化脚本未能运行，需要 /usr/bin/python3",
        ));
    }
    decode_output(&output)
}
fn uncertain() -> Box<CommandError> {
    let mut result = error(
        "INITIALIZATION_OUTCOME_UNKNOWN",
        "初始化结果尚未确认，请重查原操作",
    );
    result.remote_changed = None;
    result.retryable = true;
    result
}
fn decode_output(bytes: &[u8]) -> CommandResult<Value> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| uncertain())?;
    match value.get("ok").and_then(Value::as_bool) {
        Some(true) => value
            .get("data")
            .filter(|data| data.is_object())
            .cloned()
            .ok_or_else(uncertain),
        Some(false) => Err(remote_error(
            value.get("code").and_then(Value::as_str).unwrap_or(""),
        )),
        None => Err(uncertain()),
    }
}
fn response(
    request: &ConnectionRequest,
    plan: Option<InitializationPlan>,
) -> InitializationResponse {
    InitializationResponse {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.server_id.clone(),
        record_version: request.expected_record_version,
        plan,
    }
}
fn paths() -> Vec<String> {
    [
        ROOT.to_string(),
        format!("{ROOT}/host.json"),
        format!("{ROOT}/registry.json"),
        format!("{ROOT}.bootstrap"),
    ]
    .into()
}
fn plan_view(
    plan_id: &str,
    operation_id: &str,
    payload: &Value,
    status: &str,
) -> CommandResult<InitializationPlan> {
    Ok(InitializationPlan {
        plan_id: plan_id.into(),
        operation_id: operation_id.into(),
        host_id: payload
            .get("hostId")
            .and_then(Value::as_str)
            .ok_or_else(uncertain)?
            .into(),
        expires_at_unix_seconds: payload
            .get("expiresAtUnixSeconds")
            .and_then(Value::as_u64)
            .ok_or_else(uncertain)?,
        status: status.into(),
        management_root: ROOT,
        paths: paths(),
    })
}
fn fingerprint(database: &Path, request: &ConnectionRequest) -> CommandResult<String> {
    ssh_state::load(database, request, OP)?
        .trust
        .map(|key| key.fingerprint)
        .ok_or_else(|| error("SSH_TRUST_REQUIRED", "请先确认 SSH 指纹"))
}
fn validate_payload(
    payload: &Value,
    request: &ConnectionRequest,
    fingerprint: &str,
) -> CommandResult<()> {
    if payload["serverId"] != request.server_id
        || payload["recordVersion"] != request.expected_record_version
        || payload["fingerprint"] != fingerprint
        || payload["scriptDigest"] != format!("{:x}", Sha256::digest(SCRIPT.as_bytes()))
    {
        return Err(error(
            "PLAN_STALE",
            "连接身份或初始化脚本已变化，请核对原操作",
        ));
    }
    Ok(())
}
type StoredPlan = (String, String, String);
fn pending(database: &Path, request: &ConnectionRequest) -> CommandResult<Option<StoredPlan>> {
    persistence::open(database).and_then(|connection| connection.query_row("SELECT plan_id,operation_id,payload FROM host_initialization_plans WHERE server_id=?1 AND record_version=?2 AND started=1 AND finished=0", rusqlite::params![request.server_id, request.expected_record_version], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?))).optional()).map_err(|_| CommandError::storage(OP))
}
fn finish(database: &Path, plan_id: &str) -> CommandResult<()> {
    persistence::open(database)
        .and_then(|connection| {
            connection.execute(
                "UPDATE host_initialization_plans SET finished=1 WHERE plan_id=?1",
                [plan_id],
            )
        })
        .map(|_| ())
        .map_err(|_| CommandError::storage(OP))
}
fn verify_result(
    session: &Session,
    database: &Path,
    request: &ConnectionRequest,
    expected: &str,
) -> CommandResult<()> {
    crate::registry::list_at(session, Path::new(ROOT), request, expected)?;
    ssh_state::bind_host(database, request, Some(expected))
}

pub fn preview(
    database: &Path,
    request: ConnectionRequest,
) -> CommandResult<InitializationResponse> {
    crate::ssh::with_verified_session(database, &request, |session, host_id| {
        if pending(database, &request)?.is_some() {
            return Err(error("INITIALIZATION_PENDING", "请先重查原初始化操作"));
        }
        let current = execute(session, &json!({"action":"preview"}))?;
        if host_id.is_some() || !current["hostId"].is_null() {
            return Err(error(
                "HOST_ALREADY_INITIALIZED",
                "主机已初始化，无需重复创建",
            ));
        }
        let snapshot = current["snapshot"]
            .as_str()
            .filter(|value| value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
            .ok_or_else(uncertain)?;
        let plan_id = uuid::Uuid::new_v4().to_string();
        let operation_id = uuid::Uuid::new_v4().to_string();
        let payload = json!({"serverId":request.server_id,"recordVersion":request.expected_record_version,"fingerprint":fingerprint(database,&request)?,"hostId":uuid::Uuid::new_v4().to_string(),"snapshot":snapshot,"expiresAtUnixSeconds":ssh_state::now()?+600,"scriptDigest":format!("{:x}",Sha256::digest(SCRIPT.as_bytes()))});
        persistence::open(database).and_then(|connection| connection.execute("INSERT INTO host_initialization_plans(plan_id,operation_id,server_id,record_version,payload) VALUES (?1,?2,?3,?4,?5)", rusqlite::params![plan_id,operation_id,request.server_id,request.expected_record_version,payload.to_string()])).map_err(|_| CommandError::storage(OP))?;
        Ok(response(
            &request,
            Some(plan_view(&plan_id, &operation_id, &payload, "preview")?),
        ))
    })
}

pub fn inspect(
    database: &Path,
    request: ConnectionRequest,
) -> CommandResult<InitializationResponse> {
    crate::ssh::with_verified_session(database, &request, |session, _| {
        let Some((plan_id, operation_id, serialized)) = pending(database, &request)? else {
            return Ok(response(&request, None));
        };
        let payload: Value =
            serde_json::from_str(&serialized).map_err(|_| CommandError::storage(OP))?;
        validate_payload(&payload, &request, &fingerprint(database, &request)?)?;
        let remote = execute(
            session,
            &json!({"action":"query","operationId":operation_id,"payload":payload}),
        )?;
        let status = remote["status"].as_str().ok_or_else(uncertain)?;
        if status == "notFound" {
            // A submitted SSH process may not have reached remote admission yet.
            // Keep the original operation while admission is still allowed.
            if payload["expiresAtUnixSeconds"]
                .as_u64()
                .ok_or_else(uncertain)?
                >= ssh_state::now()?
            {
                return Ok(response(
                    &request,
                    Some(plan_view(&plan_id, &operation_id, &payload, "incomplete")?),
                ));
            }
            finish(database, &plan_id)?;
            return Ok(response(&request, None));
        }
        if !["incomplete", "succeeded"].contains(&status) || remote["hostId"] != payload["hostId"] {
            return Err(uncertain());
        }
        if status == "succeeded" {
            verify_result(
                session,
                database,
                &request,
                payload["hostId"].as_str().ok_or_else(uncertain)?,
            )?;
            finish(database, &plan_id)?;
        }
        Ok(response(
            &request,
            Some(plan_view(&plan_id, &operation_id, &payload, status)?),
        ))
    })
}

pub fn initialize(
    database: &Path,
    request: InitializeRequest,
) -> CommandResult<InitializationResponse> {
    crate::server_input::validate_id(&request.plan_id, OP, "planId")?;
    let connection_request = &request.connection;
    crate::ssh::with_verified_session(database, connection_request, |session, host_id| {
        let (operation_id, serialized): (String,String) = persistence::open(database).and_then(|connection| connection.query_row("SELECT operation_id,payload FROM host_initialization_plans WHERE plan_id=?1 AND server_id=?2 AND record_version=?3",rusqlite::params![request.plan_id,connection_request.server_id,connection_request.expected_record_version],|row| Ok((row.get(0)?,row.get(1)?)))).map_err(|_| error("PLAN_STALE","初始化预览不存在或连接版本已变化"))?;
        let payload: Value =
            serde_json::from_str(&serialized).map_err(|_| CommandError::storage(OP))?;
        validate_payload(
            &payload,
            connection_request,
            &fingerprint(database, connection_request)?,
        )?;
        let expected = payload["hostId"].as_str().ok_or_else(uncertain)?;
        if host_id.as_deref().is_some_and(|host| host != expected) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "主机身份与初始化目标不一致",
            ));
        }
        persistence::open(database)
            .and_then(|connection| {
                connection.execute(
                    "UPDATE host_initialization_plans SET started=1,finished=0 WHERE plan_id=?1",
                    [&request.plan_id],
                )
            })
            .map_err(|_| error("INITIALIZATION_PENDING", "存在待核对的初始化操作"))?;
        let result = execute(
            session,
            &json!({"action":"initialize","operationId":operation_id,"payload":payload}),
        );
        let remote = result.map_err(|mut error| {
            error.remote_changed = None;
            error
        })?;
        if remote["status"] != "succeeded" || remote["hostId"] != expected {
            return Err(uncertain());
        }
        verify_result(session, database, connection_request, expected).map_err(|mut error| {
            error.remote_changed = Some(true);
            error
        })?;
        finish(database, &request.plan_id)?;
        Ok(response(
            connection_request,
            Some(plan_view(
                &request.plan_id,
                &operation_id,
                &payload,
                "succeeded",
            )?),
        ))
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    #[test]
    fn protocol_rejects_banner_truncation_and_unknown_errors() {
        assert!(decode_output(b"banner\n{\"ok\":true,\"data\":{}}").is_err());
        assert!(decode_output(b"{\"ok\":true}").is_err());
        assert!(decode_output(b"{\"ok\":true,\"data\":null}").is_err());
        assert!(
            matches!(decode_output(b"{\"ok\":false,\"code\":\"PLAN_STALE\"}"),Err(error) if error.code=="PLAN_STALE")
        );
        assert!(
            matches!(decode_output(b"{\"ok\":false,\"code\":\"secret remote stderr\"}"),Err(error) if error.code=="INITIALIZATION_FILE_INVALID")
        );
    }
    #[test]
    fn initialize_request_accepts_plan_and_rejects_arbitrary_paths() {
        let value = json!({"schemaVersion":1,"requestId":uuid::Uuid::new_v4().to_string(),"serverId":uuid::Uuid::new_v4().to_string(),"expectedRecordVersion":1,"planId":uuid::Uuid::new_v4().to_string()});
        assert!(serde_json::from_value::<InitializeRequest>(value.clone()).is_ok());
        let mut invalid = value;
        invalid["root"] = "/tmp/arbitrary".into();
        assert!(serde_json::from_value::<InitializeRequest>(invalid).is_err());
    }

    pub(crate) fn exercise_ssh(session: &Session, parent: &Path) -> CommandResult<()> {
        let root = parent
            .canonicalize()
            .map_err(|_| error("TEST_FIXTURE", "测试目录不可用"))?
            .join("initialized-over-ssh");
        let script = SCRIPT.replace(
            "ROOT = \"/var/lib/famine-hub\"",
            &format!("ROOT = {}", json!(root.to_string_lossy())),
        );
        // This loopback fixture runs on macOS; emulate only the platform check.
        let script = format!("{script}\nsys.platform = 'linux'\n");
        let before = execute_script(session, &json!({"action":"preview"}), &script)?;
        assert_eq!(before["state"], "missing");
        assert!(!root.exists());
        let operation_id = uuid::Uuid::new_v4().to_string();
        let host_id = uuid::Uuid::new_v4().to_string();
        let payload = json!({"snapshot":before["snapshot"],"hostId":host_id,"expiresAtUnixSeconds":ssh_state::now()?+600});
        let request = json!({"action":"initialize","operationId":operation_id,"payload":payload});
        let result = execute_script(session, &request, &script)?;
        assert_eq!(result["hostId"], host_id);
        assert_eq!(result["status"], "succeeded");
        assert_eq!(execute_script(session, &request, &script)?, result);
        assert_eq!(
            execute_script(
                session,
                &json!({"action":"query","operationId":operation_id,"payload":payload}),
                &script
            )?,
            result
        );
        let connection = ConnectionRequest {
            schema_version: 1,
            request_id: uuid::Uuid::new_v4().to_string(),
            server_id: uuid::Uuid::new_v4().to_string(),
            expected_record_version: 1,
        };
        let list = crate::registry::list_at(session, &root, &connection, &host_id)?;
        assert!(list.instances.is_empty());
        assert_eq!(list.registry_revision, 0);
        Ok(())
    }
}
