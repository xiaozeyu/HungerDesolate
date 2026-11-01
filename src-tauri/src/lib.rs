mod backups;
mod build_cache;
mod commands;
mod credential_source;
mod diagnostics;
mod docker_installation;
mod dto;
mod environment;
mod environment_dto;
mod host_initialization;
mod host_mods;
mod image_build;
mod instance_cleanup;
mod instance_creation;
mod instance_deletion;
mod instance_dto;
mod instance_runtime;
mod instance_task;
mod managed_files;
mod mod_parser;
mod mods;
mod persistence;
mod registry;
mod release_catalog;
mod release_import;
mod secrets;
mod server_input;
mod ssh;
mod ssh_dto;
mod ssh_state;
mod workshop;
mod workshop_collection;
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
            commands::get_instance_diagnostics,
            commands::list_instance_backups,
            commands::preview_instance_backup,
            commands::create_instance_backup,
            commands::get_instance_backup_task,
            commands::cancel_instance_backup,
            commands::list_instance_backup_tasks,
            commands::pick_backup_download_path,
            commands::download_instance_backup,
            commands::get_backup_download_task,
            commands::list_backup_download_tasks,
            commands::delete_instance_backup,
            commands::restore_instance_backup,
            commands::get_instance_restore_task,
            commands::cancel_instance_restore,
            commands::list_instance_restore_tasks,
            commands::start_instance,
            commands::create_instance,
            commands::get_instance_create_status,
            commands::cleanup_orphan_instance,
            commands::preview_instance_delete,
            commands::delete_instance,
            commands::get_world_config,
            commands::reveal_world_config_secrets,
            commands::get_workshop_metadata,
            commands::get_workshop_collection,
            commands::list_instance_mods,
            commands::save_instance_mods,
            commands::preview_apply_instance_mods,
            commands::apply_instance_mods,
            commands::cancel_instance_mod_apply,
            commands::get_instance_mod_apply_task,
            commands::list_instance_mod_apply_tasks,
            commands::list_host_mods,
            commands::delete_host_mod,
            commands::prepare_host_mods,
            commands::get_host_mod_task,
            commands::cancel_host_mod_task,
            commands::list_host_mod_tasks,
            commands::save_world_config,
            commands::apply_world_config,
            commands::list_instance_releases,
            commands::preview_instance_release,
            commands::import_instance_release,
            commands::verify_instance_release_runtime,
            commands::verify_instance_release_base,
            commands::preview_instance_release_delete,
            commands::delete_instance_release,
            commands::build_dst_image,
            commands::manage_build_cache,
            commands::get_build_status,
        ])
        .run(tauri::generate_context!())
}
