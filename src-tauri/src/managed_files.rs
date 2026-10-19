use crate::dto::{CommandError, CommandResult};
use ssh2::{Session, Sftp};
use std::{
    io::Read,
    path::{Component, Path, PathBuf},
    time::{Duration, Instant},
};

pub(crate) struct ManagedFiles {
    sftp: Sftp,
    root: PathBuf,
    remaining: usize,
    deadline: Instant,
}
fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "identity",
        "listInstances",
        message,
        "检查管理目录、权限与文件归属后重试",
    )
}
impl ManagedFiles {
    pub fn new(session: &Session, root: &Path) -> CommandResult<Self> {
        let sftp = session
            .sftp()
            .map_err(|_| error("REGISTRY_READ_FAILED", "无法建立 SFTP 读取通道"))?;
        let stat = sftp
            .lstat(root)
            .map_err(|_| error("REGISTRY_READ_FAILED", "无法读取管理目录"))?;
        if !stat.is_dir() {
            return Err(error("REMOTE_PATH_INVALID", "管理根不是普通目录"));
        }
        let root = sftp
            .realpath(root)
            .map_err(|_| error("REMOTE_PATH_INVALID", "无法核对管理目录路径"))?;
        Ok(Self {
            sftp,
            root,
            remaining: 4 * 1024 * 1024,
            deadline: Instant::now() + Duration::from_secs(25),
        })
    }
    pub fn read(&mut self, relative: &str, limit: usize) -> CommandResult<Option<Vec<u8>>> {
        if Instant::now() >= self.deadline {
            return Err(error("REGISTRY_READ_TIMEOUT", "注册表读取超时"));
        }
        let parts: Vec<_> = Path::new(relative).components().collect();
        if parts.is_empty()
            || parts
                .iter()
                .any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err(error("REMOTE_PATH_INVALID", "文件路径超出管理目录"));
        }
        let mut path = self.root.clone();
        for (index, part) in parts.iter().enumerate() {
            path.push(part.as_os_str());
            let stat = match self.sftp.lstat(&path) {
                Ok(stat) => stat,
                Err(error) if error.code() == ssh2::ErrorCode::SFTP(2) => return Ok(None),
                Err(_) => {
                    return Err(error(
                        "REGISTRY_PERMISSION_DENIED",
                        "无法读取管理文件，请检查权限",
                    ))
                }
            };
            if index + 1 < parts.len() {
                if !stat.is_dir() {
                    return Err(error(
                        "REMOTE_PATH_INVALID",
                        "实例路径包含符号链接或非目录文件",
                    ));
                }
            } else if !stat.is_file() || stat.size.is_none_or(|size| size > limit as u64) {
                return Err(error("REGISTRY_FILE_INVALID", "管理文件类型或大小不合法"));
            }
        }
        let canonical = self
            .sftp
            .realpath(&path)
            .map_err(|_| error("REMOTE_PATH_INVALID", "无法核对管理文件路径"))?;
        if canonical != path || !canonical.starts_with(&self.root) {
            return Err(error("REMOTE_PATH_INVALID", "管理文件指向受控路径之外"));
        }
        let mut file = self
            .sftp
            .open(&path)
            .map_err(|_| error("REGISTRY_READ_FAILED", "无法打开管理文件"))?;
        let stat = file
            .stat()
            .map_err(|_| error("REGISTRY_READ_FAILED", "无法核对已打开文件"))?;
        if !stat.is_file() || stat.size.is_none_or(|size| size > limit as u64) {
            return Err(error(
                "REGISTRY_FILE_INVALID",
                "已打开管理文件类型或大小不合法",
            ));
        }
        let mut bytes = Vec::new();
        let mut buffer = [0; 4096];
        loop {
            if Instant::now() >= self.deadline {
                return Err(error("REGISTRY_READ_TIMEOUT", "管理文件读取超时"));
            }
            let count = file
                .read(&mut buffer)
                .map_err(|_| error("REGISTRY_READ_FAILED", "管理文件读取中断"))?;
            if count == 0 {
                break;
            }
            if bytes.len() + count > limit || count > self.remaining {
                return Err(error("REGISTRY_READ_LIMIT", "管理文件读取超出容量限制"));
            }
            bytes.extend_from_slice(&buffer[..count]);
            self.remaining -= count;
        }
        Ok(Some(bytes))
    }
}
