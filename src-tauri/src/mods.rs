use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::InstanceTarget,
    mod_parser::{self, ModSchema},
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "listInstanceMods";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-mods.py");
const SAVE_SCRIPT: &str = include_str!("../../remote-scripts/actions/mods-save.py");
const DOWNLOAD_SCRIPT: &str = include_str!("../../remote-scripts/actions/mods-download.py");
const MAX_OUTPUT: usize = 4 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ListRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    snapshot_id: String,
    captured_at: String,
    source: String,
    freshness: String,
    config_revision: u64,
    items: Vec<RemoteItem>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteItem {
    workshop_id: String,
    desired: Option<RemoteState>,
    deployed: Option<RemoteState>,
    artifact: Option<RemoteArtifact>,
    #[serde(default)]
    deployed_artifact: Option<RemoteArtifact>,
    #[serde(default)]
    candidate_artifact: Option<RemoteArtifact>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteState {
    enabled: bool,
    configuration_options: Value,
    artifact_digest: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteArtifact {
    folder_name: String,
    source_base64: String,
    source_sha256: String,
    content_digest: Option<String>,
    prepared_at: Option<String>,
    #[serde(default)]
    source_handle: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModSummary {
    pub workshop_id: String,
    pub desired_enabled: Option<bool>,
    pub deployed_enabled: Option<bool>,
    pub desired_configuration: Option<Value>,
    pub deployed_configuration: Option<Value>,
    pub schema: ModSchema,
    pub artifact_digest: Option<String>,
    pub desired_artifact_digest: Option<String>,
    pub deployed_artifact_digest: Option<String>,
    pub prepared_digest: Option<String>,
    pub prepared_at: Option<String>,
    pub modinfo_sha256: Option<String>,
    pub prepared_source_handle: Option<String>,
    pub deployed_version: Option<String>,
    pub candidate_digest: Option<String>,
    pub candidate_version: Option<String>,
    pub candidate_source_handle: Option<String>,
    pub candidate_prepared_at: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ListResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub snapshot_id: String,
    pub captured_at: String,
    pub source: String,
    pub freshness: String,
    pub config_revision: u64,
    pub items: Vec<ModSummary>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub operation_id: String,
    pub mods: Vec<DesiredMod>,
    #[serde(default)]
    pub schema_bindings: Vec<ModSchemaBinding>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ModSchemaBinding {
    pub workshop_id: String,
    pub artifact_digest: String,
    pub modinfo_sha256: String,
    pub changed_fields: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DesiredMod {
    pub workshop_id: String,
    pub enabled: bool,
    pub configuration_options: Value,
    pub artifact_digest: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveRemoteResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    operation_id: String,
    verified_revision: u64,
    data: Value,
    invalidate: Vec<Value>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub operation_id: String,
    pub verified_revision: u64,
    pub data: Value,
    pub invalidate: Vec<Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrepareRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub operation_id: String,
    pub workshop_ids: Vec<String>,
    #[serde(default)]
    pub source_handles: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DownloadTaskRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadTaskResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub task: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadTaskListResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub items: Vec<Value>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "mods", OP, message, "刷新专服 MOD 状态后重试")
}

fn validate_request(request: &ListRequest) -> CommandResult<()> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "MOD 请求版本不受支持"));
    }
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    for (field, value) in [
        ("serverId", &request.target.server_id),
        ("hostId", &request.target.host_id),
        ("instanceId", &request.target.instance_id),
    ] {
        crate::server_input::validate_id(value, OP, field)?;
    }
    Ok(())
}

fn invoke(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 请求编码失败"))?,
    );
    let script = STANDARD.encode(SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nnamespace={{'__name__':'instance_mods_probe'}}\nexec(compile(base64.b64decode('{script}'),'<instance-mods>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'schemaVersion':1,'ok':False,'code':'MOD_PROBE_FAILED'}},separators=(',',':')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法建立 MOD 查询通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法配置 MOD 查询通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("MOD_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法提交 MOD 查询"))?;
    channel
        .send_eof()
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法结束 MOD 查询请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("MOD_QUERY_TIMEOUT", "MOD 查询超时"));
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
            _ => return Err(error("MOD_CHANNEL_FAILED", "无法读取 MOD 查询结果")),
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("MOD_CHANNEL_FAILED", "MOD 查询通道未正常结束"))?;
    let value: Value = serde_json::from_slice(&output)
        .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 查询响应不是 JSON"))?;
    if value.get("ok") == Some(&Value::Bool(false)) {
        let code = value
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("MOD_QUERY_FAILED");
        return Err(match code {
            "MANIFEST_NOT_FOUND" => error("MANIFEST_NOT_FOUND", "专服创建尚未完成"),
            "INSTANCE_IDENTITY_MISMATCH" => {
                error("INSTANCE_IDENTITY_MISMATCH", "专服身份与主机记录不一致")
            }
            "MOD_STATE_INVALID" | "MOD_STATE_REVISION_INVALID" => {
                error("MOD_STATE_INVALID", "专服 MOD 状态文件无效")
            }
            "MOD_ARTIFACT_INVALID" | "MOD_ARTIFACT_READ_FAILED" => {
                error("MOD_ARTIFACT_INVALID", "MOD 固定产物不可读取")
            }
            _ => error("MOD_QUERY_FAILED", "读取远端 MOD 状态失败"),
        });
    }
    Ok(value)
}

fn invoke_save(session: &Session, payload: &Value) -> CommandResult<Value> {
    let encoded_request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 保存请求编码失败"))?,
    );
    let encoded_script = STANDARD.encode(SAVE_SCRIPT.as_bytes());
    let source = format!("import base64,json\nnamespace={{'__name__':'mods_save'}}\nexec(compile(base64.b64decode('{encoded_script}'),'<mods-save>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{encoded_request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'schemaVersion':1,'ok':False,'code':'MOD_SAVE_FAILED'}},separators=(',',':')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法建立 MOD 保存通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法配置 MOD 保存通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("MOD_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法提交 MOD 保存"))?;
    channel
        .send_eof()
        .map_err(|_| error("MOD_CHANNEL_FAILED", "无法结束 MOD 保存请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("MOD_SAVE_TIMEOUT", "MOD 保存超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= 2 * 1024 * 1024 => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => return Err(error("MOD_CHANNEL_FAILED", "无法读取 MOD 保存结果")),
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("MOD_CHANNEL_FAILED", "MOD 保存通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 保存响应不是 JSON"))?;
    if envelope.get("ok") == Some(&Value::Bool(false)) {
        return Err(
            match envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("MOD_SAVE_FAILED")
            {
                "REVISION_CONFLICT" => {
                    error("REVISION_CONFLICT", "MOD 期望配置已变化，请刷新后重试")
                }
                "MOD_DUPLICATE" | "MOD_INPUT_INVALID" => {
                    error("MOD_INPUT_INVALID", "MOD 期望配置无效")
                }
                "OPERATION_ID_REUSED" => {
                    error("OPERATION_ID_REUSED", "该保存操作已使用不同内容提交")
                }
                _ => error("MOD_SAVE_FAILED", "保存 MOD 期望配置失败"),
            },
        );
    }
    Ok(envelope)
}

fn invoke_download(session: &Session, payload: &Value) -> CommandResult<Value> {
    let encoded_request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("MOD_TASK_PROTOCOL_INVALID", "MOD 任务请求编码失败"))?,
    );
    let encoded_script = STANDARD.encode(DOWNLOAD_SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nworker_source=base64.b64decode('{encoded_script}').decode('utf-8')\nnamespace={{'__name__':'mods_download'}}\nexec(compile(worker_source,'<mods-download>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{encoded_request}').decode('utf-8')),worker_source)\nexcept Exception:\n    print(json.dumps({{'ok':False,'code':'MOD_TASK_CONTROLLER_FAILED'}},separators=(',',':')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("MOD_TASK_CHANNEL_FAILED", "无法建立 MOD 任务通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("MOD_TASK_CHANNEL_FAILED", "无法配置 MOD 任务通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("MOD_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("MOD_TASK_CHANNEL_FAILED", "无法提交 MOD 任务请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("MOD_TASK_CHANNEL_FAILED", "无法结束 MOD 任务请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(60);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("MOD_TASK_QUERY_TIMEOUT", "MOD 任务状态查询超时"));
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
            _ => return Err(error("MOD_TASK_CHANNEL_FAILED", "无法读取 MOD 任务响应")),
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("MOD_TASK_CHANNEL_FAILED", "MOD 任务通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("MOD_TASK_PROTOCOL_INVALID", "MOD 任务响应不是 JSON"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(
            match envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("MOD_TASK_FAILED")
            {
                "REVISION_CONFLICT" => {
                    error("REVISION_CONFLICT", "MOD 期望配置已变化，请刷新后重试")
                }
                "MOD_FETCH_CAPABILITY_UNAVAILABLE" => error(
                    "MOD_FETCH_CAPABILITY_UNAVAILABLE",
                    "主机没有包含 SteamCMD Workshop 获取能力的已验证 Release，请先构建新 Release",
                ),
                "MOD_RELEASE_IMAGE_MISSING" => error(
                    "MOD_RELEASE_IMAGE_MISSING",
                    "没有可用的本地 DST Release 镜像，请先准备 Release",
                ),
                "MOD_RELEASE_INVALID" | "MOD_RELEASE_REQUIRED" => {
                    error("MOD_RELEASE_INVALID", "实例活动 Release 无效，无法准备 MOD")
                }
                "MOD_NOT_IN_DESIRED" => error("MOD_NOT_IN_DESIRED", "只能准备已加入且启用的 MOD"),
                "MOD_TASK_BUSY" | "MOD_TASK_LIMIT" => {
                    error("MOD_TASK_BUSY", "主机 MOD 准备任务已排队，请稍后重试")
                }
                _ => error("MOD_TASK_FAILED", "无法启动或查询 MOD 准备任务"),
            },
        );
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("MOD_TASK_PROTOCOL_INVALID", "MOD 任务响应缺少数据"))
}

fn schema_for(item: &RemoteItem) -> CommandResult<(ModSchema, Option<String>)> {
    let Some(artifact) = &item.artifact else {
        return Ok((
            mod_parser::unavailable_schema(&format!("workshop-{}", item.workshop_id), "zh"),
            None,
        ));
    };
    let (schema, digest) = schema_for_artifact(artifact)?;
    Ok((schema, Some(digest)))
}

fn schema_for_artifact(artifact: &RemoteArtifact) -> CommandResult<(ModSchema, String)> {
    let source = STANDARD
        .decode(&artifact.source_base64)
        .map_err(|_| error("MOD_ARTIFACT_INVALID", "MOD 配置源码编码无效"))?;
    let source = String::from_utf8(source)
        .map_err(|_| error("MOD_ARTIFACT_INVALID", "MOD 配置源码不是 UTF-8"))?;
    if artifact.source_sha256.len() != 64
        || !artifact
            .source_sha256
            .bytes()
            .all(|value| value.is_ascii_hexdigit())
    {
        return Err(error("MOD_ARTIFACT_INVALID", "MOD 配置源码摘要无效"));
    }
    let digest = format!("{:x}", Sha256::digest(source.as_bytes()));
    if digest != artifact.source_sha256 {
        return Err(error("MOD_ARTIFACT_INVALID", "MOD 配置源码摘要不匹配"));
    }
    let schema = mod_parser::parse_modinfo(&source, &artifact.folder_name, "zh");
    Ok((schema, artifact.source_sha256.clone()))
}

fn validate_schema_bindings(session: &Session, request: &SaveRequest) -> CommandResult<()> {
    if request.schema_bindings.is_empty() {
        return Ok(());
    }
    if request.schema_bindings.len() > 50 {
        return Err(error(
            "MOD_SCHEMA_BINDING_INVALID",
            "MOD 配置绑定数量超出限制",
        ));
    }
    let snapshot_request_id = uuid::Uuid::new_v4().to_string();
    let response = invoke(
        session,
        &serde_json::json!({
            "schemaVersion": 1,
            "requestId": snapshot_request_id,
            "serverId": request.target.server_id,
            "hostId": request.target.host_id,
            "instanceId": request.target.instance_id,
        }),
    )?;
    let snapshot: RemoteResponse = serde_json::from_value(response)
        .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 配置校验快照无效"))?;
    if snapshot.request_id != snapshot_request_id
        || snapshot.target.server_id != request.target.server_id
        || snapshot.target.host_id != request.target.host_id
        || snapshot.target.instance_id != request.target.instance_id
    {
        return Err(error("MOD_PROTOCOL_INVALID", "MOD 配置校验快照目标不匹配"));
    }
    if snapshot.config_revision != request.expected_revision {
        // Let the save controller replay an existing operation receipt before
        // applying CAS. A new stale operation is still rejected there.
        return Ok(());
    }

    let mut bound_ids = std::collections::HashSet::new();
    for binding in &request.schema_bindings {
        if !binding
            .workshop_id
            .bytes()
            .all(|byte| byte.is_ascii_digit())
            || binding.workshop_id.is_empty()
            || binding.workshop_id.len() > 20
            || !is_sha256(&binding.artifact_digest)
            || !is_sha256(&binding.modinfo_sha256)
            || binding.changed_fields.is_empty()
            || binding.changed_fields.len() > 256
            || !bound_ids.insert(binding.workshop_id.as_str())
        {
            return Err(error(
                "MOD_SCHEMA_BINDING_INVALID",
                "MOD 配置与固定产物绑定无效",
            ));
        }
        let requested = request
            .mods
            .iter()
            .find(|item| item.workshop_id == binding.workshop_id)
            .ok_or_else(|| {
                error(
                    "MOD_SCHEMA_BINDING_INVALID",
                    "配置绑定的 MOD 不在保存列表中",
                )
            })?;
        if requested.artifact_digest.as_deref() != Some(binding.artifact_digest.as_str()) {
            return Err(error(
                "MOD_SCHEMA_BINDING_INVALID",
                "保存请求未固定到正在编辑的 MOD 产物",
            ));
        }
        let current = snapshot
            .items
            .iter()
            .find(|item| item.workshop_id == binding.workshop_id)
            .ok_or_else(|| error("MOD_SCHEMA_STALE", "MOD 已从实例期望列表移除，请刷新后重试"))?;
        let (schema, modinfo_sha256) = schema_for(current)?;
        let content_digest = current
            .artifact
            .as_ref()
            .and_then(|artifact| artifact.content_digest.as_deref());
        if content_digest != Some(binding.artifact_digest.as_str())
            || modinfo_sha256.as_deref() != Some(binding.modinfo_sha256.as_str())
            || !matches!(
                schema.status,
                mod_parser::SchemaStatus::Available | mod_parser::SchemaStatus::Partial
            )
        {
            return Err(error(
                "MOD_SCHEMA_STALE",
                "MOD 配置 schema 或固定产物已变化，请刷新后重试",
            ));
        }

        let current_configuration = current
            .desired
            .as_ref()
            .and_then(|state| state.configuration_options.as_object())
            .ok_or_else(|| error("MOD_STATE_INVALID", "当前 MOD 配置格式无效"))?;
        let mut changed = std::collections::HashSet::new();
        for name in &binding.changed_fields {
            if name.is_empty() || name.len() > 256 || !changed.insert(name.as_str()) {
                return Err(error("MOD_SCHEMA_BINDING_INVALID", "MOD 配置字段绑定无效"));
            }
            let matching_fields = schema
                .fields
                .iter()
                .filter(|field| field.name == *name)
                .collect::<Vec<_>>();
            if matching_fields.is_empty() || matching_fields.iter().any(|field| !field.editable) {
                return Err(error(
                    "MOD_CONFIG_UNSUPPORTED",
                    "缺失或不支持的 MOD 配置字段不能保存",
                ));
            }
            let fields = matching_fields;
            if !schema_fields_share_control(&fields, current_configuration.get(name)) {
                return Err(error(
                    "MOD_CONFIG_UNSUPPORTED",
                    "同名 MOD 配置字段的类型不兼容",
                ));
            }
            if fields.len() > 1
                && fields[0].options.is_empty()
                && !current_configuration.contains_key(name)
                && fields
                    .iter()
                    .any(|field| field.default_value != fields[0].default_value)
            {
                return Err(error(
                    "MOD_CONFIG_UNSUPPORTED",
                    "同名 MOD 配置字段的默认值不兼容",
                ));
            }
        }

        let next_configuration = requested
            .configuration_options
            .as_object()
            .ok_or_else(|| error("MOD_INPUT_INVALID", "MOD 配置必须是对象"))?;
        for (key, old_value) in current_configuration {
            if !changed.contains(key.as_str()) && next_configuration.get(key) != Some(old_value) {
                return Err(error(
                    "MOD_CONFIG_UNBOUND_CHANGE",
                    "保存包含未绑定的 MOD 配置变更",
                ));
            }
        }
        for (key, value) in next_configuration {
            if changed.contains(key.as_str()) {
                let fields = schema
                    .fields
                    .iter()
                    .filter(|field| field.name == *key)
                    .collect::<Vec<_>>();
                if fields.is_empty() {
                    return Err(error("MOD_SCHEMA_STALE", "配置字段不属于当前 MOD schema"));
                }
                for field in fields {
                    validate_field_value(field, value, current_configuration.get(key))?;
                }
            } else if current_configuration.get(key) != Some(value) {
                return Err(error(
                    "MOD_CONFIG_UNBOUND_CHANGE",
                    "保存包含未绑定的 MOD 配置变更",
                ));
            }
        }
        for key in current_configuration.keys() {
            if !changed.contains(key.as_str()) && !next_configuration.contains_key(key) {
                return Err(error(
                    "MOD_CONFIG_UNBOUND_CHANGE",
                    "保存包含未绑定的 MOD 配置变更",
                ));
            }
        }
    }
    Ok(())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn validate_field_value(
    field: &mod_parser::ModField,
    value: &Value,
    current: Option<&Value>,
) -> CommandResult<()> {
    if !field.options.is_empty() {
        if field.options.iter().any(|option| option.value == *value) {
            return Ok(());
        }
        return Err(error(
            "MOD_CONFIG_VALUE_INVALID",
            "配置值不在 MOD 提供的候选项中",
        ));
    }
    let valid = match schema_field_control(field, current) {
        "boolean" => value.is_boolean(),
        "number" => value.as_f64().is_some_and(f64::is_finite),
        "text" => value.is_string(),
        _ => false,
    };
    if valid {
        Ok(())
    } else {
        Err(error(
            "MOD_CONFIG_VALUE_INVALID",
            "配置值类型与 MOD schema 不匹配",
        ))
    }
}

fn schema_field_control(field: &mod_parser::ModField, current: Option<&Value>) -> &'static str {
    if !field.options.is_empty() {
        return "choice";
    }
    let value = field
        .default_value
        .as_ref()
        .filter(|value| !value.is_null())
        .or(current);
    match value {
        Some(Value::Bool(_)) => "boolean",
        Some(Value::Number(_)) => "number",
        Some(Value::String(_)) => "text",
        _ => "unknown",
    }
}

fn schema_fields_share_control(fields: &[&mod_parser::ModField], current: Option<&Value>) -> bool {
    fields
        .first()
        .map(|first| {
            let expected = schema_field_control(first, current);
            expected != "unknown"
                && fields
                    .iter()
                    .all(|field| schema_field_control(field, current) == expected)
        })
        .unwrap_or(false)
}

pub fn list(database: &Path, request: ListRequest) -> CommandResult<ListResponse> {
    validate_request(&request)?;
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "MOD 目标主机身份不一致"));
        }
        let value = invoke(
            session,
            &serde_json::json!({"schemaVersion":1,"requestId":request.request_id,"serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id}),
        )?;
        let remote: RemoteResponse = serde_json::from_value(value)
            .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 查询响应字段无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.snapshot_id.is_empty()
            || remote.captured_at.is_empty()
            || remote.source != "remote"
            || remote.freshness != "fresh"
            || remote.items.len() > 200
        {
            return Err(error("MOD_PROTOCOL_INVALID", "MOD 查询响应身份或版本无效"));
        }
        let mut items = Vec::with_capacity(remote.items.len());
        for item in remote.items {
            if !item.workshop_id.bytes().all(|value| value.is_ascii_digit())
                || item.workshop_id.len() > 20
            {
                return Err(error("MOD_PROTOCOL_INVALID", "MOD Workshop ID 无效"));
            }
            let (schema, modinfo_sha256) = schema_for(&item)?;
            let desired_artifact_digest = item
                .desired
                .as_ref()
                .and_then(|state| state.artifact_digest.clone());
            let deployed_artifact_digest = item
                .deployed
                .as_ref()
                .and_then(|state| state.artifact_digest.clone());
            let artifact_digest = desired_artifact_digest
                .clone()
                .or_else(|| deployed_artifact_digest.clone());
            let prepared_digest = item
                .artifact
                .as_ref()
                .and_then(|artifact| artifact.content_digest.clone());
            let prepared_at = item
                .artifact
                .as_ref()
                .and_then(|artifact| artifact.prepared_at.clone());
            let prepared_source_handle = item
                .artifact
                .as_ref()
                .and_then(|artifact| artifact.source_handle.clone());
            let deployed_version = if let Some(artifact) = item.deployed_artifact.as_ref() {
                schema_for_artifact(artifact)
                    .ok()
                    .and_then(|(schema, _)| schema.metadata.version)
            } else if deployed_artifact_digest.is_some()
                && deployed_artifact_digest.as_deref() == prepared_digest.as_deref()
            {
                schema.metadata.version.clone()
            } else {
                None
            };
            let candidate_digest = item
                .candidate_artifact
                .as_ref()
                .and_then(|artifact| artifact.content_digest.clone());
            let candidate_prepared_at = item
                .candidate_artifact
                .as_ref()
                .and_then(|artifact| artifact.prepared_at.clone());
            let candidate_source_handle = item
                .candidate_artifact
                .as_ref()
                .and_then(|artifact| artifact.source_handle.clone());
            let candidate_version = item
                .candidate_artifact
                .as_ref()
                .and_then(|artifact| schema_for_artifact(artifact).ok())
                .and_then(|(schema, _)| schema.metadata.version);
            items.push(ModSummary {
                workshop_id: item.workshop_id,
                desired_enabled: item.desired.as_ref().map(|state| state.enabled),
                deployed_enabled: item.deployed.as_ref().map(|state| state.enabled),
                desired_configuration: item
                    .desired
                    .as_ref()
                    .map(|state| state.configuration_options.clone()),
                deployed_configuration: item
                    .deployed
                    .as_ref()
                    .map(|state| state.configuration_options.clone()),
                schema,
                artifact_digest,
                desired_artifact_digest,
                deployed_artifact_digest,
                prepared_digest,
                prepared_at,
                modinfo_sha256,
                prepared_source_handle,
                deployed_version,
                candidate_digest,
                candidate_version,
                candidate_source_handle,
                candidate_prepared_at,
            });
        }
        Ok(ListResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            snapshot_id: remote.snapshot_id,
            captured_at: remote.captured_at,
            source: remote.source,
            freshness: remote.freshness,
            config_revision: remote.config_revision,
            items,
        })
    })
}

pub fn save(database: &Path, request: SaveRequest) -> CommandResult<SaveResponse> {
    if request.schema_version != 1 || request.mods.len() > 200 {
        return Err(error("MOD_INPUT_INVALID", "MOD 保存请求无效"));
    }
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    for (field, value) in [
        ("serverId", &request.target.server_id),
        ("hostId", &request.target.host_id),
        ("instanceId", &request.target.instance_id),
    ] {
        crate::server_input::validate_id(value, OP, field)?;
    }
    if request.expected_revision > crate::server_input::MAX_REVISION {
        return Err(error("MOD_INPUT_INVALID", "MOD 配置版本无效"));
    }
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "MOD 目标主机身份不一致"));
        }
        validate_schema_bindings(session, &request)?;
        let value = invoke_save(
            session,
            &serde_json::json!({"schemaVersion":1,"requestId":request.request_id,"operationId":request.operation_id,"serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,"expectedRevision":request.expected_revision,"mods":request.mods}),
        )?;
        let remote: SaveRemoteResponse = serde_json::from_value(value)
            .map_err(|_| error("MOD_PROTOCOL_INVALID", "MOD 保存响应字段无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.operation_id != request.operation_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.verified_revision <= request.expected_revision
            || remote.invalidate.len() > 8
        {
            return Err(error("MOD_PROTOCOL_INVALID", "MOD 保存响应身份或版本无效"));
        }
        Ok(SaveResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            operation_id: request.operation_id,
            verified_revision: remote.verified_revision,
            data: remote.data,
            invalidate: remote.invalidate,
        })
    })
}

fn validate_download_target(
    target: &InstanceTarget,
    request_id: &str,
    record_version: u64,
) -> CommandResult<()> {
    crate::server_input::validate_id(request_id, "prepareInstanceMods", "requestId")?;
    for (field, value) in [
        ("serverId", &target.server_id),
        ("hostId", &target.host_id),
        ("instanceId", &target.instance_id),
    ] {
        crate::server_input::validate_id(value, "prepareInstanceMods", field)?;
    }
    if record_version == 0 || record_version > crate::server_input::MAX_REVISION {
        return Err(error("MOD_TASK_INPUT_INVALID", "主机连接版本无效"));
    }
    Ok(())
}

pub fn prepare(database: &Path, request: PrepareRequest) -> CommandResult<DownloadTaskResponse> {
    validate_download_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
    )?;
    crate::server_input::validate_id(&request.operation_id, "prepareInstanceMods", "operationId")?;
    if request.schema_version != 1
        || request.expected_revision > crate::server_input::MAX_REVISION
        || request.workshop_ids.is_empty()
        || request.workshop_ids.len() > 50
        || request.workshop_ids.iter().any(|value| {
            value.is_empty() || value.len() > 20 || !value.bytes().all(|byte| byte.is_ascii_digit())
        })
    {
        return Err(error("MOD_TASK_INPUT_INVALID", "MOD 准备任务参数无效"));
    }
    if request.source_handles.len() > request.workshop_ids.len()
        || request
            .source_handles
            .iter()
            .any(|(workshop_id, source_handle)| {
                !request.workshop_ids.contains(workshop_id)
                    || source_handle.is_empty()
                    || source_handle.len() > 64
                    || !source_handle.bytes().all(|byte| byte.is_ascii_digit())
            })
    {
        return Err(error("MOD_TASK_INPUT_INVALID", "Workshop 候选版本绑定无效"));
    }
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "MOD 任务目标主机身份不一致",
            ));
        }
        let value = invoke_download(
            session,
            &serde_json::json!({"schemaVersion":1,"action":"start","requestId":request.request_id,"operationId":request.operation_id,"serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,"expectedRecordVersion":request.expected_record_version,"expectedRevision":request.expected_revision,"workshopIds":request.workshop_ids,"sourceHandles":request.source_handles}),
        )?;
        let task = value
            .get("task")
            .cloned()
            .ok_or_else(|| error("MOD_TASK_PROTOCOL_INVALID", "MOD 准备响应缺少任务"))?;
        if task.get("operationId").and_then(Value::as_str) != Some(request.operation_id.as_str())
            || task.get("operation").and_then(Value::as_str) != Some("modPrepare")
            || task
                .get("target")
                .and_then(|value| value.get("instanceId"))
                .and_then(Value::as_str)
                != Some(request.target.instance_id.as_str())
        {
            return Err(error(
                "MOD_TASK_PROTOCOL_INVALID",
                "MOD 准备任务目标或编号无效",
            ));
        }
        Ok(DownloadTaskResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            task,
        })
    })
}

pub fn download_status(
    database: &Path,
    request: DownloadTaskRequest,
) -> CommandResult<DownloadTaskResponse> {
    validate_download_target(
        &request.target,
        &request.request_id,
        request.expected_record_version,
    )?;
    crate::server_input::validate_id(&request.operation_id, "getInstanceModTask", "operationId")?;
    let connection = ConnectionRequest {
        schema_version: request.schema_version,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "MOD 任务目标主机身份不一致",
            ));
        }
        let value = invoke_download(
            session,
            &serde_json::json!({"schemaVersion":1,"action":"status","requestId":request.request_id,"serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,"operationId":request.operation_id}),
        )?;
        let task = value
            .get("task")
            .cloned()
            .ok_or_else(|| error("MOD_TASK_PROTOCOL_INVALID", "MOD 任务查询响应缺少任务"))?;
        Ok(DownloadTaskResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            task,
        })
    })
}

pub fn download_tasks(
    database: &Path,
    request: ListRequest,
) -> CommandResult<DownloadTaskListResponse> {
    validate_request(&request)?;
    with_verified_session(
        database,
        &ConnectionRequest {
            schema_version: 1,
            request_id: request.request_id.clone(),
            server_id: request.target.server_id.clone(),
            expected_record_version: request.expected_record_version,
        },
        |session, host_id| {
            if host_id.as_deref() != Some(request.target.host_id.as_str()) {
                return Err(error(
                    "HOST_IDENTITY_MISMATCH",
                    "MOD 任务目标主机身份不一致",
                ));
            }
            let value = invoke_download(
                session,
                &serde_json::json!({"schemaVersion":1,"action":"list","requestId":request.request_id,"serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id}),
            )?;
            let items = value
                .as_array()
                .cloned()
                .ok_or_else(|| error("MOD_TASK_PROTOCOL_INVALID", "MOD 任务列表格式无效"))?;
            if items.len() > 100
                || items
                    .iter()
                    .any(|task| task.get("operation").and_then(Value::as_str) != Some("modPrepare"))
            {
                return Err(error("MOD_TASK_PROTOCOL_INVALID", "MOD 任务列表内容无效"));
            }
            Ok(DownloadTaskListResponse {
                schema_version: 1,
                request_id: request.request_id,
                target: request.target,
                items,
            })
        },
    )
}

#[cfg(test)]
mod schema_binding_tests {
    use super::*;

    fn field(default_value: Option<Value>, options: Vec<Value>) -> mod_parser::ModField {
        mod_parser::ModField {
            name: "value".into(),
            label: "Value".into(),
            hover: None,
            default_value,
            options: options
                .into_iter()
                .map(|value| mod_parser::ModOption {
                    label: value.to_string(),
                    value,
                    hover: None,
                })
                .collect(),
            editable: true,
            reason: None,
        }
    }

    #[test]
    fn validates_option_values_and_scalar_types_against_schema() {
        let choices = field(None, vec![Value::from(1), Value::from(2)]);
        assert!(validate_field_value(&choices, &Value::from(2), None).is_ok());
        assert!(validate_field_value(&choices, &Value::from("2"), None).is_err());

        let boolean = field(Some(Value::Bool(false)), Vec::new());
        assert!(validate_field_value(&boolean, &Value::Bool(true), None).is_ok());
        assert!(validate_field_value(&boolean, &Value::from(1), None).is_err());

        let numeric = field(Some(Value::from(2)), Vec::new());
        assert!(validate_field_value(&numeric, &Value::from(3.5), None).is_ok());
        assert!(validate_field_value(&numeric, &Value::from("3.5"), None).is_err());
    }

    #[test]
    fn same_name_fields_can_share_only_compatible_values() {
        let first = field(None, vec![Value::from(1), Value::from(2)]);
        let second = field(None, vec![Value::from(2), Value::from(3)]);
        assert!(schema_fields_share_control(&[&first, &second], None));
        assert!(validate_field_value(&first, &Value::from(2), None).is_ok());
        assert!(validate_field_value(&second, &Value::from(2), None).is_ok());
        assert!(validate_field_value(&first, &Value::from(1), None).is_ok());
        assert!(validate_field_value(&second, &Value::from(1), None).is_err());

        let boolean = field(Some(Value::Bool(false)), Vec::new());
        let number = field(Some(Value::from(1)), Vec::new());
        assert!(!schema_fields_share_control(&[&boolean, &number], None));
        assert!(!schema_fields_share_control(
            &[&boolean, &number],
            Some(&Value::Bool(false))
        ));
        assert!(schema_fields_share_control(&[&boolean, &boolean], None));
    }
}
