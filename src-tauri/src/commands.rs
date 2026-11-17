use crate::{
    dto::{
        BootstrapSnapshot, CommandError, CommandResult, DeleteServerRequest, DeleteServerResult,
        SaveServerRequest, ServerSummary,
    },
    persistence, AppState,
};
use tauri::State;
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
pub async fn pick_private_key_file(app: tauri::AppHandle) -> CommandResult<Option<String>> {
    ssh_blocking("pickPrivateKeyFile", None, move || {
        app.dialog()
            .file()
            .set_title("选择 SSH 私钥文件")
            .blocking_pick_file()
            .map(|file| {
                file.into_path()
                    .ok()
                    .and_then(|path| path.into_os_string().into_string().ok())
                    .ok_or_else(|| {
                        CommandError::new(
                            "PRIVATE_KEY_PATH_INVALID",
                            "validation",
                            "pickPrivateKeyFile",
                            "无法使用该文件路径",
                            "选择本机 UTF-8 路径的私钥文件",
                        )
                    })
            })
            .transpose()
    })
    .await
}

#[tauri::command]
pub fn get_bootstrap_snapshot(app: tauri::AppHandle) -> BootstrapSnapshot {
    BootstrapSnapshot {
        schema_version: 1,
        product_name: "荒野中枢",
        product_version: app.package_info().version.to_string(),
        application_id: "app.hungerdesolate.hub",
        remote_operations_available: false,
    }
}

#[tauri::command]
pub async fn list_servers(state: State<'_, AppState>) -> CommandResult<Vec<ServerSummary>> {
    let path = state.database_path.clone();
    tauri::async_runtime::spawn_blocking(move || persistence::list_servers(&path))
        .await
        .map_err(|_| CommandError::storage("listServers"))?
        .map_err(|_| CommandError::storage("listServers"))
}

#[tauri::command]
pub async fn save_server(
    state: State<'_, AppState>,
    request: SaveServerRequest,
) -> CommandResult<ServerSummary> {
    let path = state.database_path.clone();
    let request_id = uuid::Uuid::try_parse(&request.request_id)
        .ok()
        .map(|id| id.to_string());
    tauri::async_runtime::spawn_blocking(move || -> CommandResult<ServerSummary> {
        let saved = persistence::save_server(&path, request)?;
        crate::secrets::cleanup(&path)?;
        Ok(saved)
    })
    .await
    .map_err(|_| CommandError::storage("saveServer"))?
    .map_err(|mut error| {
        error.request_id = request_id;
        error
    })
}

#[tauri::command]
pub async fn delete_server(
    state: State<'_, AppState>,
    request: DeleteServerRequest,
) -> CommandResult<DeleteServerResult> {
    let path = state.database_path.clone();
    let request_id = uuid::Uuid::try_parse(&request.request_id)
        .ok()
        .map(|id| id.to_string());
    tauri::async_runtime::spawn_blocking(move || -> CommandResult<DeleteServerResult> {
        let deleted = persistence::delete_server(&path, request)?;
        crate::secrets::cleanup(&path)?;
        Ok(deleted)
    })
    .await
    .map_err(|_| CommandError::storage("deleteServer"))?
    .map_err(|mut error| {
        error.request_id = request_id;
        error
    })
}

async fn ssh_blocking<T: Send + 'static>(
    operation: &'static str,
    request_id: Option<String>,
    work: impl FnOnce() -> CommandResult<T> + Send + 'static,
) -> CommandResult<T> {
    tauri::async_runtime::spawn_blocking(work)
        .await
        .map_err(|_| {
            CommandError::new(
                "CONNECTION_WORKER_FAILED",
                "connection",
                operation,
                "连接工作线程未能完成",
                "刷新状态并重试",
            )
        })?
        .map_err(|mut error| {
            error.request_id = request_id;
            error
        })
}

#[tauri::command]
pub async fn preview_host_initialization(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::host_initialization::InitializationResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewHostInitialization",
        safe_request_id(&request.request_id),
        move || crate::host_initialization::preview(&path, request),
    )
    .await
}
#[tauri::command]
pub async fn get_host_initialization(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::host_initialization::InitializationResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getHostInitialization",
        safe_request_id(&request.request_id),
        move || crate::host_initialization::inspect(&path, request),
    )
    .await
}
#[tauri::command]
pub async fn initialize_host(
    state: State<'_, AppState>,
    request: crate::host_initialization::InitializeRequest,
) -> CommandResult<crate::host_initialization::InitializationResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "initializeHost",
        safe_request_id(&request.connection.request_id),
        move || crate::host_initialization::initialize(&path, request),
    )
    .await
}
fn safe_request_id(id: &str) -> Option<String> {
    uuid::Uuid::try_parse(id).ok().map(|id| id.to_string())
}

#[tauri::command]
pub async fn get_host_environment(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::environment_dto::HostEnvironment> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getHostEnvironment",
        safe_request_id(&request.request_id),
        move || crate::environment::inspect(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_docker_installation(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::docker_installation::PreviewResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewDockerInstallation",
        safe_request_id(&request.request_id),
        move || crate::docker_installation::preview(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn start_docker_installation(
    state: State<'_, AppState>,
    request: crate::docker_installation::StartRequest,
) -> CommandResult<crate::docker_installation::TaskResponse> {
    let path = state.database_path.clone();
    let request_id = safe_request_id(&request.connection.request_id);
    ssh_blocking("startDockerInstallation", request_id, move || {
        crate::docker_installation::start(&path, request)
    })
    .await
}

#[tauri::command]
pub async fn get_docker_installation_status(
    state: State<'_, AppState>,
    request: crate::docker_installation::StatusRequest,
) -> CommandResult<crate::docker_installation::TaskResponse> {
    let path = state.database_path.clone();
    let request_id = safe_request_id(&request.connection.request_id);
    ssh_blocking("getDockerInstallationStatus", request_id, move || {
        crate::docker_installation::status(&path, request)
    })
    .await
}

#[tauri::command]
pub async fn list_instances(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::instance_dto::InstanceListResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstances",
        safe_request_id(&request.request_id),
        move || crate::registry::list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_overview(
    state: State<'_, AppState>,
    request: crate::instance_dto::OverviewRequest,
) -> CommandResult<crate::instance_dto::OverviewResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceOverview",
        safe_request_id(&request.request_id),
        move || crate::registry::overview(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_diagnostics(
    state: State<'_, AppState>,
    request: crate::diagnostics::Request,
) -> CommandResult<crate::diagnostics::Response> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceDiagnostics",
        safe_request_id(&request.request_id),
        move || crate::diagnostics::get(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_workshop_metadata(
    state: State<'_, AppState>,
    request: crate::workshop::Request,
) -> CommandResult<crate::workshop::Response> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getWorkshopMetadata",
        safe_request_id(&request.request_id),
        move || crate::workshop::get(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_workshop_collection(
    request: crate::workshop_collection::Request,
) -> CommandResult<crate::workshop_collection::Response> {
    ssh_blocking(
        "getWorkshopCollection",
        safe_request_id(&request.request_id),
        move || crate::workshop_collection::get(request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_mods(
    state: State<'_, AppState>,
    request: crate::mods::ListRequest,
) -> CommandResult<crate::mods::ListResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceMods",
        safe_request_id(&request.request_id),
        move || crate::mods::list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn save_instance_mods(
    state: State<'_, AppState>,
    request: crate::mods::SaveRequest,
) -> CommandResult<crate::mods::SaveResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "saveInstanceMods",
        safe_request_id(&request.request_id),
        move || crate::mods::save(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_apply_instance_mods(
    state: State<'_, AppState>,
    request: crate::mods::ApplyRequest,
) -> CommandResult<crate::mods::ApplyPreviewResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewApplyInstanceMods",
        safe_request_id(&request.request_id),
        move || crate::mods::preview_apply(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn apply_instance_mods(
    state: State<'_, AppState>,
    request: crate::mods::ApplyStartRequest,
) -> CommandResult<crate::mods::DownloadTaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "applyInstanceMods",
        safe_request_id(&request.request_id),
        move || crate::mods::apply_start(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_players(
    state: State<'_, AppState>,
    request: crate::players::PlayersRequest,
) -> CommandResult<crate::players::PlayersResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstancePlayers",
        safe_request_id(&request.request_id),
        move || crate::players::list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_instance_player_bridge(
    state: State<'_, AppState>,
    request: crate::players::BridgeRequest,
) -> CommandResult<crate::players::BridgeStatusResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewInstancePlayerBridge",
        safe_request_id(&request.request_id),
        move || crate::players::preview_bridge(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn install_instance_player_bridge(
    state: State<'_, AppState>,
    request: crate::players::BridgeRequest,
) -> CommandResult<crate::players::BridgeStatusResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "installInstancePlayerBridge",
        safe_request_id(&request.request_id),
        move || crate::players::apply_bridge(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn cancel_instance_mod_apply(
    state: State<'_, AppState>,
    request: crate::mods::ApplyTaskRequest,
) -> CommandResult<crate::mods::DownloadTaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "cancelInstanceModApply",
        safe_request_id(&request.request_id),
        move || crate::mods::apply_cancel(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_mod_apply_task(
    state: State<'_, AppState>,
    request: crate::mods::ApplyTaskRequest,
) -> CommandResult<crate::mods::DownloadTaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceModApplyTask",
        safe_request_id(&request.request_id),
        move || crate::mods::apply_status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_mod_apply_tasks(
    state: State<'_, AppState>,
    request: crate::mods::ListRequest,
) -> CommandResult<crate::mods::DownloadTaskListResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceModApplyTasks",
        safe_request_id(&request.request_id),
        move || crate::mods::apply_tasks(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_backups(
    state: State<'_, AppState>,
    request: crate::backups::ListRequest,
) -> CommandResult<crate::backups::ListResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceBackups",
        safe_request_id(&request.request_id),
        move || crate::backups::list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_instance_backup(
    state: State<'_, AppState>,
    request: crate::backups::PreviewRequest,
) -> CommandResult<crate::backups::PreviewResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewInstanceBackup",
        safe_request_id(&request.request_id),
        move || crate::backups::preview(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn create_instance_backup(
    state: State<'_, AppState>,
    request: crate::backups::CreateRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "createInstanceBackup",
        safe_request_id(&request.request_id),
        move || crate::backups::create(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_backup_task(
    state: State<'_, AppState>,
    request: crate::backups::TaskStatusRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceBackupTask",
        safe_request_id(&request.request_id),
        move || crate::backups::status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn cancel_instance_backup(
    state: State<'_, AppState>,
    request: crate::backups::TaskStatusRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "cancelInstanceBackup",
        safe_request_id(&request.request_id),
        move || crate::backups::cancel(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_backup_tasks(
    state: State<'_, AppState>,
    request: crate::backups::TaskListRequest,
) -> CommandResult<Vec<serde_json::Value>> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceBackupTasks",
        safe_request_id(&request.request_id),
        move || crate::backups::list_tasks(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn pick_backup_download_path(app: tauri::AppHandle) -> CommandResult<Option<String>> {
    ssh_blocking("pickBackupDownloadPath", None, move || {
        Ok(app
            .dialog()
            .file()
            .set_title("保存备份归档")
            .blocking_save_file()
            .and_then(|file| {
                file.into_path()
                    .ok()
                    .and_then(|path| path.into_os_string().into_string().ok())
            }))
    })
    .await
}

#[tauri::command]
pub async fn download_instance_backup(
    state: State<'_, AppState>,
    request: crate::backups::DownloadRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "downloadInstanceBackup",
        safe_request_id(&request.request_id),
        move || crate::backups::download(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_backup_download_task(
    state: State<'_, AppState>,
    request: crate::backups::DownloadRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getBackupDownloadTask",
        safe_request_id(&request.request_id),
        move || crate::backups::download_status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_backup_download_tasks(
    state: State<'_, AppState>,
    request: crate::backups::TaskListRequest,
) -> CommandResult<Vec<serde_json::Value>> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listBackupDownloadTasks",
        safe_request_id(&request.request_id),
        move || crate::backups::list_download_tasks(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn delete_instance_backup(
    state: State<'_, AppState>,
    request: crate::backups::DeleteRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "deleteInstanceBackup",
        safe_request_id(&request.request_id),
        move || crate::backups::delete(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn restore_instance_backup(
    state: State<'_, AppState>,
    request: crate::backups::RestoreRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "restoreInstanceBackup",
        safe_request_id(&request.request_id),
        move || crate::backups::restore(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_restore_task(
    state: State<'_, AppState>,
    request: crate::backups::RestoreTaskStatusRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceRestoreTask",
        safe_request_id(&request.request_id),
        move || crate::backups::restore_status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn cancel_instance_restore(
    state: State<'_, AppState>,
    request: crate::backups::RestoreTaskStatusRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "cancelInstanceRestore",
        safe_request_id(&request.request_id),
        move || crate::backups::restore_cancel(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_restore_tasks(
    state: State<'_, AppState>,
    request: crate::backups::TaskListRequest,
) -> CommandResult<Vec<serde_json::Value>> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceRestoreTasks",
        safe_request_id(&request.request_id),
        move || crate::backups::restore_list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn start_instance(
    state: State<'_, AppState>,
    request: crate::instance_runtime::StartRequest,
) -> CommandResult<crate::instance_runtime::StartResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "startInstance",
        safe_request_id(&request.request_id),
        move || crate::instance_runtime::start(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_ssh_connection_state(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::ssh_dto::SshConnectionState> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getSshConnectionState",
        safe_request_id(&request.request_id),
        move || crate::ssh_state::state(&path, &request),
    )
    .await
}
#[tauri::command]
pub async fn scan_server_fingerprint(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::ssh_dto::FingerprintChallenge> {
    let path = state.database_path.clone();
    ssh_blocking(
        "scanServerFingerprint",
        safe_request_id(&request.request_id),
        move || crate::ssh::scan(&path, request),
    )
    .await
}
#[tauri::command]
pub async fn confirm_server_fingerprint(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConfirmFingerprintRequest,
) -> CommandResult<crate::ssh_dto::SshConnectionState> {
    let path = state.database_path.clone();
    ssh_blocking(
        "confirmServerFingerprint",
        safe_request_id(&request.request_id),
        move || crate::ssh::confirm(&path, request),
    )
    .await
}
#[tauri::command]
pub async fn save_server_credentials(
    state: State<'_, AppState>,
    request: crate::ssh_dto::SaveCredentialRequest,
) -> CommandResult<crate::ssh_dto::SshConnectionState> {
    let path = state.database_path.clone();
    ssh_blocking(
        "saveServerCredentials",
        safe_request_id(&request.request_id),
        move || crate::secrets::save(&path, request),
    )
    .await
}
#[tauri::command]
pub async fn connect_server(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::ssh_dto::HostConnection> {
    let path = state.database_path.clone();
    ssh_blocking(
        "connectServer",
        safe_request_id(&request.request_id),
        move || crate::ssh::connect(&path, request),
    )
    .await
}
#[tauri::command]
pub async fn get_credential_cleanup_status(
    state: State<'_, AppState>,
) -> CommandResult<crate::ssh_dto::CleanupStatus> {
    let path = state.database_path.clone();
    ssh_blocking("getCredentialCleanupStatus", None, move || {
        crate::secrets::status(&path)
    })
    .await
}
#[tauri::command]
pub async fn cleanup_credentials(
    state: State<'_, AppState>,
) -> CommandResult<crate::ssh_dto::CleanupStatus> {
    let path = state.database_path.clone();
    ssh_blocking("cleanupCredentials", None, move || {
        crate::secrets::cleanup(&path)
    })
    .await
}

#[tauri::command]
pub async fn create_instance(
    state: State<'_, AppState>,
    request: crate::instance_task::StartRequest,
) -> CommandResult<crate::instance_task::TaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "createInstance",
        safe_request_id(&request.request_id),
        move || crate::instance_task::start(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_create_status(
    state: State<'_, AppState>,
    request: crate::instance_task::StatusRequest,
) -> CommandResult<crate::instance_task::TaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceCreateStatus",
        safe_request_id(&request.request_id),
        move || crate::instance_task::status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn cleanup_orphan_instance(
    state: State<'_, AppState>,
    request: crate::instance_cleanup::Request,
) -> CommandResult<crate::instance_cleanup::Response> {
    let path = state.database_path.clone();
    ssh_blocking(
        "cleanupOrphanInstance",
        safe_request_id(&request.request_id),
        move || crate::instance_cleanup::cleanup(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_instance_delete(
    state: State<'_, AppState>,
    request: crate::instance_deletion::PreviewRequest,
) -> CommandResult<crate::instance_deletion::PreviewResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewInstanceDelete",
        safe_request_id(&request.request_id),
        move || crate::instance_deletion::preview(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn delete_instance(
    state: State<'_, AppState>,
    request: crate::instance_deletion::DeleteRequest,
) -> CommandResult<crate::instance_deletion::DeleteResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "deleteInstance",
        safe_request_id(&request.request_id),
        move || crate::instance_deletion::delete(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_world_config(
    state: State<'_, AppState>,
    request: crate::world_config::GetRequest,
) -> CommandResult<crate::world_config::WorldQueryResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getWorldConfig",
        safe_request_id(&request.request_id),
        move || crate::world_config::get(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn reveal_world_config_secrets(
    state: State<'_, AppState>,
    request: crate::world_config::GetRequest,
) -> CommandResult<crate::world_config::RevealResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "revealWorldConfigSecrets",
        safe_request_id(&request.request_id),
        move || crate::world_config::reveal(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_host_mods(
    state: State<'_, AppState>,
    request: crate::host_mods::ListRequest,
) -> CommandResult<crate::host_mods::ListResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listHostMods",
        safe_request_id(&request.request_id),
        move || crate::host_mods::list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn delete_host_mod(
    state: State<'_, AppState>,
    request: crate::host_mods::DeleteRequest,
) -> CommandResult<crate::host_mods::DeleteResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "deleteHostMod",
        safe_request_id(&request.request_id),
        move || crate::host_mods::delete(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn prepare_host_mods(
    state: State<'_, AppState>,
    request: crate::host_mods::PrepareRequest,
) -> CommandResult<crate::host_mods::TaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "prepareHostMods",
        safe_request_id(&request.request_id),
        move || crate::host_mods::prepare(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_host_mod_task(
    state: State<'_, AppState>,
    request: crate::host_mods::TaskRequest,
) -> CommandResult<crate::host_mods::TaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getHostModTask",
        safe_request_id(&request.request_id),
        move || crate::host_mods::task(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn cancel_host_mod_task(
    state: State<'_, AppState>,
    request: crate::host_mods::TaskRequest,
) -> CommandResult<crate::host_mods::TaskResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "cancelHostModTask",
        safe_request_id(&request.request_id),
        move || crate::host_mods::cancel(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_host_mod_tasks(
    state: State<'_, AppState>,
    request: crate::host_mods::TaskListRequest,
) -> CommandResult<crate::host_mods::TaskListResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listHostModTasks",
        safe_request_id(&request.request_id),
        move || crate::host_mods::tasks(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn save_world_config(
    state: State<'_, AppState>,
    request: crate::world_config::SaveRequest,
) -> CommandResult<crate::world_config::WorldMutationResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "saveWorldConfig",
        safe_request_id(&request.request_id),
        move || crate::world_config::save(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn apply_world_config(
    state: State<'_, AppState>,
    request: crate::world_config::ApplyRequest,
) -> CommandResult<crate::world_config::WorldApplyResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "applyWorldConfig",
        safe_request_id(&request.request_id),
        move || crate::world_config::apply(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_releases(
    state: State<'_, AppState>,
    request: crate::ssh_dto::ConnectionRequest,
) -> CommandResult<crate::release_catalog::Response> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceReleases",
        safe_request_id(&request.request_id),
        move || crate::release_catalog::list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn verify_instance_release_base(
    state: State<'_, AppState>,
    request: crate::release_import::BaseVerifyRequest,
) -> CommandResult<crate::release_import::BaseVerifyResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "verifyInstanceReleaseBase",
        safe_request_id(&request.request_id),
        move || crate::release_import::verify_base(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_instance_release_delete(
    state: State<'_, AppState>,
    request: crate::release_import::ReleaseDeletePreviewRequest,
) -> CommandResult<crate::release_import::ReleaseDeletePreviewResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewInstanceReleaseDelete",
        safe_request_id(&request.request_id),
        move || crate::release_import::preview_delete(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn delete_instance_release(
    state: State<'_, AppState>,
    request: crate::release_import::ReleaseDeleteRequest,
) -> CommandResult<crate::release_import::ReleaseDeleteResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "deleteInstanceRelease",
        safe_request_id(&request.request_id),
        move || crate::release_import::delete_release(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn build_dst_image(
    state: State<'_, AppState>,
    request: crate::image_build::BuildImageRequest,
) -> CommandResult<crate::image_build::BuildImageResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "buildDstImage",
        safe_request_id(&request.request_id),
        move || crate::image_build::build_dst_image(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn manage_build_cache(
    state: State<'_, AppState>,
    request: crate::build_cache::Request,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "manageBuildCache",
        safe_request_id(&request.request_id),
        move || crate::build_cache::manage(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_dst_update_status(
    state: State<'_, AppState>,
    request: crate::updates::HostUpdateRequest,
) -> CommandResult<crate::updates::HostDstUpdateStatus> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getDstUpdateStatus",
        safe_request_id(&request.request_id),
        move || crate::updates::get_status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn check_dst_updates(
    state: State<'_, AppState>,
    request: crate::updates::HostUpdateRequest,
) -> CommandResult<crate::updates::HostDstUpdateStatus> {
    let path = state.database_path.clone();
    ssh_blocking(
        "checkDstUpdates",
        safe_request_id(&request.request_id),
        move || crate::updates::check(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn preview_instance_update(
    state: State<'_, AppState>,
    request: crate::updates::PreviewRequest,
) -> CommandResult<crate::updates::UpdatePreview> {
    let path = state.database_path.clone();
    ssh_blocking(
        "previewInstanceUpdate",
        safe_request_id(&request.request_id),
        move || crate::updates::preview(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn run_instance_update(
    state: State<'_, AppState>,
    request: crate::updates::RunRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "runInstanceUpdate",
        safe_request_id(&request.request_id),
        move || crate::updates::run(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_instance_update_task(
    state: State<'_, AppState>,
    request: crate::updates::TaskStatusRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getInstanceUpdateTask",
        safe_request_id(&request.request_id),
        move || crate::updates::task_status(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn cancel_instance_update(
    state: State<'_, AppState>,
    request: crate::updates::TaskStatusRequest,
) -> CommandResult<serde_json::Value> {
    let path = state.database_path.clone();
    ssh_blocking(
        "cancelInstanceUpdate",
        safe_request_id(&request.request_id),
        move || crate::updates::task_cancel(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn list_instance_update_tasks(
    state: State<'_, AppState>,
    request: crate::updates::TaskListRequest,
) -> CommandResult<Vec<serde_json::Value>> {
    let path = state.database_path.clone();
    ssh_blocking(
        "listInstanceUpdateTasks",
        safe_request_id(&request.request_id),
        move || crate::updates::task_list(&path, request),
    )
    .await
}

#[tauri::command]
pub async fn get_build_status(
    state: State<'_, AppState>,
    request: crate::image_build::GetBuildStatusRequest,
) -> CommandResult<crate::image_build::GetBuildStatusResponse> {
    let path = state.database_path.clone();
    ssh_blocking(
        "getBuildStatus",
        safe_request_id(&request.request_id),
        move || crate::image_build::get_build_status(&path, request),
    )
    .await
}
