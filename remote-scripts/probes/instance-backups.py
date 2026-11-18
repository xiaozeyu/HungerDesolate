"""Read the backup catalog and preview one backup mode for an instance."""
import json
import fcntl
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

ROOT = Path("/var/lib/famine-hub")
MAX_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024 * 1024
IMPORT_STALE_SECONDS = 24 * 60 * 60
RUNTIME_UID = 1000
WORLD_SAVE_ROOTS = {"Master": "config/Cluster_1/Master/save",
                    "Caves": "config/Cluster_1/Caves/save"}
EXCLUDED_NAMES = {"cluster_token.txt", "adminlist.txt", "blocklist.txt", "whitelist.txt",
                  "server_log.txt", "server_chat_log.txt", "client_log.txt"}


class Rejected(Exception):
    pass


def reject(code):
    raise Rejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def read_json(path, limit=MAX_BYTES):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
        reject("BACKUP_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("BACKUP_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("BACKUP_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("BACKUP_STATE_INVALID")


def safe_directory(path, allow_runtime=False):
    info = path.lstat()
    owners = (0, os.geteuid(), RUNTIME_UID) if allow_runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
            or info.st_mode & 0o022):
        reject("BACKUP_PATH_UNSAFE")
    return info


def ensure_directory(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700)
        info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022):
        reject("BACKUP_PATH_UNSAFE")
    return info


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path, value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def acquire_heavy_lock():
    lock_fd = os.open(ROOT / "locks" / "heavy.lock",
                      os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        reject("INSTANCE_BUSY")
    return lock_fd


def inspect_backup_archive(path, host_id, instance_id, checksum=None, caves_enabled=False):
    source = globals().get("ARCHIVE_SOURCE")
    if not isinstance(source, str):
        reject("BACKUP_ARCHIVE_INVALID")
    module = {}
    exec(compile(source, "<backup-archive>", "exec"), module)
    try:
        inspected = module["inspect_archive"](path, host_id, instance_id, checksum)
    except module["BackupRejected"] as failure:
        reject(failure.code)
    return inspected, module["world_save_roots"](inspected["manifest"], caves_enabled is True)


def identity(request):
    host_id, instance_id = request.get("hostId"), request.get("instanceId")
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or not valid_uuid(host_id) or not valid_uuid(instance_id)):
        reject("BACKUP_INPUT_INVALID")
    safe_directory(ROOT)
    safe_directory(ROOT / "instances")
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json")
    if host.get("hostId") != host_id or registry.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    entries = [entry for entry in registry.get("instances", [])
               if isinstance(entry, dict) and entry.get("instanceId") == instance_id]
    if len(entries) != 1 or entries[0].get("lifecycle") not in ("ready", "deleteFailed"):
        reject("INSTANCE_NOT_FOUND")
    instance = ROOT / "instances" / instance_id
    safe_directory(instance)
    manifest = read_json(instance / "manifest.json", 65536)
    if (manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id
            or manifest.get("composeProject") != "fh_" + instance_id.replace("-", "")
            or manifest.get("configRevision") != request.get("expectedRevision")):
        reject("INSTANCE_IDENTITY_MISMATCH")
    return host_id, instance_id, instance, manifest


def runtime_state(instance_id, manifest):
    shards = ["master"] + (["caves"] if manifest.get("cavesEnabled") else [])
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                                 "--filter", "label=com.hungerdesolate.instanceId=" + instance_id],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=10, check=False, env=environment)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown", False
    if result.returncode != 0 or len(result.stdout.splitlines()) != len(shards):
        return "unknown", False
    ids = result.stdout.splitlines()
    if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in ids):
        reject("BACKUP_RUNTIME_INVALID")
    try:
        inspected = subprocess.run(["docker", "container", "inspect", "--format",
                                    "{{json .Config.Labels}}\t{{.State.Status}}", *ids],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, timeout=10, check=False, env=environment)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown", False
    rows = inspected.stdout.splitlines()
    if inspected.returncode != 0 or len(rows) != len(shards):
        return "unknown", False
    states = []
    roles = set()
    for row in rows:
        labels_raw, separator, state = row.partition("\t")
        try:
            labels = json.loads(labels_raw)
        except ValueError:
            reject("BACKUP_RUNTIME_INVALID")
        role = labels.get("com.hungerdesolate.role") if isinstance(labels, dict) else None
        if (not separator or labels.get("com.hungerdesolate.hostId") != manifest["hostId"]
                or labels.get("com.hungerdesolate.instanceId") != instance_id
                or labels.get("com.hungerdesolate.schemaVersion") != "1"
                or role not in shards or role in roles
                or state not in ("created", "running", "paused", "restarting", "exited", "dead")):
            reject("BACKUP_RUNTIME_INVALID")
        roles.add(role)
        states.append(state)
    if all(state in ("created", "exited") for state in states):
        return "stopped", True
    if all(state == "running" for state in states):
        return "running", True
    return "degraded", True


def restore_acceptance(host_id):
    try:
        evidence = read_json(ROOT / "restore-acceptance.json", 65536)
    except FileNotFoundError:
        return False
    return (evidence.get("schemaVersion") == 1
            and evidence.get("hostId") == host_id
            and evidence.get("runtimeVerified") is True
            and valid_uuid(evidence.get("instanceId"))
            and valid_uuid(evidence.get("taskId"))
            and isinstance(evidence.get("verifiedAt"), str))


def list_backups(request):
    host_id, instance_id, instance, manifest = identity(request)
    catalog = ROOT / "backups" / instance_id
    try:
        safe_directory(catalog)
    except FileNotFoundError:
        return {"schemaVersion": 1, "requestId": request["requestId"],
                "target": {"serverId": request["serverId"], "hostId": host_id,
                           "instanceId": instance_id},
                "restoreAcceptancePassed": restore_acceptance(host_id), "items": []}
    try:
        release_catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
        catalog_releases = release_catalog.get("releases", []) if isinstance(release_catalog, dict) else []
    except (FileNotFoundError, Rejected):
        catalog_releases = []
    items = []
    for child in catalog.iterdir():
        if not valid_uuid(child.name):
            reject("BACKUP_STATE_INVALID")
        safe_directory(child)
        metadata_path = child / "metadata.json"
        try:
            metadata_path.lstat()
        except FileNotFoundError:
            reject("BACKUP_METADATA_MISSING")
        metadata = read_json(metadata_path, 262144)
        if (metadata.get("schemaVersion") != 1 or metadata.get("hostId") != host_id
                or metadata.get("instanceId") != instance_id or metadata.get("backupId") != child.name):
            reject("BACKUP_TARGET_MISMATCH")
        metadata = dict(metadata)
        if metadata.get("dstBuild") is None and isinstance(metadata.get("dstReleaseId"), str):
            release = next((item for item in catalog_releases if isinstance(item, dict)
                            and item.get("releaseId") == metadata["dstReleaseId"]), None)
            if release is not None:
                metadata["dstBuild"] = release.get("dstBuild")
        if metadata.get("verification") == "verified":
            metadata["restorability"] = assess_restorability(
                child, metadata, host_id, instance_id, manifest.get("cavesEnabled") is True)
        items.append(metadata)
        if len(items) > 5000:
            reject("BACKUP_READ_LIMIT")
    items.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id,
                       "instanceId": instance_id},
            "restoreAcceptancePassed": restore_acceptance(host_id), "items": items}


def assess_restorability(backup, metadata, host_id, instance_id, caves_enabled):
    archive = backup / "archive.tar.gz"
    try:
        info = archive.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return "incompatible"
        if not isinstance(globals().get("ARCHIVE_SOURCE"), str):
            return "unknown"
        _, roots = inspect_backup_archive(archive, host_id, instance_id,
                                          metadata.get("checksum"), caves_enabled)
        return "restorable" if roots is not None else "missingArtifacts"
    except FileNotFoundError:
        return "missingArtifacts"
    except (OSError, Rejected, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return "incompatible"


def preview(request):
    host_id, instance_id, instance, manifest = identity(request)
    mode = request.get("captureMode")
    if mode not in ("cold", "quasiHot"):
        reject("BACKUP_INPUT_INVALID")
    status, verified = runtime_state(instance_id, manifest)
    shards = ["Master"] + (["Caves"] if manifest.get("cavesEnabled") is True else [])
    file_count = 0
    size_bytes = 0
    world_present = True
    for shard in shards:
        save_root = instance / WORLD_SAVE_ROOTS[shard]
        try:
            safe_directory(save_root, allow_runtime=True)
        except FileNotFoundError:
            world_present = False
            continue
        shard_files = 0
        for current, directories, files in os.walk(save_root, followlinks=False):
            current_path = Path(current)
            for name in directories:
                info = (current_path / name).lstat()
                if (not stat.S_ISDIR(info.st_mode)
                        or info.st_uid not in (0, os.geteuid(), RUNTIME_UID)
                        or info.st_mode & 0o022):
                    reject("BACKUP_SOURCE_UNSAFE")
            for name in files:
                path = current_path / name
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid not in (0, os.geteuid(), RUNTIME_UID)
                        or info.st_mode & 0o022):
                    reject("BACKUP_SOURCE_UNSAFE")
                if name not in EXCLUDED_NAMES:
                    shard_files += 1
                    file_count += 1
                    size_bytes += info.st_size
        if shard_files == 0:
            world_present = False
    available = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize
    warnings = ["QUASI_HOT_FILES_MAY_CHANGE_DURING_CAPTURE"] if mode == "quasiHot" else []
    if not world_present:
        warnings.append("BACKUP_WORLD_DATA_MISSING")
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id,
                       "instanceId": instance_id}, "captureMode": mode,
            "runtimeStatus": status, "runtimeVerified": verified,
            "estimatedArchiveBytes": size_bytes + max(size_bytes // 10, 1024 * 1024),
            "availableBytes": available, "fileCount": file_count,
            "worldDataPresent": world_present,
            "allowed": world_present and (status in ("running", "stopped", "degraded") and verified if mode == "cold" else True),
            "warnings": warnings}


def installed_build(instance):
    active_path = instance / "active-release.json"
    if not active_path.exists():
        return None
    active = read_json(active_path, 65536)
    release_id = active.get("dstReleaseId") if isinstance(active, dict) else None
    if not isinstance(release_id, str):
        return None
    try:
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
    except (FileNotFoundError, Rejected):
        return None
    release = next((item for item in catalog.get("releases", [])
                    if isinstance(item, dict) and item.get("releaseId") == release_id), None)
    return release.get("dstBuild") if release else None


def restore_preflight(request):
    host_id, instance_id, instance, manifest = identity(request)
    backup_id = request.get("backupId")
    if not valid_uuid(backup_id):
        reject("BACKUP_INPUT_INVALID")
    backup = ROOT / "backups" / instance_id / backup_id
    safe_directory(backup)
    metadata = read_json(backup / "metadata.json", 262144)
    if (metadata.get("backupId") != backup_id or metadata.get("hostId") != host_id
            or metadata.get("instanceId") != instance_id or metadata.get("verification") != "verified"
            or not isinstance(metadata.get("checksum"), str) or len(metadata["checksum"]) != 64):
        reject("BACKUP_TARGET_MISMATCH")
    archive = backup / "archive.tar.gz"
    archive_info = archive.lstat()
    if not stat.S_ISREG(archive_info.st_mode) or archive_info.st_nlink != 1:
        reject("BACKUP_ARCHIVE_INVALID")
    inspected, save_roots = inspect_backup_archive(archive, host_id, instance_id,
                                                   metadata["checksum"], manifest.get("cavesEnabled") is True)
    archive_manifest = inspected["manifest"]
    runtime, runtime_verified = runtime_state(instance_id, manifest)
    available = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize
    blockers = []
    if save_roots is None:
        blockers.append("BACKUP_WORLD_DATA_MISSING")
    if runtime not in ("running", "stopped", "degraded") or not runtime_verified:
        blockers.append("RUNTIME_STATE_UNVERIFIED")
    if available < archive_info.st_size * 2:
        blockers.append("BACKUP_SPACE_INSUFFICIENT")
    backup_build = metadata.get("dstBuild")
    current_build = installed_build(instance)
    if (isinstance(backup_build, str) and isinstance(current_build, str)
            and backup_build.isdigit() and current_build.isdigit()
            and int(backup_build) > int(current_build)):
        blockers.append("BACKUP_BUILD_NEWER_THAN_INSTANCE")
    current_size = 0
    protection_roots = [instance / WORLD_SAVE_ROOTS["Master"]]
    if manifest.get("cavesEnabled") is True:
        protection_roots.append(instance / WORLD_SAVE_ROOTS["Caves"])
    for root in protection_roots:
        for current, _, files in os.walk(root, followlinks=False):
            for name in files:
                if name in EXCLUDED_NAMES:
                    continue
                info = (Path(current) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    current_size += info.st_size
    required = archive_info.st_size + int(archive_manifest.get("sizeBytes", 0)) + current_size + max(current_size // 10, 1024 * 1024)
    if available < required:
        blockers.append("RESTORE_SPACE_INSUFFICIENT")
    protection = {"runtimeStatus": runtime, "estimatedSizeBytes": current_size}
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id, "instanceId": instance_id},
            "backupId": backup_id, "backupChecksum": metadata["checksum"],
            "backupSizeBytes": archive_info.st_size, "backupBuild": backup_build,
            "runtimeStatus": runtime, "availableBytes": available, "currentProtection": protection,
            "allowed": not blockers, "blockers": blockers}


def delete_backup(request):
    host_id, instance_id, _, _ = identity(request)
    backup_id = request.get("backupId")
    checksum = request.get("expectedChecksum")
    if (not valid_uuid(backup_id) or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", checksum) is None):
        reject("BACKUP_INPUT_INVALID")
    lock_fd = acquire_heavy_lock()
    try:
        parent = ROOT / "backups" / instance_id
        safe_directory(ROOT / "backups")
        safe_directory(parent)
        backup = parent / backup_id
        safe_directory(backup)
        metadata_path = backup / "metadata.json"
        metadata = read_json(metadata_path, 262144)
        if (metadata.get("backupId") != backup_id or metadata.get("hostId") != host_id
                or metadata.get("instanceId") != instance_id):
            reject("BACKUP_TARGET_MISMATCH")
        if metadata.get("checksum") != checksum:
            reject("BACKUP_CHANGED")
        entries = list(backup.iterdir())
        if {entry.name for entry in entries} != {"metadata.json", "archive.tar.gz"}:
            reject("BACKUP_PATH_UNSAFE")
        for path in entries:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, os.geteuid()):
                reject("BACKUP_PATH_UNSAFE")
        trash = parent / (".deleting-" + backup_id)
        if trash.exists() or trash.is_symlink():
            reject("BACKUP_DELETE_INCOMPLETE")
        os.rename(backup, trash)
        try:
            shutil.rmtree(trash)
        except OSError:
            if not backup.exists():
                os.rename(trash, backup)
            reject("BACKUP_DELETE_FAILED")
        return {"schemaVersion": 1, "requestId": request["requestId"],
                "target": {"serverId": request["serverId"], "hostId": host_id, "instanceId": instance_id},
                "backupId": backup_id, "deleted": True}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def update_note(request):
    host_id, instance_id, _, _ = identity(request)
    backup_id = request.get("backupId")
    note = request.get("note")
    expected = request.get("expectedNote")
    if (not valid_uuid(backup_id) or not isinstance(note, str) or not isinstance(expected, str)
            or len(note) > 96 or len(expected) > 96
            or any(ord(character) < 32 or ord(character) == 127 for character in note)
            or any(ord(character) < 32 or ord(character) == 127 for character in expected)):
        reject("BACKUP_INPUT_INVALID")
    lock_fd = acquire_heavy_lock()
    try:
        backup = ROOT / "backups" / instance_id / backup_id
        safe_directory(backup)
        metadata_path = backup / "metadata.json"
        metadata = read_json(metadata_path, 262144)
        if (metadata.get("backupId") != backup_id or metadata.get("hostId") != host_id
                or metadata.get("instanceId") != instance_id):
            reject("BACKUP_TARGET_MISMATCH")
        if (metadata.get("reason") or "") != expected:
            reject("BACKUP_CHANGED")
        metadata["reason"] = note
        atomic_json(metadata_path, metadata)
        return {"schemaVersion": 1, "requestId": request["requestId"],
                "target": {"serverId": request["serverId"], "hostId": host_id,
                           "instanceId": instance_id},
                "backupId": backup_id, "reason": note}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def import_staging(operation_id):
    return ROOT / "backup-imports" / operation_id


def import_prepare(request):
    host_id, instance_id, _, _ = identity(request)
    operation_id = request.get("operationId")
    size = request.get("sizeBytes")
    if (not valid_uuid(operation_id) or type(size) is not int
            or size <= 0 or size > MAX_ARCHIVE_BYTES):
        reject("BACKUP_INPUT_INVALID")
    ensure_directory(ROOT / "backup-imports")
    root = ROOT / "backup-imports"
    current = time.time()
    for child in root.iterdir():
        if child.name == operation_id or not valid_uuid(child.name):
            continue
        try:
            info = safe_directory(child)
        except (FileNotFoundError, Rejected):
            continue
        if current - info.st_mtime > IMPORT_STALE_SECONDS:
            shutil.rmtree(child, ignore_errors=True)
    staging = import_staging(operation_id)
    try:
        info = staging.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or info.st_mode & 0o022):
            reject("BACKUP_IMPORT_STAGING_INVALID")
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    filesystem = os.statvfs(ROOT)
    available = filesystem.f_bavail * filesystem.f_frsize
    if available < size + 64 * 1024 * 1024:
        shutil.rmtree(staging, ignore_errors=True)
        reject("BACKUP_SPACE_INSUFFICIENT")
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id,
                       "instanceId": instance_id},
            "operationId": operation_id, "stagingPath": str(staging / "archive.tar.gz"),
            "availableBytes": available}


def import_finalize(request):
    host_id, instance_id, _, instance_manifest = identity(request)
    operation_id = request.get("operationId")
    checksum = request.get("sourceChecksum")
    size = request.get("sizeBytes")
    if (not valid_uuid(operation_id) or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
            or type(size) is not int or size <= 0 or size > MAX_ARCHIVE_BYTES):
        reject("BACKUP_INPUT_INVALID")
    staging = import_staging(operation_id)
    safe_directory(staging)
    published = False
    try:
        if {entry.name for entry in staging.iterdir()} != {"archive.tar.gz"}:
            reject("BACKUP_IMPORT_STAGING_INVALID")
        archive = staging / "archive.tar.gz"
        info = archive.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in (0, os.geteuid()) or info.st_size != size):
            reject("BACKUP_ARCHIVE_INVALID")
        inspected, save_roots = inspect_backup_archive(
            archive, host_id, instance_id, checksum, instance_manifest.get("cavesEnabled") is True)
        if save_roots is None:
            reject("BACKUP_WORLD_DATA_MISSING")
        manifest = inspected["manifest"]
        backup_id = manifest.get("backupId")
        capture_mode = manifest.get("captureMode")
        if not valid_uuid(backup_id) or capture_mode not in ("cold", "quasiHot"):
            reject("BACKUP_ARCHIVE_INVALID")
        capture_semantics = manifest.get("captureSemantics")
        if capture_semantics not in ("stoppedBeforeRead", "onDiskBestEffort"):
            capture_semantics = ("stoppedBeforeRead" if capture_mode == "cold"
                                 else "onDiskBestEffort")
        runtime_status = manifest.get("runtimeStatus")
        if runtime_status not in ("running", "stopped", "degraded", "unknown"):
            runtime_status = "unknown"
        created_at = manifest.get("captureCompletedAt")
        if (not isinstance(created_at, str) or not 0 < len(created_at) <= 64
                or any(ord(character) < 32 for character in created_at)):
            created_at = now()
        reason = manifest.get("reason")
        if (not isinstance(reason, str) or len(reason) > 96
                or any(ord(character) < 32 or ord(character) == 127 for character in reason)):
            reason = "本地导入"
        metadata = {"schemaVersion": 1, "backupId": backup_id, "hostId": host_id,
                    "instanceId": instance_id, "createdAt": created_at, "reason": reason,
                    "captureMode": capture_mode, "captureSemantics": capture_semantics,
                    "runtimeStatusAtCapture": runtime_status,
                    "sizeBytes": inspected["archiveSizeBytes"],
                    "checksum": inspected["checksum"], "verification": "verified",
                    "restorability": "restorable", "dstBuild": None,
                    "mods": [], "containsSecrets": False}
        parent = ROOT / "backups" / instance_id
        ensure_directory(ROOT / "backups")
        ensure_directory(parent)
        backup_dir = parent / backup_id
        try:
            backup_dir.lstat()
        except FileNotFoundError:
            pass
        else:
            try:
                existing = read_json(backup_dir / "metadata.json", 262144)
            except (FileNotFoundError, Rejected):
                existing = None
            if (isinstance(existing, dict) and existing.get("checksum") == metadata["checksum"]
                    and existing.get("verification") == "verified"):
                shutil.rmtree(staging, ignore_errors=True)
                return {"schemaVersion": 1, "requestId": request["requestId"],
                        "target": {"serverId": request["serverId"], "hostId": host_id,
                                   "instanceId": instance_id},
                        "operationId": operation_id, "backup": existing}
            reject("BACKUP_ID_EXISTS")
        atomic_json(staging / "metadata.json", metadata)
        if {entry.name for entry in staging.iterdir()} != {"archive.tar.gz", "metadata.json"}:
            reject("BACKUP_IMPORT_STAGING_INVALID")
        os.rename(staging, backup_dir)
        published = True
        try:
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
        return {"schemaVersion": 1, "requestId": request["requestId"],
                "target": {"serverId": request["serverId"], "hostId": host_id,
                           "instanceId": instance_id},
                "operationId": operation_id, "backup": metadata}
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def main(request):
    try:
        action = request.get("action")
        if action == "list":
            data = list_backups(request)
        elif action == "preview":
            data = preview(request)
        elif action == "restorePreflight":
            data = restore_preflight(request)
        elif action == "delete":
            data = delete_backup(request)
        elif action == "updateNote":
            data = update_note(request)
        elif action == "importPrepare":
            data = import_prepare(request)
        elif action == "importFinalize":
            data = import_finalize(request)
        else:
            reject("BACKUP_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"ok": False, "code": "BACKUP_STATE_INVALID"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
