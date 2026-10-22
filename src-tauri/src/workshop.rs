use crate::{
    dto::{CommandError, CommandResult},
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use ssh2::{ExtendedData, Session};
use std::{
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const OP: &str = "getWorkshopMetadata";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/workshop-metadata.py");
const MAX_IDS: usize = 100;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub expected_record_version: u64,
    pub workshop_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RemoteItem {
    workshop_id: String,
    result: Option<i64>,
    title: Option<String>,
    description: Option<String>,
    creator_app_id: Option<u32>,
    consumer_app_id: Option<u32>,
    time_created: Option<i64>,
    time_updated: Option<i64>,
    file_size: Option<i64>,
    preview_url: Option<String>,
    content_handle: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Item {
    pub workshop_id: String,
    pub result: Option<i64>,
    pub title: Option<String>,
    pub description: Option<String>,
    pub creator_app_id: Option<u32>,
    pub consumer_app_id: Option<u32>,
    pub time_created: Option<i64>,
    pub time_updated: Option<i64>,
    pub file_size: Option<i64>,
    pub preview_url: Option<String>,
    pub content_handle: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Response {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub items: Vec<Item>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "workshop",
        OP,
        message,
        "检查 Workshop ID 和远端网络后重试",
    )
}

fn safe_preview_url(value: Option<String>) -> Option<String> {
    value.filter(|url| {
        url.len() <= 2048 && url.starts_with("https://") && !url.chars().any(char::is_control)
    })
}

fn invoke(session: &Session, request: &Request) -> CommandResult<Vec<RemoteItem>> {
    let payload = serde_json::json!({"schemaVersion":1,"workshopIds":request.workshop_ids});
    let encoded_request = STANDARD.encode(
        serde_json::to_vec(&payload)
            .map_err(|_| error("WORKSHOP_PROTOCOL_INVALID", "Workshop 请求编码失败"))?,
    );
    let encoded_script = STANDARD.encode(SCRIPT.as_bytes());
    let source = format!("import base64,json\nnamespace={{'__name__':'workshop_probe'}}\nexec(compile(base64.b64decode('{encoded_script}'),'<workshop-metadata>','exec'),namespace)\ntry:\n    namespace['main'](json.loads(base64.b64decode('{encoded_request}').decode('utf-8')))\nexcept Exception:\n    print(json.dumps({{'schemaVersion':1,'ok':False,'code':'WORKSHOP_PROBE_FAILED'}},separators=(',',':')))\n");
    let mut channel = session
        .channel_session()
        .map_err(|_| error("WORKSHOP_CHANNEL_FAILED", "无法建立 Workshop 查询通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("WORKSHOP_CHANNEL_FAILED", "无法配置 Workshop 查询通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("WORKSHOP_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("WORKSHOP_CHANNEL_FAILED", "无法提交 Workshop 查询"))?;
    channel
        .send_eof()
        .map_err(|_| error("WORKSHOP_CHANNEL_FAILED", "无法结束 Workshop 查询"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("WORKSHOP_QUERY_TIMEOUT", "Workshop 查询超时"));
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
            _ => {
                return Err(error(
                    "WORKSHOP_CHANNEL_FAILED",
                    "无法读取 Workshop 查询结果",
                ))
            }
        }
    }
    session.set_blocking(true);
    channel
        .wait_close()
        .map_err(|_| error("WORKSHOP_CHANNEL_FAILED", "Workshop 查询通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("WORKSHOP_PROTOCOL_INVALID", "Workshop 查询响应不是 JSON"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        return Err(
            match envelope
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("WORKSHOP_QUERY_FAILED")
            {
                "WORKSHOP_METADATA_UNAVAILABLE" => error(
                    "WORKSHOP_METADATA_UNAVAILABLE",
                    "无法连接 Steam Workshop 元数据服务",
                ),
                "WORKSHOP_INPUT_INVALID" => error("WORKSHOP_INPUT_INVALID", "Workshop ID 无效"),
                _ => error("WORKSHOP_QUERY_FAILED", "读取 Workshop 元数据失败"),
            },
        );
    }
    serde_json::from_value(
        envelope
            .get("items")
            .cloned()
            .ok_or_else(|| error("WORKSHOP_PROTOCOL_INVALID", "Workshop 响应缺少条目"))?,
    )
    .map_err(|_| error("WORKSHOP_PROTOCOL_INVALID", "Workshop 条目字段无效"))
}

pub fn get(database: &Path, request: Request) -> CommandResult<Response> {
    if request.schema_version != 1
        || request.workshop_ids.is_empty()
        || request.workshop_ids.len() > MAX_IDS
    {
        return Err(error("WORKSHOP_INPUT_INVALID", "Workshop 查询参数无效"));
    }
    for id in &request.workshop_ids {
        if id.is_empty() || id.len() > 20 || !id.bytes().all(|value| value.is_ascii_digit()) {
            return Err(error("WORKSHOP_INPUT_INVALID", "Workshop ID 必须是数字"));
        }
    }
    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };
    with_verified_session(database, &connection, |session, _| {
        let items = invoke(session, &request)?;
        Ok(Response {
            schema_version: 1,
            request_id: request.request_id.clone(),
            server_id: request.server_id.clone(),
            record_version: request.expected_record_version,
            items: items
                .into_iter()
                .map(|item| Item {
                    workshop_id: item.workshop_id,
                    result: item.result,
                    title: item.title,
                    description: item.description,
                    creator_app_id: item.creator_app_id,
                    consumer_app_id: item.consumer_app_id,
                    time_created: item.time_created,
                    time_updated: item.time_updated,
                    file_size: item.file_size,
                    preview_url: safe_preview_url(item.preview_url),
                    content_handle: item.content_handle,
                })
                .collect(),
        })
    })
}
