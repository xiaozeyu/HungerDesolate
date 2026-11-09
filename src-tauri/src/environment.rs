use crate::{
    dto::{CommandError, CommandResult},
    environment_dto::*,
    ssh_dto::ConnectionRequest,
    ssh_state,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Deserialize;
use ssh2::{ExtendedData, Session};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    path::Path,
    time::{Duration, Instant},
};

const SCRIPT: &str = include_str!("../../remote-scripts/probes/host-environment.sh");
const MAX_OUTPUT: usize = 65536;
const MAX_SAFE: u64 = 9_007_199_254_740_991;
const TOOLS: [&str; 14] = [
    "bash",
    "base64",
    "tr",
    "head",
    "uname",
    "id",
    "df",
    "timeout",
    "tar",
    "gzip",
    "sha256sum",
    "flock",
    "jq",
    "python3",
];
const FIELDS: [&str; 14] = [
    "kernel",
    "architecture",
    "uid",
    "osRelease",
    "meminfo",
    "diskPath",
    "disk",
    "managementAccess",
    "managementRoot",
    "dockerClient",
    "compose",
    "dockerServer",
    "dockerRoot",
    "dockerDisk",
];
struct ProbeValue {
    status: String,
    value: String,
}
type Probe = BTreeMap<String, ProbeValue>;
fn probe_error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "environment",
        "getHostEnvironment",
        message,
        "检查 Bash、base64、tr 和 SSH 权限后重新检查",
    )
}

pub(crate) fn execute(session: &Session) -> CommandResult<Vec<u8>> {
    let mut channel = session
        .channel_session()
        .map_err(|_| probe_error("ENVIRONMENT_CHANNEL_FAILED", "无法创建环境检查通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| probe_error("ENVIRONMENT_CHANNEL_FAILED", "无法配置环境检查通道"))?;
    channel
        .exec("bash --noprofile --norc -s")
        .map_err(|_| probe_error("ENVIRONMENT_PROBE_FAILED", "无法执行环境检查"))?;
    channel
        .write_all(SCRIPT.as_bytes())
        .map_err(|_| probe_error("ENVIRONMENT_PROBE_FAILED", "无法提交只读检查脚本"))?;
    channel
        .send_eof()
        .map_err(|_| probe_error("ENVIRONMENT_PROBE_FAILED", "无法启动只读环境检查"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(35);
    let mut output = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        if Instant::now() >= deadline {
            return Err(probe_error("ENVIRONMENT_TIMEOUT", "主机环境检查超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) => {
                if output.len() + count > MAX_OUTPUT {
                    return Err(probe_error(
                        "ENVIRONMENT_OUTPUT_TOO_LARGE",
                        "主机环境检查输出超出限制",
                    ));
                }
                output.extend_from_slice(&buffer[..count]);
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            Err(_) => {
                return Err(probe_error(
                    "ENVIRONMENT_READ_FAILED",
                    "无法完成环境结果读取",
                ))
            }
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| probe_error("ENVIRONMENT_PROBE_FAILED", "环境检查未正常结束"))?;
    let status = channel
        .exit_status()
        .map_err(|_| probe_error("ENVIRONMENT_PROBE_FAILED", "无法确认环境检查结果"))?;
    if status != 0 {
        return Err(probe_error(
            "ENVIRONMENT_PROBE_FAILED",
            "检查脚本执行失败，需 Bash、base64 和 tr",
        ));
    }
    Ok(output)
}
fn parse_protocol(bytes: &[u8]) -> CommandResult<Probe> {
    let invalid = || probe_error("ENVIRONMENT_PROTOCOL_INVALID", "主机环境结果格式无效");
    if bytes.len() > MAX_OUTPUT {
        return Err(invalid());
    }
    let text = std::str::from_utf8(bytes).map_err(|_| invalid())?;
    let mut lines = text.lines();
    if lines.next() != Some("FHENV\t1") {
        return Err(invalid());
    }
    let mut result = Probe::new();
    let mut ended = false;
    for line in lines {
        if line == "FHENV\tEND" {
            if ended {
                return Err(invalid());
            }
            ended = true;
            continue;
        }
        if ended {
            return Err(invalid());
        }
        let parts: Vec<_> = line.split('\t').collect();
        if parts.len() != 3
            || ![
                "ok",
                "missing",
                "failed",
                "invalid",
                "writable",
                "permissionDenied",
                "present",
                "unavailable",
            ]
            .contains(&parts[1])
        {
            return Err(invalid());
        }
        let key = parts[0];
        if !FIELDS.contains(&key)
            && !key
                .strip_prefix("tool.")
                .is_some_and(|tool| TOOLS.contains(&tool))
        {
            return Err(invalid());
        }
        let bytes = STANDARD.decode(parts[2]).map_err(|_| invalid())?;
        if bytes.len() > 16384 {
            return Err(invalid());
        }
        let value = String::from_utf8(bytes).map_err(|_| invalid())?;
        if value.contains('\0')
            || result
                .insert(
                    key.into(),
                    ProbeValue {
                        status: parts[1].into(),
                        value,
                    },
                )
                .is_some()
        {
            return Err(invalid());
        }
    }
    if !ended || result.len() != FIELDS.len() + TOOLS.len() {
        return Err(invalid());
    }
    Ok(result)
}
fn status<'a>(probe: &'a Probe, key: &str) -> &'a str {
    probe
        .get(key)
        .map_or("missing", |field| field.status.as_str())
}
fn value<'a>(probe: &'a Probe, key: &str) -> Option<&'a str> {
    probe
        .get(key)
        .filter(|field| field.status == "ok")
        .map(|field| field.value.as_str())
}
fn short(value: &str) -> Option<String> {
    let text = value.trim();
    if text.is_empty() || text.len() > 256 || text.chars().any(char::is_control) {
        None
    } else {
        Some(text.into())
    }
}
fn kib(value: &str) -> Option<u64> {
    value
        .parse::<u64>()
        .ok()?
        .checked_mul(1024)
        .filter(|value| *value <= MAX_SAFE)
}
fn memory(text: &str) -> Option<MemorySnapshot> {
    let mut values = BTreeMap::new();
    for line in text.lines() {
        let columns: Vec<_> = line.split_whitespace().collect();
        if columns.len() == 3 && columns[2] == "kB" {
            values.insert(columns[0], kib(columns[1]));
        }
    }
    let total = values
        .get("MemTotal:")
        .copied()
        .flatten()
        .filter(|value| *value > 0)?;
    let available = values
        .get("MemAvailable:")
        .copied()
        .flatten()
        .filter(|value| *value <= total);
    Some(MemorySnapshot {
        total_bytes: total,
        available_bytes: available,
    })
}
fn parse_disk(text: &str, path: String) -> Option<DiskSnapshot> {
    let row = text.lines().last()?;
    let columns: Vec<_> = row.split_whitespace().collect();
    if columns.len() < 6 {
        return None;
    }
    let total = kib(columns[1])?;
    let available = kib(columns[3])?;
    if total == 0 || available > total {
        return None;
    }
    Some(DiskSnapshot {
        path,
        total_bytes: total,
        available_bytes: available,
    })
}
fn os_name(text: &str) -> Option<String> {
    for key in ["PRETTY_NAME=", "NAME=", "ID="] {
        if let Some(line) = text.lines().find_map(|line| line.strip_prefix(key)) {
            if let Some(value) = short(line.trim_matches('"').trim_matches('\'')) {
                return Some(value);
            }
        }
    }
    None
}
fn os_release_value<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let line = text
        .lines()
        .find(|line| line.split_once('=').is_some_and(|(found, _)| found == key))?;
    let value = line.split_once('=')?.1.trim();
    let unquoted = value
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .or_else(|| {
            value
                .strip_prefix('\'')
                .and_then(|value| value.strip_suffix('\''))
        })
        .unwrap_or(value);
    (!unquoted.is_empty() && !unquoted.chars().any(char::is_control)).then_some(unquoted)
}
fn release_token(text: &str, key: &str, allowed: impl Fn(char) -> bool) -> Option<String> {
    let value = os_release_value(text, key)?;
    (value.len() <= 64 && value.chars().all(allowed)).then(|| value.to_ascii_lowercase())
}
fn distribution(os_release: Option<&str>) -> (Option<String>, Option<String>, Option<String>) {
    let Some(text) = os_release else {
        return (None, None, None);
    };
    let id = release_token(text, "ID", |ch| {
        ch.is_ascii_alphanumeric() || ch == '_' || ch == '-'
    });
    let version = release_token(text, "VERSION_ID", |ch| {
        ch.is_ascii_alphanumeric() || ch == '.' || ch == '_' || ch == '-'
    });
    let codename = release_token(text, "VERSION_CODENAME", |ch| {
        ch.is_ascii_alphanumeric() || ch == '.' || ch == '_' || ch == '-'
    })
    .or_else(|| {
        release_token(text, "UBUNTU_CODENAME", |ch| {
            ch.is_ascii_alphanumeric() || ch == '.' || ch == '_' || ch == '-'
        })
    });
    (id, version, codename)
}
#[derive(Deserialize)]
struct DockerServer {
    #[serde(rename = "Version")]
    version: String,
    #[serde(rename = "Os")]
    os: String,
    #[serde(rename = "Arch")]
    arch: String,
}
fn check(
    checks: &mut Vec<EnvironmentCheck>,
    code: &'static str,
    level: CheckLevel,
    label: &str,
    message: &'static str,
    recovery: &[&'static str],
) {
    checks.push(EnvironmentCheck {
        code,
        level,
        label: label.into(),
        message,
        recovery_steps: recovery.to_vec(),
    });
}
pub(crate) fn from_output(
    bytes: &[u8],
    request: &ConnectionRequest,
    host_id: Option<String>,
) -> CommandResult<HostEnvironment> {
    let probe = parse_protocol(bytes)?;
    let mut checks = Vec::new();
    let kernel = value(&probe, "kernel").and_then(short);
    let arch = value(&probe, "architecture").and_then(short);
    let release = value(&probe, "osRelease");
    let (distribution_id, distribution_version, distribution_codename) = distribution(release);
    let linux = kernel.as_deref() == Some("Linux");
    check(
        &mut checks,
        "LINUX_REQUIRED",
        if linux {
            CheckLevel::Passed
        } else {
            CheckLevel::Blocker
        },
        "操作系统",
        if linux {
            "通过"
        } else {
            "需要 Linux 主机"
        },
        &["选择 Linux amd64 主机"],
    );
    let amd64 = matches!(arch.as_deref(), Some("x86_64" | "amd64"));
    check(
        &mut checks,
        "AMD64_REQUIRED",
        if amd64 {
            CheckLevel::Passed
        } else {
            CheckLevel::Blocker
        },
        "系统架构",
        if amd64 {
            "通过"
        } else {
            "当前仅支持 amd64"
        },
        &["使用 Linux amd64 主机"],
    );
    let memory = value(&probe, "meminfo").and_then(memory);
    if memory.is_none() {
        check(
            &mut checks,
            "MEMORY_UNKNOWN",
            CheckLevel::Warning,
            "内存",
            "未能采集内存",
            &["检查 /proc/meminfo 读取权限"],
        );
    }
    if memory
        .as_ref()
        .is_some_and(|memory| memory.available_bytes.is_none())
    {
        check(
            &mut checks,
            "MEMORY_AVAILABLE_UNKNOWN",
            CheckLevel::Warning,
            "可用内存",
            "未能采集可用内存",
            &["检查 /proc/meminfo 中的 MemAvailable 字段"],
        );
    }
    let disk_path = value(&probe, "diskPath")
        .and_then(short)
        .filter(|path| path.starts_with('/'));
    let disk =
        disk_path.and_then(|path| value(&probe, "disk").and_then(|text| parse_disk(text, path)));
    if disk.is_none() {
        check(
            &mut checks,
            "DISK_UNKNOWN",
            CheckLevel::Blocker,
            "存储空间",
            "未能采集可用空间",
            &["检查管理目录所在磁盘与 df 权限"],
        );
    }
    match status(&probe, "managementAccess") {
        "writable" => check(
            &mut checks,
            "MANAGEMENT_PATH_WRITABLE",
            CheckLevel::Passed,
            "目录权限",
            "可写",
            &[],
        ),
        _ => check(
            &mut checks,
            "MANAGEMENT_PATH_PERMISSION",
            CheckLevel::Blocker,
            "目录权限",
            "管理目录不可写",
            &["请管理员仅为缺失或空目录，将 /var/lib/famine-hub 与 /var/lib/famine-hub.bootstrap 设为 SSH 用户所有且权限 0700；已有数据先核实归属"],
        ),
    }
    match status(&probe, "managementRoot") {
        "missing" => check(
            &mut checks,
            "MANAGEMENT_ROOT_MISSING",
            CheckLevel::Warning,
            "管理目录",
            "未创建",
            &["管理目录需要通过初始化"],
        ),
        "invalid" => check(
            &mut checks,
            "MANAGEMENT_ROOT_INVALID",
            CheckLevel::Blocker,
            "管理目录",
            "路径不是目录",
            &["核对 /var/lib/famine-hub 路径"],
        ),
        _ => {}
    }
    for tool in TOOLS {
        if status(&probe, &format!("tool.{tool}")) != "ok" {
            check(
                &mut checks,
                "REQUIRED_COMMAND_MISSING",
                CheckLevel::Blocker,
                tool,
                "未安装",
                &["安装所需工具后重新检查"],
            );
        }
    }
    let server = value(&probe, "dockerServer")
        .and_then(|text| serde_json::from_str::<DockerServer>(text).ok())
        .filter(|server| short(&server.version).is_some());
    let engine_state = match status(&probe, "dockerServer") {
        _ if status(&probe, "dockerClient") == "missing" => EngineState::NotInstalled,
        "permissionDenied" => EngineState::PermissionDenied,
        "ok" if server.is_some() => EngineState::Available,
        _ => EngineState::Unavailable,
    };
    match engine_state {
        EngineState::NotInstalled => check(
            &mut checks,
            "DOCKER_NOT_INSTALLED",
            CheckLevel::Blocker,
            "Docker",
            "未安装",
            &["安装 Docker Engine 后重新检查"],
        ),
        EngineState::PermissionDenied => check(
            &mut checks,
            "DOCKER_PERMISSION_DENIED",
            CheckLevel::Blocker,
            "Docker",
            "权限不足",
            &["检查当前账户对本机 Docker socket 的访问权限"],
        ),
        EngineState::Unavailable => check(
            &mut checks,
            "DOCKER_UNAVAILABLE",
            CheckLevel::Blocker,
            "Docker",
            "引擎不可访问",
            &["检查本机 Docker 服务和 /var/run/docker.sock"],
        ),
        EngineState::Available => check(
            &mut checks,
            "DOCKER_AVAILABLE",
            CheckLevel::Passed,
            "Docker",
            "可访问",
            &[],
        ),
    }
    if server.as_ref().is_some_and(|server| {
        server.os != "linux" || !matches!(server.arch.as_str(), "amd64" | "x86_64")
    }) {
        check(
            &mut checks,
            "DOCKER_PLATFORM_UNSUPPORTED",
            CheckLevel::Blocker,
            "容器平台",
            "需要 linux/amd64",
            &["使用本机 Linux amd64 Docker Engine"],
        );
    }
    let compose = value(&probe, "compose").and_then(short);
    let compose_v2 = compose
        .as_deref()
        .and_then(|text| {
            text.trim_start_matches('v')
                .split('.')
                .next()?
                .parse::<u64>()
                .ok()
        })
        .is_some_and(|major| major >= 2);
    check(
        &mut checks,
        "COMPOSE_V2_REQUIRED",
        if compose_v2 {
            CheckLevel::Passed
        } else {
            CheckLevel::Blocker
        },
        "Docker Compose",
        if compose_v2 {
            "通过"
        } else {
            "需要 Compose v2 或更新版本"
        },
        &["安装 Docker Compose 插件后重新检查"],
    );
    let data_root = value(&probe, "dockerRoot")
        .and_then(short)
        .filter(|value| value.starts_with('/'));
    let docker_disk = data_root.as_ref().and_then(|path| {
        value(&probe, "dockerDisk").and_then(|text| parse_disk(text, path.clone()))
    });
    if engine_state == EngineState::Available && docker_disk.is_none() {
        check(
            &mut checks,
            "DOCKER_DISK_UNKNOWN",
            CheckLevel::Warning,
            "Docker 存储",
            "未能采集可用空间",
            &["检查 Docker 数据目录所在磁盘的读取权限"],
        );
    }
    Ok(HostEnvironment {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.server_id.clone(),
        record_version: request.expected_record_version,
        host_id,
        environment_check_id: uuid::Uuid::new_v4().to_string(),
        checked_at_unix_seconds: ssh_state::now()?,
        os: release.and_then(os_name),
        distribution_id,
        distribution_version,
        distribution_codename,
        kernel,
        architecture: arch,
        uid: value(&probe, "uid").and_then(|text| text.trim().parse().ok()),
        disk,
        memory,
        docker: DockerEnvironment {
            client_version: value(&probe, "dockerClient").and_then(short),
            server_version: server.and_then(|server| short(&server.version)),
            compose_version: compose,
            engine_state,
            data_root,
            disk: docker_disk,
        },
        checks,
        deployment_verified: false,
    })
}
pub fn inspect(database: &Path, request: ConnectionRequest) -> CommandResult<HostEnvironment> {
    crate::ssh::with_verified_session(database, &request, |session, host_id| {
        from_output(&execute(session)?, &request, host_id)
    })
    .map_err(|mut error| {
        error.operation = "getHostEnvironment";
        error
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request() -> ConnectionRequest {
        ConnectionRequest {
            schema_version: 1,
            request_id: uuid::Uuid::new_v4().to_string(),
            server_id: uuid::Uuid::new_v4().to_string(),
            expected_record_version: 1,
        }
    }
    fn fixture(changes: &[(&str, &str, &str)]) -> Vec<u8> {
        let mut fields: BTreeMap<String, (String, String)> = FIELDS
            .iter()
            .map(|key| (key.to_string(), ("ok".into(), String::new())))
            .chain(
                TOOLS
                    .iter()
                    .map(|tool| (format!("tool.{tool}"), ("ok".into(), String::new()))),
            )
            .collect();
        let defaults=[("kernel","ok","Linux"),("architecture","ok","x86_64"),("uid","ok","1000"),("osRelease","ok","PRETTY_NAME=\"Ubuntu 26.04 LTS\"\nID=ubuntu\nVERSION_ID=\"26.04\"\nVERSION_CODENAME=resolute"),("meminfo","ok","MemTotal: 8388608 kB\nMemAvailable: 4194304 kB"),("diskPath","ok","/var/lib"),("disk","ok","Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/a 1000000 200000 800000 20% /var"),("managementAccess","writable",""),("managementRoot","present",""),("dockerClient","ok","Docker version 27.1.0, build example"),("compose","ok","2.29.0"),("dockerServer","ok",r#"{"Version":"27.1.0","Os":"linux","Arch":"amd64"}"#),("dockerRoot","ok","/var/lib/docker"),("dockerDisk","ok","Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/b 500000 100000 400000 20% /var/lib/docker")];
        for (key, status, value) in defaults.iter().chain(changes.iter()) {
            fields.insert(key.to_string(), (status.to_string(), value.to_string()));
        }
        let mut text = String::from("FHENV\t1\n");
        for (key, (status, value)) in fields {
            text.push_str(&format!("{key}\t{status}\t{}\n", STANDARD.encode(value)));
        }
        text.push_str("FHENV\tEND\n");
        text.into_bytes()
    }
    fn has(report: &HostEnvironment, code: &str, level: CheckLevel) -> bool {
        report
            .checks
            .iter()
            .any(|check| check.code == code && check.level == level)
    }
    #[test]
    fn valid_report_keeps_deployment_gate_and_separate_filesystems() -> CommandResult<()> {
        let report = from_output(&fixture(&[]), &request(), None)?;
        assert_eq!(report.os.as_deref(), Some("Ubuntu 26.04 LTS"));
        assert_eq!(report.distribution_id.as_deref(), Some("ubuntu"));
        assert_eq!(report.distribution_version.as_deref(), Some("26.04"));
        assert_eq!(report.distribution_codename.as_deref(), Some("resolute"));
        assert_eq!(
            report.memory.as_ref().map(|memory| memory.available_bytes),
            Some(Some(4194304 * 1024))
        );
        assert_eq!(
            report.disk.as_ref().map(|disk| disk.available_bytes),
            Some(800000 * 1024)
        );
        assert_eq!(
            report.docker.disk.as_ref().map(|disk| disk.available_bytes),
            Some(400000 * 1024)
        );
        assert!(!report
            .checks
            .iter()
            .any(|check| check.level == CheckLevel::Blocker));
        assert!(!report
            .checks
            .iter()
            .any(|check| { check.code == "DEPLOYMENT_COMPATIBILITY_UNVERIFIED" }));
        assert!(!report.deployment_verified);
        Ok(())
    }

    #[test]
    fn distribution_identity_is_parsed_as_data_without_shell_evaluation() {
        assert_eq!(
            distribution(Some(
                "ID=Ubuntu\nVERSION_ID=\"26.04\"\nVERSION_CODENAME=Resolute"
            )),
            (
                Some("ubuntu".into()),
                Some("26.04".into()),
                Some("resolute".into())
            )
        );
        assert_eq!(
            distribution(Some(
                "ID=ubuntu\nVERSION_ID=26.04\nUBUNTU_CODENAME=resolute"
            )),
            (
                Some("ubuntu".into()),
                Some("26.04".into()),
                Some("resolute".into())
            )
        );
        assert_eq!(
            distribution(Some("ID=$(touch /tmp/unsafe)\nVERSION_ID=26.04")),
            (None, Some("26.04".into()), None)
        );
        assert_eq!(
            distribution(Some("ID=ubuntu\nVERSION_ID=bad version")),
            (Some("ubuntu".into()), None, None)
        );
    }
    #[test]
    fn docker_failures_are_typed_not_classified_by_stderr() -> CommandResult<()> {
        for (client, state, expected, code) in [
            (
                "missing",
                "missing",
                EngineState::NotInstalled,
                "DOCKER_NOT_INSTALLED",
            ),
            (
                "ok",
                "permissionDenied",
                EngineState::PermissionDenied,
                "DOCKER_PERMISSION_DENIED",
            ),
            (
                "ok",
                "failed",
                EngineState::Unavailable,
                "DOCKER_UNAVAILABLE",
            ),
        ] {
            let report = from_output(
                &fixture(&[("dockerClient", client, ""), ("dockerServer", state, "")]),
                &request(),
                None,
            )?;
            assert_eq!(report.docker.engine_state, expected);
            assert!(has(&report, code, CheckLevel::Blocker));
        }
        Ok(())
    }
    #[test]
    fn missing_or_invalid_memory_disk_and_compose_do_not_become_success() -> CommandResult<()> {
        let report = from_output(
            &fixture(&[
                ("meminfo", "missing", ""),
                ("disk", "ok", "bad"),
                ("compose", "ok", "1.29.2"),
                ("managementAccess", "permissionDenied", ""),
            ]),
            &request(),
            None,
        )?;
        assert!(report.memory.is_none());
        assert!(report.disk.is_none());
        assert!(has(&report, "MEMORY_UNKNOWN", CheckLevel::Warning));
        assert!(has(&report, "DISK_UNKNOWN", CheckLevel::Blocker));
        assert!(has(&report, "COMPOSE_V2_REQUIRED", CheckLevel::Blocker));
        assert!(has(
            &report,
            "MANAGEMENT_PATH_PERMISSION",
            CheckLevel::Blocker
        ));
        Ok(())
    }
    #[test]
    fn unsupported_host_and_remote_docker_platform_are_blocked() -> CommandResult<()> {
        let report = from_output(
            &fixture(&[
                ("kernel", "ok", "Darwin"),
                ("architecture", "ok", "arm64"),
                (
                    "dockerServer",
                    "ok",
                    r#"{"Version":"27.1.0","Os":"linux","Arch":"arm64"}"#,
                ),
            ]),
            &request(),
            None,
        )?;
        for code in [
            "LINUX_REQUIRED",
            "AMD64_REQUIRED",
            "DOCKER_PLATFORM_UNSUPPORTED",
        ] {
            assert!(has(&report, code, CheckLevel::Blocker));
        }
        Ok(())
    }
    #[test]
    fn rejects_truncated_duplicate_unknown_and_invalid_protocol() {
        let valid = fixture(&[]);
        let prefix = &valid[..valid.len() - 10];
        assert!(parse_protocol(prefix).is_err());
        assert!(parse_protocol(b"FHENV\t2\nFHENV\tEND\n").is_err());
        let text =
            String::from_utf8_lossy(&valid).replace("FHENV\tEND", "uid\tok\tMA==\nFHENV\tEND");
        assert!(parse_protocol(text.as_bytes()).is_err());
        let text = String::from_utf8_lossy(&valid).replace("uid\tok", "unexpected\tok");
        assert!(parse_protocol(text.as_bytes()).is_err());
        assert!(parse_protocol(&vec![b'x'; MAX_OUTPUT + 1]).is_err());
    }
    #[test]
    fn malformed_data_and_missing_tools_are_explicit() -> CommandResult<()> {
        let report = from_output(
            &fixture(&[
                ("dockerServer", "ok", "null"),
                ("tool.jq", "missing", ""),
                (
                    "meminfo",
                    "ok",
                    "MemTotal: 1024 kB\nMemAvailable: 999999 kB",
                ),
            ]),
            &request(),
            None,
        )?;
        assert_eq!(report.docker.engine_state, EngineState::Unavailable);
        assert!(has(
            &report,
            "REQUIRED_COMMAND_MISSING",
            CheckLevel::Blocker
        ));
        assert!(has(
            &report,
            "MEMORY_AVAILABLE_UNKNOWN",
            CheckLevel::Warning
        ));
        Ok(())
    }

    #[test]
    #[ignore = "run scripts/test-host-environment.py to generate isolated probe outputs"]
    fn isolated_probe_fixture_files() -> Result<(), Box<dyn std::error::Error>> {
        let directory = std::path::PathBuf::from(std::env::var("HUNGER_ENV_FIXTURE_DIR")?);
        for (name, state) in [
            ("available", EngineState::Available),
            ("missing", EngineState::NotInstalled),
            ("permissionDenied", EngineState::PermissionDenied),
            ("unavailable", EngineState::Unavailable),
            ("badServer", EngineState::Unavailable),
            ("composeMissing", EngineState::Available),
            ("timeoutMissing", EngineState::Unavailable),
            ("invalidRoot", EngineState::Available),
        ] {
            let report = from_output(
                &std::fs::read(directory.join(format!("{name}.out")))?,
                &request(),
                None,
            )?;
            assert_eq!(report.docker.engine_state, state, "{name}");
            assert_eq!(
                report.disk.as_ref().map(|disk| disk.available_bytes),
                Some(98000 * 1024),
                "{name}"
            );
            if name == "composeMissing" {
                assert!(has(&report, "COMPOSE_V2_REQUIRED", CheckLevel::Blocker));
            }
            if name == "invalidRoot" {
                assert!(has(&report, "MANAGEMENT_ROOT_INVALID", CheckLevel::Blocker));
            }
        }
        Ok(())
    }
}
