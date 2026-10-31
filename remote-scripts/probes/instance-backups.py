"""Read the backup catalog and preview one backup mode for an instance."""
import json
import hashlib
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import uuid

ROOT = Path("/var/lib/famine-hub")
MAX_BYTES = 1024 * 1024
RUNTIME_UID = 1000


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
            metadata["restorability"] = assess_restorability(child, metadata, instance, manifest, host_id, instance_id)
        items.append(metadata)
        if len(items) > 5000:
            reject("BACKUP_READ_LIMIT")
    items.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id,
                       "instanceId": instance_id},
            "restoreAcceptancePassed": restore_acceptance(host_id), "items": items}


def assess_restorability(backup, metadata, instance, manifest, host_id, instance_id):
    archive = backup / "archive.tar.gz"
    try:
        info = archive.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return "incompatible"
        archive_module = {}
        source = globals().get("ARCHIVE_SOURCE")
        if not isinstance(source, str):
            return "unknown"
        exec(compile(source, "<backup-archive>", "exec"), archive_module)
        inspected = archive_module["inspect_archive"](archive, host_id, instance_id, metadata.get("checksum"))
        blocker = world_config_snapshot_blocker(archive, inspected["manifest"], instance, instance_id)
        if blocker:
            return "incompatible" if blocker == "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID" else "missingArtifacts"
        release_id = metadata.get("dstReleaseId")
        active = read_json(instance / "active-release.json", 65536)
        if not valid_uuid(release_id) or release_id != active.get("dstReleaseId"):
            return "incompatible"
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
        release = next((item for item in catalog.get("releases", [])
                        if isinstance(item, dict) and item.get("releaseId") == release_id
                        and item.get("verified") is True), None)
        if not release:
            return "missingArtifacts"
        artifact_blocker = release_artifact_blocker(release_id, release)
        if artifact_blocker:
            return "missingArtifacts"
        environment = dict(os.environ)
        for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
            environment.pop(key, None)
        environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
        image = subprocess.run(["docker", "image", "inspect", release["imageDigest"], "--format", "{{.Id}}"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=10, check=False, env=environment)
        if image.returncode != 0 or image.stdout.strip() != release["imageDigest"]:
            return "missingArtifacts"
        return "restorable"
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
    roots = [instance / "data" / "Master", instance / "config" / "Cluster_1"]
    if manifest.get("cavesEnabled") is True:
        roots.append(instance / "data" / "Caves")
    safe_directory(instance / "config", allow_runtime=True)
    file_count = 0
    size_bytes = 0
    for data_root in roots:
        safe_directory(data_root, allow_runtime=True)
        for current, directories, files in os.walk(data_root, followlinks=False):
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
                if name not in ("cluster_token.txt", "adminlist.txt", "blocklist.txt", "whitelist.txt"):
                    file_count += 1
                    size_bytes += info.st_size
    available = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id,
                       "instanceId": instance_id}, "captureMode": mode,
            "runtimeStatus": status, "runtimeVerified": verified,
            "estimatedArchiveBytes": size_bytes + max(size_bytes // 10, 1024 * 1024),
            "availableBytes": available, "fileCount": file_count,
            "worldDataPresent": file_count > 0,
            "allowed": (status in ("running", "stopped", "degraded") and verified if mode == "cold" else True),
            "warnings": (["QUASI_HOT_FILES_MAY_CHANGE_DURING_CAPTURE"] if mode == "quasiHot" else [])}


def release_artifact_blocker(release_id, release):
    staging = ROOT / "staging" / release_id
    try:
        info = staging.lstat()
    except FileNotFoundError:
        return "DST_RELEASE_UNAVAILABLE"
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        return "DST_RELEASE_ARTIFACTS_UNSAFE"
    try:
        metadata = read_json(staging / "release-metadata.json", 262144)
    except FileNotFoundError:
        return "DST_RELEASE_UNAVAILABLE"
    metadata.pop("schemaVersion", None)
    return None if metadata == release else "DST_RELEASE_ARTIFACTS_MISMATCH"


def world_config_snapshot_blocker(archive_path, archive_manifest, instance, expected_instance_id):
    revision = archive_manifest.get("configRevision")
    archived = {item.get("path") for item in archive_manifest.get("files", []) if isinstance(item, dict)}
    desired_name = "desired/world.json"
    deployed_name = "deployed-world.json"
    has_desired = desired_name in archived
    has_deployed = deployed_name in archived
    if has_desired != has_deployed:
        return "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID"
    try:
        if has_desired:
            with tarfile.open(archive_path, "r:gz") as bundle:
                values = {}
                for relative in (desired_name, deployed_name):
                    member = bundle.getmember("data/" + relative)
                    if not member.isfile() or member.size > 262144:
                        return "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID"
                    stream = bundle.extractfile(member)
                    values[relative] = json.loads(stream.read(262145))
            desired = values[desired_name]
            deployed = values[deployed_name]
        else:
            desired = read_json(instance / desired_name, 262144)
            deployed = read_json(instance / deployed_name, 262144)
            if desired.get("revision") != revision or deployed.get("revision") != revision:
                return "BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING"
        settings = desired.get("settings") if isinstance(desired, dict) else None
        deployed_settings = deployed.get("settings") if isinstance(deployed, dict) else None
        deployed_digest = "sha256:" + hashlib.sha256(json.dumps(deployed_settings, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if (not isinstance(desired, dict) or not isinstance(deployed, dict)
                or desired.get("schemaVersion") != 1 or desired.get("instanceId") != expected_instance_id
                or type(desired.get("revision")) is not int or desired.get("revision") < 0
                or not isinstance(settings, dict)
                or not isinstance(desired.get("secrets"), dict)
                or deployed.get("schemaVersion") != 1 or deployed.get("instanceId") != expected_instance_id
                or type(deployed.get("revision")) is not int or deployed.get("revision") < 0
                or not isinstance(deployed_settings, dict) or deployed.get("digest") != deployed_digest):
            return "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID"
        token_ref = desired["secrets"].get("tokenRef")
        if not valid_uuid(token_ref):
            return "WORLD_TOKEN_MISSING"
        token_path = instance / "desired-secrets" / (token_ref + ".token")
        token_info = token_path.lstat()
        if (not stat.S_ISREG(token_info.st_mode) or token_info.st_nlink != 1
                or token_info.st_size > 8192 or token_info.st_uid not in (0, os.geteuid())
                or token_info.st_mode & 0o077):
            return "WORLD_TOKEN_MISSING"
        return None
    except FileNotFoundError:
        return "WORLD_TOKEN_MISSING" if has_desired else "BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, tarfile.TarError):
        return "BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING" if not has_desired else "BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID"


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
    archive_module = {}
    source = globals().get("ARCHIVE_SOURCE")
    if not isinstance(source, str):
        reject("BACKUP_ARCHIVE_INVALID")
    exec(compile(source, "<backup-archive>", "exec"), archive_module)
    inspected = archive_module["inspect_archive"](archive, host_id, instance_id, metadata["checksum"])
    archive_manifest = inspected["manifest"]
    world_config_blocker = world_config_snapshot_blocker(archive, archive_manifest, instance, instance_id)
    runtime, runtime_verified = runtime_state(instance_id, manifest)
    available = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize
    blockers = []
    if metadata.get("configRevision", -1) > request.get("expectedRevision", -2):
        blockers.append("BACKUP_REVISION_NEWER_THAN_INSTANCE")
    if runtime not in ("running", "stopped", "degraded") or not runtime_verified:
        blockers.append("RUNTIME_STATE_UNVERIFIED")
    if world_config_blocker:
        blockers.append(world_config_blocker)
    if available < archive_info.st_size * 2:
        blockers.append("BACKUP_SPACE_INSUFFICIENT")
    current_release = None
    current_active = {}
    try:
        current_active = read_json(instance / "active-release.json", 65536)
        current_release = current_active.get("dstReleaseId")
    except FileNotFoundError:
        pass
    backup_release = metadata.get("dstReleaseId")
    if backup_release and current_release and backup_release != current_release:
        blockers.append("DST_RELEASE_MISMATCH")
    if backup_release:
        catalog_path = ROOT / "releases" / "catalog.json"
        try:
            catalog = read_json(catalog_path, 1024 * 1024)
            releases = catalog.get("releases", [])
            release = next((item for item in releases if isinstance(item, dict) and item.get("releaseId") == backup_release), None)
            if not release or release.get("verified") is not True:
                blockers.append("DST_RELEASE_UNAVAILABLE")
            else:
                artifact_blocker = release_artifact_blocker(backup_release, release)
                if artifact_blocker:
                    blockers.append(artifact_blocker)
        except FileNotFoundError:
            blockers.append("DST_RELEASE_UNAVAILABLE")
        if archive_manifest.get("dstReleaseId") != backup_release:
            blockers.append("BACKUP_RELEASE_MISMATCH")
    current_size = 0
    protection_roots = [instance / "data" / "Master", instance / "config" / "Cluster_1"]
    if manifest.get("cavesEnabled") is True:
        protection_roots.append(instance / "data" / "Caves")
    for root in protection_roots:
        for current, _, files in os.walk(root, followlinks=False):
            for name in files:
                info = (Path(current) / name).lstat()
                if stat.S_ISREG(info.st_mode) and name not in ("cluster_token.txt", "adminlist.txt", "blocklist.txt", "whitelist.txt"):
                    current_size += info.st_size
    required = archive_info.st_size + int(archive_manifest.get("sizeBytes", 0)) + current_size + max(current_size // 10, 1024 * 1024)
    if available < required:
        blockers.append("RESTORE_SPACE_INSUFFICIENT")
    if manifest.get("configRevision") >= 9007199254740991:
        blockers.append("CONFIG_REVISION_EXHAUSTED")
    protection = {"configRevision": manifest.get("configRevision"), "runtimeStatus": runtime,
                  "activeReleaseId": current_release, "estimatedSizeBytes": current_size}
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": host_id, "instanceId": instance_id},
            "backupId": backup_id, "backupChecksum": metadata["checksum"],
            "backupSizeBytes": archive_info.st_size, "backupConfigRevision": metadata.get("configRevision", 0),
            "backupDstReleaseId": backup_release, "runtimeStatus": runtime,
            "availableBytes": available, "currentProtection": protection,
            "allowed": not blockers, "blockers": blockers}


def delete_backup(request):
    host_id, instance_id, _, _ = identity(request)
    backup_id = request.get("backupId")
    checksum = request.get("expectedChecksum")
    if (not valid_uuid(backup_id) or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", checksum) is None):
        reject("BACKUP_INPUT_INVALID")
    lock_path = ROOT / "locks" / "heavy.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            reject("INSTANCE_BUSY")
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
        else:
            reject("BACKUP_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"ok": False, "code": "BACKUP_STATE_INVALID"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
