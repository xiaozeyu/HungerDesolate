#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() -> std::process::ExitCode {
    match hunger_desolate_lib::run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("荒野中枢启动失败：{error}");
            std::process::ExitCode::FAILURE
        }
    }
}
