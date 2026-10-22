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
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModSummary {
    pub workshop_id: String,
    pub desired_enabled: Option<bool>,
    pub deployed_enabled: Option<bool>,
    pub desired_configuration: Option<Value>,
    pub schema: ModSchema,
    pub artifact_digest: Option<String>,
    pub prepared_digest: Option<String>,
    pub modinfo_sha256: Option<String>,
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
    Ok((schema, Some(artifact.source_sha256.clone())))
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
            let artifact_digest = item
                .desired
                .as_ref()
                .and_then(|state| state.artifact_digest.clone())
                .or_else(|| {
                    item.deployed
                        .as_ref()
                        .and_then(|state| state.artifact_digest.clone())
                });
            let prepared_digest = item
                .artifact
                .as_ref()
                .and_then(|artifact| artifact.content_digest.clone());
            items.push(ModSummary {
                workshop_id: item.workshop_id,
                desired_enabled: item.desired.as_ref().map(|state| state.enabled),
                deployed_enabled: item.deployed.as_ref().map(|state| state.enabled),
                desired_configuration: item.desired.map(|state| state.configuration_options),
                schema,
                artifact_digest,
                prepared_digest,
                modinfo_sha256,
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
            &serde_json::json!({"schemaVersion":1,"action":"start","requestId":request.request_id,"operationId":request.operation_id,"serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,"expectedRecordVersion":request.expected_record_version,"expectedRevision":request.expected_revision,"workshopIds":request.workshop_ids}),
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
