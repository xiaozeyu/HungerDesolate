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
    collections::BTreeMap,
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

const OP: &str = "worldConfig";
const SCRIPT: &str = include_str!("../../remote-scripts/actions/world-config.py");
const RENDERER: &str = include_str!("../../remote-scripts/actions/world-config-render.py");
const OPTIONS: &str = include_str!("../../src/lib/dst-world-options.json");
const MAX_OUTPUT: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GetRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AdvancedConfig {
    pub vote_enabled: bool,
    pub vote_kick_percentage: u8,
    pub admin_list: Vec<String>,
    pub whitelist: Vec<String>,
    pub whitelist_slots: u8,
    pub blocklist: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ShardWorldConfig {
    pub preset: String,
    pub worldgen_overrides: BTreeMap<String, String>,
    pub world_settings_overrides: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorldgenConfig {
    pub master: ShardWorldConfig,
    pub caves: ShardWorldConfig,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct WorldSettings {
    pub room_name: String,
    pub description: String,
    pub game_mode: String,
    pub max_players: u8,
    pub pvp: bool,
    pub language: String,
    pub pause_when_empty: bool,
    pub caves_enabled: bool,
    pub advanced: AdvancedConfig,
    pub worldgen: WorldgenConfig,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "action", rename_all = "camelCase", deny_unknown_fields)]
pub enum SecretChange {
    Keep {},
    Replace { value: Zeroizing<String> },
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub operation_id: String,
    pub settings: WorldSettings,
    pub token_change: Option<SecretChange>,
    pub server_password_change: Option<SecretChange>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreviewApplyRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApplyRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub desired_digest: String,
    pub active_release_id: String,
    pub operation_id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteApplyRequest<'a> {
    schema_version: u32,
    action: &'static str,
    request_id: &'a str,
    operation_id: &'a str,
    server_id: &'a str,
    host_id: &'a str,
    instance_id: &'a str,
    expected_revision: u64,
    desired_digest: &'a str,
    active_release_id: &'a str,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteApplyResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    revision: u64,
    active_release_id: String,
    desired_digest: String,
    runtime_verified: bool,
    running_shards: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorldApplyResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    revision: u64,
    active_release_id: String,
    desired_digest: String,
    runtime_verified: bool,
    running_shards: Vec<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RemoteSaveRequest<'a> {
    schema_version: u32,
    action: &'static str,
    request_id: &'a str,
    operation_id: &'a str,
    server_id: &'a str,
    host_id: &'a str,
    instance_id: &'a str,
    expected_revision: u64,
    settings: &'a WorldSettings,
    token_change: &'a Option<SecretChange>,
    server_password_change: &'a Option<SecretChange>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorldConfig {
    pub revision: u64,
    pub desired: WorldSettings,
    pub deployed: Option<WorldSettings>,
    pub desired_digest: String,
    pub deployed_digest: Option<String>,
    pub deployed_revision: Option<u64>,
    pub token_status: String,
    pub server_password_status: String,
    pub validation_warnings: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct QueryResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    snapshot_id: String,
    captured_at: String,
    source: String,
    freshness: String,
    config_revision: u64,
    data: WorldConfig,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorldQueryResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    snapshot_id: String,
    captured_at: String,
    source: String,
    freshness: String,
    config_revision: u64,
    data: WorldConfig,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MutationResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    operation_id: String,
    verified_revision: u64,
    data: WorldConfig,
    invalidate: Vec<Value>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorldMutationResponse {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    operation_id: String,
    verified_revision: u64,
    data: WorldConfig,
    invalidate: Vec<Value>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteApplyPreview {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    record_version: u64,
    revision: u64,
    active_release_id: Option<String>,
    desired_digest: String,
    world_data_present: bool,
    world_data_file_count: u64,
    new_world_options: Vec<Value>,
    token_status: String,
    server_password_status: String,
    backup_requirement: String,
    warnings: Vec<String>,
    blockers: Vec<String>,
    allowed: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorldApplyPreview {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    record_version: u64,
    revision: u64,
    active_release_id: Option<String>,
    desired_digest: String,
    world_data_present: bool,
    world_data_file_count: u64,
    new_world_options: Vec<Value>,
    token_status: String,
    server_password_status: String,
    backup_requirement: String,
    warnings: Vec<String>,
    blockers: Vec<String>,
    allowed: bool,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "worldConfig",
        OP,
        message,
        "刷新世界配置后核对期望版本；秘密值需重新输入，不会从服务器读回",
    )
}

fn connection(
    schema_version: u32,
    request_id: &str,
    target: &InstanceTarget,
    expected_record_version: u64,
) -> CommandResult<ConnectionRequest> {
    if schema_version != 1 || expected_record_version > crate::server_input::MAX_REVISION {
        return Err(error("WORLD_CONFIG_INPUT_INVALID", "世界配置请求版本无效"));
    }
    crate::server_input::validate_id(request_id, OP, "requestId")?;
    crate::server_input::validate_id(&target.server_id, OP, "serverId")?;
    crate::server_input::validate_id(&target.host_id, OP, "hostId")?;
    crate::server_input::validate_id(&target.instance_id, OP, "instanceId")?;
    Ok(ConnectionRequest {
        schema_version,
        request_id: request_id.to_string(),
        server_id: target.server_id.clone(),
        expected_record_version,
    })
}

fn validate_settings(settings: &WorldSettings) -> CommandResult<()> {
    let advanced = &settings.advanced;
    let ids_valid = |values: &[String]| {
        values.len() <= 128
            && values.iter().all(|value| {
                !value.is_empty()
                    && value.len() <= 64
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
            })
            && values
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len()
                == values.len()
    };
    if settings.room_name.trim().is_empty()
        || settings.room_name.chars().count() > 64
        || settings.room_name.chars().any(char::is_control)
        || settings.description.len() > 256
        || settings.description.chars().any(char::is_control)
        || !["relaxed", "survival", "endless", "wilderness", "lightsout"]
            .contains(&settings.game_mode.as_str())
        || !(1..=128).contains(&settings.max_players)
        || !["zh", "en"].contains(&settings.language.as_str())
        || advanced.vote_kick_percentage > 100
        || advanced.whitelist_slots > settings.max_players
        || (advanced.whitelist_slots > 0 && advanced.whitelist.is_empty())
        || !ids_valid(&advanced.admin_list)
        || !ids_valid(&advanced.whitelist)
        || !ids_valid(&advanced.blocklist)
    {
        return Err(error("WORLD_CONFIG_INPUT_INVALID", "世界配置字段无效"));
    }
    for (shard_name, shard) in [
        ("master", &settings.worldgen.master),
        ("caves", &settings.worldgen.caves),
    ] {
        let expected_preset = if shard_name == "master" {
            "SURVIVAL_TOGETHER"
        } else {
            "DST_CAVE"
        };
        if shard.preset != expected_preset
            || shard.worldgen_overrides.len() > 512
            || shard.world_settings_overrides.len() > 512
            || shard
                .worldgen_overrides
                .iter()
                .chain(shard.world_settings_overrides.iter())
                .any(|(key, value)| {
                    key.is_empty()
                        || key.len() > 128
                        || value.len() > 128
                        || key.chars().any(char::is_control)
                        || value.chars().any(char::is_control)
                })
        {
            return Err(error("WORLD_CONFIG_INPUT_INVALID", "分片配置字段无效"));
        }
    }
    Ok(())
}

fn execute<T: Serialize>(session: &Session, request: &T) -> CommandResult<Value> {
    execute_timeout(session, request, 30)
}

fn execute_timeout<T: Serialize>(
    session: &Session,
    request: &T,
    timeout_seconds: u64,
) -> CommandResult<Value> {
    let request_bytes = Zeroizing::new(
        serde_json::to_vec(request)
            .map_err(|_| error("WORLD_CONFIG_PROTOCOL_INVALID", "世界配置请求编码失败"))?,
    );
    let request_data = Zeroizing::new(STANDARD.encode(&*request_bytes));
    let options_data = STANDARD.encode(OPTIONS.as_bytes());
    let renderer_data = STANDARD.encode(RENDERER.as_bytes());
    let source = Zeroizing::new(format!(
        "import base64,json\nOPTION_SCHEMA=json.loads(base64.b64decode('{options_data}'))\nexec(compile(base64.b64decode('{renderer_data}'),'<world-config-render>','exec'))\n{SCRIPT}\nmain(json.loads(base64.b64decode('{}')))\n",
        &*request_data
    ));
    let mut channel = session
        .channel_session()
        .map_err(|_| error("WORLD_CONFIG_CHANNEL_FAILED", "无法建立世界配置通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("WORLD_CONFIG_CHANNEL_FAILED", "无法配置世界配置通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("WORLD_CONFIG_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("WORLD_CONFIG_CHANNEL_FAILED", "无法提交世界配置请求"))?;
    channel
        .send_eof()
        .map_err(|_| error("WORLD_CONFIG_CHANNEL_FAILED", "无法结束世界配置请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(timeout_seconds);
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("WORLD_CONFIG_TIMEOUT", "世界配置请求超时"));
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
            _ => return Err(error("WORLD_CONFIG_CHANNEL_FAILED", "无法读取世界配置响应")),
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("WORLD_CONFIG_CHANNEL_FAILED", "世界配置通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("WORLD_CONFIG_PROTOCOL_INVALID", "远端世界配置响应格式无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(remote_error(
            envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            envelope.get("failureType").and_then(Value::as_str),
            envelope.get("failureKey").and_then(Value::as_str),
        ));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("WORLD_CONFIG_PROTOCOL_INVALID", "远端世界配置响应缺少数据"))
}

fn remote_error(
    code: &str,
    failure_type: Option<&str>,
    failure_key: Option<&str>,
) -> Box<CommandError> {
    let mut result = match code {
        "REVISION_CONFLICT" => error("REVISION_CONFLICT", "世界配置已被其他操作更新"),
        "WORLD_SCHEMA_INVALID" | "WORLD_OPTION_INVALID" => {
            error("WORLD_OPTION_INVALID", "世界规则选项不受支持")
        }
        "WORLD_CONFIG_INVALID" | "WORLD_CONFIG_INPUT_INVALID" => {
            error("WORLD_CONFIG_INPUT_INVALID", "世界配置字段无效")
        }
        "WORLD_SECRET_INPUT_INVALID" => {
            error("WORLD_SECRET_INPUT_INVALID", "Token 或服务器密码格式无效")
        }
        "WORLD_SECRET_FILE_INVALID" => {
            error("WORLD_SECRET_FILE_INVALID", "受保护的世界凭据文件异常")
        }
        "WORLD_CONFIG_PATH_UNSAFE" => {
            error("WORLD_CONFIG_PATH_UNSAFE", "实例配置目录权限或路径不安全")
        }
        "WORLD_CONFIG_DATA_LIMIT" => error("WORLD_CONFIG_DATA_LIMIT", "世界目录项目超过预览上限"),
        "WORLD_CONFIG_DATA_READ_FAILED" => error(
            "WORLD_CONFIG_DATA_READ_FAILED",
            "读取世界存档目录失败，请检查远端文件系统状态",
        ),
        "WORLD_CONFIG_REVISION_EXHAUSTED" => {
            error("WORLD_CONFIG_REVISION_EXHAUSTED", "世界配置版本已达上限")
        }
        "WORLD_CONFIG_FILE_INVALID" => error(
            "WORLD_CONFIG_STATE_INVALID",
            "远端世界配置文件格式或权限无效",
        ),
        "HOST_IDENTITY_MISMATCH" => error("HOST_IDENTITY_MISMATCH", "世界配置目标主机身份不一致"),
        "INSTANCE_IDENTITY_MISMATCH" => {
            error("INSTANCE_IDENTITY_MISMATCH", "世界配置实例身份不一致")
        }
        "INSTANCE_NOT_FOUND" => error("INSTANCE_NOT_FOUND", "专服不存在或尚未完成创建"),
        "WORLD_TARGET_INVALID" => error("WORLD_TARGET_INVALID", "世界配置目标标识无效"),
        "WORLD_CONFIG_REVISION_INVALID" => {
            error("WORLD_CONFIG_STATE_INVALID", "远端世界配置版本无效")
        }
        "ROOT_REQUIRED" => error("ROOT_REQUIRED", "写入实例配置需要 root 管理权限"),
        "WORLD_CONFIG_STATE_INVALID" | "WORLD_CONFIG_TRANSACTION_INVALID" => error(
            "WORLD_CONFIG_STATE_INVALID",
            "远端世界配置状态无效，未覆盖现有数据",
        ),
        "OPERATION_ID_REUSED" => error("OPERATION_ID_REUSED", "操作编号已用于不同的世界配置"),
        "WORLD_DEPLOYMENT_MISSING" => error("WORLD_DEPLOYMENT_MISSING", "专服尚未完成容器部署"),
        "WORLD_RELEASE_INVALID" => error(
            "WORLD_RELEASE_INVALID",
            "当前 DST Release 无效或未通过基础验证",
        ),
        "WORLD_TOKEN_MISSING" => error("WORLD_TOKEN_MISSING", "应用配置前需要设置 Klei Token"),
        "PROTECTION_BACKUP_REQUIRED" => error(
            "PROTECTION_BACKUP_REQUIRED",
            "发现现有世界数据；备份功能完成前不会覆盖这些数据",
        ),
        "WORLD_DOCKER_UNAVAILABLE" => error(
            "WORLD_DOCKER_UNAVAILABLE",
            "远端 Docker 不可用，世界配置未应用",
        ),
        "WORLD_DOCKER_TIMEOUT" => {
            error("WORLD_DOCKER_TIMEOUT", "Docker 操作超时，应用结果需要核对")
        }
        "WORLD_DOCKER_OPERATION_FAILED" => {
            error("WORLD_DOCKER_OPERATION_FAILED", "Docker 未能完成配置部署")
        }
        "WORLD_DOCKER_RESPONSE_INVALID" => error(
            "WORLD_DOCKER_RESPONSE_INVALID",
            "Docker 返回了无法识别的运行分片名称",
        ),
        "WORLD_APPLY_PLAN_STALE" => {
            error("WORLD_APPLY_PLAN_STALE", "期望配置已变化，请重新预览应用")
        }
        "WORLD_APPLY_OUTCOME_UNKNOWN" => error(
            "WORLD_APPLY_OUTCOME_UNKNOWN",
            "发现未完成的应用现场，请核对后重试",
        ),
        "WORLD_APPLY_ROLLBACK_FAILED" => error(
            "WORLD_APPLY_ROLLBACK_FAILED",
            "应用失败且自动恢复未完成，请检查容器与配置",
        ),
        _ => error(
            "WORLD_CONFIG_FAILED",
            "世界配置操作未能完成，请刷新核对后重试",
        ),
    };
    if !code.is_empty()
        && code.len() <= 96
        && code
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
    {
        result.details = serde_json::json!({"remoteCode":code});
    }
    if let Some(name) = failure_type.filter(|name| {
        matches!(
            *name,
            "OSError"
                | "PermissionError"
                | "ValueError"
                | "TypeError"
                | "KeyError"
                | "AttributeError"
        )
    }) {
        result.details = serde_json::json!({"remoteCode":code,"remoteFailureType":name});
    }
    if let Some(key) = failure_key.filter(|key| {
        !key.is_empty()
            && key.len() <= 64
            && key.is_ascii()
            && key
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    }) {
        result.details = serde_json::json!({
            "remoteCode":code,
            "remoteFailureType":failure_type.unwrap_or_default(),
            "remoteFailureKey":key,
        });
    }
    result
}

fn preview_request_payload(request: &PreviewApplyRequest) -> Value {
    json!({
        "schemaVersion": 1,
        "action": "previewApply",
        "requestId": request.request_id,
        "serverId": request.target.server_id,
        "hostId": request.target.host_id,
        "instanceId": request.target.instance_id,
        "expectedRecordVersion": request.expected_record_version,
        "expectedRevision": request.expected_revision,
    })
}

pub fn get(database: &Path, request: GetRequest) -> CommandResult<WorldQueryResponse> {
    let connection = connection(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "世界配置目标主机身份不一致",
            ));
        }
        let remote_value = execute(
            session,
            &json!({"schemaVersion":1,"action":"get",
            "requestId":request.request_id,"serverId":request.target.server_id,
            "hostId":request.target.host_id,"instanceId":request.target.instance_id}),
        )?;
        let remote: QueryResponse = serde_json::from_value(remote_value)
            .map_err(|_| error("WORLD_CONFIG_PROTOCOL_INVALID", "世界配置查询响应字段无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.config_revision != remote.data.revision
            || remote.snapshot_id.is_empty()
            || remote.captured_at.is_empty()
            || remote.source != "remote"
            || remote.freshness != "fresh"
        {
            return Err(error(
                "WORLD_CONFIG_PROTOCOL_INVALID",
                "世界配置查询响应身份或版本无效",
            ));
        }
        validate_settings(&remote.data.desired)?;
        Ok(WorldQueryResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            snapshot_id: remote.snapshot_id,
            captured_at: remote.captured_at,
            source: remote.source,
            freshness: remote.freshness,
            config_revision: remote.config_revision,
            data: remote.data,
        })
    })
}

pub fn save(database: &Path, request: SaveRequest) -> CommandResult<WorldMutationResponse> {
    let connection = connection(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    if request.expected_revision > crate::server_input::MAX_REVISION {
        return Err(error("WORLD_CONFIG_INPUT_INVALID", "配置版本超出安全范围"));
    }
    validate_settings(&request.settings)?;
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "世界配置目标主机身份不一致",
            ));
        }
        let remote_request = RemoteSaveRequest {
            schema_version: 1,
            action: "save",
            request_id: &request.request_id,
            operation_id: &request.operation_id,
            server_id: &request.target.server_id,
            host_id: &request.target.host_id,
            instance_id: &request.target.instance_id,
            expected_revision: request.expected_revision,
            settings: &request.settings,
            token_change: &request.token_change,
            server_password_change: &request.server_password_change,
        };
        let remote_value = execute(session, &remote_request)?;
        let remote: MutationResponse = serde_json::from_value(remote_value)
            .map_err(|_| error("WORLD_CONFIG_PROTOCOL_INVALID", "世界配置保存响应字段无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.operation_id != request.operation_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.data.revision != remote.verified_revision
            || remote.verified_revision <= request.expected_revision
            || remote.invalidate.len() > 8
        {
            return Err(error(
                "WORLD_CONFIG_PROTOCOL_INVALID",
                "世界配置保存响应身份或版本无效",
            ));
        }
        validate_settings(&remote.data.desired)?;
        Ok(WorldMutationResponse {
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

pub fn preview_apply(
    database: &Path,
    request: PreviewApplyRequest,
) -> CommandResult<WorldApplyPreview> {
    let connection = connection(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "世界配置目标主机身份不一致",
            ));
        }
        let value = execute(session, &preview_request_payload(&request))?;
        let remote: RemoteApplyPreview = serde_json::from_value(value)
            .map_err(|_| error("WORLD_CONFIG_PROTOCOL_INVALID", "配置应用预览响应格式无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.record_version != request.expected_record_version
            || remote.revision != request.expected_revision
            || remote.desired_digest.len() != 71
            || !remote.desired_digest.starts_with("sha256:")
            || remote.world_data_file_count > 20_000
            || remote.new_world_options.len() > 1024
            || remote.warnings.len() > 128
            || remote.blockers.len() > 32
            || !["required", "none"].contains(&remote.backup_requirement.as_str())
            || !["configured", "missing"].contains(&remote.token_status.as_str())
            || !["configured", "missing"].contains(&remote.server_password_status.as_str())
        {
            return Err(error(
                "WORLD_CONFIG_PROTOCOL_INVALID",
                "配置应用预览目标或影响字段无效",
            ));
        }
        Ok(WorldApplyPreview {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            record_version: remote.record_version,
            revision: remote.revision,
            active_release_id: remote.active_release_id,
            desired_digest: remote.desired_digest,
            world_data_present: remote.world_data_present,
            world_data_file_count: remote.world_data_file_count,
            new_world_options: remote.new_world_options,
            token_status: remote.token_status,
            server_password_status: remote.server_password_status,
            backup_requirement: remote.backup_requirement,
            warnings: remote.warnings,
            blockers: remote.blockers,
            allowed: remote.allowed,
        })
    })
}

pub fn apply(database: &Path, request: ApplyRequest) -> CommandResult<WorldApplyResponse> {
    let connection = connection(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    if request.expected_revision > crate::server_input::MAX_REVISION
        || request.desired_digest.len() != 71
        || !request.desired_digest.starts_with("sha256:")
        || !request.desired_digest[7..]
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
        || uuid::Uuid::try_parse(&request.active_release_id).is_err()
    {
        return Err(error("WORLD_CONFIG_INPUT_INVALID", "世界配置应用请求无效"));
    }
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "世界配置目标主机身份不一致",
            ));
        }
        let remote_request = RemoteApplyRequest {
            schema_version: 1,
            action: "apply",
            request_id: &request.request_id,
            operation_id: &request.operation_id,
            server_id: &request.target.server_id,
            host_id: &request.target.host_id,
            instance_id: &request.target.instance_id,
            expected_revision: request.expected_revision,
            desired_digest: &request.desired_digest,
            active_release_id: &request.active_release_id,
        };
        let value = execute_timeout(session, &remote_request, 420)?;
        let remote: RemoteApplyResponse = serde_json::from_value(value)
            .map_err(|_| error("WORLD_CONFIG_PROTOCOL_INVALID", "世界配置应用响应格式无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || remote.target.server_id != request.target.server_id
            || remote.target.host_id != request.target.host_id
            || remote.target.instance_id != request.target.instance_id
            || remote.revision != request.expected_revision
            || remote.desired_digest != request.desired_digest
            || remote.active_release_id != request.active_release_id
            || remote.runtime_verified
            || remote.running_shards.len() > 2
            || remote
                .running_shards
                .iter()
                .any(|shard| !["master", "caves"].contains(&shard.as_str()))
        {
            return Err(error(
                "WORLD_CONFIG_PROTOCOL_INVALID",
                "世界配置应用响应身份或状态无效",
            ));
        }
        Ok(WorldApplyResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            revision: remote.revision,
            active_release_id: remote.active_release_id,
            desired_digest: remote.desired_digest,
            runtime_verified: false,
            running_shards: remote.running_shards,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> WorldSettings {
        WorldSettings {
            room_name: "Test world".into(),
            description: String::new(),
            game_mode: "survival".into(),
            max_players: 6,
            pvp: false,
            language: "zh".into(),
            pause_when_empty: true,
            caves_enabled: true,
            advanced: AdvancedConfig {
                vote_enabled: true,
                vote_kick_percentage: 50,
                admin_list: Vec::new(),
                whitelist: Vec::new(),
                whitelist_slots: 0,
                blocklist: Vec::new(),
            },
            worldgen: WorldgenConfig {
                master: ShardWorldConfig {
                    preset: "SURVIVAL_TOGETHER".into(),
                    worldgen_overrides: BTreeMap::new(),
                    world_settings_overrides: BTreeMap::new(),
                },
                caves: ShardWorldConfig {
                    preset: "DST_CAVE".into(),
                    worldgen_overrides: BTreeMap::new(),
                    world_settings_overrides: BTreeMap::new(),
                },
            },
        }
    }

    #[test]
    fn settings_reject_password_paths_and_invalid_values() {
        assert!(validate_settings(&settings()).is_ok());
        let mut invalid = settings();
        invalid.game_mode = "systemd".into();
        assert!(validate_settings(&invalid).is_err());
        let mut invalid = settings();
        invalid.advanced.whitelist_slots = 1;
        assert!(validate_settings(&invalid).is_err());
    }

    #[test]
    fn secret_replace_values_are_zeroized_and_shape_checked() {
        let value: SecretChange =
            serde_json::from_value(json!({"action":"replace","value":"secret"})).unwrap();
        assert!(matches!(value, SecretChange::Replace { .. }));
        assert!(
            serde_json::from_value::<SecretChange>(json!({"action":"keep","value":"secret"}))
                .is_err()
        );
    }

    #[test]
    fn preview_request_includes_both_expected_versions() {
        let request = PreviewApplyRequest {
            schema_version: 1,
            request_id: "44444444-4444-4444-8444-444444444444".into(),
            target: InstanceTarget {
                server_id: "33333333-3333-4333-8333-333333333333".into(),
                host_id: "11111111-1111-4111-8111-111111111111".into(),
                instance_id: "22222222-2222-4222-8222-222222222222".into(),
            },
            expected_record_version: 7,
            expected_revision: 12,
        };

        let payload = preview_request_payload(&request);

        assert_eq!(payload["expectedRecordVersion"], 7);
        assert_eq!(payload["expectedRevision"], 12);
        assert_eq!(payload["action"], "previewApply");
    }
}
