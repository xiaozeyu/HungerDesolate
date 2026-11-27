use crate::{
    dto::{CommandError, CommandResult},
    instance_dto::*,
    managed_files::ManagedFiles,
    ssh::with_verified_session,
    ssh_dto::ConnectionRequest,
    ssh_state,
};
use serde::Deserialize;
use ssh2::Session;
use std::{collections::HashSet, path::Path};

const ROOT: &str = "/var/lib/famine-hub";
const MAX_BYTES: usize = 1024 * 1024;
const MAX_INSTANCES: usize = 200;
const MAX_REVISION: u64 = 9_007_199_254_740_991;

// Read only the identity fields we own; forward-compatible metadata is never returned as-is.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegistryFile {
    schema_version: u32,
    host_id: String,
    registry_revision: u64,
    instances: Vec<RegistryEntry>,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RegistryEntry {
    instance_id: String,
    lifecycle: Lifecycle,
    relative_path: Option<String>,
    display_name: Option<String>,
    pending_task_id: Option<String>,
}
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Manifest {
    schema_version: u32,
    host_id: String,
    instance_id: String,
    display_name: String,
    config_revision: u64,
    compose_project: String,
    caves_enabled: bool,
    autostart: bool,
    max_players: Option<u16>,
    game_mode: Option<String>,
    pvp: Option<bool>,
}
struct Discovered {
    registry_revision: u64,
    instances: Vec<(InstanceSummary, Option<ManifestSummary>)>,
}
fn error(code: &'static str, message: &'static str) -> Box<CommandError> {
    CommandError::new(
        code,
        "identity",
        "listInstances",
        message,
        "检查注册表与实例清单或刷新",
    )
}
fn id(value: &str, field: &'static str) -> CommandResult<()> {
    crate::server_input::validate_id(value, "listInstances", field)
        .map_err(|_| error("REGISTRY_IDENTITY_INVALID", "管理文件中的身份标识无效"))
}
fn name(value: &str) -> bool {
    !value.trim().is_empty() && value.chars().count() <= 64 && !value.chars().any(char::is_control)
}
fn parse_registry(bytes: &[u8], host_id: &str) -> CommandResult<RegistryFile> {
    if bytes.len() > MAX_BYTES {
        return Err(error("REGISTRY_FILE_INVALID", "注册表超出大小限制"));
    }
    let registry: RegistryFile = serde_json::from_slice(bytes)
        .map_err(|_| error("REGISTRY_INVALID", "远端注册表格式无效"))?;
    if registry.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "注册表版本不受支持"));
    }
    id(&registry.host_id, "hostId")?;
    if registry.host_id != host_id {
        return Err(error(
            "HOST_IDENTITY_MISMATCH",
            "注册表归属与当前主机不一致",
        ));
    }
    if registry.registry_revision > MAX_REVISION {
        return Err(error("REGISTRY_INVALID", "注册表版本数值超出范围"));
    }
    if registry.instances.len() > MAX_INSTANCES {
        return Err(error(
            "REGISTRY_READ_LIMIT",
            "注册表超过当前读取上限（200 项）",
        ));
    }
    let mut ids = HashSet::new();
    for entry in &registry.instances {
        id(&entry.instance_id, "instanceId")?;
        if !ids.insert(&entry.instance_id) {
            return Err(error(
                "REGISTRY_DUPLICATE_INSTANCE",
                "注册表包含重复专服身份",
            ));
        }
        let expected = if entry.lifecycle == Lifecycle::Retired {
            format!("retired/{}", entry.instance_id)
        } else {
            format!("instances/{}", entry.instance_id)
        };
        if entry
            .relative_path
            .as_ref()
            .is_some_and(|path| path != &expected)
        {
            return Err(error("REMOTE_PATH_INVALID", "注册表路径与专服身份不一致"));
        }
        if entry
            .display_name
            .as_ref()
            .is_some_and(|value| !name(value))
        {
            return Err(error("REGISTRY_INVALID", "注册表专服名称无效"));
        }
        if let Some(task) = &entry.pending_task_id {
            id(task, "pendingTaskId")?;
        }
    }
    Ok(registry)
}
fn parse_manifest(bytes: &[u8], host_id: &str, instance_id: &str) -> CommandResult<Manifest> {
    let manifest: Manifest = serde_json::from_slice(bytes)
        .map_err(|_| error("MANIFEST_INVALID", "专服 manifest 格式无效"))?;
    if manifest.schema_version != 1 {
        return Err(error("SCHEMA_UNSUPPORTED", "专服 manifest 版本不受支持"));
    }
    if manifest.host_id != host_id || manifest.instance_id != instance_id {
        return Err(error(
            "INSTANCE_IDENTITY_MISMATCH",
            "manifest 与注册表专服身份不一致",
        ));
    }
    let project = format!("fh_{}", instance_id.replace('-', ""));
    if manifest.compose_project != project
        || !name(&manifest.display_name)
        || manifest.config_revision > MAX_REVISION
        || manifest
            .max_players
            .is_some_and(|value| value == 0 || value > 128)
        || manifest
            .game_mode
            .as_ref()
            .is_some_and(|value| value.len() > 64 || value.chars().any(char::is_control))
    {
        return Err(error("MANIFEST_INVALID", "专服 manifest 字段不合法"));
    }
    Ok(manifest)
}
fn summarize(
    entry: RegistryEntry,
    host_id: &str,
    manifest: Option<Manifest>,
) -> CommandResult<(InstanceSummary, Option<ManifestSummary>)> {
    let (display_name, max_players, game_mode, caves_enabled, config_revision, detail) =
        if let Some(manifest) = manifest {
            (
                manifest.display_name,
                manifest.max_players,
                manifest.game_mode.clone(),
                Some(manifest.caves_enabled),
                Some(manifest.config_revision),
                Some(ManifestSummary {
                    config_revision: manifest.config_revision,
                    compose_project: manifest.compose_project,
                    caves_enabled: manifest.caves_enabled,
                    autostart: manifest.autostart,
                    game_mode: manifest.game_mode.clone(),
                    pvp: manifest.pvp,
                }),
            )
        } else {
            if !matches!(
                entry.lifecycle,
                Lifecycle::Creating | Lifecycle::CreateFailed
            ) {
                return Err(error("MANIFEST_NOT_FOUND", "已注册专服缺少 manifest"));
            }
            (
                entry
                    .display_name
                    .unwrap_or_else(|| entry.instance_id.clone()),
                None,
                None,
                None,
                None,
                None,
            )
        };
    Ok((
        InstanceSummary {
            host_id: host_id.into(),
            instance_id: entry.instance_id,
            display_name,
            lifecycle: entry.lifecycle,
            runtime_status: RuntimeStatus::Unknown,
            players: None,
            max_players,
            game_mode,
            caves_enabled,
            installed_build: None,
            last_synced_at: None,
            active_task_id: entry.pending_task_id,
            manifest_available: detail.is_some(),
            config_revision,
        },
        detail,
    ))
}
fn discover(
    files: &mut ManagedFiles,
    host_id: &str,
    selected: Option<&str>,
) -> CommandResult<Discovered> {
    let before = files
        .read("registry.json", MAX_BYTES)?
        .ok_or_else(|| error("REGISTRY_NOT_FOUND", "远端尚未创建专服注册表"))?;
    let registry = parse_registry(&before, host_id)?;
    let mut instances = Vec::new();
    for entry in registry.instances {
        if entry.lifecycle == Lifecycle::Retired {
            continue;
        }
        if selected.is_some_and(|id| id != entry.instance_id) {
            continue;
        }
        let bytes = files
            .read(
                &format!("instances/{}/manifest.json", entry.instance_id),
                65536,
            )
            .map_err(|mut error| {
                error.details = serde_json::json!({"instanceId":entry.instance_id});
                error
            })?;
        let manifest = bytes
            .as_ref()
            .map(|bytes| parse_manifest(bytes, host_id, &entry.instance_id))
            .transpose()?;
        instances.push(summarize(entry, host_id, manifest)?);
    }
    let after = files
        .read("registry.json", MAX_BYTES)?
        .ok_or_else(|| error("REGISTRY_CHANGED", "注册表读取期间被移除"))?;
    if before != after {
        return Err(error("REGISTRY_CHANGED", "注册表读取期间发生变化，请刷新"));
    }
    let host = files
        .read("host.json", 65536)?
        .ok_or_else(|| error("HOST_IDENTITY_MISMATCH", "读取期间主机身份文件被移除"))?;
    if crate::ssh::parse_host_identity(&host)? != host_id {
        return Err(error("HOST_IDENTITY_MISMATCH", "读取期间主机身份发生变化"));
    }
    instances.sort_by(|a, b| {
        a.0.display_name
            .cmp(&b.0.display_name)
            .then(a.0.instance_id.cmp(&b.0.instance_id))
    });
    Ok(Discovered {
        registry_revision: registry.registry_revision,
        instances,
    })
}
pub(crate) fn list_at(
    session: &Session,
    root: &Path,
    request: &ConnectionRequest,
    host_id: &str,
) -> CommandResult<InstanceListResponse> {
    let discovered = discover(&mut ManagedFiles::new(session, root)?, host_id, None)?;
    Ok(InstanceListResponse {
        schema_version: 1,
        request_id: request.request_id.clone(),
        server_id: request.server_id.clone(),
        record_version: request.expected_record_version,
        host_id: host_id.into(),
        registry_revision: discovered.registry_revision,
        captured_at_unix_seconds: ssh_state::now()?,
        instances: discovered
            .instances
            .into_iter()
            .map(|item| item.0)
            .collect(),
    })
}
fn read_consistent<T>(mut read: impl FnMut() -> CommandResult<T>) -> CommandResult<T> {
    match read() {
        Err(error) if matches!(error.code, "REGISTRY_CHANGED" | "REVISION_CONFLICT") => read(),
        result => result,
    }
}

pub fn list(database: &Path, request: ConnectionRequest) -> CommandResult<InstanceListResponse> {
    with_verified_session(database, &request, |session, host_id| {
        let host_id =
            host_id.ok_or_else(|| error("HOST_UNINITIALIZED", "远端管理目录尚未初始化"))?;
        read_consistent(|| {
            let mut response = list_at(session, Path::new(ROOT), &request, &host_id)?;
            let ids: Vec<String> = response
                .instances
                .iter()
                .filter(|item| item.manifest_available)
                .map(|item| item.instance_id.clone())
                .collect();
            let snapshots = crate::instance_runtime::inspect_many(
                session,
                &host_id,
                &request.request_id,
                response.registry_revision,
                &ids,
            )?;
            for snapshot in snapshots {
                if let Some(identity) = response
                    .instances
                    .iter_mut()
                    .find(|item| item.instance_id == snapshot.instance_id)
                {
                    apply_runtime(identity, &snapshot);
                }
            }
            Ok(response)
        })
    })
    .map_err(|mut error| {
        error.operation = "listInstances";
        error
    })
}
pub fn overview(database: &Path, request: OverviewRequest) -> CommandResult<OverviewResponse> {
    let connection = request.connection();
    connection.validate("getInstanceOverview")?;
    id(&request.target.instance_id, "instanceId")?;
    id(&request.target.host_id, "hostId")?;
    with_verified_session(database, &connection, |session, host_id| {
        if host_id.as_deref() != Some(&request.target.host_id) {
            return Err(error(
                "HOST_IDENTITY_MISMATCH",
                "专服目标与远端主机身份不一致",
            ));
        }
        read_consistent(|| {
            let discovered = discover(
                &mut ManagedFiles::new(session, Path::new(ROOT))?,
                &request.target.host_id,
                Some(&request.target.instance_id),
            )?;
            let registry_revision = discovered.registry_revision;
            let (mut identity, manifest) = discovered
                .instances
                .into_iter()
                .find(|item| item.0.instance_id == request.target.instance_id)
                .ok_or_else(|| error("INSTANCE_NOT_FOUND", "专服已移除或不存在"))?;
            let mut manifest =
                manifest.ok_or_else(|| error("MANIFEST_NOT_FOUND", "专服创建尚未完成"))?;
            let runtime = crate::instance_runtime::inspect_many(
                session,
                &request.target.host_id,
                &request.request_id,
                registry_revision,
                std::slice::from_ref(&request.target.instance_id),
            )?
            .into_iter()
            .next()
            .ok_or_else(|| error("INSTANCE_RUNTIME_PROTOCOL_INVALID", "运行采集结果缺失"))?;
            if runtime.config_revision != manifest.config_revision {
                return Err(error("REVISION_CONFLICT", "读取期间配置已变化，请刷新"));
            }
            apply_runtime(&mut identity, &runtime);
            manifest.game_mode = runtime.game_mode.clone().or(manifest.game_mode);
            manifest.pvp = runtime.pvp.or(manifest.pvp);
            Ok(OverviewResponse {
                schema_version: 1,
                request_id: request.request_id.clone(),
                target: InstanceTarget {
                    server_id: request.target.server_id.clone(),
                    host_id: request.target.host_id.clone(),
                    instance_id: request.target.instance_id.clone(),
                },
                snapshot_id: uuid::Uuid::new_v4().to_string(),
                captured_at_unix_seconds: ssh_state::now()?,
                record_version: connection.expected_record_version,
                config_revision: manifest.config_revision,
                source: "remote",
                freshness: "fresh",
                data: InstanceOverview {
                    identity,
                    manifest_summary: manifest,
                    verification_level: runtime.verification_level.clone(),
                    blockers: runtime
                        .warnings
                        .iter()
                        .map(|code| runtime_warning(code).to_string())
                        .collect(),
                    desired_vs_deployed: runtime.desired_vs_deployed.clone(),
                    runtime,
                },
            })
        })
    })
    .map_err(|mut error| {
        error.operation = "getInstanceOverview";
        error
    })
}

fn apply_runtime(
    identity: &mut InstanceSummary,
    snapshot: &crate::instance_runtime::RuntimeSnapshot,
) {
    identity.runtime_status = snapshot.status;
    identity.installed_build = snapshot.installed_build.clone();
    identity.players = snapshot.players.or(identity.players);
    identity.max_players = snapshot.max_players.or(identity.max_players);
    identity.game_mode = snapshot.game_mode.clone().or(identity.game_mode.take());
    identity.last_synced_at = Some(snapshot.captured_at.clone());
}

fn runtime_warning(code: &str) -> &'static str {
    match code {
        "PLAYERS_QUERY_UNAVAILABLE" => "无法核实在线玩家人数，请刷新重试",
        "MANIFEST_MISSING" => "实例创建尚未完成",
        "WORLD_CONFIG_UNAVAILABLE" => "世界配置尚未应用或无法读取",
        "DOCKER_UNAVAILABLE" => "无法读取 Docker 当前状态",
        "RESOURCE_IDENTITY_INVALID" => "容器归属与实例身份不一致",
        "CONTAINERS_MISSING" => "缺少预期分片容器",
        "BUILD_MISMATCH" => "分片版本或镜像与活动 Release 不一致",
        "PORT_BINDING_MISMATCH" => "宿主端口发布与实例配置不一致",
        "RUNTIME_PROBE_UNAVAILABLE" => "进程或端口采集未完成，请刷新核对",
        _ => "部分运行信息尚未核实",
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    const HOST: &str = "11111111-1111-4111-8111-111111111111";
    const INSTANCE: &str = "22222222-2222-4222-8222-222222222222";
    const REGISTRY: &[u8] = include_bytes!("../tests/fixtures/registry-v1.json");
    const MANIFEST: &[u8] = include_bytes!("../tests/fixtures/manifest-v1.json");
    fn registry_json() -> serde_json::Value {
        serde_json::from_slice(REGISTRY).expect("valid registry fixture")
    }
    fn manifest_json() -> serde_json::Value {
        serde_json::from_slice(MANIFEST).expect("valid manifest fixture")
    }
    fn bytes(value: serde_json::Value) -> Vec<u8> {
        serde_json::to_vec(&value).expect("fixture serialization")
    }
    #[test]
    fn snapshot_retries_only_consistency_conflicts_once() -> CommandResult<()> {
        for code in ["REGISTRY_CHANGED", "REVISION_CONFLICT"] {
            let mut attempts = 0;
            let revision = read_consistent(|| {
                attempts += 1;
                if attempts == 1 {
                    Err(error(code, "read crossed a registry update"))
                } else {
                    Ok(parse_registry(REGISTRY, HOST)?.registry_revision)
                }
            })?;
            assert_eq!(
                revision,
                registry_json()["registryRevision"].as_u64().unwrap()
            );
            assert_eq!(attempts, 2);
        }
        for (code, expected) in [("REGISTRY_CHANGED", 2), ("HOST_IDENTITY_MISMATCH", 1)] {
            let mut attempts = 0;
            let result = read_consistent(|| {
                attempts += 1;
                Err::<(), _>(error(code, "snapshot rejected"))
            });
            assert_eq!(result.unwrap_err().code, code);
            assert_eq!(attempts, expected);
        }
        Ok(())
    }

    #[test]
    fn parses_identity_metadata_without_promoting_runtime_claims() -> CommandResult<()> {
        let mut value = manifest_json();
        value["runtimeStatus"] = "running".into();
        value["players"] = 4.into();
        value["installedBuild"] = "old-cached-build".into();
        let registry = parse_registry(REGISTRY, HOST)?;
        let manifest = parse_manifest(&bytes(value), HOST, INSTANCE)?;
        let (summary, detail) = summarize(
            registry
                .instances
                .into_iter()
                .next()
                .ok_or_else(|| error("TEST_FIXTURE", "missing fixture"))?,
            HOST,
            Some(manifest),
        )?;
        assert!(matches!(summary.runtime_status, RuntimeStatus::Unknown));
        assert_eq!(summary.players, None);
        assert_eq!(summary.installed_build, None);
        assert_eq!(summary.max_players, Some(6));
        assert!(summary.manifest_available);
        assert_eq!(summary.config_revision, Some(7));
        assert_eq!(detail.map(|detail| detail.config_revision), Some(7));
        Ok(())
    }
    #[test]
    fn rejects_wrong_host_unsupported_version_duplicate_ids_and_unsafe_paths() {
        assert!(
            matches!(parse_registry(REGISTRY,"55555555-5555-4555-8555-555555555555"),Err(error) if error.code=="HOST_IDENTITY_MISMATCH")
        );
        let mut value = registry_json();
        value["schemaVersion"] = 2.into();
        assert!(parse_registry(&bytes(value), HOST).is_err());
        let mut value = registry_json();
        value["instances"][1]["instanceId"] = INSTANCE.into();
        assert!(
            matches!(parse_registry(&bytes(value),HOST),Err(error) if error.code=="REGISTRY_DUPLICATE_INSTANCE")
        );
        for path in [
            "../default",
            "/etc",
            "instances/another",
            "instances/22222222-2222-4222-8222-222222222222/../",
        ] {
            let mut value = registry_json();
            value["instances"][0]["relativePath"] = path.into();
            assert!(
                matches!(parse_registry(&bytes(value),HOST),Err(error) if error.code=="REMOTE_PATH_INVALID")
            );
        }
        let mut value = registry_json();
        value["registryRevision"] = serde_json::json!(MAX_REVISION + 1);
        assert!(parse_registry(&bytes(value), HOST).is_err());
    }
    #[test]
    fn rejects_broken_schema_identity_and_manifest_fields() {
        for (field, value) in [
            ("schemaVersion", serde_json::json!(2)),
            ("hostId", serde_json::json!("other")),
            ("instanceId", serde_json::json!("../default")),
            ("composeProject", serde_json::json!("default")),
            ("displayName", serde_json::json!("\n")),
            ("maxPlayers", serde_json::json!(129)),
        ] {
            let mut json = manifest_json();
            json[field] = value;
            assert!(
                parse_manifest(&bytes(json), HOST, INSTANCE).is_err(),
                "{field}"
            );
        }
        assert!(parse_manifest(b"{}", HOST, INSTANCE).is_err());
        assert!(parse_registry(b"not-json", HOST).is_err());
        let mut json = registry_json();
        json["instances"][0]["lifecycle"] = "almostReady".into();
        assert!(parse_registry(&bytes(json), HOST).is_err());
    }
    #[test]
    fn preserves_incomplete_creation_but_never_hides_missing_ready_manifest() -> CommandResult<()> {
        let registry = parse_registry(REGISTRY, HOST)?;
        let mut entries = registry.instances.into_iter();
        assert!(
            matches!(summarize(entries.next().ok_or_else(||error("TEST_FIXTURE","fixture"))?,HOST,None),Err(error) if error.code=="MANIFEST_NOT_FOUND")
        );
        let (failed, manifest) = summarize(
            entries
                .next()
                .ok_or_else(|| error("TEST_FIXTURE", "fixture"))?,
            HOST,
            None,
        )?;
        assert_eq!(failed.display_name, "未完成专服");
        assert_eq!(failed.max_players, None);
        assert!(!failed.manifest_available);
        assert!(manifest.is_none());
        Ok(())
    }
    #[test]
    fn empty_registry_is_valid_and_capacity_limit_is_explicit() -> CommandResult<()> {
        let mut json = registry_json();
        json["instances"] = serde_json::json!([]);
        assert!(parse_registry(&bytes(json), HOST)?.instances.is_empty());
        let mut json = registry_json();
        let entry = json["instances"][0].clone();
        json["instances"] = serde_json::Value::Array(vec![entry; 201]);
        assert!(
            matches!(parse_registry(&bytes(json),HOST),Err(error) if error.code=="REGISTRY_READ_LIMIT")
        );
        Ok(())
    }

    pub(crate) fn exercise_sftp_fixtures(
        session: &Session,
        root: &Path,
        request: &ConnectionRequest,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let list = list_at(session, root, request, HOST)?;
        assert_eq!(list.instances.len(), 2);
        assert!(list
            .instances
            .iter()
            .all(|item| matches!(item.runtime_status, RuntimeStatus::Unknown)));
        assert!(list
            .instances
            .iter()
            .any(|item| !item.manifest_available && item.lifecycle == Lifecycle::CreateFailed));
        let registry_path = root.join("registry.json");
        std::fs::write(&registry_path, "not-json")?;
        assert!(
            matches!(list_at(session,root,request,HOST),Err(error) if error.code=="REGISTRY_INVALID")
        );
        std::fs::write(&registry_path, REGISTRY)?;
        let manifest_path = root.join(format!("instances/{INSTANCE}/manifest.json"));
        std::fs::remove_file(&manifest_path)?;
        assert!(
            matches!(list_at(session,root,request,HOST),Err(error) if error.code=="MANIFEST_NOT_FOUND")
        );
        let mut wrong = manifest_json();
        wrong["hostId"] = "55555555-5555-4555-8555-555555555555".into();
        std::fs::write(&manifest_path, bytes(wrong))?;
        assert!(
            matches!(list_at(session,root,request,HOST),Err(error) if error.code=="INSTANCE_IDENTITY_MISMATCH")
        );
        std::fs::write(&manifest_path, MANIFEST)?;
        #[cfg(unix)]
        {
            let outside = root
                .parent()
                .ok_or("test root parent missing")?
                .join("outside-manifest.json");
            std::fs::write(&outside, MANIFEST)?;
            std::fs::remove_file(&manifest_path)?;
            std::os::unix::fs::symlink(&outside, &manifest_path)?;
            assert!(
                matches!(list_at(session,root,request,HOST),Err(error) if error.code=="REGISTRY_FILE_INVALID" || error.code=="REMOTE_PATH_INVALID")
            );
            std::fs::remove_file(&manifest_path)?;
            std::fs::write(&manifest_path, MANIFEST)?;
        }
        let mut files = ManagedFiles::new(session, root)?;
        assert!(
            matches!(files.read("../authorized_keys",1024),Err(error) if error.code=="REMOTE_PATH_INVALID")
        );
        assert!(
            matches!(files.read("registry.json",8),Err(error) if error.code=="REGISTRY_FILE_INVALID")
        );
        std::fs::remove_file(&registry_path)?;
        assert!(
            matches!(list_at(session,root,request,HOST),Err(error) if error.code=="REGISTRY_NOT_FOUND")
        );
        std::fs::write(&registry_path, REGISTRY)?;
        let mut empty = registry_json();
        empty["instances"] = serde_json::json!([]);
        std::fs::write(&registry_path, bytes(empty))?;
        assert!(list_at(session, root, request, HOST)?.instances.is_empty());
        std::fs::write(&registry_path, REGISTRY)?;
        let mut host =
            serde_json::json!({"schemaVersion":1,"hostId":"55555555-5555-4555-8555-555555555555"});
        std::fs::write(root.join("host.json"), bytes(host.clone()))?;
        assert!(
            matches!(list_at(session,root,request,HOST),Err(error) if error.code=="HOST_IDENTITY_MISMATCH")
        );
        host["hostId"] = HOST.into();
        std::fs::write(root.join("host.json"), bytes(host))?;
        Ok(())
    }
}
