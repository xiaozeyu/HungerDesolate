use crate::dto::{CommandError, CommandResult};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::HashSet, io::Read, thread, time::Duration};

const OP: &str = "getWorkshopCollection";
const API: &str = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/";
const MAX_CHILDREN: usize = 1000;
const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub schema_version: u32,
    pub request_id: String,
    pub collection_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Response {
    pub schema_version: u32,
    pub request_id: String,
    pub collection_id: String,
    pub children: Vec<String>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "workshop", OP, message, "检查 Steam 合集链接后重试")
}

fn validate(request: &Request) -> CommandResult<()> {
    if request.schema_version != 1 {
        return Err(error(
            "WORKSHOP_COLLECTION_INPUT_INVALID",
            "合集查询参数无效",
        ));
    }
    crate::server_input::validate_id(&request.request_id, OP, "requestId")?;
    if request.collection_id.is_empty()
        || request.collection_id.len() > 20
        || !request
            .collection_id
            .bytes()
            .all(|byte| byte.is_ascii_digit())
    {
        return Err(error(
            "WORKSHOP_COLLECTION_INPUT_INVALID",
            "合集 ID 必须是数字",
        ));
    }
    Ok(())
}

fn child_id(value: &Value) -> Option<String> {
    value
        .get("publishedfileid")
        .and_then(|value| match value {
            Value::String(text) => Some(text.clone()),
            Value::Number(number) => Some(number.to_string()),
            _ => None,
        })
        .filter(|value| {
            !value.is_empty()
                && value.len() <= 20
                && value.bytes().all(|byte| byte.is_ascii_digit())
        })
}

fn parse_response(body: &Value, collection_id: &str) -> CommandResult<Vec<String>> {
    let details = body
        .get("response")
        .and_then(|value| value.get("collectiondetails"))
        .and_then(Value::as_array)
        .ok_or_else(|| {
            error(
                "WORKSHOP_COLLECTION_RESPONSE_INVALID",
                "Steam 合集响应缺少条目",
            )
        })?;
    let detail = details.first().ok_or_else(|| {
        error(
            "WORKSHOP_COLLECTION_RESPONSE_INVALID",
            "Steam 合集响应缺少条目",
        )
    })?;
    if detail
        .get("result")
        .and_then(Value::as_i64)
        .is_some_and(|result| result != 1)
    {
        return Err(error(
            "WORKSHOP_COLLECTION_NOT_FOUND",
            "Steam 合集不可用或不存在",
        ));
    }
    let children = detail
        .get("children")
        .and_then(Value::as_array)
        .ok_or_else(|| error("WORKSHOP_COLLECTION_RESPONSE_INVALID", "Steam 合集缺少子项"))?;
    if children.len() > MAX_CHILDREN {
        return Err(error(
            "WORKSHOP_COLLECTION_TOO_LARGE",
            "Steam 合集条目超过上限",
        ));
    }
    let mut seen = HashSet::new();
    let mut ids = Vec::new();
    for child in children {
        let Some(id) = child_id(child) else {
            continue;
        };
        if id == collection_id || !seen.insert(id.clone()) {
            continue;
        }
        ids.push(id);
    }
    Ok(ids)
}

fn fetch(collection_id: &str) -> CommandResult<Vec<String>> {
    crate::workshop::ensure_tls_provider()?;
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(8))
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|_| error("WORKSHOP_COLLECTION_CLIENT_FAILED", "无法初始化合集查询"))?;
    let form = [
        ("collectioncount".to_string(), "1".to_string()),
        ("publishedfileids[0]".to_string(), collection_id.to_string()),
    ];
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
                    "WORKSHOP_COLLECTION_SERVICE_UNAVAILABLE",
                    "Steam 合集服务暂时不可用",
                )
            } else {
                error(
                    "WORKSHOP_COLLECTION_RESPONSE_INVALID",
                    "Steam 返回了无效合集响应",
                )
            });
        }
        if response
            .content_length()
            .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
        {
            return Err(error(
                "WORKSHOP_COLLECTION_RESPONSE_TOO_LARGE",
                "Steam 合集响应超过大小限制",
            ));
        }
        let mut bytes = Vec::new();
        response
            .by_ref()
            .take(MAX_RESPONSE_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| {
                error(
                    "WORKSHOP_COLLECTION_RESPONSE_INVALID",
                    "无法读取 Steam 合集响应",
                )
            })?;
        if bytes.len() > MAX_RESPONSE_BYTES {
            return Err(error(
                "WORKSHOP_COLLECTION_RESPONSE_TOO_LARGE",
                "Steam 合集响应超过大小限制",
            ));
        }
        let body: Value = serde_json::from_slice(&bytes).map_err(|_| {
            error(
                "WORKSHOP_COLLECTION_RESPONSE_INVALID",
                "Steam 合集响应格式无效",
            )
        })?;
        return parse_response(&body, collection_id);
    }
    let _ = last_error;
    Err(error(
        "WORKSHOP_COLLECTION_SERVICE_UNAVAILABLE",
        "Steam 合集服务暂时不可用",
    ))
}

pub fn get(request: Request) -> CommandResult<Response> {
    validate(&request)?;
    let children = fetch(&request.collection_id)?;
    Ok(Response {
        schema_version: 1,
        request_id: request.request_id,
        collection_id: request.collection_id,
        children,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_children_and_ignores_invalid_duplicates() {
        let body = json!({
            "response": {"collectiondetails": [{
                "publishedfileid": "100",
                "result": 1,
                "children": [
                    {"publishedfileid": "200", "sortorder": 0},
                    {"publishedfileid": 300, "sortorder": 1},
                    {"publishedfileid": "200", "sortorder": 2},
                    {"publishedfileid": "../bad", "sortorder": 3},
                    {"publishedfileid": "100", "sortorder": 4}
                ]
            }]}
        });
        assert_eq!(
            parse_response(&body, "100").expect("valid collection"),
            vec!["200".to_string(), "300".to_string()]
        );
    }

    #[test]
    fn rejects_unavailable_collection() {
        let body = json!({"response": {"collectiondetails": [{"result": 9, "children": []}]}});
        assert!(parse_response(&body, "100").is_err());
    }

    #[test]
    fn validates_collection_id() {
        let request = Request {
            schema_version: 1,
            request_id: "11111111-1111-4111-8111-111111111111".to_string(),
            collection_id: "not-a-number".to_string(),
        };
        assert!(validate(&request).is_err());
    }
}
