mod commands;
mod credential_source;
mod docker_installation;
mod dto;
mod environment;
mod environment_dto;
mod host_initialization;
mod image_build;
mod instance_cleanup;
mod instance_creation;
mod instance_deletion;
mod instance_dto;
mod instance_runtime;
mod instance_task;
mod managed_files;
mod persistence;
mod registry;
mod release_catalog;
mod release_import;
mod secrets;
mod server_input;
mod ssh;
mod ssh_dto;
mod ssh_state;
mod world_config;

use std::{error::Error, path::PathBuf};
use tauri::Manager;

pub struct AppState {
    database_path: PathBuf,
}

pub fn run() -> tauri::Result<()> {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let directory = app.path().app_data_dir()?;
            std::fs::create_dir_all(&directory)?;
            let database_path = directory.join("hunger-desolate.sqlite3");
            persistence::initialize(&database_path)
                .map_err(|error| Box::<dyn Error>::from(error.to_string()))?;
            app.manage(AppState { database_path });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_bootstrap_snapshot,
            commands::list_servers,
            commands::save_server,
            commands::delete_server,
            commands::get_ssh_connection_state,
            commands::scan_server_fingerprint,
            commands::confirm_server_fingerprint,
            commands::save_server_credentials,
            commands::connect_server,
            commands::get_credential_cleanup_status,
            commands::cleanup_credentials,
            commands::pick_private_key_file,
            commands::get_host_environment,
            commands::preview_docker_installation,
            commands::start_docker_installation,
            commands::get_docker_installation_status,
            commands::preview_host_initialization,
            commands::initialize_host,
            commands::get_host_initialization,
            commands::list_instances,
            commands::get_instance_overview,
            commands::start_instance,
            commands::preview_instance_create,
            commands::create_instance,
            commands::get_instance_create_status,
            commands::cleanup_orphan_instance,
            commands::preview_instance_delete,
            commands::delete_instance,
            commands::get_world_config,
            commands::save_world_config,
            commands::preview_world_apply,
            commands::apply_world_config,
            commands::list_instance_releases,
            commands::preview_instance_release,
            commands::import_instance_release,
            commands::verify_instance_release_runtime,
            commands::verify_instance_release_base,
            commands::preview_instance_release_delete,
            commands::delete_instance_release,
            commands::build_dst_image,
            commands::get_build_status,
        ])
        .run(tauri::generate_context!())
}
