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
const BASE_VERIFY: &str = include_str!("../../remote-scripts/actions/register-built-release.py");
const DELETE_RELEASE: &str =
    include_str!("../../remote-scripts/actions/delete-instance-release.py");

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BaseVerifyRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub expected_record_version: u64,
    pub release_id: String,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReleaseDeletePreviewRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub expected_record_version: u64,
    pub release_id: String,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReleaseDeleteRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub expected_record_version: u64,
    pub release_id: String,
    pub catalog_digest: String,
    pub typed_name: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseDeletePreviewResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub release_id: String,
    pub dst_build: String,
    pub image_digest: String,
    pub instance_references: Vec<String>,
    pub task_references: Vec<String>,
    pub container_references: Vec<String>,
    pub shared_image: bool,
    pub allowed: bool,
    pub catalog_digest: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseDeleteResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub release_id: String,
    pub image_removed: bool,
    pub image_shared: bool,
    pub staging_removed: bool,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BaseVerifyResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: crate::instance_creation::HostTarget,
    pub record_version: u64,
    pub release_id: String,
    pub verified: bool,
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

fn remote_error(code: &str) -> Box<CommandError> {
    let (stable_code, message) = match code {
        "CREATE_RELEASE_INPUT_INVALID" => (
            "CREATE_RELEASE_INPUT_INVALID",
            "Release 验证请求无效，请刷新 Release 列表后重试",
        ),
        "HOST_IDENTITY_MISMATCH" => (
            "HOST_IDENTITY_MISMATCH",
            "Release 所属主机与当前连接不一致，请重新连接主机",
        ),
        "CREATE_RELEASE_CATALOG_INVALID" => (
            "CREATE_RELEASE_CATALOG_INVALID",
            "主机上的 Release catalog 无效，请检查 catalog 文件",
        ),
        "CREATE_RELEASE_NOT_VERIFIED" => (
            "CREATE_RELEASE_NOT_VERIFIED",
            "Release 尚未通过基础镜像验证，请先验证镜像",
        ),
        "CREATE_RELEASE_METADATA_INVALID" => (
            "CREATE_RELEASE_METADATA_INVALID",
            "Release metadata 与主机 catalog 不一致，请重新构建并登记镜像",
        ),
        "CREATE_RELEASE_BINARY_CHECK_FAILED" => (
            "CREATE_RELEASE_BINARY_CHECK_FAILED",
            "DST 64 位服务端文件不可执行或缺少动态库，请检查镜像运行库依赖后重新构建 Release",
        ),
        "CREATE_DOCKER_UNAVAILABLE" => (
            "CREATE_DOCKER_UNAVAILABLE",
            "远端主机未安装 Docker CLI，请检查 Docker 安装",
        ),
        "CREATE_RELEASE_IMAGE_DIGEST_MISMATCH" => (
            "CREATE_RELEASE_IMAGE_DIGEST_MISMATCH",
            "主机上的镜像与 Release digest 不匹配",
        ),
        "RELEASE_CATALOG_CHANGED" => (
            "RELEASE_CATALOG_CHANGED",
            "Release catalog 已变化，请刷新后重试",
        ),
        "RELEASE_NOT_FOUND" => ("RELEASE_NOT_FOUND", "Release 已不存在，请刷新列表"),
        "RELEASE_IN_USE" => (
            "RELEASE_IN_USE",
            "该 Release 仍被实例、任务或容器引用，不能删除",
        ),
        "RELEASE_CONFIRMATION_MISMATCH" => (
            "RELEASE_CONFIRMATION_MISMATCH",
            "输入的 DST 版本与待删除 Release 不一致",
        ),
        "RELEASE_DELETE_IMAGE_FAILED" => (
            "RELEASE_DELETE_IMAGE_FAILED",
            "Docker 未能删除该镜像，Release 登记已恢复，请检查镜像状态后重试",
        ),
        "RELEASE_DELETE_IMAGE_IN_USE" => (
            "RELEASE_DELETE_IMAGE_IN_USE",
            "Docker 仍有容器引用该镜像，Release 登记已恢复；请先清理引用容器",
        ),
        "RELEASE_DELETE_DOCKER_PERMISSION_DENIED" => (
            "RELEASE_DELETE_DOCKER_PERMISSION_DENIED",
            "当前主机账户没有删除 Docker 镜像的权限，Release 登记已恢复",
        ),
        "RELEASE_DELETE_DOCKER_UNAVAILABLE" => (
            "RELEASE_DELETE_DOCKER_UNAVAILABLE",
            "无法连接主机 Docker，未执行 Release 删除",
        ),
        "RELEASE_DELETE_DOCKER_TIMEOUT" => (
            "RELEASE_DELETE_DOCKER_TIMEOUT",
            "Docker 资源检查超时，未执行 Release 删除",
        ),
        "RELEASE_DELETE_STATE_INVALID" => (
            "RELEASE_DELETE_STATE_INVALID",
            "Release、实例或任务状态无法安全核对，未执行删除",
        ),
        "RELEASE_DELETE_FAILED" => (
            "RELEASE_DELETE_FAILED",
            "Release 删除检查或清理过程遇到未分类错误；请刷新列表并核对主机 catalog、staging 和 Docker 状态",
        ),
        "RELEASE_DELETE_DOCKER_RESPONSE_INVALID" => (
            "RELEASE_DELETE_DOCKER_RESPONSE_INVALID",
            "Docker 返回的镜像或容器信息无效，未完成 Release 删除",
        ),
        "RELEASE_DELETE_PROTOCOL_INVALID" => (
            "RELEASE_DELETE_PROTOCOL_INVALID",
            "远端 Release 删除响应格式不匹配，请重新连接并重试",
        ),
        _ => (
            "CREATE_RELEASE_PROTOCOL_INVALID",
            "Release 操作返回未知错误，请刷新后重试",
        ),
    };
    error(stable_code, message)
}

fn execute(
    session: &Session,
    script: &str,
    request: &Value,
    timeout_seconds: u64,
) -> CommandResult<Value> {
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
    let deadline = Instant::now() + Duration::from_secs(timeout_seconds);
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
    let exit_status = channel.exit_status().map_err(|_| {
        error(
            "CREATE_RELEASE_CHANNEL_FAILED",
            "无法读取 release 脚本退出状态",
        )
    })?;
    let envelope: Value = match serde_json::from_slice(&output) {
        Ok(value) => value,
        Err(_) => {
            let mut result = error(
                "CREATE_RELEASE_PROTOCOL_INVALID",
                "远端 Release 脚本未返回有效响应；请检查主机 Python 运行环境后重试",
            );
            result.details = json!({"responseBytes": output.len(), "exitStatus": exit_status});
            return Err(result);
        }
    };
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        let code = envelope
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or_default();
        return Err(remote_error(code));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("CREATE_RELEASE_PROTOCOL_INVALID", "release 响应缺少数据"))
}

pub fn verify_base(
    database: &Path,
    request: BaseVerifyRequest,
) -> CommandResult<BaseVerifyResponse> {
    validate_target(&request.target)?;
    if request.schema_version != 1 || !valid_id(&request.release_id) {
        return Err(error("CREATE_RELEASE_INPUT_INVALID", "基础验证请求无效"));
    }
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "Release 目标主机不一致"));
        }
        let data = execute(
            session,
            BASE_VERIFY,
            &json!({"releaseId":request.release_id}),
            60,
        )?;
        if data.get("releaseId").and_then(Value::as_str) != Some(request.release_id.as_str())
            || data.get("verified") != Some(&Value::Bool(true))
        {
            return Err(error("CREATE_RELEASE_PROTOCOL_INVALID", "基础验证结果无效"));
        }
        Ok(BaseVerifyResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            record_version: request.expected_record_version,
            release_id: request.release_id,
            verified: true,
        })
    })
}

fn validate_delete_request(
    schema_version: u32,
    target: &crate::instance_creation::HostTarget,
    release_id: &str,
) -> CommandResult<()> {
    validate_target(target)?;
    if schema_version != 1 || !valid_id(release_id) {
        return Err(error(
            "RELEASE_DELETE_INPUT_INVALID",
            "Release 删除请求无效",
        ));
    }
    Ok(())
}

pub fn preview_delete(
    database: &Path,
    request: ReleaseDeletePreviewRequest,
) -> CommandResult<ReleaseDeletePreviewResponse> {
    validate_delete_request(request.schema_version, &request.target, &request.release_id)?;
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "Release 目标主机不一致"));
        }
        let data = execute(
            session,
            DELETE_RELEASE,
            &json!({"action":"preview","hostId":request.target.host_id,"releaseId":request.release_id}),
            30,
        )?;
        let allowed = data
            .get("allowed")
            .and_then(Value::as_bool)
            .ok_or_else(|| {
                error(
                    "RELEASE_DELETE_PROTOCOL_INVALID",
                    "Release 删除预览缺少 allowed",
                )
            })?;
        let release_id = data
            .get("releaseId")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                error(
                    "RELEASE_DELETE_PROTOCOL_INVALID",
                    "Release 删除预览缺少身份",
                )
            })?;
        let dst_build = data
            .get("dstBuild")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                error(
                    "RELEASE_DELETE_PROTOCOL_INVALID",
                    "Release 删除预览缺少版本",
                )
            })?;
        let image_digest = data
            .get("imageDigest")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                error(
                    "RELEASE_DELETE_PROTOCOL_INVALID",
                    "Release 删除预览缺少镜像摘要",
                )
            })?;
        let string_list = |field: &str| -> CommandResult<Vec<String>> {
            serde_json::from_value(data.get(field).cloned().unwrap_or(Value::Null))
                .map_err(|_| error("RELEASE_DELETE_PROTOCOL_INVALID", "Release 引用列表无效"))
        };
        if release_id != request.release_id || !image_digest.starts_with("sha256:") {
            return Err(error(
                "RELEASE_DELETE_PROTOCOL_INVALID",
                "Release 删除预览身份不匹配",
            ));
        }
        Ok(ReleaseDeletePreviewResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            release_id: release_id.to_owned(),
            dst_build: dst_build.to_owned(),
            image_digest: image_digest.to_owned(),
            instance_references: string_list("instanceReferences")?,
            task_references: string_list("taskReferences")?,
            container_references: string_list("containerReferences")?,
            shared_image: data
                .get("sharedImage")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            allowed,
            catalog_digest: data
                .get("catalogDigest")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        })
    })
}

pub fn delete_release(
    database: &Path,
    request: ReleaseDeleteRequest,
) -> CommandResult<ReleaseDeleteResponse> {
    validate_delete_request(request.schema_version, &request.target, &request.release_id)?;
    if !request.catalog_digest.starts_with("sha256:") || request.typed_name.trim().is_empty() {
        return Err(error(
            "RELEASE_DELETE_INPUT_INVALID",
            "Release 删除确认无效",
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
            return Err(error("HOST_IDENTITY_MISMATCH", "Release 目标主机不一致"));
        }
        let data = execute(
            session,
            DELETE_RELEASE,
            &json!({
                "action":"delete", "hostId":request.target.host_id, "releaseId":request.release_id,
                "catalogDigest":request.catalog_digest, "typedName":request.typed_name
            }),
            90,
        )?;
        if data.get("releaseId").and_then(Value::as_str) != Some(request.release_id.as_str()) {
            return Err(error(
                "RELEASE_DELETE_PROTOCOL_INVALID",
                "Release 删除结果身份无效",
            ));
        }
        Ok(ReleaseDeleteResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            release_id: request.release_id,
            image_removed: data
                .get("imageRemoved")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            image_shared: data
                .get("imageShared")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            staging_removed: data
                .get("stagingRemoved")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        })
    })
}

#[cfg(test)]
mod tests {
    use super::remote_error;
    use serde_json::{json, Value};

    fn safe_ports(value: &Value, key: &str) -> Option<Vec<u64>> {
        let values = value.get(key)?.as_array()?;
        if values.len() > 64 {
            return None;
        }
        values
            .iter()
            .map(|value| value.as_u64().filter(|port| (1..=65535).contains(port)))
            .collect()
    }

    #[test]
    fn release_rejection_codes_have_actionable_errors() {
        let cases = [
            (
                "CREATE_RELEASE_NOT_VERIFIED",
                "CREATE_RELEASE_NOT_VERIFIED",
                "基础镜像验证",
            ),
            (
                "CREATE_RELEASE_METADATA_INVALID",
                "CREATE_RELEASE_METADATA_INVALID",
                "metadata",
            ),
            (
                "CREATE_RELEASE_BINARY_CHECK_FAILED",
                "CREATE_RELEASE_BINARY_CHECK_FAILED",
                "不可执行或缺少动态库",
            ),
            (
                "CREATE_DOCKER_UNAVAILABLE",
                "CREATE_DOCKER_UNAVAILABLE",
                "Docker CLI",
            ),
        ];

        for (remote_code, expected_code, message_fragment) in cases {
            let error = remote_error(remote_code);
            assert_eq!(error.code, expected_code);
            assert!(error.message.contains(message_fragment));
        }
    }

    #[test]
    fn listener_diagnostics_accept_only_port_numbers() {
        let details =
            json!({"masterMissingPorts":[11000,8768],"cavesMissingPorts":[27019],"log":"secret"});
        assert_eq!(
            safe_ports(&details, "masterMissingPorts"),
            Some(vec![11000, 8768])
        );
        assert_eq!(safe_ports(&details, "cavesMissingPorts"), Some(vec![27019]));
        assert_eq!(safe_ports(&details, "log"), None);
        assert_eq!(safe_ports(&json!({"ports":[0]}), "ports"), None);
    }
}
