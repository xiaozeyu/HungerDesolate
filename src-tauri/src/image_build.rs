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
const BUILD_MIRROR_SCRIPT: &str =
    include_str!("../../remote-scripts/actions/configure-buildkit-mirrors.py");
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

fn execute_build(session: &Session, release_id: &str) -> CommandResult<()> {
    // 确保脚本目录存在
    let mut channel = session.channel_session().map_err(|e| {
        make_error(
            "SSH_CHANNEL_FAILED",
            "无法创建 SSH 通道",
            json!({"error": e.to_string()}),
        )
    })?;

    channel
        .exec("mkdir -p /opt/famine-hub/scripts/actions")
        .map_err(|e| {
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

    // 上传构建脚本
    let script_path = "/opt/famine-hub/scripts/actions/build-dst-image.sh";
    let mut remote_file = session
        .scp_send(
            std::path::Path::new(script_path),
            0o755,
            BUILD_SCRIPT.len() as u64,
            None,
        )
        .map_err(|e| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法上传构建脚本",
                json!({"error": e.to_string()}),
            )
        })?;

    remote_file
        .write_all(BUILD_SCRIPT.as_bytes())
        .map_err(|e| {
            make_error(
                "SCRIPT_WRITE_FAILED",
                "无法写入构建脚本",
                json!({"error": e.to_string()}),
            )
        })?;

    remote_file.send_eof().map_err(|e| {
        make_error(
            "SCRIPT_EOF_FAILED",
            "脚本传输异常",
            json!({"error": e.to_string()}),
        )
    })?;

    remote_file.wait_eof().map_err(|e| {
        make_error(
            "SCRIPT_WAIT_EOF_FAILED",
            "等待脚本传输完成失败",
            json!({"error": e.to_string()}),
        )
    })?;

    remote_file.close().map_err(|e| {
        make_error(
            "SCRIPT_CLOSE_FAILED",
            "关闭脚本文件失败",
            json!({"error": e.to_string()}),
        )
    })?;

    drop(remote_file);

    let register_path = "/opt/famine-hub/scripts/actions/register-built-release.py";
    let mut remote_file = session
        .scp_send(
            Path::new(register_path),
            0o644,
            REGISTER_RELEASE_SCRIPT.len() as u64,
            None,
        )
        .map_err(|e| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法上传 Release 登记脚本",
                json!({"error": e.to_string()}),
            )
        })?;
    remote_file
        .write_all(REGISTER_RELEASE_SCRIPT.as_bytes())
        .map_err(|e| {
            make_error(
                "SCRIPT_WRITE_FAILED",
                "无法写入 Release 登记脚本",
                json!({"error": e.to_string()}),
            )
        })?;
    remote_file
        .send_eof()
        .and_then(|_| remote_file.wait_eof())
        .and_then(|_| remote_file.close())
        .map_err(|e| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "Release 登记脚本传输异常",
                json!({"error": e.to_string()}),
            )
        })?;
    drop(remote_file);

    let mirror_config_path = "/opt/famine-hub/scripts/actions/configure-buildkit-mirrors.py";
    let mut remote_file = session
        .scp_send(
            Path::new(mirror_config_path),
            0o644,
            BUILD_MIRROR_SCRIPT.len() as u64,
            None,
        )
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法上传 BuildKit 镜像源配置脚本",
                json!({}),
            )
        })?;
    remote_file
        .write_all(BUILD_MIRROR_SCRIPT.as_bytes())
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法写入 BuildKit 镜像源配置脚本",
                json!({}),
            )
        })?;
    remote_file
        .send_eof()
        .and_then(|_| remote_file.wait_eof())
        .and_then(|_| remote_file.close())
        .and_then(|_| remote_file.wait_close())
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "BuildKit 镜像源配置脚本传输异常",
                json!({}),
            )
        })?;
    drop(remote_file);

    // 上传与本地构建共用的 SteamCMD 安装脚本。
    let mut installer = session
        .scp_send(
            Path::new("/opt/famine-hub/scripts/actions/install-dst.sh"),
            0o644,
            INSTALL_SCRIPT.len() as u64,
            None,
        )
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法上传 SteamCMD 安装脚本",
                json!({}),
            )
        })?;
    installer
        .write_all(INSTALL_SCRIPT.as_bytes())
        .and_then(|_| installer.send_eof().map_err(std::io::Error::other))
        .and_then(|_| installer.wait_eof().map_err(std::io::Error::other))
        .and_then(|_| installer.close().map_err(std::io::Error::other))
        .and_then(|_| installer.wait_close().map_err(std::io::Error::other))
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "SteamCMD 安装脚本传输异常",
                json!({}),
            )
        })?;
    drop(installer);

    // Bundle the pinned-image Workshop fetch wrapper in releases built on managed hosts.
    let mut fetcher = session
        .scp_send(
            Path::new("/opt/famine-hub/scripts/actions/fetch-workshop.sh"),
            0o644,
            FETCH_WORKSHOP_SCRIPT.len() as u64,
            None,
        )
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "无法上传 Workshop 下载脚本",
                json!({}),
            )
        })?;
    fetcher
        .write_all(FETCH_WORKSHOP_SCRIPT.as_bytes())
        .and_then(|_| fetcher.send_eof().map_err(std::io::Error::other))
        .and_then(|_| fetcher.wait_eof().map_err(std::io::Error::other))
        .and_then(|_| fetcher.close().map_err(std::io::Error::other))
        .and_then(|_| fetcher.wait_close().map_err(std::io::Error::other))
        .map_err(|_| {
            make_error(
                "SCRIPT_UPLOAD_FAILED",
                "Workshop 下载脚本传输异常",
                json!({}),
            )
        })?;
    drop(fetcher);

    // 执行构建（后台运行）。
    let build_command = format!(
        "nohup bash {} {} > /var/lib/famine-hub/build-{}.log 2>&1 &",
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

fn parse_build_progress(log_content: &str) -> (BuildStatus, Option<BuildProgress>, Vec<String>) {
    let lines: Vec<String> = log_content.lines().map(|s| s.to_string()).collect();
    let tail: Vec<String> = lines.iter().rev().take(20).rev().cloned().collect();

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
            }),
            tail,
        );
    }

    // 解析当前步骤
    let current_step = if log_content.contains("==> 生成 release 元数据") {
        Some(("生成元数据".to_string(), 90))
    } else if log_content.contains("==> 提取 DST build 版本") {
        Some(("提取版本信息".to_string(), 80))
    } else if log_content.contains("SteamCMD phase=download") {
        Some(("下载并校验 DST 服务端".to_string(), 60))
    } else if log_content.contains("SteamCMD phase=bootstrap") {
        Some(("初始化 SteamCMD".to_string(), 30))
    } else if log_content.contains("==> 开始构建 Docker 镜像") {
        Some(("正在构建镜像".to_string(), 50))
    } else if log_content.contains("==> 创建 Dockerfile") {
        Some(("准备 Dockerfile".to_string(), 20))
    } else if log_content.contains("==> 准备构建目录") {
        Some(("准备构建环境".to_string(), 10))
    } else {
        None
    };

    if let Some((step, percent)) = current_step {
        (
            BuildStatus::Building,
            Some(BuildProgress {
                current_step: step,
                percent: Some(percent),
            }),
            tail,
        )
    } else {
        (
            BuildStatus::Building,
            Some(BuildProgress {
                current_step: "正在构建...".to_string(),
                percent: None,
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
    use super::{parse_build_progress, BuildStatus};

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
