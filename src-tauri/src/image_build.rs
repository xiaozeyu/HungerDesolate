use crate::{
    dto::{CommandError, CommandResult},
    ssh_dto::ConnectionRequest,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use ssh2::Session;
use std::{
    io::{Read, Write},
    path::Path,
};

const BUILD_SCRIPT: &str = include_str!("../../remote-scripts/actions/build-dst-image.sh");
const DOCKERFILE: &str = include_str!("../../docker/runtime/Dockerfile");
const BASE_IMAGE_SCRIPT: &str = include_str!("../../docker/runtime/prepare-base-image.sh");
const INSTALL_SCRIPT: &str = include_str!("../../docker/runtime/install-dst.sh");
const FETCH_WORKSHOP_SCRIPT: &str = include_str!("../../docker/runtime/fetch-workshop.sh");
const REGISTER_RELEASE_SCRIPT: &str =
    include_str!("../../remote-scripts/actions/register-built-release.py");
const OP: &str = "buildDstImage";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BuildImageRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub release_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HostTarget {
    pub server_id: String,
    pub host_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildImageResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub server_id: String,
    pub record_version: u64,
    pub release_id: String,
    pub status: String,
    pub message: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GetBuildStatusRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: HostTarget,
    pub expected_record_version: u64,
    pub release_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GetBuildStatusResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub release_id: String,
    pub status: BuildStatus,
    pub progress: Option<BuildProgress>,
    pub log_tail: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BuildStatus {
    Building,
    Completed,
    Failed,
    NotFound,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildProgress {
    pub current_step: String,
    pub percent: Option<u8>,
    pub step_percent: Option<u8>,
}

fn make_error(
    code: &'static str,
    message: &'static str,
    details: serde_json::Value,
) -> Box<CommandError> {
    Box::new(CommandError {
        schema_version: 1,
        code,
        category: "image_build",
        operation: OP,
        request_id: None,
        target: None,
        message,
        retryable: true,
        remote_changed: None,
        task_id: None,
        recovery_steps: vec!["检查主机 Docker 状态和网络连接后重试"],
        details,
    })
}

fn upload_script(
    session: &Session,
    path: &str,
    content: &str,
    mode: i32,
    label: &'static str,
) -> CommandResult<()> {
    let mut remote_file = session
        .scp_send(Path::new(path), mode, content.len() as u64, None)
        .map_err(|e| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法上传构建脚本",
                json!({"script": label, "error": e.to_string()}),
            )
        })?;
    remote_file
        .write_all(content.as_bytes())
        .and_then(|_| remote_file.send_eof().map_err(std::io::Error::other))
        .and_then(|_| remote_file.wait_eof().map_err(std::io::Error::other))
        .and_then(|_| remote_file.close().map_err(std::io::Error::other))
        .and_then(|_| remote_file.wait_close().map_err(std::io::Error::other))
        .map_err(|e| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "构建脚本传输异常",
                json!({"script": label, "error": e.to_string()}),
            )
        })?;
    Ok(())
}

fn execute_build(session: &Session, release_id: &str) -> CommandResult<()> {
    // Claim a new immutable script snapshot; never overwrite another build's scripts.
    let script_root = format!("/opt/famine-hub/scripts/image-build/{release_id}");
    let script_dir = format!("{script_root}/actions");
    let prepare_scripts = format!(
        "mkdir -p /opt/famine-hub/scripts/image-build && mkdir {script_root} && mkdir {script_dir}"
    );
    let mut channel = session.channel_session().map_err(|e| {
        make_error(
            "SSH_CHANNEL_FAILED",
            "无法创建 SSH 通道",
            json!({"error": e.to_string()}),
        )
    })?;

    channel.exec(&prepare_scripts).map_err(|e| {
        make_error(
            "MKDIR_FAILED",
            "无法创建脚本目录",
            json!({"error": e.to_string()}),
        )
    })?;

    let mut output = String::new();
    channel.read_to_string(&mut output).ok();

    channel.wait_close().map_err(|e| {
        make_error(
            "MKDIR_WAIT_FAILED",
            "等待目录创建失败",
            json!({"error": e.to_string(), "output": output.trim()}),
        )
    })?;

    let exit_status = channel.exit_status().map_err(|e| {
        make_error(
            "MKDIR_EXIT_FAILED",
            "无法获取退出状态",
            json!({"error": e.to_string()}),
        )
    })?;

    if exit_status != 0 {
        return Err(make_error(
            "MKDIR_FAILED",
            "创建目录失败",
            json!({"exitCode": exit_status, "output": output.trim()}),
        ));
    }

    drop(channel);

    // 上传构建脚本、共享 Dockerfile 与基础镜像准备脚本。
    let script_path = format!("{script_dir}/build-dst-image.sh");
    upload_script(
        session,
        &script_path,
        BUILD_SCRIPT,
        0o755,
        "build-dst-image.sh",
    )?;
    upload_script(
        session,
        &format!("{script_dir}/Dockerfile"),
        DOCKERFILE,
        0o644,
        "Dockerfile",
    )?;
    upload_script(
        session,
        &format!("{script_dir}/prepare-base-image.sh"),
        BASE_IMAGE_SCRIPT,
        0o644,
        "prepare-base-image.sh",
    )?;
    upload_script(
        session,
        &format!("{script_dir}/install-dst.sh"),
        INSTALL_SCRIPT,
        0o644,
        "install-dst.sh",
    )?;
    upload_script(
        session,
        &format!("{script_dir}/fetch-workshop.sh"),
        FETCH_WORKSHOP_SCRIPT,
        0o644,
        "fetch-workshop.sh",
    )?;
    upload_script(
        session,
        &format!("{script_dir}/register-built-release.py"),
        REGISTER_RELEASE_SCRIPT,
        0o644,
        "register-built-release.py",
    )?;

    // 执行构建（后台运行）。
    let build_command = format!(
        "nohup bash {} {} < /dev/null > /var/lib/famine-hub/build-{}.log 2>&1 &",
        script_path, release_id, release_id
    );

    let mut channel = session.channel_session().map_err(|e| {
        make_error(
            "SSH_CHANNEL_FAILED",
            "无法创建执行通道",
            json!({"error": e.to_string()}),
        )
    })?;

    channel.exec(&build_command).map_err(|e| {
        make_error(
            "BUILD_EXEC_FAILED",
            "无法启动构建任务",
            json!({"error": e.to_string()}),
        )
    })?;

    let mut output = String::new();
    channel.read_to_string(&mut output).ok();

    channel.wait_close().map_err(|e| {
        make_error(
            "BUILD_WAIT_CLOSE_FAILED",
            "等待命令完成失败",
            json!({"error": e.to_string()}),
        )
    })?;

    let exit_status = channel.exit_status().map_err(|e| {
        make_error(
            "BUILD_EXIT_STATUS_FAILED",
            "无法获取退出状态",
            json!({"error": e.to_string()}),
        )
    })?;

    if exit_status != 0 {
        return Err(make_error(
            "BUILD_START_FAILED",
            "构建任务启动失败",
            json!({"exitCode": exit_status, "output": output.trim()}),
        ));
    }

    Ok(())
}

fn percent(value: f32) -> Option<u8> {
    (value.is_finite() && (0.0..=100.0).contains(&value)).then(|| value.round() as u8)
}

fn download_progress(log_content: &str) -> Option<u8> {
    log_content.lines().rev().find_map(|line| {
        if !line.to_ascii_lowercase().contains("download") {
            return None;
        }
        if let Some((_, after)) = line.split_once("progress:") {
            let raw = after.split_whitespace().next()?;
            return percent(raw.trim_end_matches(['%', ',']).parse::<f32>().ok()?);
        }
        // SteamCMD 自更新/下载阶段使用形如 `[ 29%] Downloading update (...)` 的进度。
        let start = line.find('[')?;
        let rest = &line[start + 1..];
        let end = rest.find('%')?;
        percent(rest[..end].trim().parse::<f32>().ok()?)
    })
}
fn parse_build_progress(log_content: &str) -> (BuildStatus, Option<BuildProgress>, Vec<String>) {
    let lines: Vec<String> = log_content.lines().map(|s| s.to_string()).collect();
    // Keep enough context for SteamCMD assertions preceding minidump and disk diagnostics.
    let tail: Vec<String> = lines.iter().rev().take(120).rev().cloned().collect();

    let structured = lines.iter().any(|line| line == "FAMINE_BUILD_STARTED");
    // Intermediate SteamCMD errors may be retried; only the host script marks a terminal result.
    let terminal = lines.iter().rev().find_map(|line| {
        if line == "FAMINE_BUILD_COMPLETED" {
            Some(true)
        } else if line
            .strip_prefix("FAMINE_BUILD_FAILED:")
            .and_then(|value| value.parse::<u8>().ok())
            .is_some_and(|code| code != 0)
        {
            Some(false)
        } else {
            None
        }
    });
    if terminal == Some(true) || (!structured && log_content.contains("==> 镜像构建完成")) {
        return (
            BuildStatus::Completed,
            Some(BuildProgress {
                current_step: "构建完成".to_string(),
                percent: Some(100),
                step_percent: None,
            }),
            tail,
        );
    }

    // 检查是否失败
    if terminal == Some(false)
        || (!structured && (log_content.contains("错误：") || log_content.contains("error:")))
    {
        return (
            BuildStatus::Failed,
            Some(BuildProgress {
                current_step: "构建失败".to_string(),
                percent: None,
                step_percent: None,
            }),
            tail,
        );
    }

    // 解析当前步骤。Docker 构建开始后，SteamCMD 的 bootstrap/download 是其内部阶段，
    // 百分比必须在 50% 之后继续前进，不能从 50% 回退到 30%。
    let current_step = if log_content.contains("==> 登记 Release 到主机 catalog") {
        Some(("登记 Release".to_string(), 95))
    } else if log_content.contains("==> 生成 release 元数据") {
        Some(("生成元数据".to_string(), 90))
    } else if log_content.contains("==> 提取 DST build 版本") {
        Some(("提取版本信息".to_string(), 80))
    } else if log_content.contains("SteamCMD phase=download") {
        Some(("下载并校验 DST 服务端".to_string(), 65))
    } else if log_content.contains("SteamCMD phase=bootstrap") {
        Some(("初始化 SteamCMD".to_string(), 55))
    } else if log_content.contains("==> 开始构建 Docker 镜像") {
        Some(("正在构建镜像".to_string(), 50))
    } else if log_content.contains("==> 准备 Debian 基础镜像") {
        Some(("准备 Debian 基础镜像".to_string(), 20))
    } else if log_content.contains("==> 准备构建目录") {
        Some(("准备构建环境".to_string(), 10))
    } else {
        None
    };

    if let Some((step, percent)) = current_step {
        let step_percent = if matches!(percent, 55 | 65) {
            download_progress(log_content)
        } else {
            None
        };
        (
            BuildStatus::Building,
            Some(BuildProgress {
                current_step: step,
                percent: Some(percent),
                step_percent,
            }),
            tail,
        )
    } else {
        (
            BuildStatus::Building,
            Some(BuildProgress {
                current_step: "正在构建...".to_string(),
                percent: None,
                step_percent: None,
            }),
            tail,
        )
    }
}

fn get_build_status_internal(
    session: &Session,
    release_id: &str,
) -> CommandResult<(BuildStatus, Option<BuildProgress>, Vec<String>)> {
    let log_path = format!("/var/lib/famine-hub/build-{}.log", release_id);

    // 检查日志文件是否存在
    let mut channel = session.channel_session().map_err(|e| {
        make_error(
            "SSH_CHANNEL_FAILED",
            "无法创建 SSH 通道",
            json!({"error": e.to_string()}),
        )
    })?;

    let check_command = format!("test -f {} && echo EXISTS || echo NOT_FOUND", log_path);
    channel.exec(&check_command).map_err(|e| {
        make_error(
            "CHECK_LOG_FAILED",
            "无法检查日志文件",
            json!({"error": e.to_string()}),
        )
    })?;

    let mut check_output = String::new();
    channel.read_to_string(&mut check_output).ok();
    channel.wait_close().ok();
    drop(channel);

    if check_output.trim() == "NOT_FOUND" {
        return Ok((BuildStatus::NotFound, None, vec![]));
    }

    // 读取日志文件
    let mut channel = session.channel_session().map_err(|e| {
        make_error(
            "SSH_CHANNEL_FAILED",
            "无法创建 SSH 通道",
            json!({"error": e.to_string()}),
        )
    })?;

    let read_command = format!("cat {}", log_path);
    channel.exec(&read_command).map_err(|e| {
        make_error(
            "READ_LOG_FAILED",
            "无法读取日志文件",
            json!({"error": e.to_string()}),
        )
    })?;

    let mut log_content = String::new();
    channel.read_to_string(&mut log_content).ok();
    channel.wait_close().ok();

    let (status, progress, tail) = parse_build_progress(&log_content);
    Ok((status, progress, tail))
}

pub fn build_dst_image(
    database: &Path,
    request: BuildImageRequest,
) -> CommandResult<BuildImageResponse> {
    if request.schema_version != 1 {
        return Err(CommandError::new(
            "SCHEMA_VERSION_MISMATCH",
            "validation",
            OP,
            "不支持的协议版本",
            "使用最新版本的应用",
        ));
    }

    let release_id = request
        .release_id
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    // Validate before interpolating the ID into remote commands and log paths.
    crate::server_input::validate_id(&release_id, OP, "releaseId")?;

    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };

    crate::ssh::with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(CommandError::new(
                "HOST_IDENTITY_MISMATCH",
                "validation",
                OP,
                "主机身份不匹配",
                "刷新主机列表后重试",
            ));
        }

        execute_build(session, &release_id)?;

        Ok(BuildImageResponse {
            schema_version: 1,
            request_id: request.request_id,
            server_id: request.target.server_id,
            record_version: request.expected_record_version,
            release_id: release_id.clone(),
            status: "building".to_string(),
            message: format!("DST 镜像正在后台构建中，Release ID: {}", release_id),
        })
    })
}

pub fn get_build_status(
    database: &Path,
    request: GetBuildStatusRequest,
) -> CommandResult<GetBuildStatusResponse> {
    if request.schema_version != 1 {
        return Err(CommandError::new(
            "SCHEMA_VERSION_MISMATCH",
            "validation",
            "getBuildStatus",
            "不支持的协议版本",
            "使用最新版本的应用",
        ));
    }
    crate::server_input::validate_id(&request.release_id, "getBuildStatus", "releaseId")?;

    let connection = ConnectionRequest {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.target.server_id.clone(),
        expected_record_version: request.expected_record_version,
    };

    crate::ssh::with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(CommandError::new(
                "HOST_IDENTITY_MISMATCH",
                "validation",
                "getBuildStatus",
                "主机身份不匹配",
                "刷新主机列表后重试",
            ));
        }

        let (status, progress, log_tail) = get_build_status_internal(session, &request.release_id)?;

        Ok(GetBuildStatusResponse {
            schema_version: 1,
            request_id: request.request_id,
            release_id: request.release_id,
            status,
            progress,
            log_tail,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::{
        build_dst_image, get_build_status, parse_build_progress, BuildImageRequest, BuildStatus,
        GetBuildStatusRequest, HostTarget,
    };
    use std::path::Path;

    #[test]
    fn base_image_stage_precedes_docker_build_and_failure_is_terminal() {
        let (_, progress, _) =
            parse_build_progress("FAMINE_BUILD_STARTED\n==> 准备 Debian 基础镜像\n");
        assert_eq!(
            progress.map(|item| item.current_step),
            Some("准备 Debian 基础镜像".into())
        );
        let (status, progress, _) =
            parse_build_progress("FAMINE_BUILD_STARTED\n==> 开始构建 Docker 镜像\n");
        assert!(matches!(status, BuildStatus::Building));
        assert_eq!(
            progress.map(|item| item.current_step),
            Some("正在构建镜像".into())
        );
        let (status, _, _) = parse_build_progress(
            "FAMINE_BUILD_STARTED\nBASE_IMAGE_UNAVAILABLE\nFAMINE_BUILD_FAILED:1\n",
        );
        assert!(matches!(status, BuildStatus::Failed));
    }

    #[test]
    fn command_and_log_release_ids_are_validated_before_accessing_connections() {
        for release_id in [
            "../outside",
            "$(id)",
            "bad; echo injected",
            "11111111-1111-4111-8111-111111111111\n",
            "11111111-1111-1111-8111-111111111111",
        ] {
            let target = || HostTarget {
                server_id: "22222222-2222-4222-8222-222222222222".into(),
                host_id: "33333333-3333-4333-8333-333333333333".into(),
            };
            let database = Path::new("/nonexistent-image-build-test.sqlite");
            let build = build_dst_image(
                database,
                BuildImageRequest {
                    schema_version: 1,
                    request_id: "44444444-4444-4444-8444-444444444444".into(),
                    target: target(),
                    expected_record_version: 1,
                    release_id: Some(release_id.into()),
                },
            )
            .expect_err("invalid release ID must not reach the database or SSH");
            assert_eq!(build.code, "INPUT_INVALID");
            assert_eq!(build.details["field"], "releaseId");
            let status = get_build_status(
                database,
                GetBuildStatusRequest {
                    schema_version: 1,
                    request_id: "44444444-4444-4444-8444-444444444444".into(),
                    target: target(),
                    expected_record_version: 1,
                    release_id: release_id.into(),
                },
            )
            .expect_err("invalid log ID must not reach the database or SSH");
            assert_eq!(status.code, "INPUT_INVALID");
            assert_eq!(status.details["field"], "releaseId");
        }
    }

    #[test]
    fn failure_tail_keeps_steamcmd_context_and_remains_bounded() {
        let mut log = "FAMINE_BUILD_STARTED\n".to_string();
        for _ in 0..150 {
            log.push_str("Downloading update...\n");
        }
        log.push_str("Assertion Failed: mock SteamCMD diagnostic\n");
        for _ in 0..40 {
            log.push_str("minidump upload and filesystem diagnostics\n");
        }
        log.push_str("FAMINE_BUILD_FAILED:1\n");
        let (status, _, tail) = parse_build_progress(&log);
        assert!(matches!(status, BuildStatus::Failed));
        assert_eq!(tail.len(), 120);
        assert!(tail.iter().any(|line| line.contains("Assertion Failed")));
        assert_eq!(
            tail.last().map(String::as_str),
            Some("FAMINE_BUILD_FAILED:1")
        );
    }

    #[test]
    fn retry_diagnostics_do_not_terminate_the_build() {
        let (status, progress, _) = parse_build_progress(
            "FAMINE_BUILD_STARTED\nerror: mock SteamCMD failure\n==> SteamCMD phase=download attempt=2/3\n",
        );
        assert!(matches!(status, BuildStatus::Building));
        assert_eq!(
            progress.map(|item| item.current_step),
            Some("下载并校验 DST 服务端".into())
        );
    }

    #[test]
    fn download_progress_is_separate_from_stage_progress() {
        let start = "FAMINE_BUILD_STARTED\n==> 开始构建 Docker 镜像\n";
        let (_, progress, _) = parse_build_progress(start);
        let progress = progress.expect("start progress");
        assert_eq!(progress.percent, Some(50));
        assert_eq!(progress.step_percent, None);

        let bootstrap = format!(
            "{start}==> SteamCMD phase=bootstrap\n#15 110.0 [ 29%] Downloading update (12221 of 41370 KB)...\n"
        );
        let (_, progress, _) = parse_build_progress(&bootstrap);
        let progress = progress.expect("bootstrap progress");
        assert_eq!(progress.percent, Some(55));
        assert_eq!(progress.step_percent, Some(29));

        let download = format!(
            "{bootstrap}==> SteamCMD phase=download attempt=1/3\nUpdate state (0x61) downloading, progress: 42.60 (4260 / 10000)\n"
        );
        let (_, progress, _) = parse_build_progress(&download);
        let progress = progress.expect("download progress");
        assert_eq!(progress.percent, Some(65));
        assert_eq!(progress.step_percent, Some(43));

        let verify = format!(
            "{download}Update state (0x5) verifying install, progress: 90.00 (9000 / 10000)\n"
        );
        let (_, progress, _) = parse_build_progress(&verify);
        assert_eq!(progress.expect("verify progress").step_percent, Some(43));

        let extracted = format!("{verify}==> 提取 DST build 版本\n");
        let (_, progress, _) = parse_build_progress(&extracted);
        let progress = progress.expect("extract progress");
        assert_eq!(progress.percent, Some(80));
        assert_eq!(progress.step_percent, None);
    }

    #[test]
    fn only_terminal_markers_complete_a_structured_build() {
        let (status, _, _) = parse_build_progress("FAMINE_BUILD_STARTED\n==> 镜像构建完成\n");
        assert!(matches!(status, BuildStatus::Building));
        let (status, _, _) =
            parse_build_progress("FAMINE_BUILD_STARTED\nerror: retry\nFAMINE_BUILD_COMPLETED\n");
        assert!(matches!(status, BuildStatus::Completed));
        let (status, _, _) = parse_build_progress("FAMINE_BUILD_STARTED\nFAMINE_BUILD_FAILED:1\n");
        assert!(matches!(status, BuildStatus::Failed));
    }
}
