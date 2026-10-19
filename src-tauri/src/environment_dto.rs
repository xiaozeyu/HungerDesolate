use serde::Serialize;

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum CheckLevel {
    Passed,
    Warning,
    Blocker,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EnvironmentCheck {
    pub code: &'static str,
    pub level: CheckLevel,
    pub label: String,
    pub message: &'static str,
    pub recovery_steps: Vec<&'static str>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DiskSnapshot {
    pub path: String,
    pub total_bytes: u64,
    pub available_bytes: u64,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MemorySnapshot {
    pub total_bytes: u64,
    pub available_bytes: Option<u64>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DockerEnvironment {
    pub client_version: Option<String>,
    pub server_version: Option<String>,
    pub compose_version: Option<String>,
    pub engine_state: EngineState,
    pub data_root: Option<String>,
    pub disk: Option<DiskSnapshot>,
}
#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum EngineState {
    NotInstalled,
    PermissionDenied,
    Unavailable,
    Available,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HostEnvironment {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub host_id: Option<String>,
    pub environment_check_id: String,
    pub checked_at_unix_seconds: u64,
    pub os: Option<String>,
    pub distribution_id: Option<String>,
    pub distribution_version: Option<String>,
    pub distribution_codename: Option<String>,
    pub kernel: Option<String>,
    pub architecture: Option<String>,
    pub uid: Option<u32>,
    pub disk: Option<DiskSnapshot>,
    pub memory: Option<MemorySnapshot>,
    pub docker: DockerEnvironment,
    pub checks: Vec<EnvironmentCheck>,
    pub deployment_verified: bool,
}
