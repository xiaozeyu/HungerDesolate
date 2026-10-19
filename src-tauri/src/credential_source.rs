use crate::{
    dto::{CommandError, CommandResult},
    ssh_dto::CredentialInput,
};
use serde::Deserialize;
use std::{
    fs::File,
    io::Read,
    path::{Path, PathBuf},
};
use zeroize::{Zeroize, Zeroizing};

// Request-only type: file paths never become persisted credential content or response DTOs.
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase", deny_unknown_fields)]
pub enum CredentialSource {
    Password {
        password: String,
    },
    PrivateKey {
        #[serde(rename = "privateKey")]
        private_key: String,
        passphrase: Option<String>,
    },
    PrivateKeyFile {
        path: String,
        passphrase: Option<String>,
    },
}

impl CredentialSource {
    pub fn resolve(&mut self) -> CommandResult<CredentialInput> {
        let credential = match self {
            Self::Password { password } => CredentialInput::Password {
                password: std::mem::take(password),
            },
            Self::PrivateKey {
                private_key,
                passphrase,
            } => CredentialInput::PrivateKey {
                private_key: std::mem::take(private_key),
                passphrase: passphrase.take(),
            },
            Self::PrivateKeyFile { path, passphrase } => CredentialInput::PrivateKey {
                private_key: read_private_key(path)?,
                passphrase: passphrase.take(),
            },
        };
        credential.validate()?;
        Ok(credential)
    }
}
impl Drop for CredentialSource {
    fn drop(&mut self) {
        match self {
            Self::Password { password } => password.zeroize(),
            Self::PrivateKey {
                private_key,
                passphrase,
            } => {
                private_key.zeroize();
                passphrase.zeroize();
            }
            Self::PrivateKeyFile { path, passphrase } => {
                path.zeroize();
                passphrase.zeroize();
            }
        }
    }
}

fn file_error(code: &'static str, message: &'static str) -> Box<CommandError> {
    let mut error = CommandError::new(
        code,
        "security",
        "saveServerCredentials",
        message,
        "选择本机可读取的 PEM/OpenSSH 私钥文件后重试",
    );
    error.details = serde_json::json!({"field":"privateKeyPath"});
    error
}
fn absolute_path(value: &str) -> CommandResult<PathBuf> {
    if value.is_empty() || value.len() > 4096 || value.contains('\0') {
        return Err(file_error(
            "PRIVATE_KEY_PATH_INVALID",
            "请填写本机私钥文件路径",
        ));
    }
    let path = if let Some(relative) = value.strip_prefix("~/") {
        let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
            .ok_or_else(|| {
                file_error(
                    "PRIVATE_KEY_PATH_INVALID",
                    "无法解析用户目录，请填写绝对路径",
                )
            })?;
        PathBuf::from(home).join(relative)
    } else {
        PathBuf::from(value)
    };
    if !path.is_absolute() {
        return Err(file_error(
            "PRIVATE_KEY_PATH_INVALID",
            "私钥路径必须是绝对路径或以 ~/ 开头",
        ));
    }
    Ok(path)
}
fn read_private_key(value: &str) -> CommandResult<String> {
    read_file(&absolute_path(value)?)
}
fn read_file(path: &Path) -> CommandResult<String> {
    // Reject devices, FIFOs and directories before opening; verify the opened handle again.
    let metadata = std::fs::metadata(path)
        .map_err(|_| file_error("PRIVATE_KEY_READ_FAILED", "私钥文件不存在或无法读取"))?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 65536 {
        return Err(file_error(
            "PRIVATE_KEY_FILE_INVALID",
            "私钥必须是非空普通文件，且不超过 64 KiB",
        ));
    }
    let file = File::open(path).map_err(|_| {
        file_error(
            "PRIVATE_KEY_READ_FAILED",
            "无法读取私钥文件，请检查访问权限",
        )
    })?;
    let metadata = file
        .metadata()
        .map_err(|_| file_error("PRIVATE_KEY_READ_FAILED", "无法检查私钥文件"))?;
    if !metadata.is_file() {
        return Err(file_error(
            "PRIVATE_KEY_FILE_INVALID",
            "私钥路径不是普通文件",
        ));
    }
    let mut bytes = Zeroizing::new(Vec::new());
    file.take(65537)
        .read_to_end(&mut bytes)
        .map_err(|_| file_error("PRIVATE_KEY_READ_FAILED", "读取私钥文件失败"))?;
    if bytes.len() > 65536 {
        return Err(file_error(
            "PRIVATE_KEY_FILE_INVALID",
            "私钥文件超过 64 KiB",
        ));
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| {
        file_error(
            "PRIVATE_KEY_FORMAT_INVALID",
            "私钥文件必须是 UTF-8 编码的 PEM/OpenSSH 格式",
        )
    })?;
    let text = text.trim_start_matches('\u{feff}').trim();
    let headers = [
        "RSA PRIVATE KEY",
        "EC PRIVATE KEY",
        "DSA PRIVATE KEY",
        "PRIVATE KEY",
        "ENCRYPTED PRIVATE KEY",
        "OPENSSH PRIVATE KEY",
    ];
    if !headers.iter().any(|kind| {
        text.starts_with(&format!("-----BEGIN {kind}-----"))
            && text.ends_with(&format!("-----END {kind}-----"))
    }) {
        return Err(file_error(
            "PRIVATE_KEY_FORMAT_INVALID",
            "请选择 PEM/OpenSSH 私钥文件，不能使用 .pub 公钥",
        ));
    }
    Ok(text.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn resolves_private_file_without_persisting_its_path() -> Result<(), Box<dyn std::error::Error>>
    {
        let path = std::env::temp_dir().join(format!("hd-key-{}", uuid::Uuid::new_v4()));
        std::fs::write(
            &path,
            "-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n-----END OPENSSH PRIVATE KEY-----\n",
        )?;
        let result = (|| -> Result<(), Box<dyn std::error::Error>> {
            let mut source = CredentialSource::PrivateKeyFile {
                path: path.to_string_lossy().into_owned(),
                passphrase: Some("fixture-passphrase".into()),
            };
            let input = source.resolve()?;
            let encoded = serde_json::to_string(&input)?;
            assert!(!encoded.contains("hd-key-"));
            assert!(!encoded.contains("privateKeyFile"));
            assert!(encoded.contains("privateKey"));
            std::fs::write(&path, "ssh-ed25519 public-key-fixture")?;
            assert!(
                matches!(read_file(&path),Err(error) if error.code=="PRIVATE_KEY_FORMAT_INVALID")
            );
            std::fs::write(&path, vec![b'x'; 65537])?;
            assert!(
                matches!(read_file(&path),Err(error) if error.code=="PRIVATE_KEY_FILE_INVALID")
            );
            Ok(())
        })();
        std::fs::remove_file(&path)?;
        result
    }
    #[test]
    fn rejects_missing_relative_and_directory_paths() {
        assert!(absolute_path("id_ed25519").is_err());
        assert!(read_file(&std::env::temp_dir()).is_err());
        assert!(read_file(&std::env::temp_dir().join(uuid::Uuid::new_v4().to_string())).is_err());
    }
}
