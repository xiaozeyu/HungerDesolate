use serde::{Deserialize, Serialize};

pub type CommandResult<T> = Result<T, Box<CommandError>>;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BootstrapSnapshot {
    pub schema_version: u32,
    pub product_name: &'static str,
    pub product_version: String,
    pub application_id: &'static str,
    pub remote_operations_available: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum AuthKind {
    Password,
    Key,
}
impl AuthKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Password => "password",
            Self::Key => "key",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerSummary {
    pub server_id: String,
    pub display_name: String,
    pub host: String,
    pub ssh_port: u16,
    pub username: String,
    pub auth_kind: AuthKind,
    pub host_id: Option<String>,
    pub connection_state: ConnectionState,
    pub record_version: u64,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ConnectionState {
    Disconnected,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ServerInput {
    pub display_name: String,
    pub host: String,
    pub ssh_port: u16,
    pub username: String,
    pub auth_kind: AuthKind,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveServerRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub operation_id: String,
    pub server_id: Option<String>,
    pub expected_record_version: Option<u64>,
    pub input: ServerInput,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DeleteServerRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub operation_id: String,
    pub server_id: String,
    pub expected_record_version: u64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DeleteServerResult {
    pub server_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CommandError {
    pub schema_version: u32,
    pub code: &'static str,
    pub category: &'static str,
    pub operation: &'static str,
    pub request_id: Option<String>,
    pub target: Option<serde_json::Value>,
    pub message: &'static str,
    pub retryable: bool,
    pub remote_changed: Option<bool>,
    pub task_id: Option<String>,
    pub recovery_steps: Vec<&'static str>,
    pub details: serde_json::Value,
}

impl CommandError {
    pub fn new(
        code: &'static str,
        category: &'static str,
        operation: &'static str,
        message: &'static str,
        recovery: &'static str,
    ) -> Box<Self> {
        Box::new(Self {
            schema_version: 1,
            code,
            category,
            operation,
            request_id: None,
            target: None,
            message,
            retryable: false,
            remote_changed: Some(false),
            task_id: None,
            recovery_steps: vec![recovery],
            details: serde_json::json!({}),
        })
    }
    pub fn storage(operation: &'static str) -> Box<Self> {
        let mut error = Self::new(
            "LOCAL_STORAGE_FAILED",
            "storage",
            operation,
            "读写本地连接记录失败",
            "刷新连接列表核实结果，检查应用数据目录权限后重试",
        );
        error.retryable = true;
        error
    }
    pub fn invalid(
        operation: &'static str,
        field: &'static str,
        message: &'static str,
    ) -> Box<Self> {
        let mut error = Self::new(
            "INPUT_INVALID",
            "validation",
            operation,
            message,
            "修正对应字段后重试",
        );
        error.details = serde_json::json!({"field":field});
        error
    }
    pub fn conflict(operation: &'static str, version: u64) -> Box<Self> {
        let mut error = Self::new(
            "REVISION_CONFLICT",
            "conflict",
            operation,
            "连接记录已被修改",
            "保留输入，重新读取连接记录并比较后再保存",
        );
        error.details = serde_json::json!({"currentRevision":version});
        error
    }
}

impl std::fmt::Display for CommandError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for CommandError {}
