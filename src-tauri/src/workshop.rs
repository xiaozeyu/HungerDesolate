use crate::{
    dto::{CommandError, CommandResult},
    persistence,
    ssh_dto::ConnectionRequest,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    io::Read,
    path::Path,
    thread,
    time::Duration,
};

const OP: &str = "getWorkshopMetadata";
const API: &str = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/";
const MAX_IDS: usize = 100;
const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub expected_record_version: u64,
    pub workshop_ids: Vec<String>,
}

#[derive(Debug)]
struct RemoteItem {
    workshop_id: String,
    result: Option<i64>,
    title: Option<String>,
    description: Option<String>,
    creator_steam_id: Option<String>,
    author: Option<String>,
    creator_app_id: Option<u32>,
    consumer_app_id: Option<u32>,
    time_created: Option<i64>,
    time_updated: Option<i64>,
    file_size: Option<i64>,
    preview_url: Option<String>,
    content_handle: Option<String>,
}

impl RemoteItem {
    fn parse(value: Value) -> CommandResult<Self> {
        let object = value
            .as_object()
            .ok_or_else(|| error("WORKSHOP_RESPONSE_INVALID", "Steam Workshop 条目格式无效"))?;
        let workshop_id = scalar_string(object.get("publishedfileid"))
            .filter(|value| {
                !value.is_empty()
                    && value.len() <= 20
                    && value.bytes().all(|byte| byte.is_ascii_digit())
            })
            .ok_or_else(|| error("WORKSHOP_RESPONSE_INVALID", "Steam Workshop 条目标识无效"))?;
        Ok(Self {
            workshop_id,
            result: scalar_i64(object.get("result")),
            title: string_value(object.get("title")),
            description: string_value(object.get("short_description")),
            creator_steam_id: scalar_string(object.get("creator")).filter(|value| {
                value.len() == 17 && value.bytes().all(|byte| byte.is_ascii_digit())
            }),
            author: None,
            creator_app_id: scalar_i64(object.get("creator_appid"))
                .and_then(|value| u32::try_from(value).ok()),
            consumer_app_id: scalar_i64(object.get("consumer_appid"))
                .and_then(|value| u32::try_from(value).ok()),
            time_created: scalar_i64(object.get("time_created")),
            time_updated: scalar_i64(object.get("time_updated")),
            file_size: scalar_i64(object.get("file_size")),
            preview_url: string_value(object.get("preview_url")),
            content_handle: scalar_string(object.get("hcontent_file")).filter(|handle| {
                !handle.is_empty()
                    && handle.len() <= 64
                    && handle.bytes().all(|byte| byte.is_ascii_digit())
                    && handle.bytes().any(|byte| byte != b'0')
            }),
        })
    }
}

fn string_value(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).map(str::to_owned)
}

fn scalar_string(value: Option<&Value>) -> Option<String> {
    value.and_then(|value| match value {
        Value::String(text) => Some(text.clone()),
        Value::Number(number) => Some(number.to_string()),
        _ => None,
    })
}

fn scalar_i64(value: Option<&Value>) -> Option<i64> {
    value.and_then(|value| match value {
        Value::Number(number) => number.as_i64(),
        Value::String(text) => text.parse().ok(),
        _ => None,
    })
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Item {
    pub workshop_id: String,
    pub result: Option<i64>,
    pub title: Option<String>,
    pub description: Option<String>,
    pub creator_steam_id: Option<String>,
    pub author: Option<String>,
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
        "检查 Workshop ID 和本机网络后重试",
    )
}

fn safe_preview_url(value: Option<String>) -> Option<String> {
    value.filter(|url| {
        url.len() <= 2048 && url.starts_with("https://") && !url.chars().any(char::is_control)
    })
}

pub(crate) fn ensure_tls_provider() -> CommandResult<()> {
    if rustls::crypto::CryptoProvider::get_default().is_none()
        && rustls::crypto::ring::default_provider()
            .install_default()
            .is_err()
        && rustls::crypto::CryptoProvider::get_default().is_none()
    {
        return Err(error(
            "WORKSHOP_CLIENT_FAILED",
            "无法初始化 Workshop 安全连接",
        ));
    }
    Ok(())
}

fn fetch_author(client: &reqwest::blocking::Client, steam_id: &str) -> Option<String> {
    let response = client
        .get(format!(
            "https://steamcommunity.com/profiles/{steam_id}/?xml=1"
        ))
        .header(reqwest::header::USER_AGENT, "hunger-desolate/0.1")
        .timeout(Duration::from_secs(5))
        .send()
        .ok()?;
    let text = response.text().ok()?;
    let start = text.find("<steamID>")? + "<steamID>".len();
    let end = text[start..].find("</steamID>")? + start;
    let value = text[start..end]
        .trim()
        .trim_start_matches("<![CDATA[")
        .trim_end_matches("]]>")
        .trim();
    if value.is_empty() || value.len() > 128 {
        return None;
    }
    Some(value.to_string())
}

fn fetch_authors(
    client: &reqwest::blocking::Client,
    creators: &HashSet<String>,
) -> HashMap<String, Option<String>> {
    let ids: Vec<String> = creators.iter().cloned().collect();
    let mut authors = HashMap::new();
    thread::scope(|scope| {
        let mut handles = Vec::new();
        for chunk in ids.chunks(8) {
            handles.push(scope.spawn(move || {
                chunk
                    .iter()
                    .map(|id| (id.clone(), fetch_author(client, id)))
                    .collect::<Vec<_>>()
            }));
        }
        for handle in handles {
            for (id, author) in handle.join().unwrap_or_default() {
                authors.insert(id, author);
            }
        }
    });
    authors
}

fn invoke(request: &Request) -> CommandResult<Vec<RemoteItem>> {
    ensure_tls_provider()?;
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(8))
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|_| error("WORKSHOP_CLIENT_FAILED", "无法初始化 Workshop 查询"))?;
    let mut form = vec![(
        "itemcount".to_string(),
        request.workshop_ids.len().to_string(),
    )];
    form.extend(
        request
            .workshop_ids
            .iter()
            .enumerate()
            .map(|(index, id)| (format!("publishedfileids[{index}]"), id.clone())),
    );
    let mut last_error = None;
    for attempt in 0..2 {
        let mut response = match client.post(API).form(&form).send() {
            Ok(response) => response,
            Err(cause) => {
                last_error = Some(cause);
                if attempt == 0 {
                    thread::sleep(Duration::from_millis(250));
                    continue;
                }
                break;
            }
        };
        if !response.status().is_success() {
            let status = response.status();
            return Err(if status.is_server_error() || status.as_u16() == 429 {
                error(
                    "WORKSHOP_SERVICE_UNAVAILABLE",
                    "Steam Workshop 暂时无法提供元数据",
                )
            } else {
                error("WORKSHOP_RESPONSE_INVALID", "Steam Workshop 返回了无效响应")
            });
        }
        if response
            .content_length()
            .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
        {
            return Err(error(
                "WORKSHOP_RESPONSE_TOO_LARGE",
                "Steam Workshop 响应超过大小限制",
            ));
        }
        let mut bytes = Vec::new();
        response
            .by_ref()
            .take(MAX_RESPONSE_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| error("WORKSHOP_RESPONSE_INVALID", "无法读取 Steam Workshop 响应"))?;
        if bytes.len() > MAX_RESPONSE_BYTES {
            return Err(error(
                "WORKSHOP_RESPONSE_TOO_LARGE",
                "Steam Workshop 响应超过大小限制",
            ));
        }
        let envelope: Value = serde_json::from_slice(&bytes)
            .map_err(|_| error("WORKSHOP_RESPONSE_INVALID", "Steam Workshop 响应格式无效"))?;
        let details = envelope
            .get("response")
            .and_then(|value| value.get("publishedfiledetails"))
            .ok_or_else(|| error("WORKSHOP_RESPONSE_INVALID", "Steam Workshop 响应缺少条目"))?;
        let details = details.as_array().ok_or_else(|| {
            error(
                "WORKSHOP_RESPONSE_INVALID",
                "Steam Workshop 条目列表格式无效",
            )
        })?;
        if details.len() > MAX_IDS {
            return Err(error(
                "WORKSHOP_RESPONSE_INVALID",
                "Steam Workshop 返回条目超过上限",
            ));
        }
        let mut items = details
            .iter()
            .cloned()
            .map(RemoteItem::parse)
            .collect::<CommandResult<Vec<_>>>()?;
        let creators: HashSet<String> = items
            .iter()
            .filter_map(|item| item.creator_steam_id.clone())
            .collect();
        let authors = fetch_authors(&client, &creators);
        for item in &mut items {
            item.author = item
                .creator_steam_id
                .as_ref()
                .and_then(|id| authors.get(id).cloned().flatten());
        }
        let requested: HashSet<&str> = request.workshop_ids.iter().map(String::as_str).collect();
        let mut returned = std::collections::HashSet::new();
        if items.iter().any(|item| {
            !requested.contains(item.workshop_id.as_str())
                || !returned.insert(item.workshop_id.as_str())
        }) {
            return Err(error(
                "WORKSHOP_RESPONSE_INVALID",
                "Steam Workshop 返回了重复或非请求条目",
            ));
        }
        return Ok(items);
    }
    let cause = last_error;
    let code = cause
        .as_ref()
        .map(reqwest::Error::is_timeout)
        .unwrap_or(false);
    Err(if code {
        error(
            "WORKSHOP_QUERY_TIMEOUT",
            "连接 Steam Workshop 超时，请检查本机网络",
        )
    } else {
        error(
            "WORKSHOP_METADATA_UNAVAILABLE",
            "无法连接 Steam Workshop，请检查本机网络或 DNS",
        )
    })
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
    connection.validate(OP)?;
    let database_connection = persistence::open(database).map_err(|_| CommandError::storage(OP))?;
    let server = persistence::get_server(&database_connection, &request.server_id, OP)?;
    if server.record_version != request.expected_record_version {
        return Err(CommandError::conflict(OP, server.record_version));
    }
    let items = invoke(&request)?;
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
                creator_steam_id: item.creator_steam_id,
                author: item.author,
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
}

#[cfg(test)]
mod tests {
    use super::RemoteItem;
    use serde_json::json;

    #[test]
    fn parses_numeric_and_string_encoded_steam_fields() {
        let item = RemoteItem::parse(json!({
            "publishedfileid": 2893991859_u64,
            "result": "1",
            "title": "Example mod",
            "short_description": "Description",
            "creator": 76561198000000000_u64,
            "creator_appid": "322330",
            "consumer_appid": 322330,
            "time_created": "1700000000",
            "time_updated": 1700000100,
            "file_size": "1024",
            "preview_url": "https://example.invalid/preview.png",
            "hcontent_file": 12345
        }))
        .expect("valid Steam item");
        assert_eq!(item.workshop_id, "2893991859");
        assert_eq!(item.result, Some(1));
        assert_eq!(item.creator_steam_id.as_deref(), Some("76561198000000000"));
        assert_eq!(item.creator_app_id, Some(322330));
        assert_eq!(item.time_created, Some(1700000000));
        assert_eq!(item.file_size, Some(1024));
        assert_eq!(item.content_handle.as_deref(), Some("12345"));
    }

    #[test]
    fn rejects_malformed_workshop_ids_without_rejecting_optional_unknown_fields() {
        assert!(RemoteItem::parse(json!({"publishedfileid": "../bad", "title": "x"})).is_err());
        let item =
            RemoteItem::parse(json!({"publishedfileid": "123", "time_updated": {"unknown": true}}))
                .expect("unknown optional value is ignored");
        assert_eq!(item.time_updated, None);
    }
}
