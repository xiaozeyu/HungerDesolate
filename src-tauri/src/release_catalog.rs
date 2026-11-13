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

const OP: &str = "listInstanceReleases";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-release-catalog.py");

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseSummary {
    pub release_id: String,
    pub platform: String,
    pub image: String,
    pub image_digest: String,
    pub dst_build: String,
    pub artifact_digest: String,
    pub script_version: String,
    #[serde(default)]
    pub created_at: Option<String>,
    pub verified: bool,
    pub runtime_verified: bool,
    pub commands: Value,
    #[serde(default)]
    pub image_size: Option<u64>,
    #[serde(default)]
    pub in_use: bool,
    #[serde(default)]
    pub reclaimable: bool,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Probe {
    schema_version: u32,
    host_id: String,
    catalog_digest: Option<String>,
    releases: Vec<ReleaseSummary>,
    blockers: Vec<String>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Response {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub host_id: String,
    pub record_version: u64,
    pub catalog_digest: Option<String>,
    pub releases: Vec<ReleaseSummary>,
    pub blockers: Vec<String>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "release",
        OP,
        message,
        "刷新版本列表后重试；仍失败时重新准备或验证 DST 版本",
    )
}

fn execute(session: &Session, host_id: &str) -> CommandResult<Probe> {
    let encoded =
        STANDARD.encode(serde_json::to_vec(&json!({"hostId":host_id})).map_err(|_| {
            error(
                "CREATE_RELEASE_PROTOCOL_INVALID",
                "release 查询请求编码失败",
            )
        })?);
    let source = format!("import base64\n{SCRIPT}\nmain(json.loads(base64.b64decode('{encoded}').decode('utf-8')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法创建 release 查询通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法配置 release 查询通道"))?;
    channel.exec("/usr/bin/python3 -I -").map_err(|_| {
        error(
            "CREATE_RELEASE_RUNTIME_REQUIRED",
            "远端需要 /usr/bin/python3",
        )
    })?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法提交 release 查询"))?;
    channel
        .send_eof()
        .map_err(|_| error("CREATE_RELEASE_CHANNEL_FAILED", "无法结束 release 查询"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(error("CREATE_RELEASE_TIMEOUT", "release 查询超时"));
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
                    "无法读取 release 查询结果",
                ))
            }
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel.wait_close().map_err(|_| {
        error(
            "CREATE_RELEASE_CHANNEL_FAILED",
            "release 查询通道未正常结束",
        )
    })?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("CREATE_RELEASE_PROTOCOL_INVALID", "release 查询响应无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(error(
            "CREATE_RELEASE_CATALOG_INVALID",
            "无法读取主机上的版本清单",
        ));
    }
    serde_json::from_value(
        envelope
            .get("data")
            .cloned()
            .ok_or_else(|| error("CREATE_RELEASE_PROTOCOL_INVALID", "release 查询缺少数据"))?,
    )
    .map_err(|_| {
        error(
            "CREATE_RELEASE_PROTOCOL_INVALID",
            "release catalog 字段无效",
        )
    })
}

pub fn list(database: &Path, request: ConnectionRequest) -> CommandResult<Response> {
    with_verified_session(database, &request, |session, host_id| {
        let host_id =
            host_id.ok_or_else(|| error("HOST_UNINITIALIZED", "请先初始化主机管理目录"))?;
        let probe = execute(session, &host_id)?;
        if probe.schema_version != 1 || probe.host_id != host_id || probe.releases.len() > 16 {
            return Err(error(
                "CREATE_RELEASE_PROTOCOL_INVALID",
                "release catalog 身份或数量无效",
            ));
        }
        Ok(Response {
            schema_version: 1,
            request_id: request.request_id.clone(),
            server_id: request.server_id.clone(),
            host_id,
            record_version: request.expected_record_version,
            catalog_digest: probe.catalog_digest,
            releases: probe.releases,
            blockers: probe.blockers,
        })
    })
}
