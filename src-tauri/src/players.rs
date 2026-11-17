use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::InstanceTarget,
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

pub const BRIDGE_VERSION: &str = "1.0.0";
const MODINFO: &str = include_str!("../../remote-scripts/player-bridge/modinfo.lua");
const MODMAIN: &str = include_str!("../../remote-scripts/player-bridge/modmain.lua");
const PLAYERS_SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-players.py");
const BRIDGE_SCRIPT: &str = include_str!("../../remote-scripts/actions/player-bridge-apply.py");
const OVERRIDES_SCRIPT: &str = include_str!("../../remote-scripts/actions/mods-overrides.py");
const OP: &str = "getInstancePlayers";
const MAX_OUTPUT: usize = 1024 * 1024;

pub fn bridge_digest() -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"modinfo.lua\0");
    hasher.update(MODINFO.as_bytes());
    hasher.update(b"modmain.lua\0");
    hasher.update(MODMAIN.as_bytes());
    format!("sha256:{:x}", hasher.finalize())
}

pub fn bridge_payload() -> Value {
    json!({
        "version": BRIDGE_VERSION,
        "digest": bridge_digest(),
        "files": {
            "modinfo.lua": STANDARD.encode(MODINFO.as_bytes()),
            "modmain.lua": STANDARD.encode(MODMAIN.as_bytes()),
        }
    })
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PlayersRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BridgeRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct PlayerBridgeInfo {
    pub enabled: bool,
    pub version: String,
    pub digest: String,
    pub installed_at: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlayerEntry {
    pub user_id: String,
    pub name: String,
    pub character: String,
    pub shard: String,
    pub captured_at_unix_seconds: i64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlayersData {
    pub bridge: Option<PlayerBridgeInfo>,
    pub runtime_status: String,
    pub online_count: u32,
    pub players: Vec<PlayerEntry>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlayersResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub snapshot_id: String,
    pub captured_at: String,
    pub source: String,
    pub freshness: String,
    pub config_revision: u64,
    pub data: PlayersData,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeStatusResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub config_revision: u64,
    pub bridge: Option<PlayerBridgeInfo>,
    pub blockers: Vec<String>,
    pub allowed: bool,
    pub restart_required: bool,
    pub runtime_status: String,
    pub shards: Vec<String>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "players", OP, message, "刷新专服状态后重试")
}

fn validate_request(
    schema_version: u32,
    request_id: &str,
    target: &InstanceTarget,
    record_version: u64,
) -> CommandResult<()> {
    if schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "玩家列表请求版本不受支持"));
    }
    crate::server_input::validate_id(request_id, OP, "requestId")?;
    for (field, value) in [
        ("serverId", &target.server_id),
        ("hostId", &target.host_id),
        ("instanceId", &target.instance_id),
    ] {
        crate::server_input::validate_id(value, OP, field)?;
    }
    if record_version == 0 || record_version > crate::server_input::MAX_REVISION {
        return Err(error("PLAYER_INPUT_INVALID", "主机连接版本无效"));
    }
    Ok(())
}

fn connection(request_id: &str, target: &InstanceTarget, record_version: u64) -> ConnectionRequest {
    ConnectionRequest {
        schema_version: 1,
        request_id: request_id.to_string(),
        server_id: target.server_id.clone(),
        expected_record_version: record_version,
    }
}

fn run_python(session: &Session, source: &str, failure: &'static str) -> CommandResult<Value> {
    let mut channel = session
        .channel_session()
        .map_err(|_| error(failure, "无法建立玩家查询通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error(failure, "无法配置玩家查询通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("PLAYER_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error(failure, "无法提交玩家查询"))?;
    channel
        .send_eof()
        .map_err(|_| error(failure, "无法结束玩家查询请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            session.set_blocking(true);
            return Err(error("PLAYER_QUERY_TIMEOUT", "玩家查询超时"));
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
                session.set_blocking(true);
                return Err(error(failure, "无法读取玩家查询结果"));
            }
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error(failure, "玩家查询通道未正常结束"))?;
    serde_json::from_slice(&output)
        .map_err(|_| error("PLAYER_PROTOCOL_INVALID", "玩家查询响应不是 JSON"))
}

fn encoded_source(script: &str, request: &Value) -> CommandResult<String> {
    let script = STANDARD.encode(script.as_bytes());
    let request = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("PLAYER_PROTOCOL_INVALID", "玩家查询请求编码失败"))?,
    );
    Ok(format!(
        "import base64,json\nnamespace={{'__name__':'instance_players'}}\nexec(compile(base64.b64decode('{script}'),'<instance-players>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'ok':False,'code':'PLAYER_QUERY_FAILED'}},separators=(',',':')))\n"
    ))
}

fn bridge_source(request: &Value) -> CommandResult<String> {
    let script = STANDARD.encode(BRIDGE_SCRIPT.as_bytes());
    let overrides = STANDARD.encode(OVERRIDES_SCRIPT.as_bytes());
    let request = STANDARD.encode(
        serde_json::to_vec(request)
            .map_err(|_| error("PLAYER_PROTOCOL_INVALID", "玩家桥接请求编码失败"))?,
    );
    Ok(format!(
        "import base64,json\nBRIDGE_SOURCE=base64.b64decode('{script}').decode('utf-8')\nOVERRIDES_SOURCE=base64.b64decode('{overrides}').decode('utf-8')\nnamespace={{'__name__':'player_bridge_apply'}}\nexec(compile(BRIDGE_SOURCE,'<player-bridge-apply>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{request}').decode('utf-8')),OVERRIDES_SOURCE)\nexcept Exception:\n    print(json.dumps({{'ok':False,'code':'PLAYER_BRIDGE_FAILED'}},separators=(',',':')))\n"
    ))
}

fn envelope_data(envelope: Value) -> CommandResult<Value> {
    if envelope.get("ok") == Some(&Value::Bool(false)) {
        let code = envelope
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("PLAYER_QUERY_FAILED");
        return Err(match code {
            "MANIFEST_NOT_FOUND" => error("MANIFEST_NOT_FOUND", "专服创建尚未完成"),
            "INSTANCE_IDENTITY_MISMATCH" => {
                error("INSTANCE_IDENTITY_MISMATCH", "专服身份与主机记录不一致")
            }
            "PLAYER_STATE_INVALID" => error("PLAYER_STATE_INVALID", "玩家状态文件无效"),
            "PLAYER_INPUT_INVALID" => error("PLAYER_INPUT_INVALID", "玩家查询请求无效"),
            "REVISION_CONFLICT" => error("REVISION_CONFLICT", "专服配置已变化，请刷新后重试"),
            "PLAYER_BRIDGE_LAYOUT_UNSUPPORTED" | "PLAYER_BRIDGE_RELEASE_REQUIRED" => error(
                "PLAYER_BRIDGE_LAYOUT_UNSUPPORTED",
                "当前实例布局不支持玩家桥接",
            ),
            "PLAYER_BRIDGE_OVERRIDES_UNMANAGED" => error(
                "PLAYER_BRIDGE_OVERRIDES_UNMANAGED",
                "MOD 覆盖文件不是应用管理的格式",
            ),
            "PLAYER_BRIDGE_BUSY" => error("PLAYER_BRIDGE_BUSY", "专服正在执行其他维护操作"),
            _ if code.starts_with("PLAYER_BRIDGE") => {
                error("PLAYER_BRIDGE_FAILED", "玩家桥接未能完成")
            }
            _ => error("PLAYER_QUERY_FAILED", "玩家请求未能完成"),
        });
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("PLAYER_PROTOCOL_INVALID", "玩家响应缺少数据"))
}

fn valid_bridge(bridge: &PlayerBridgeInfo) -> bool {
    bridge.enabled
        && bridge.version.len() <= 32
        && !bridge.version.is_empty()
        && bridge.digest.len() == 71
        && bridge.digest.starts_with("sha256:")
        && bridge.digest[7..]
            .bytes()
            .all(|value| value.is_ascii_hexdigit())
        && bridge
            .installed_at
            .as_ref()
            .map(|value| value.len() <= 32)
            .unwrap_or(true)
}

fn validate_players(mut response: PlayersResponse) -> CommandResult<PlayersResponse> {
    if response.schema_version != 1
        || response.snapshot_id.is_empty()
        || response.captured_at.is_empty()
        || response.source != "remote"
        || !["fresh", "stale", "unavailable", "disabled"].contains(&response.freshness.as_str())
        || !["running", "stopped", "degraded", "unknown"]
            .contains(&response.data.runtime_status.as_str())
        || response.data.players.len() > 128
        || response.data.warnings.len() > 32
        || response.data.warnings.iter().any(|warning| {
            !["PLAYER_ENTRY_INVALID", "PLAYER_STATE_INVALID"].contains(&warning.as_str())
        })
        || (response.freshness == "disabled"
            && (response.data.bridge.is_some()
                || !response.data.players.is_empty()
                || response.data.online_count != 0))
        || (response.freshness == "unavailable" && !response.data.players.is_empty())
        || (response.freshness != "disabled" && response.data.bridge.is_none())
        || response
            .data
            .bridge
            .as_ref()
            .map(|bridge| !valid_bridge(bridge))
            .unwrap_or(false)
        || response.data.online_count as usize != response.data.players.len()
    {
        return Err(error("PLAYER_PROTOCOL_INVALID", "玩家响应身份或状态无效"));
    }
    for player in &response.data.players {
        if player.user_id.len() > 64
            || !player
                .user_id
                .bytes()
                .all(|value| value.is_ascii_alphanumeric() || value == b'_' || value == b'-')
            || player.name.is_empty()
            || player.name.chars().count() > 64
            || player.character.chars().count() > 64
            || !["Master", "Caves"].contains(&player.shard.as_str())
            || player.captured_at_unix_seconds < 0
            || player
                .name
                .chars()
                .chain(player.character.chars())
                .any(|value| value < ' ')
        {
            return Err(error("PLAYER_PROTOCOL_INVALID", "玩家条目无效"));
        }
    }
    response.data.warnings.sort();
    response.data.warnings.dedup();
    Ok(response)
}

fn validate_bridge(mut response: BridgeStatusResponse) -> CommandResult<BridgeStatusResponse> {
    if response.schema_version != 1
        || response.blockers.len() > 16
        || !["running", "stopped", "degraded", "unknown"]
            .contains(&response.runtime_status.as_str())
        || response.shards.is_empty()
        || response.shards.len() > 2
        || response
            .shards
            .iter()
            .any(|shard| !["master", "caves"].contains(&shard.as_str()))
        || response.blockers.iter().any(|code| {
            ![
                "PLAYER_BRIDGE_RELEASE_REQUIRED",
                "PLAYER_BRIDGE_LAYOUT_UNSUPPORTED",
                "PLAYER_BRIDGE_OVERRIDES_UNMANAGED",
            ]
            .contains(&code.as_str())
        })
        || response
            .bridge
            .as_ref()
            .map(|bridge| !bridge.enabled || !valid_bridge(bridge))
            .unwrap_or(false)
    {
        return Err(error("PLAYER_PROTOCOL_INVALID", "玩家桥接响应无效"));
    }
    response.allowed = response.allowed && response.blockers.is_empty();
    Ok(response)
}

pub fn list(database: &Path, request: PlayersRequest) -> CommandResult<PlayersResponse> {
    validate_request(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "玩家目标主机身份不一致"));
        }
        let payload = json!({
            "schemaVersion": 1,
            "requestId": request.request_id,
            "serverId": request.target.server_id,
            "hostId": request.target.host_id,
            "instanceId": request.target.instance_id
        });
        let envelope = run_python(
            session,
            &encoded_source(PLAYERS_SCRIPT, &payload)?,
            "PLAYER_QUERY_FAILED",
        )?;
        let response: PlayersResponse = serde_json::from_value(envelope_data(envelope)?)
            .map_err(|_| error("PLAYER_PROTOCOL_INVALID", "玩家响应字段无效"))?;
        if response.request_id != request.request_id
            || response.target.server_id != request.target.server_id
            || response.target.host_id != request.target.host_id
            || response.target.instance_id != request.target.instance_id
        {
            return Err(error("PLAYER_PROTOCOL_INVALID", "玩家响应身份不匹配"));
        }
        validate_players(response)
    })
}

fn bridge_request_payload(request: &BridgeRequest, action: &str) -> CommandResult<Value> {
    let mut payload = json!({
        "schemaVersion": 1,
        "action": action,
        "requestId": request.request_id,
        "serverId": request.target.server_id,
        "hostId": request.target.host_id,
        "instanceId": request.target.instance_id,
        "expectedRevision": request.expected_revision
    });
    if action == "apply" {
        payload["bridge"] = bridge_payload();
    }
    Ok(payload)
}

fn bridge_action(
    database: &Path,
    request: BridgeRequest,
    action: &'static str,
) -> CommandResult<BridgeStatusResponse> {
    validate_request(
        request.schema_version,
        &request.request_id,
        &request.target,
        request.expected_record_version,
    )?;
    if request.expected_revision > crate::server_input::MAX_REVISION {
        return Err(error("PLAYER_INPUT_INVALID", "专服配置版本无效"));
    }
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "玩家桥接目标主机身份不一致",
            ));
        }
        let envelope = run_python(
            session,
            &bridge_source(&bridge_request_payload(&request, action)?)?,
            "PLAYER_BRIDGE_FAILED",
        )?;
        let response: BridgeStatusResponse = serde_json::from_value(envelope_data(envelope)?)
            .map_err(|_| error("PLAYER_PROTOCOL_INVALID", "玩家桥接响应字段无效"))?;
        if response.request_id != request.request_id
            || response.target.server_id != request.target.server_id
            || response.target.host_id != request.target.host_id
            || response.target.instance_id != request.target.instance_id
            || response.config_revision != request.expected_revision
        {
            return Err(error(
                "PLAYER_PROTOCOL_INVALID",
                "玩家桥接响应身份或版本不匹配",
            ));
        }
        let response = validate_bridge(response)?;
        if action == "apply" && (!response.allowed || response.bridge.is_none()) {
            return Err(error("PLAYER_BRIDGE_FAILED", "玩家桥接未能完成安装"));
        }
        Ok(response)
    })
}

pub fn preview_bridge(
    database: &Path,
    request: BridgeRequest,
) -> CommandResult<BridgeStatusResponse> {
    bridge_action(database, request, "preview")
}

pub fn apply_bridge(
    database: &Path,
    request: BridgeRequest,
) -> CommandResult<BridgeStatusResponse> {
    bridge_action(database, request, "apply")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bridge_digest_matches_the_bundled_sources() {
        let digest = bridge_digest();
        assert!(digest.starts_with("sha256:"));
        assert_eq!(digest.len(), 71);
        let payload = bridge_payload();
        assert_eq!(payload["version"], BRIDGE_VERSION);
        assert_eq!(payload["digest"], digest);
    }

    #[test]
    fn player_response_rejects_more_online_players_than_entries() {
        let response = PlayersResponse {
            schema_version: 1,
            request_id: "req".into(),
            target: InstanceTarget {
                server_id: "11111111-1111-4111-8111-111111111111".into(),
                host_id: "22222222-2222-4222-8222-222222222222".into(),
                instance_id: "33333333-3333-4333-8333-333333333333".into(),
            },
            snapshot_id: "snapshot".into(),
            captured_at: "2026-11-17T00:00:00Z".into(),
            source: "remote".into(),
            freshness: "fresh".into(),
            config_revision: 1,
            data: PlayersData {
                bridge: Some(PlayerBridgeInfo {
                    enabled: true,
                    version: BRIDGE_VERSION.into(),
                    digest: bridge_digest(),
                    installed_at: None,
                }),
                runtime_status: "running".into(),
                online_count: 1,
                players: vec![],
                warnings: vec![],
            },
        };
        assert!(validate_players(response).is_err());
    }

    #[test]
    fn bridge_response_rejects_unknown_blockers() {
        let response = BridgeStatusResponse {
            schema_version: 1,
            request_id: "req".into(),
            target: InstanceTarget {
                server_id: "11111111-1111-4111-8111-111111111111".into(),
                host_id: "22222222-2222-4222-8222-222222222222".into(),
                instance_id: "33333333-3333-4333-8333-333333333333".into(),
            },
            config_revision: 1,
            bridge: None,
            blockers: vec!["UNEXPECTED".into()],
            allowed: true,
            restart_required: false,
            runtime_status: "stopped".into(),
            shards: vec!["master".into()],
        };
        assert!(validate_bridge(response).is_err());
    }
}
