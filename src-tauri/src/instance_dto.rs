use crate::ssh_dto::ConnectionRequest;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Lifecycle {
    Creating,
    Ready,
    CreateFailed,
    Deleting,
    DeleteFailed,
    Retired,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum RuntimeStatus {
    Unknown,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstanceSummary {
    pub host_id: String,
    pub instance_id: String,
    pub display_name: String,
    pub lifecycle: Lifecycle,
    pub runtime_status: RuntimeStatus,
    pub players: Option<u16>,
    pub max_players: Option<u16>,
    pub game_mode: Option<String>,
    pub caves_enabled: Option<bool>,
    pub installed_build: Option<String>,
    pub last_synced_at: Option<String>,
    pub active_task_id: Option<String>,
    pub manifest_available: bool,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstanceListResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub host_id: String,
    pub registry_revision: u64,
    pub captured_at_unix_seconds: u64,
    pub instances: Vec<InstanceSummary>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct OverviewRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
}
impl OverviewRequest {
    pub fn connection(&self) -> ConnectionRequest {
        ConnectionRequest {
            schema_version: self.schema_version,
            request_id: self.request_id.clone(),
            server_id: self.target.server_id.clone(),
            expected_record_version: self.expected_record_version,
        }
    }
}
#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InstanceTarget {
    pub server_id: String,
    pub host_id: String,
    pub instance_id: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ManifestSummary {
    pub config_revision: u64,
    pub compose_project: String,
    pub caves_enabled: bool,
    pub autostart: bool,
    pub game_mode: Option<String>,
    pub pvp: Option<bool>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstanceOverview {
    pub identity: InstanceSummary,
    pub manifest_summary: ManifestSummary,
    pub verification_level: &'static str,
    pub blockers: Vec<&'static str>,
    pub desired_vs_deployed: &'static str,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverviewResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub snapshot_id: String,
    pub captured_at_unix_seconds: u64,
    pub record_version: u64,
    pub config_revision: u64,
    pub source: &'static str,
    pub freshness: &'static str,
    pub data: InstanceOverview,
}
