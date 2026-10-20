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

const OP: &str = "cleanupOrphanInstance";
const SCRIPT: &str = include_str!("../../remote-scripts/actions/cleanup-orphan-instance.py");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_registry_revision: u64,
    pub typed_name: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteResult {
    host_id: String,
    instance_id: String,
    registry_revision: u64,
    released_reservations: u64,
    removed: bool,
    #[serde(default)]
    retired: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Response {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub registry_revision: u64,
    pub released_reservations: u64,
    pub removed: bool,
    pub retired: bool,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "instance_cleanup",
        OP,
        message,
        "刷新专服列表并核对远端任务、容器和实例目录后重试",
    )
}

fn remote_error(code: &str) -> Box<CommandError> {
    let (stable, message) = match code {
        "INSTANCE_NOT_FOUND" => ("INSTANCE_NOT_FOUND", "专服记录已不存在，请刷新列表"),
        "REGISTRY_CHANGED" => ("REGISTRY_CHANGED", "专服列表已变化，请刷新后重试"),
        "INSTANCE_CONFIRMATION_MISMATCH" => {
            ("INSTANCE_CONFIRMATION_MISMATCH", "确认名称与专服记录不一致")
        }
        "ORPHAN_INSTANCE_TASK_ACTIVE" => (
            "ORPHAN_INSTANCE_TASK_ACTIVE",
            "专服仍有创建任务或任务状态待核实，不能清理",
        ),
        "ORPHAN_INSTANCE_RESOURCES_PRESENT" => (
            "ORPHAN_INSTANCE_RESOURCES_PRESENT",
            "发现关联 Docker 容器或网络，不能按孤立记录清理",
        ),
        "ORPHAN_INSTANCE_DATA_PRESENT" => (
            "ORPHAN_INSTANCE_DATA_PRESENT",
            "实例目录或归档目标不安全，未移动数据",
        ),
        "ORPHAN_INSTANCE_DOCKER_UNAVAILABLE" => (
            "ORPHAN_INSTANCE_DOCKER_UNAVAILABLE",
            "无法核对主机 Docker 资源",
        ),
        "ORPHAN_INSTANCE_CHECK_TIMEOUT" => (
            "ORPHAN_INSTANCE_CHECK_TIMEOUT",
            "核对专服现场超时，请检查主机状态",
        ),
        "ORPHAN_INSTANCE_STATE_INVALID" => (
            "ORPHAN_INSTANCE_STATE_INVALID",
            "远端专服状态不完整或不安全，未执行清理",
        ),
        "ORPHAN_INSTANCE_SCRIPT_NO_OUTPUT" => (
            "ORPHAN_INSTANCE_SCRIPT_NO_OUTPUT",
            "远端没有返回清理结果；请确认主机 `/usr/bin/python3` 可用并重启最新桌面版本",
        ),
        "ORPHAN_INSTANCE_PROTOCOL_INVALID" => (
            "ORPHAN_INSTANCE_PROTOCOL_INVALID",
            "远端清理脚本响应格式不匹配；请重启最新桌面版本后刷新重试",
        ),
        "ORPHAN_INSTANCE_SCRIPT_FAILED" => (
            "ORPHAN_INSTANCE_SCRIPT_FAILED",
            "远端清理脚本未能执行；请确认主机 Python 环境与脚本版本",
        ),
        "ORPHAN_INSTANCE_SCRIPT_INVALID" => (
            "ORPHAN_INSTANCE_SCRIPT_INVALID",
            "主机执行的清理脚本版本不兼容；请重启并重新构建最新桌面版本",
        ),
        "ORPHAN_INSTANCE_PYTHON_UNAVAILABLE" => (
            "ORPHAN_INSTANCE_PYTHON_UNAVAILABLE",
            "主机缺少可用的 /usr/bin/python3，清理没有执行",
        ),
        "ORPHAN_INSTANCE_PERMISSION_DENIED" => (
            "ORPHAN_INSTANCE_PERMISSION_DENIED",
            "主机拒绝启动清理脚本，请检查 SSH 提权和管理目录权限",
        ),
        "HOST_IDENTITY_MISMATCH" => ("HOST_IDENTITY_MISMATCH", "目标主机身份不一致"),
        _ => (
            "ORPHAN_INSTANCE_CLEANUP_FAILED",
            "孤立专服记录清理失败，请刷新后核实现场",
        ),
    };
    error(stable, message)
}

fn execute(session: &Session, request: &Value) -> CommandResult<RemoteResult> {
    let encoded = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("ORPHAN_INSTANCE_PROTOCOL_INVALID", "清理请求编码失败"))?,
    );
    let source = format!("import base64\n{SCRIPT}\nmain(json.loads(base64.b64decode('{encoded}').decode('utf-8')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("ORPHAN_INSTANCE_CHANNEL_FAILED", "无法创建专服清理通道"))?;
    channel
        .handle_extended_data(ExtendedData::Normal)
        .map_err(|_| error("ORPHAN_INSTANCE_CHANNEL_FAILED", "无法配置专服清理通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "CREATE_RELEASE_RUNTIME_REQUIRED",
            "远端需要 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("ORPHAN_INSTANCE_CHANNEL_FAILED", "无法提交专服清理请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("ORPHAN_INSTANCE_CHANNEL_FAILED", "无法结束专服清理请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut stderr = Vec::new();
    let mut stderr_stream = channel.stderr();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("ORPHAN_INSTANCE_CHECK_TIMEOUT", "核对专服现场超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= 64 * 1024 => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => {
                return Err(error(
                    "ORPHAN_INSTANCE_CHANNEL_FAILED",
                    "无法读取专服清理结果",
                ))
            }
        }
        match stderr_stream.read(&mut buffer) {
            Ok(count) if count > 0 && stderr.len() + count <= 8 * 1024 => {
                stderr.extend_from_slice(&buffer[..count]);
            }
            Ok(_) => {}
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(_) => {
                return Err(error(
                    "ORPHAN_INSTANCE_CHANNEL_FAILED",
                    "无法读取清理脚本状态",
                ))
            }
        }
        if channel.eof() {
            break;
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("ORPHAN_INSTANCE_CHANNEL_FAILED", "专服清理通道未正常结束"))?;
    if output.is_empty() {
        let code = classify_script_failure(channel.exit_status().unwrap_or(-1), &stderr);
        return Err(script_failure_error(code, &stderr));
    }
    let envelope: Value = serde_json::from_slice(&output).map_err(|_| {
        error(
            "ORPHAN_INSTANCE_PROTOCOL_INVALID",
            "远端清理脚本响应不是有效 JSON",
        )
    })?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        let code = envelope
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or_default();
        return Err(remote_error(code));
    }
    serde_json::from_value(envelope.get("data").cloned().unwrap_or(Value::Null))
        .map_err(|_| error("ORPHAN_INSTANCE_PROTOCOL_INVALID", "专服清理结果字段无效"))
}

fn classify_script_failure(exit_status: i32, stderr: &[u8]) -> &'static str {
    let message = String::from_utf8_lossy(stderr).to_ascii_lowercase();
    if exit_status == 127
        || message.contains("/usr/bin/python3: not found")
        || message.contains("python3: command not found")
    {
        "ORPHAN_INSTANCE_PYTHON_UNAVAILABLE"
    } else if message.contains("permission denied") {
        "ORPHAN_INSTANCE_PERMISSION_DENIED"
    } else if message.contains("syntaxerror") || message.contains("indentationerror") {
        "ORPHAN_INSTANCE_SCRIPT_INVALID"
    } else if exit_status != 0 {
        "ORPHAN_INSTANCE_SCRIPT_FAILED"
    } else {
        "ORPHAN_INSTANCE_SCRIPT_NO_OUTPUT"
    }
}

fn python_diagnostic(stderr: &[u8]) -> (Option<&'static str>, Option<u32>) {
    let text = String::from_utf8_lossy(stderr);
    let exception = [
        "ModuleNotFoundError",
        "ImportError",
        "PermissionError",
        "FileNotFoundError",
        "NotADirectoryError",
        "IsADirectoryError",
        "SyntaxError",
        "IndentationError",
        "NameError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "OSError",
        "RuntimeError",
    ]
    .into_iter()
    .find(|name| text.contains(name));
    let line = text.lines().find_map(|value| {
        let marker = value.find(", line ")? + 7;
        let digits: String = value[marker..]
            .chars()
            .take_while(char::is_ascii_digit)
            .collect();
        digits.parse::<u32>().ok()
    });
    (exception, line)
}

fn script_failure_error(code: &'static str, stderr: &[u8]) -> Box<CommandError> {
    let message = match code {
        "ORPHAN_INSTANCE_PYTHON_UNAVAILABLE" => "主机缺少可用的 /usr/bin/python3",
        "ORPHAN_INSTANCE_SCRIPT_INVALID" => "主机执行的清理脚本语法无效或版本不兼容",
        "ORPHAN_INSTANCE_PERMISSION_DENIED" => "主机拒绝执行清理脚本",
        "ORPHAN_INSTANCE_SCRIPT_FAILED" => "清理脚本异常退出",
        _ => "远端清理脚本未返回结果",
    };
    let mut result = error(code, message);
    let (exception, line) = python_diagnostic(stderr);
    if exception.is_some() || line.is_some() {
        result.details = json!({"pythonException": exception, "pythonLine": line});
    }
    result
}

pub fn cleanup(database: &Path, request: Request) -> CommandResult<Response> {
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    crate::server_input::validate_id(&request.target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&request.target.host_id, OP, "hostId")?;
    crate::server_input::validate_id(&request.target.instance_id, OP, "instanceId")?;
    if request.schema_version != 1
        || request.typed_name.trim().is_empty()
        || request.typed_name.chars().count() > 128
    {
        return Err(error(
            "ORPHAN_INSTANCE_INPUT_INVALID",
            "孤立专服清理请求无效",
        ));
    }
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "孤立专服目标主机不一致"));
        }
        let remote = execute(
            session,
            &json!({"hostId": request.target.host_id,
            "instanceId": request.target.instance_id,
            "expectedRegistryRevision": request.expected_registry_revision,
            "typedName": request.typed_name}),
        )?;
        if remote.host_id != request.target.host_id
            || remote.instance_id != request.target.instance_id
            || !remote.removed
        {
            return Err(error(
                "ORPHAN_INSTANCE_PROTOCOL_INVALID",
                "孤立专服清理响应身份无效",
            ));
        }
        Ok(Response {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            registry_revision: remote.registry_revision,
            released_reservations: remote.released_reservations,
            removed: remote.removed,
            retired: remote.retired,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::{classify_script_failure, python_diagnostic, RemoteResult};

    #[test]
    fn cleanup_response_accepts_older_scripts_without_retired_flag() {
        let result: RemoteResult = serde_json::from_value(serde_json::json!({
            "hostId": "11111111-1111-4111-8111-111111111111",
            "instanceId": "22222222-2222-4222-8222-222222222222",
            "registryRevision": 4,
            "releasedReservations": 3,
            "removed": true
        }))
        .expect("legacy cleanup result should deserialize");
        assert!(!result.retired);
    }

    #[test]
    fn script_start_failures_are_classified_without_returning_stderr() {
        assert_eq!(
            classify_script_failure(127, b"/usr/bin/python3: not found"),
            "ORPHAN_INSTANCE_PYTHON_UNAVAILABLE"
        );
        assert_eq!(
            classify_script_failure(1, b"SyntaxError: invalid syntax"),
            "ORPHAN_INSTANCE_SCRIPT_INVALID"
        );
        assert_eq!(
            classify_script_failure(1, b"Permission denied"),
            "ORPHAN_INSTANCE_PERMISSION_DENIED"
        );
        assert_eq!(
            classify_script_failure(1, b"Traceback (most recent call last)"),
            "ORPHAN_INSTANCE_SCRIPT_FAILED"
        );
        assert_eq!(
            python_diagnostic(b"  File \"<stdin>\", line 217, in remove\nKeyError: 'status'"),
            (Some("KeyError"), Some(217))
        );
    }
}
