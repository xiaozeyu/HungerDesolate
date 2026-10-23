use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::InstanceTarget,
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use ssh2::{ExtendedData, Session};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

const OP: &str = "instanceBackups";
const SCRIPT: &str = include_str!("../../remote-scripts/probes/instance-backups.py");
const TASK_SCRIPT: &str = include_str!("../../remote-scripts/actions/backup-task.py");
const ARCHIVE_SCRIPT: &str = include_str!("../../remote-scripts/actions/backup-archive.py");
const RESTORE_SCRIPT: &str = include_str!("../../remote-scripts/actions/restore-task.py");
const MAX_OUTPUT: usize = 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ListRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreviewRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub capture_mode: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CreateRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub capture_mode: String,
    pub operation_id: String,
    pub backup_id: String,
    pub reason: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskStatusRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TaskListRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DownloadRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub backup_id: String,
    pub operation_id: String,
    pub destination: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DeleteRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub backup_id: String,
    pub expected_checksum: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RestoreRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub expected_revision: u64,
    pub backup_id: String,
    pub operation_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RestoreTaskStatusRequest {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub expected_record_version: u64,
    pub operation_id: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BackupSummary {
    pub schema_version: u32,
    pub backup_id: String,
    pub host_id: String,
    pub instance_id: String,
    pub created_at: String,
    pub reason: String,
    pub capture_mode: String,
    pub capture_semantics: String,
    pub runtime_status_at_capture: String,
    pub size_bytes: u64,
    pub checksum: Option<String>,
    pub verification: String,
    pub restorability: String,
    pub dst_build: Option<String>,
    pub dst_release_id: Option<String>,
    pub mods_release_id: Option<String>,
    pub config_revision: u64,
    pub contains_secrets: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RemoteList {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    restore_acceptance_passed: bool,
    items: Vec<BackupSummary>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ListResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub config_revision: u64,
    pub restore_acceptance_passed: bool,
    pub items: Vec<BackupSummary>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RemotePreview {
    schema_version: u32,
    request_id: String,
    target: InstanceTarget,
    capture_mode: String,
    runtime_status: String,
    runtime_verified: bool,
    estimated_archive_bytes: u64,
    available_bytes: u64,
    file_count: u64,
    world_data_present: bool,
    allowed: bool,
    warnings: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PreviewResponse {
    pub schema_version: u32,
    pub request_id: String,
    pub target: InstanceTarget,
    pub config_revision: u64,
    pub capture_mode: String,
    pub runtime_status: String,
    pub runtime_verified: bool,
    pub estimated_archive_bytes: u64,
    pub available_bytes: u64,
    pub file_count: u64,
    pub world_data_present: bool,
    pub allowed: bool,
    pub warnings: Vec<String>,
}

fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(code, "backup", OP, message, "刷新专服备份状态后再重试")
}

fn valid_target(target: &InstanceTarget) -> CommandResult<()> {
    for (field, value) in [
        ("serverId", &target.server_id),
        ("hostId", &target.host_id),
        ("instanceId", &target.instance_id),
    ] {
        crate::server_input::validate_id(value, OP, field)?;
    }
    Ok(())
}

fn valid_request_id(request_id: &str) -> CommandResult<()> {
    crate::server_input::validate_id(request_id, OP, "requestId")
}

fn connection(request_id: &str, target: &InstanceTarget, record_version: u64) -> ConnectionRequest {
    ConnectionRequest {
        schema_version: 1,
        request_id: request_id.to_string(),
        server_id: target.server_id.clone(),
        expected_record_version: record_version,
    }
}

fn invoke(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份请求编码失败"))?,
    );
    let script = STANDARD.encode(SCRIPT.as_bytes());
    let archive_source = STANDARD.encode(ARCHIVE_SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nARCHIVE_SOURCE=base64.b64decode('{archive_source}').decode('utf-8')\nexec(compile(base64.b64decode('{script}'),'<instance-backups>','exec'))\nmain(json.loads(base64.b64decode('{request}').decode('utf-8')))\n"
    );
    let mut channel = session
        .channel_session()
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法建立备份查询通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法配置备份查询通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("BACKUP_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法提交备份查询"))?;
    channel
        .send_eof()
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法结束备份查询请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("BACKUP_QUERY_TIMEOUT", "备份查询超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= MAX_OUTPUT => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => return Err(error("BACKUP_CHANNEL_FAILED", "无法读取备份查询结果")),
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "备份查询通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份查询响应格式无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        let code = envelope
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or_default();
        return Err(match code {
            "HOST_IDENTITY_MISMATCH" => error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"),
            "INSTANCE_IDENTITY_MISMATCH" | "BACKUP_TARGET_MISMATCH" => {
                error("BACKUP_TARGET_MISMATCH", "备份或实例归属不一致")
            }
            "REVISION_CONFLICT" => error("REVISION_CONFLICT", "专服配置已变化，请刷新后重试"),
            "BACKUP_INPUT_INVALID" => error("BACKUP_INPUT_INVALID", "备份请求参数无效"),
            "BACKUP_CHANGED" => error("BACKUP_CHANGED", "备份已变化，请刷新后重试"),
            "BACKUP_PROTECTION_POINT" => error("BACKUP_PROTECTION_POINT", "恢复保护点不能删除"),
            "BACKUP_DELETE_FAILED" | "BACKUP_DELETE_INCOMPLETE" => {
                error("BACKUP_DELETE_FAILED", "备份删除未完成，请刷新核对现场")
            }
            "INSTANCE_BUSY" => error("INSTANCE_BUSY", "专服正由其他维护操作占用"),
            "BACKUP_METADATA_MISSING" => error(
                "BACKUP_METADATA_MISSING",
                "发现未完成的备份现场，需先检查任务状态",
            ),
            "BACKUP_RUNTIME_INVALID" => {
                error("BACKUP_RUNTIME_INVALID", "容器身份或运行状态无法确认")
            }
            "BACKUP_PATH_UNSAFE" | "BACKUP_SOURCE_UNSAFE" | "BACKUP_STATE_INVALID" => {
                error("BACKUP_STATE_INVALID", "备份目录或元数据状态不安全")
            }
            _ => error("BACKUP_QUERY_FAILED", "读取远端备份状态失败"),
        });
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("BACKUP_PROTOCOL_INVALID", "备份查询响应缺少数据"))
}

fn invoke_task(session: &Session, payload: &Value) -> CommandResult<Value> {
    let request = STANDARD.encode(
        serde_json::to_vec(payload)
            .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份任务请求编码失败"))?,
    );
    let task_source = STANDARD.encode(TASK_SCRIPT.as_bytes());
    let archive_source = STANDARD.encode(ARCHIVE_SCRIPT.as_bytes());
    let restore_source = STANDARD.encode(RESTORE_SCRIPT.as_bytes());
    let source = format!(
        "import base64,json\nARCHIVE_SOURCE=base64.b64decode('{archive_source}').decode('utf-8')\nWORKER_SOURCE=base64.b64decode('{task_source}').decode('utf-8')\nRESTORE_SOURCE=base64.b64decode('{restore_source}').decode('utf-8')\nrequest=json.loads(base64.b64decode('{request}').decode('utf-8'))\nif request.get('action','').startswith('restore'):\n    namespace={{'__name__':'restore_task','ARCHIVE_SOURCE':ARCHIVE_SOURCE,'RESTORE_SOURCE':RESTORE_SOURCE}}\n    exec(compile(RESTORE_SOURCE,'<restore-task>','exec'),namespace)\n    namespace['main'](request)\nelse:\n    namespace={{'__name__':'backup_task','ARCHIVE_SOURCE':ARCHIVE_SOURCE,'WORKER_SOURCE':WORKER_SOURCE}}\n    exec(compile(WORKER_SOURCE,'<backup-task>','exec'),namespace)\n    namespace['main'](request)\n"
    );
    run_remote(session, &source)
}

fn run_remote(session: &Session, source: &str) -> CommandResult<Value> {
    let mut channel = session
        .channel_session()
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法建立备份任务通道"))?;
    channel
        .handle_extended_data(ExtendedData::Ignore)
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法配置备份任务通道"))?;
    channel
        .exec("/usr/bin/python3 -I -")
        .map_err(|_| error("BACKUP_RUNTIME_REQUIRED", "远端需要 /usr/bin/python3"))?;
    channel
        .write_all(source.as_bytes())
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法提交备份任务"))?;
    channel
        .send_eof()
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "无法结束备份任务请求"))?;
    session.set_blocking(false);
    let deadline = Instant::now() + Duration::from_secs(30);
    let mut output = Vec::new();
    let mut buffer = [0u8; 8192];
    loop {
        if Instant::now() >= deadline {
            return Err(error("BACKUP_TASK_TIMEOUT", "备份任务请求超时"));
        }
        match channel.read(&mut buffer) {
            Ok(0) if channel.eof() => break,
            Ok(0) => std::thread::sleep(Duration::from_millis(20)),
            Ok(count) if output.len() + count <= MAX_OUTPUT => {
                output.extend_from_slice(&buffer[..count])
            }
            Err(cause) if cause.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(20))
            }
            _ => return Err(error("BACKUP_CHANNEL_FAILED", "无法读取备份任务结果")),
        }
    }
    session.set_blocking(true);
    session.set_timeout(1000);
    channel
        .wait_close()
        .map_err(|_| error("BACKUP_CHANNEL_FAILED", "备份任务通道未正常结束"))?;
    let envelope: Value = serde_json::from_slice(&output)
        .or_else(|_| {
            output
                .split(|byte| *byte == b'\n' || *byte == b'\r')
                .rev()
                .filter_map(|line| std::str::from_utf8(line).ok())
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .find_map(|line| serde_json::from_str::<Value>(line).ok())
                .ok_or_else(|| {
                    serde_json::Error::io(std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        "backup task response is not JSON",
                    ))
                })
        })
        .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份任务响应格式无效"))?;
    if envelope.get("ok") != Some(&Value::Bool(true)) {
        let code = envelope
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or_default();
        return Err(task_error(code));
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| error("BACKUP_PROTOCOL_INVALID", "备份任务响应缺少数据"))
}

fn task_error(code: &str) -> Box<CommandError> {
    match code {
        "HOST_IDENTITY_MISMATCH" => error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"),
        "INSTANCE_NOT_FOUND" => error("INSTANCE_NOT_FOUND", "专服不存在或尚未就绪"),
        "REVISION_CONFLICT" => error("REVISION_CONFLICT", "专服配置已变化，请刷新后重试"),
        "INSTANCE_BUSY" => error("INSTANCE_BUSY", "专服正由其他维护操作占用"),
        "BACKUP_RUNTIME_INVALID" => error("BACKUP_RUNTIME_INVALID", "分片运行状态或身份无法确认"),
        "BACKUP_SPACE_INSUFFICIENT" => error("BACKUP_SPACE_INSUFFICIENT", "远端可用空间不足"),
        "OPERATION_ID_REUSED" => error("OPERATION_ID_REUSED", "操作编号已用于不同的备份请求"),
        "BACKUP_STOP_FAILED" => error("BACKUP_STOP_FAILED", "无法确认冷备份所需的停服状态"),
        "BACKUP_RESTART_FAILED" => error(
            "BACKUP_RESTART_FAILED",
            "备份已采集，但无法确认恢复原运行状态",
        ),
        "BACKUP_TASK_HANDLER_FAILED" | "BACKUP_TASK_STATE_INVALID" => error(
            "BACKUP_TASK_HANDLER_FAILED",
            "读取备份任务状态失败，请重试查询",
        ),
        "BACKUP_PATH_UNSAFE" | "BACKUP_SOURCE_UNSAFE" => {
            error("BACKUP_STATE_INVALID", "备份数据目录不安全")
        }
        "ROOT_REQUIRED" => error("ROOT_REQUIRED", "远端备份任务需要 root Docker 环境"),
        _ => error(
            "BACKUP_TASK_FAILED",
            "无法接受备份任务，请刷新并核对远端任务状态",
        ),
    }
}

fn validate_revision(revision: u64) -> CommandResult<()> {
    if revision > crate::server_input::MAX_REVISION {
        return Err(error("BACKUP_INPUT_INVALID", "配置版本超出范围"));
    }
    Ok(())
}

fn valid_destination(value: &str) -> CommandResult<PathBuf> {
    let path = PathBuf::from(value);
    if !path.is_absolute() || path.file_name().is_none() || value.len() > 4096 {
        return Err(error("BACKUP_DOWNLOAD_PATH_INVALID", "本地下载路径无效"));
    }
    let parent = path
        .parent()
        .ok_or_else(|| error("BACKUP_DOWNLOAD_PATH_INVALID", "本地下载路径无效"))?;
    if !parent.exists() || !parent.is_dir() {
        return Err(error("BACKUP_DOWNLOAD_PATH_INVALID", "本地下载目录不存在"));
    }
    Ok(path)
}

fn download_task_dir(database: &Path) -> CommandResult<PathBuf> {
    let parent = database
        .parent()
        .ok_or_else(|| error("LOCAL_STORAGE_FAILED", "无法定位本地任务目录"))?;
    let directory = parent.join("backup-download-tasks");
    fs::create_dir_all(&directory)
        .map_err(|_| error("LOCAL_STORAGE_FAILED", "无法创建本地下载任务目录"))?;
    Ok(directory)
}

fn write_download_state(database: &Path, operation_id: &str, state: &Value) -> CommandResult<()> {
    let directory = download_task_dir(database)?;
    let path = directory.join(format!("{operation_id}.json"));
    let temporary = directory.join(format!(".{operation_id}.{}.tmp", uuid::Uuid::new_v4()));
    let bytes = serde_json::to_vec(state)
        .map_err(|_| error("LOCAL_STORAGE_FAILED", "无法编码本地下载任务"))?;
    let write_result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, &path)?;
        if let Ok(directory_fd) = fs::File::open(directory) {
            let _ = directory_fd.sync_all();
        }
        Ok::<(), std::io::Error>(())
    })();
    let _ = fs::remove_file(&temporary);
    write_result.map_err(|_| error("LOCAL_STORAGE_FAILED", "无法保存本地下载任务"))
}

pub fn download_status(database: &Path, request: DownloadRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "下载任务查询版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    let path = download_task_dir(database)?.join(format!("{}.json", request.operation_id));
    let bytes =
        fs::read(path).map_err(|_| error("BACKUP_DOWNLOAD_TASK_MISSING", "本地下载任务不存在"))?;
    let value: Value = serde_json::from_slice(&bytes)
        .map_err(|_| error("BACKUP_DOWNLOAD_TASK_INVALID", "本地下载任务状态无效"))?;
    if value.get("operationId").and_then(Value::as_str) != Some(request.operation_id.as_str())
        || value.get("backupId").and_then(Value::as_str) != Some(request.backup_id.as_str())
    {
        return Err(error("BACKUP_TARGET_MISMATCH", "本地下载任务归属不一致"));
    }
    Ok(value)
}

pub fn list_download_tasks(database: &Path, request: TaskListRequest) -> CommandResult<Vec<Value>> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "下载任务列表版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    let directory = download_task_dir(database)?;
    let mut tasks = Vec::new();
    for entry in fs::read_dir(directory)
        .map_err(|_| error("LOCAL_STORAGE_FAILED", "无法读取本地下载任务"))?
    {
        let entry = entry.map_err(|_| error("LOCAL_STORAGE_FAILED", "无法读取本地下载任务"))?;
        if entry.path().extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        let bytes = fs::read(entry.path())
            .map_err(|_| error("LOCAL_STORAGE_FAILED", "无法读取本地下载任务"))?;
        let mut value: Value = serde_json::from_slice(&bytes)
            .map_err(|_| error("BACKUP_DOWNLOAD_TASK_INVALID", "本地下载任务状态无效"))?;
        let destination = value
            .get("destination")
            .and_then(Value::as_str)
            .map(PathBuf::from);
        let expected_checksum = value.get("checksum").and_then(Value::as_str);
        let published_matches = match (destination.as_ref(), expected_checksum) {
            (Some(path), Some(expected)) if path.is_file() && !path.is_symlink() => {
                digest_local_file(path).as_deref() == Some(expected)
            }
            _ => false,
        };
        if matches!(
            value.get("status").and_then(Value::as_str),
            Some("running" | "interrupted")
        ) && published_matches
        {
            value["status"] = json!("succeeded");
            value["stage"] = json!("finalize");
            value["bytesTransferred"] = json!(destination
                .as_ref()
                .and_then(|path| fs::metadata(path).ok())
                .map(|metadata| metadata.len())
                .unwrap_or(0));
            if let Some(operation_id) = value
                .get("operationId")
                .and_then(Value::as_str)
                .map(str::to_string)
            {
                write_download_state(database, &operation_id, &value)?;
            }
        } else if value.get("status").and_then(Value::as_str) == Some("running") {
            value["status"] = json!("interrupted");
            value["errorCode"] = json!("BACKUP_DOWNLOAD_INTERRUPTED");
            value["bytesTransferred"] = value
                .get("temporaryPath")
                .and_then(Value::as_str)
                .and_then(|path| fs::metadata(path).ok())
                .map(|metadata| metadata.len())
                .unwrap_or(0)
                .into();
            if let Some(operation_id) = value
                .get("operationId")
                .and_then(Value::as_str)
                .map(str::to_string)
            {
                write_download_state(database, &operation_id, &value)?;
            }
        }
        if value.pointer("/target/serverId").and_then(Value::as_str)
            == Some(request.target.server_id.as_str())
            && value.pointer("/target/hostId").and_then(Value::as_str)
                == Some(request.target.host_id.as_str())
            && value.pointer("/target/instanceId").and_then(Value::as_str)
                == Some(request.target.instance_id.as_str())
        {
            tasks.push(value);
        }
    }
    tasks.sort_by(|left, right| {
        right
            .get("attempt")
            .and_then(Value::as_u64)
            .cmp(&left.get("attempt").and_then(Value::as_u64))
    });
    tasks.truncate(100);
    Ok(tasks)
}

fn digest_local_file(path: &Path) -> Option<String> {
    let mut file = fs::File::open(path).ok()?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer).ok()?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Some(format!("{:x}", digest.finalize()))
}

fn remote_backup_paths(
    target: &InstanceTarget,
    backup_id: &str,
) -> CommandResult<(PathBuf, PathBuf)> {
    crate::server_input::validate_id(backup_id, OP, "backupId")?;
    let root = PathBuf::from("/var/lib/famine-hub/backups")
        .join(&target.instance_id)
        .join(backup_id);
    Ok((root.join("archive.tar.gz"), root.join("metadata.json")))
}

pub fn download(database: &Path, request: DownloadRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份下载请求版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    let destination = valid_destination(&request.destination)?;
    let (archive_path, metadata_path) = remote_backup_paths(&request.target, &request.backup_id)?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    let temporary = destination.with_file_name(format!(
        ".{}.{}.part",
        destination
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("backup"),
        request.operation_id
    ));
    let initial = json!({"schemaVersion":1,"operation":"backupDownload","operationId":request.operation_id,
        "target":request.target,"backupId":request.backup_id,"destination":destination,"temporaryPath":temporary,
        "status":"running","stage":"preflight","bytesTransferred":0,"attempt":1});
    let state_path = download_task_dir(database)?.join(format!("{}.json", request.operation_id));
    let prior = fs::read(&state_path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    let initial = if let Some(mut prior) = prior {
        if prior.get("backupId").and_then(Value::as_str) != Some(request.backup_id.as_str())
            || prior.get("destination").and_then(Value::as_str)
                != Some(request.destination.as_str())
            || prior.pointer("/target/serverId").and_then(Value::as_str)
                != Some(request.target.server_id.as_str())
            || prior.pointer("/target/hostId").and_then(Value::as_str)
                != Some(request.target.host_id.as_str())
            || prior.pointer("/target/instanceId").and_then(Value::as_str)
                != Some(request.target.instance_id.as_str())
        {
            return Err(error("BACKUP_TARGET_MISMATCH", "下载任务与目标文件不一致"));
        }
        if prior.get("status").and_then(Value::as_str) == Some("succeeded") {
            return Err(error("BACKUP_DOWNLOAD_EXISTS", "该下载任务已经完成"));
        }
        prior["attempt"] = json!(prior
            .get("attempt")
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .saturating_add(1));
        prior["status"] = json!("running");
        prior["stage"] = json!("preflight");
        prior
    } else {
        initial
    };
    write_download_state(database, &request.operation_id, &initial)?;
    let result = with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let sftp = session
            .sftp()
            .map_err(|_| error("SSH_SFTP_FAILED", "无法建立 SFTP 下载通道"))?;
        let metadata_stat = sftp
            .lstat(&metadata_path)
            .map_err(|_| error("BACKUP_METADATA_MISSING", "备份元数据不存在"))?;
        if !metadata_stat.is_file() || metadata_stat.size.is_none_or(|size| size > 262144) {
            return Err(error("BACKUP_STATE_INVALID", "备份元数据状态不安全"));
        }
        let mut metadata_bytes = Vec::new();
        sftp.open(&metadata_path)
            .map_err(|_| error("BACKUP_METADATA_MISSING", "无法读取备份元数据"))?
            .read_to_end(&mut metadata_bytes)
            .map_err(|_| error("BACKUP_METADATA_MISSING", "无法读取备份元数据"))?;
        let metadata: Value = serde_json::from_slice(&metadata_bytes)
            .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份元数据格式无效"))?;
        if metadata.get("schemaVersion") != Some(&Value::from(1))
            || metadata.get("hostId").and_then(Value::as_str)
                != Some(request.target.host_id.as_str())
            || metadata.get("instanceId").and_then(Value::as_str)
                != Some(request.target.instance_id.as_str())
            || metadata.get("backupId").and_then(Value::as_str) != Some(request.backup_id.as_str())
            || metadata.get("verification").and_then(Value::as_str) != Some("verified")
        {
            return Err(error("BACKUP_TARGET_MISMATCH", "备份归属或校验状态无效"));
        }
        let checksum = metadata
            .get("checksum")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if checksum.len() != 64 || !checksum.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(error("BACKUP_PROTOCOL_INVALID", "备份 checksum 无效"));
        }
        let archive_stat = sftp
            .lstat(&archive_path)
            .map_err(|_| error("BACKUP_ARCHIVE_MISSING", "备份归档不存在"))?;
        let expected_size = metadata
            .get("sizeBytes")
            .and_then(Value::as_u64)
            .unwrap_or(0);
        if !archive_stat.is_file() || archive_stat.size != Some(expected_size) {
            return Err(error("BACKUP_ARCHIVE_INVALID", "备份归档大小不匹配"));
        }
        let mut state = initial.clone();
        state["stage"] = json!("transfer");
        state["checksum"] = json!(checksum);
        state["sizeBytes"] = json!(expected_size);
        write_download_state(database, &request.operation_id, &state)?;
        let mut remote = sftp
            .open(&archive_path)
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法打开远端备份归档"))?;
        let mut local = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&temporary)
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法创建本地临时文件"))?;
        let mut digest = Sha256::new();
        let mut buffer = [0u8; 1024 * 1024];
        let mut total = local
            .metadata()
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法读取本地临时文件状态"))?
            .len();
        if total > expected_size {
            return Err(error(
                "BACKUP_DOWNLOAD_FAILED",
                "本地临时文件长度超过备份大小",
            ));
        }
        local
            .seek(SeekFrom::Start(0))
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法校验本地续传数据"))?;
        let mut hashed = 0u64;
        while hashed < total {
            let wanted = ((total - hashed).min(buffer.len() as u64)) as usize;
            let count = local
                .read(&mut buffer[..wanted])
                .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法校验本地续传数据"))?;
            if count == 0 {
                return Err(error("BACKUP_DOWNLOAD_FAILED", "本地临时文件读取不完整"));
            }
            digest.update(&buffer[..count]);
            hashed += count as u64;
        }
        remote
            .seek(SeekFrom::Start(total))
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法从断点续传"))?;
        local
            .seek(SeekFrom::End(0))
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法定位本地续传位置"))?;
        loop {
            let count = remote
                .read(&mut buffer)
                .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "下载过程中连接中断"))?;
            if count == 0 {
                break;
            }
            local
                .write_all(&buffer[..count])
                .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法写入本地临时文件"))?;
            digest.update(&buffer[..count]);
            total = total.saturating_add(count as u64);
            state["bytesTransferred"] = json!(total);
            if total > expected_size {
                return Err(error(
                    "BACKUP_ARCHIVE_INVALID",
                    "远端备份归档大小超出元数据",
                ));
            }
            if total % (16 * 1024 * 1024) < count as u64 {
                local
                    .sync_data()
                    .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法保存下载断点"))?;
                write_download_state(database, &request.operation_id, &state)?;
            }
        }
        local
            .flush()
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法刷新本地下载文件"))?;
        local
            .sync_all()
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法同步本地下载文件"))?;
        let actual = format!("{:x}", digest.finalize());
        state["stage"] = json!("checksumVerify");
        write_download_state(database, &request.operation_id, &state)?;
        if total != expected_size || actual != checksum {
            drop(local);
            let _ = fs::remove_file(&temporary);
            return Err(error(
                "BACKUP_CHECKSUM_MISMATCH",
                "本地备份 checksum 校验失败",
            ));
        }
        state["stage"] = json!("localPublish");
        write_download_state(database, &request.operation_id, &state)?;
        if destination.exists() {
            return Err(error("BACKUP_DOWNLOAD_EXISTS", "本地目标文件已存在"));
        }
        fs::rename(&temporary, &destination)
            .map_err(|_| error("BACKUP_DOWNLOAD_FAILED", "无法原子发布本地备份"))?;
        let response = json!({"schemaVersion":1,"requestId":request.request_id,"target":request.target,
            "operationId":request.operation_id,"backupId":request.backup_id,"status":"succeeded","stage":"finalize",
            "destination":destination,"temporaryPath":temporary,"checksum":actual,"sizeBytes":total,"bytesTransferred":total,"attempt":initial["attempt"]});
        write_download_state(database, &request.operation_id, &response)?;
        Ok(response)
    });
    if result.is_err() {
        let mut interrupted = fs::read(&state_path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .unwrap_or(initial);
        interrupted["status"] = json!("interrupted");
        if interrupted.get("stage").and_then(Value::as_str) != Some("localPublish") {
            interrupted["stage"] = json!("transfer");
        }
        interrupted["bytesTransferred"] = json!(fs::metadata(&temporary)
            .map(|metadata| metadata.len())
            .unwrap_or(0));
        interrupted["errorCode"] = json!("BACKUP_DOWNLOAD_INTERRUPTED");
        let _ = write_download_state(database, &request.operation_id, &interrupted);
    }
    result
}

pub fn create(database: &Path, request: CreateRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份请求版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    crate::server_input::validate_id(&request.backup_id, OP, "backupId")?;
    validate_revision(request.expected_revision)?;
    if !["cold", "quasiHot"].contains(&request.capture_mode.as_str())
        || request.reason.trim().is_empty()
        || request.reason.len() > 96
        || request.reason.chars().any(char::is_control)
    {
        return Err(error("BACKUP_INPUT_INVALID", "备份模式或原因无效"));
    }
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let remote = invoke_task(
            session,
            &json!({
                "schemaVersion": 1,
                "action": "start",
                "requestId": request.request_id,
                "operationId": request.operation_id,
                "backupId": request.backup_id,
                "hostId": request.target.host_id,
                "instanceId": request.target.instance_id,
                "expectedRevision": request.expected_revision,
                "captureMode": request.capture_mode,
                "reason": request.reason,
            }),
        )?;
        task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn delete(database: &Path, request: DeleteRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份删除请求版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    validate_revision(request.expected_revision)?;
    crate::server_input::validate_id(&request.backup_id, OP, "backupId")?;
    if request.expected_checksum.len() != 64
        || !request
            .expected_checksum
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(error("BACKUP_INPUT_INVALID", "备份 checksum 无效"));
    }
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let result = invoke(
            session,
            &json!({"schemaVersion":1,"action":"delete","requestId":request.request_id,
            "serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,
            "expectedRevision":request.expected_revision,"backupId":request.backup_id,"expectedChecksum":request.expected_checksum}),
        )?;
        let response_target = result
            .get("target")
            .and_then(|item| serde_json::from_value::<InstanceTarget>(item.clone()).ok());
        if result.get("deleted") != Some(&Value::Bool(true))
            || result.get("backupId").and_then(Value::as_str) != Some(request.backup_id.as_str())
            || response_target
                .as_ref()
                .is_none_or(|value| !same_target(value, &request.target))
        {
            return Err(error("BACKUP_PROTOCOL_INVALID", "备份删除回执无效"));
        }
        Ok(result)
    })
}

pub fn status(database: &Path, request: TaskStatusRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份任务查询版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let remote = match invoke_task(
            session,
            &json!({
                "schemaVersion": 1,
                "action": "status",
                "requestId": request.request_id,
                "operationId": request.operation_id,
                "hostId": request.target.host_id,
                "instanceId": request.target.instance_id,
            }),
        ) {
            Ok(remote) => remote,
            Err(cause) if cause.code == "BACKUP_PROTOCOL_INVALID" => {
                let tasks = list_tasks_remote(session, &request.request_id, &request.target)?;
                tasks
                    .into_iter()
                    .find(|task| {
                        task.get("operationId").and_then(Value::as_str)
                            == Some(request.operation_id.as_str())
                    })
                    .ok_or(cause)?
            }
            Err(cause) => return Err(cause),
        };
        task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn list_tasks(database: &Path, request: TaskListRequest) -> CommandResult<Vec<Value>> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份任务查询版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        list_tasks_remote(session, &request.request_id, &request.target)?
            .into_iter()
            .map(|task| {
                let operation_id = task
                    .get("operationId")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                task_response(task, &request.request_id, &request.target, &operation_id)
            })
            .collect()
    })
}

fn list_tasks_remote(
    session: &Session,
    request_id: &str,
    target: &InstanceTarget,
) -> CommandResult<Vec<Value>> {
    let remote = invoke_task(
        session,
        &json!({
            "schemaVersion": 1,
            "action": "listTasks",
            "requestId": request_id,
            "hostId": target.host_id,
            "instanceId": target.instance_id,
        }),
    )?;
    let tasks = remote
        .as_array()
        .ok_or_else(|| error("BACKUP_PROTOCOL_INVALID", "备份任务列表格式无效"))?;
    if tasks.len() > 100 {
        return Err(error("BACKUP_PROTOCOL_INVALID", "备份任务列表超过上限"));
    }
    Ok(tasks.clone())
}

fn task_response(
    remote: Value,
    request_id: &str,
    target: &InstanceTarget,
    expected_operation_id: &str,
) -> CommandResult<Value> {
    let operation_id = remote.get("operationId").and_then(Value::as_str);
    let task_id = remote.get("taskId").and_then(Value::as_str);
    let remote_target = remote.get("target").and_then(Value::as_object);
    let status = remote
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let stage = remote
        .get("stage")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let sequence = remote
        .get("sequence")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    let stages = [
        "queued",
        "preflight",
        "stop",
        "backup",
        "backupVerify",
        "start",
        "verify",
        "finalize",
    ];
    if remote.get("schemaVersion") != Some(&Value::from(1))
        || operation_id.is_none_or(|value| !valid_uuid(value))
        || operation_id != Some(expected_operation_id)
        || task_id != operation_id
        || remote.get("operation").and_then(Value::as_str) != Some("backupCreate")
        || remote_target
            .and_then(|value| value.get("hostId"))
            .and_then(Value::as_str)
            != Some(target.host_id.as_str())
        || remote_target
            .and_then(|value| value.get("instanceId"))
            .and_then(Value::as_str)
            != Some(target.instance_id.as_str())
        || !["queued", "running", "succeeded", "failed", "interrupted"].contains(&status)
        || !stages.contains(&stage)
        || sequence == 0
        || remote
            .get("journal")
            .and_then(Value::as_array)
            .is_none_or(|items| items.len() > 100)
        || remote.get("progress").and_then(Value::as_object).is_none()
        || remote
            .get("createdAt")
            .and_then(Value::as_str)
            .is_none_or(|value| value.len() > 64)
    {
        return Err(error(
            "BACKUP_PROTOCOL_INVALID",
            "备份任务身份、阶段或序号无效",
        ));
    }
    if status == "succeeded"
        && remote
            .get("result")
            .and_then(|value| value.get("verification"))
            .and_then(Value::as_str)
            != Some("passed")
    {
        return Err(error(
            "BACKUP_PROTOCOL_INVALID",
            "备份任务缺少通过校验的结果",
        ));
    }
    let mut task = remote;
    task["target"] = serde_json::to_value(target)
        .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份任务目标编码失败"))?;
    if let Some(error_target) = task.pointer_mut("/error/target") {
        *error_target = serde_json::to_value(target)
            .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份错误目标编码失败"))?;
    }
    Ok(json!({"schemaVersion":1,"requestId":request_id,"target":target,"task":task}))
}

fn valid_uuid(value: &str) -> bool {
    uuid::Uuid::try_parse(value)
        .is_ok_and(|id| id.get_version_num() == 4 && id.to_string() == value)
}

pub fn list(database: &Path, request: ListRequest) -> CommandResult<ListResponse> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份请求版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    validate_revision(request.expected_revision)?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let value = invoke(
            session,
            &json!({"schemaVersion":1,"action":"list","requestId":request.request_id,
            "serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,
            "expectedRevision":request.expected_revision}),
        )?;
        let remote: RemoteList = serde_json::from_value(value)
            .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份列表响应格式无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || !same_target(&remote.target, &request.target)
            || remote.items.len() > 5000
            || remote
                .items
                .iter()
                .any(|item| !valid_backup(item, &request.target, request.expected_revision))
        {
            return Err(error("BACKUP_PROTOCOL_INVALID", "备份列表身份或字段无效"));
        }
        Ok(ListResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            config_revision: request.expected_revision,
            restore_acceptance_passed: remote.restore_acceptance_passed,
            items: remote.items,
        })
    })
}

pub fn preview(database: &Path, request: PreviewRequest) -> CommandResult<PreviewResponse> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "备份请求版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    validate_revision(request.expected_revision)?;
    if !["cold", "quasiHot"].contains(&request.capture_mode.as_str()) {
        return Err(error("BACKUP_INPUT_INVALID", "备份模式无效"));
    }
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let value = invoke(
            session,
            &json!({"schemaVersion":1,"action":"preview","requestId":request.request_id,
            "serverId":request.target.server_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,
            "expectedRevision":request.expected_revision,"captureMode":request.capture_mode}),
        )?;
        let remote: RemotePreview = serde_json::from_value(value)
            .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "备份预览响应格式无效"))?;
        if remote.schema_version != 1
            || remote.request_id != request.request_id
            || !same_target(&remote.target, &request.target)
            || remote.capture_mode != request.capture_mode
            || !["running", "stopped", "degraded", "unknown"]
                .contains(&remote.runtime_status.as_str())
            || remote.warnings.len() > 16
            || remote.warnings.iter().any(|item| item.len() > 96)
        {
            return Err(error("BACKUP_PROTOCOL_INVALID", "备份预览身份或字段无效"));
        }
        Ok(PreviewResponse {
            schema_version: 1,
            request_id: request.request_id,
            target: request.target,
            config_revision: request.expected_revision,
            capture_mode: remote.capture_mode,
            runtime_status: remote.runtime_status,
            runtime_verified: remote.runtime_verified,
            estimated_archive_bytes: remote.estimated_archive_bytes,
            available_bytes: remote.available_bytes,
            file_count: remote.file_count,
            world_data_present: remote.world_data_present,
            allowed: remote.allowed && remote.estimated_archive_bytes < remote.available_bytes,
            warnings: remote.warnings,
        })
    })
}

fn check_restore_preflight(
    session: &Session,
    request_id: &str,
    target: &InstanceTarget,
    expected_revision: u64,
    backup_id: &str,
) -> CommandResult<()> {
    let value = invoke(
        session,
        &json!({"schemaVersion":1,"action":"restorePreflight","requestId":request_id,
        "serverId":target.server_id,"hostId":target.host_id,"instanceId":target.instance_id,
        "expectedRevision":expected_revision,"backupId":backup_id}),
    )?;
    let response_target = value
        .get("target")
        .and_then(|item| serde_json::from_value::<InstanceTarget>(item.clone()).ok())
        .ok_or_else(|| error("BACKUP_PROTOCOL_INVALID", "恢复检测目标无效"))?;
    let blockers = value
        .get("blockers")
        .and_then(Value::as_array)
        .ok_or_else(|| error("BACKUP_PROTOCOL_INVALID", "恢复检测结果无效"))?;
    if !same_target(&response_target, target)
        || value.get("backupId").and_then(Value::as_str) != Some(backup_id)
        || blockers.len() > 32
        || blockers
            .iter()
            .any(|item| item.as_str().is_none_or(|code| code.len() > 160))
    {
        return Err(error("BACKUP_PROTOCOL_INVALID", "恢复检测身份或字段无效"));
    }
    if value.get("allowed") != Some(&Value::Bool(true)) {
        let code = blockers
            .first()
            .and_then(Value::as_str)
            .unwrap_or("RESTORE_PREFLIGHT_BLOCKED");
        return Err(match code {
            "DST_RELEASE_UNAVAILABLE"
            | "DST_RELEASE_ARTIFACTS_UNSAFE"
            | "DST_RELEASE_ARTIFACTS_MISMATCH" => {
                error("DST_RELEASE_UNAVAILABLE", "备份所需的 DST 版本产物不可用")
            }
            "DST_RELEASE_MISMATCH" | "BACKUP_RELEASE_MISMATCH" => {
                error("DST_RELEASE_MISMATCH", "备份版本与当前专服不兼容")
            }
            "BACKUP_TARGET_MISMATCH" => error("BACKUP_TARGET_MISMATCH", "备份不属于当前专服"),
            "BACKUP_SPACE_INSUFFICIENT" | "RESTORE_SPACE_INSUFFICIENT" => error(
                "BACKUP_SPACE_INSUFFICIENT",
                "磁盘空间不足，无法创建保护点并恢复",
            ),
            "RUNTIME_STATE_UNVERIFIED" => {
                error("BACKUP_RUNTIME_INVALID", "无法确认当前专服运行状态")
            }
            "WORLD_TOKEN_MISSING" => error("WORLD_TOKEN_MISSING", "本机受保护的 Klei 令牌材料缺失，已阻止恢复"),
            "CONFIG_REVISION_EXHAUSTED" => error("BACKUP_INPUT_INVALID", "配置版本号已达上限"),
            "BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING" => error("BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING", "旧备份未保存已应用配置快照，且当前配置版本已变化；为避免恢复后无法启动，已阻止恢复"),
            "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID" => error("BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID", "备份中的已应用配置快照校验失败，已阻止恢复"),
            _ => error("RESTORE_PREFLIGHT_BLOCKED", "恢复检测未通过，已禁止恢复"),
        });
    }
    Ok(())
}

pub fn restore(database: &Path, request: RestoreRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "恢复请求版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    validate_revision(request.expected_revision)?;
    crate::server_input::validate_id(&request.backup_id, OP, "backupId")?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        check_restore_preflight(
            session,
            &request.request_id,
            &request.target,
            request.expected_revision,
            &request.backup_id,
        )?;
        let remote = invoke_task(
            session,
            &json!({"schemaVersion":1,"action":"restoreStart","requestId":request.request_id,
            "operationId":request.operation_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id,
            "expectedRevision":request.expected_revision,"backupId":request.backup_id}),
        )?;
        restore_task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn restore_status(database: &Path, request: RestoreTaskStatusRequest) -> CommandResult<Value> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "恢复任务查询版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    crate::server_input::validate_id(&request.operation_id, OP, "operationId")?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let remote = invoke_task(
            session,
            &json!({"schemaVersion":1,"action":"restoreStatus","requestId":request.request_id,
            "operationId":request.operation_id,"hostId":request.target.host_id,"instanceId":request.target.instance_id}),
        )?;
        restore_task_response(
            remote,
            &request.request_id,
            &request.target,
            &request.operation_id,
        )
    })
}

pub fn restore_list(database: &Path, request: TaskListRequest) -> CommandResult<Vec<Value>> {
    if request.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "恢复任务列表版本不受支持"));
    }
    valid_target(&request.target)?;
    valid_request_id(&request.request_id)?;
    let connection = connection(
        &request.request_id,
        &request.target,
        request.expected_record_version,
    );
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(request.target.host_id.as_str()) {
            return Err(error("HOST_IDENTITY_MISMATCH", "备份主机身份不一致"));
        }
        let remote = invoke_task(
            session,
            &json!({"schemaVersion":1,"action":"restoreList","requestId":request.request_id,
            "hostId":request.target.host_id,"instanceId":request.target.instance_id}),
        )?;
        let tasks = remote
            .as_array()
            .ok_or_else(|| error("BACKUP_PROTOCOL_INVALID", "恢复任务列表格式无效"))?;
        if tasks.len() > 100 {
            return Err(error("BACKUP_PROTOCOL_INVALID", "恢复任务列表超过上限"));
        }
        tasks
            .iter()
            .map(|task| {
                let operation_id = task
                    .get("operationId")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                restore_task_response(
                    task.clone(),
                    &request.request_id,
                    &request.target,
                    operation_id,
                )
            })
            .collect()
    })
}

fn restore_task_response(
    remote: Value,
    request_id: &str,
    target: &InstanceTarget,
    operation_id: &str,
) -> CommandResult<Value> {
    let valid_stage = [
        "queued",
        "preflight",
        "stagingVerify",
        "stop",
        "backup",
        "restoreCommit",
        "start",
        "verify",
        "rollback",
        "finalize",
    ];
    if remote.get("schemaVersion") != Some(&Value::from(1))
        || remote.pointer("/target/hostId").and_then(Value::as_str) != Some(target.host_id.as_str())
        || remote.pointer("/target/instanceId").and_then(Value::as_str)
            != Some(target.instance_id.as_str())
        || remote.get("operationId").and_then(Value::as_str) != Some(operation_id)
        || remote.get("taskId").and_then(Value::as_str) != Some(operation_id)
        || remote.get("operation").and_then(Value::as_str) != Some("backupRestore")
        || !["queued", "running", "succeeded", "failed", "interrupted"].contains(
            &remote
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )
        || !valid_stage.contains(
            &remote
                .get("stage")
                .and_then(Value::as_str)
                .unwrap_or_default(),
        )
        || remote.get("sequence").and_then(Value::as_u64).unwrap_or(0) == 0
    {
        return Err(error("BACKUP_PROTOCOL_INVALID", "恢复任务响应格式无效"));
    }
    let mut value = remote;
    value["target"] = serde_json::to_value(target)
        .map_err(|_| error("BACKUP_PROTOCOL_INVALID", "恢复任务目标编码失败"))?;
    Ok(json!({"schemaVersion":1,"requestId":request_id,"target":target,"task":value}))
}

fn same_target(left: &InstanceTarget, right: &InstanceTarget) -> bool {
    left.server_id == right.server_id
        && left.host_id == right.host_id
        && left.instance_id == right.instance_id
}

fn valid_backup(item: &BackupSummary, target: &InstanceTarget, revision: u64) -> bool {
    item.schema_version == 1
        && item.host_id == target.host_id
        && item.instance_id == target.instance_id
        && item.config_revision <= revision
        && ["cold", "quasiHot"].contains(&item.capture_mode.as_str())
        && ["stoppedBeforeRead", "onDiskBestEffort"].contains(&item.capture_semantics.as_str())
        && ["running", "stopped", "degraded", "unknown"]
            .contains(&item.runtime_status_at_capture.as_str())
        && ["pending", "verified", "failed"].contains(&item.verification.as_str())
        && ["restorable", "missingArtifacts", "incompatible", "unknown"]
            .contains(&item.restorability.as_str())
        && match item.checksum.as_deref() {
            Some(checksum) => {
                checksum.len() == 64 && checksum.bytes().all(|byte| byte.is_ascii_hexdigit())
            }
            None => item.verification != "verified",
        }
        && item.created_at.len() <= 64
        && item.reason.len() <= 96
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target() -> InstanceTarget {
        InstanceTarget {
            server_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".into(),
            host_id: "11111111-1111-4111-8111-111111111111".into(),
            instance_id: "22222222-2222-4222-8222-222222222222".into(),
        }
    }

    fn interrupted_task() -> Value {
        json!({
            "schemaVersion": 1,
            "taskId": "33333333-3333-4333-8333-333333333333",
            "operationId": "33333333-3333-4333-8333-333333333333",
            "target": {"hostId":"11111111-1111-4111-8111-111111111111",
                       "instanceId":"22222222-2222-4222-8222-222222222222"},
            "operation":"backupCreate", "status":"interrupted", "stage":"queued",
            "lastCompletedStage":null, "progress":{"kind":"indeterminate"}, "sequence":3,
            "createdAt":"2026-10-21T00:00:00Z", "startedAt":null,
            "finishedAt":"2026-10-21T00:01:00Z", "cancelAllowed":false,
            "observation":"reconciling", "result":null,
            "error":{"schemaVersion":1,"code":"BACKUP_RECOVERY_REQUIRED","category":"backupCreate",
                "operation":"backupCreate","requestId":null,
                "target":{"hostId":"11111111-1111-4111-8111-111111111111",
                         "instanceId":"22222222-2222-4222-8222-222222222222"},
                "message":"备份任务未能完成","retryable":true,"remoteChanged":true,"taskId":"33333333-3333-4333-8333-333333333333",
                "recoverySteps":[],"details":{}},
            "journal":[{"sequence":1,"status":"queued","stage":"queued","at":"2026-10-21T00:00:00Z"}]
        })
    }

    #[test]
    fn interrupted_backup_task_after_cold_worker_exit_is_valid() {
        let request_target = target();
        let value = task_response(
            interrupted_task(),
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            &request_target,
            "33333333-3333-4333-8333-333333333333",
        )
        .expect("interrupted task should preserve a queryable failure snapshot");
        assert_eq!(value["task"]["status"], "interrupted");
        assert_eq!(value["task"]["error"]["code"], "BACKUP_RECOVERY_REQUIRED");
        assert_eq!(
            value["task"]["target"]["serverId"],
            request_target.server_id
        );
    }

    #[test]
    fn backup_task_response_rejects_wrong_operation_and_unlisted_stage() {
        let request_target = target();
        let mut value = interrupted_task();
        value["operationId"] = json!("44444444-4444-4444-8444-444444444444");
        assert!(task_response(
            value,
            "request",
            &request_target,
            "33333333-3333-4333-8333-333333333333"
        )
        .is_err());

        let mut value = interrupted_task();
        value["stage"] = json!("unknown-stage");
        assert!(task_response(
            value,
            "request",
            &request_target,
            "33333333-3333-4333-8333-333333333333"
        )
        .is_err());
    }
}
