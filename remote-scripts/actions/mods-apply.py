"""Apply a pinned MOD set through a persistent, rollback-capable worker."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
TASKS = ROOT / "mod-apply-tasks"
MAX_BYTES = 2 * 1024 * 1024
MAX_REVISION = 9007199254740991
WORKSHOP = re.compile(r"^[0-9]{1,20}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
CURRENT_STAGE = "initialize"


class Rejected(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = details if isinstance(details, dict) else {}


class Cancelled(Exception):
    pass


def reject(code, details=None):
    raise Rejected(code, details)


def valid_uuid(value):
    return isinstance(value, str) and UUID.fullmatch(value) is not None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_dir(path, create=False, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid not in owners
            or info.st_mode & 0o022):
        reject("MOD_APPLY_PATH_UNSAFE")
    return info


def read_json(path, limit=MAX_BYTES, missing=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return missing
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("MOD_APPLY_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit):
            reject("MOD_APPLY_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            payload = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(payload) > limit:
        reject("MOD_APPLY_STATE_INVALID")
    try:
        return json.loads(payload)
    except (ValueError, TypeError, UnicodeError):
        reject("MOD_APPLY_STATE_INVALID")


def atomic_bytes(path, payload, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_dir(path.parent, runtime=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, canonical(value))


def env_docker():
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def docker(args, timeout=30, capture=True):
    try:
        result = subprocess.run(["docker", *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout, check=False, env=env_docker())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        reject("MOD_APPLY_DOCKER_FAILED")
    if result.returncode != 0:
        reject("MOD_APPLY_DOCKER_FAILED")
    if capture and len(result.stdout) > 1024 * 1024:
        reject("MOD_APPLY_DOCKER_RESPONSE_INVALID")
    return result.stdout if capture else ""


def compose(instance, manifest, action, *args, timeout=90):
    return docker(["compose", "-p", manifest["composeProject"], "-f", str(instance / "compose.yaml"), action, *args], timeout)


def registry_lock():
    safe_dir(ROOT / "locks")
    fd = os.open(ROOT / "locks" / "registry.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def admit_queue(request):
    task_id = request["operationId"]
    lock_fd = registry_lock()
    try:
        registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
        if (registry.get("schemaVersion") != 1 or registry.get("hostId") != request["hostId"]
                or not isinstance(registry.get("instances"), list)):
            reject("MOD_APPLY_REGISTRY_INVALID")
        if not isinstance(registry.get("heavyQueue"), list):
            registry["heavyQueue"] = []
        entry = next((item for item in registry["instances"]
                      if isinstance(item, dict) and item.get("instanceId") == request["instanceId"]), None)
        if entry is None:
            reject("MOD_APPLY_REGISTRY_INVALID")
        pending = entry.get("pendingTaskId")
        if pending and pending != task_id:
            reject("MOD_APPLY_BUSY")
        entry["pendingTaskId"] = task_id
        queue = [item for item in registry["heavyQueue"] if item.get("taskId") != task_id]
        queue.append({"taskId": task_id, "operation": "modApply", "instanceId": request["instanceId"],
                      "requestedAtUnixSeconds": now(), "ownerToken": str(uuid.uuid4()), "generation": 1})
        registry["heavyQueue"] = queue
        registry["registryRevision"] = int(registry.get("registryRevision", 0)) + 1
        atomic_json(ROOT / "registry.json", registry)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def dequeue(task_id):
    path = ROOT / "registry.json"
    if not path.exists():
        return
    try:
        lock_fd = registry_lock()
    except OSError:
        return
    try:
        registry = read_json(path, 2 * 1024 * 1024)
        queue = registry.get("heavyQueue")
        if not isinstance(queue, list):
            return
        remaining = [item for item in queue if item.get("taskId") != task_id]
        if len(remaining) == len(queue):
            return
        registry["heavyQueue"] = remaining
        registry["registryRevision"] = int(registry.get("registryRevision", 0)) + 1
        atomic_json(path, registry)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def release_queue(task_id):
    path = ROOT / "registry.json"
    if not path.exists():
        return
    try:
        lock_fd = registry_lock()
    except OSError:
        return
    try:
        registry = read_json(path, 2 * 1024 * 1024)
        if not isinstance(registry.get("heavyQueue"), list) or not isinstance(registry.get("instances"), list):
            return
        queue = [item for item in registry["heavyQueue"] if item.get("taskId") != task_id]
        changed = len(queue) != len(registry["heavyQueue"])
        for entry in registry["instances"]:
            if isinstance(entry, dict) and entry.get("pendingTaskId") == task_id:
                entry["pendingTaskId"] = None
                changed = True
        if not changed:
            return
        registry["heavyQueue"] = queue
        registry["registryRevision"] = int(registry.get("registryRevision", 0)) + 1
        atomic_json(path, registry)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def queue_position(task_id):
    try:
        registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    queue = registry.get("heavyQueue")
    if not isinstance(queue, list):
        return None
    ordered = sorted((item for item in queue if isinstance(item, dict)),
                     key=lambda item: item.get("requestedAtUnixSeconds", 0))
    for index, item in enumerate(ordered, 1):
        if item.get("taskId") == task_id:
            return index
    return None


def status_load(task_id):
    directory = TASKS / task_id
    safe_dir(directory)
    state = read_json(directory / "status.json", 262144)
    if (state.get("schemaVersion") != 1 or state.get("taskId") != task_id
            or state.get("operationId") != task_id or state.get("operation") != "modApply"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")):
        reject("MOD_APPLY_TASK_INVALID")
    return state


def set_status(directory, status, stage, *, result=None, error=None, completed=None):
    state = status_load(directory.name)
    state.update({"status": status, "stage": stage, "sequence": state.get("sequence", 0) + 1,
                  "result": result, "error": error, "observation": "live"})
    if completed is not None:
        state["lastCompletedStage"] = completed
    if status == "running" and state.get("startedAt") is None:
        state["startedAt"] = now()
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        state["finishedAt"] = now()
        state["cancelAllowed"] = False
    atomic_json(directory / "status.json", state)
    journal = directory / "journal.jsonl"
    prior = journal.read_bytes() if journal.exists() else b""
    atomic_bytes(journal, prior + canonical({"sequence": state["sequence"], "status": status,
        "stage": stage, "at": now()}) + b"\n")
    state["journal"] = json.loads("[" + ",".join(journal.read_text().splitlines()[-100:]) + "]")
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        release_queue(directory.name)
    return state


def error_payload(code, request, task_id, remote_changed=True, protection_backup=None):
    if code == "MOD_APPLY_ROLLBACK_FAILED":
        message = "MOD 应用失败且自动恢复未完成，请检查专服部署状态"
        steps = ["检查任务阶段、保护备份与容器状态；必要时手动恢复"]
    elif protection_backup is True:
        message = "MOD 应用未能完成；已使用冷保护备份恢复原部署"
        steps = ["检查任务阶段与保护备份，再刷新 MOD 部署状态"]
    elif protection_backup is False:
        message = "MOD 应用未能完成；未创建冷保护备份，请检查专服部署状态"
        steps = ["检查任务阶段与专服部署状态；未创建保护备份时无法自动恢复"]
    else:
        message = "MOD 应用未能完成；请检查任务阶段与专服部署状态"
        steps = ["检查任务阶段与专服部署状态；必要时使用已有备份恢复"]
    return {"schemaVersion": 1, "code": code, "category": "modApply", "operation": "modApply",
            "requestId": None, "target": {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")},
            "message": message, "retryable": True,
            "remoteChanged": remote_changed, "taskId": task_id,
            "recoverySteps": steps, "details": {}}


def verify_identity(request):
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json")
    if host.get("hostId") != request["hostId"] or registry.get("hostId") != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    entries = [entry for entry in registry.get("instances", []) if isinstance(entry, dict)
               and entry.get("instanceId") == request["instanceId"]]
    if len(entries) != 1 or entries[0].get("lifecycle") != "ready":
        reject("INSTANCE_NOT_FOUND")
    instance = ROOT / "instances" / request["instanceId"]
    safe_dir(instance)
    manifest = read_json(instance / "manifest.json", 65536)
    if (manifest.get("hostId") != request["hostId"] or manifest.get("instanceId") != request["instanceId"]
            or manifest.get("configRevision") != request["expectedRevision"]):
        reject("REVISION_CONFLICT")
    return instance, manifest


def validate_desired(instance, request):
    desired = read_json(instance / "desired" / "mods.json", 2 * 1024 * 1024)
    if (desired.get("schemaVersion") != 1 or desired.get("instanceId") != request["instanceId"]
            or type(desired.get("revision")) is not int or desired["revision"] > request["expectedRevision"]
            or not isinstance(desired.get("mods"), list) or len(desired["mods"]) > 200):
        reject("MOD_STATE_INVALID")
    mods = []
    seen = set()
    for item in desired["mods"]:
        workshop_id = str(item.get("workshopId", "")) if isinstance(item, dict) else ""
        if (not WORKSHOP.fullmatch(workshop_id) or workshop_id in seen or type(item.get("enabled")) is not bool
                or not isinstance(item.get("configurationOptions", {}), dict)):
            reject("MOD_STATE_INVALID")
        seen.add(workshop_id)
        artifact = item.get("artifactDigest")
        if artifact is not None and (not isinstance(artifact, str) or re.fullmatch(r"[0-9a-f]{64}", artifact) is None):
            reject("MOD_STATE_INVALID")
        if item["enabled"]:
            if not artifact:
                reject("MOD_ARTIFACT_REQUIRED")
            prepared = ROOT / "mods" / "prepared" / workshop_id / artifact
            safe_dir(prepared)
            metadata = read_json(prepared / "manifest.json", 4 * 1024 * 1024)
            if metadata.get("workshopId") != workshop_id or metadata.get("contentDigest") != artifact:
                reject("MOD_ARTIFACT_INVALID")
            if not (prepared / "content" / "modinfo.lua").is_file():
                reject("MOD_ARTIFACT_INVALID")
        mods.append({"workshopId": workshop_id, "enabled": item["enabled"],
                     "configurationOptions": item.get("configurationOptions", {}), "artifactDigest": artifact})
    mods.sort(key=lambda item: item["workshopId"])
    return desired, mods, digest(mods)


def lua_string(value):
    pieces = []
    for character in value:
        code = ord(character)
        if character == "\\": pieces.append("\\\\")
        elif character == '"': pieces.append('\\"')
        elif character == "\n": pieces.append("\\n")
        elif character == "\r": pieces.append("\\r")
        elif character == "\t": pieces.append("\\t")
        elif code < 32 or code == 127: pieces.append("\\%03d" % code)
        else: pieces.append(character)
    return '"' + "".join(pieces) + '"'


def lua_value(value, depth=0):
    if depth > 12: reject("MOD_CONFIG_UNSUPPORTED")
    if value is None: reject("MOD_CONFIG_UNSUPPORTED")
    if type(value) is bool: return "true" if value else "false"
    if type(value) is int: return str(value)
    if isinstance(value, float):
        if not __import__("math").isfinite(value): reject("MOD_CONFIG_UNSUPPORTED")
        return repr(value)
    if isinstance(value, str): return lua_string(value)
    if isinstance(value, list): return "{" + ",".join(lua_value(item, depth + 1) for item in value) + "}"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value): reject("MOD_CONFIG_UNSUPPORTED")
        return "{" + ",".join("[" + lua_string(key) + "]=" + lua_value(value[key], depth + 1)
                              for key in sorted(value)) + "}"
    reject("MOD_CONFIG_UNSUPPORTED")


def modoverrides(mods):
    enabled = [item for item in mods if item["enabled"]]
    rows = []
    for item in enabled:
        rows.append("  [" + lua_string("workshop-" + item["workshopId"]) + "]={enabled=true,configuration_options="
                    + lua_value(item["configurationOptions"]) + "}")
    return ("return {\n" + ",\n".join(rows) + ("\n" if rows else "") + "}\n").encode()


def setup_script(mods):
    return "".join('ServerModSetup("' + item["workshopId"] + '")\n' for item in mods if item["enabled"]).encode()


def container_roles(instance_id, manifest):
    roles = ["master"] + (["caves"] if manifest.get("cavesEnabled") else [])
    ids = docker(["container", "ls", "--all", "--quiet", "--no-trunc",
                  "--filter", "label=com.hungerdesolate.instanceId=" + instance_id]).splitlines()
    if len(ids) != len(roles) or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in ids):
        reject("MOD_APPLY_RUNTIME_UNKNOWN")
    output = docker(["container", "inspect", "--format", "{{json .Config.Labels}}\t{{.State.Status}}", *ids])
    states = {}
    for row in output.splitlines():
        labels_raw, separator, state = row.partition("\t")
        try: labels = json.loads(labels_raw)
        except ValueError: reject("MOD_APPLY_RUNTIME_UNKNOWN")
        role = labels.get("com.hungerdesolate.role") if isinstance(labels, dict) else None
        if (not separator or labels.get("com.hungerdesolate.hostId") != manifest["hostId"]
                or labels.get("com.hungerdesolate.instanceId") != instance_id or role not in roles
                or role in states or state not in ("created", "running", "exited", "dead", "paused", "restarting")):
            reject("MOD_APPLY_RUNTIME_UNKNOWN")
        states[role] = state
    if set(states) != set(roles): reject("MOD_APPLY_RUNTIME_UNKNOWN")
    if all(value == "running" for value in states.values()): return roles, "running"
    if all(value in ("created", "exited") for value in states.values()): return roles, "stopped"
    reject("MOD_APPLY_RUNTIME_DEGRADED")


def load_task_sources(directory):
    archive_ns = {"__name__": "backup_archive"}
    exec(compile((directory / "archive-source.py").read_text(), "<mod-apply-archive>", "exec"), archive_ns)
    release_ns = {"__name__": "instance_release"}
    exec(compile((directory / "release-source.py").read_text(), "<mod-apply-release>", "exec"), release_ns)
    runtime_ns = {"__name__": "instance_runtime_start"}
    exec(compile((directory / "runtime-source.py").read_text(), "<mod-apply-runtime>", "exec"), runtime_ns)
    restore_ns = {"__name__": "restore_task"}
    exec(compile((directory / "restore-source.py").read_text(), "<mod-apply-restore>", "exec"), restore_ns)
    download_ns = {"__name__": "mods_download"}
    exec(compile((directory / "download-source.py").read_text(), "<mod-apply-download>", "exec"), download_ns)
    return archive_ns, release_ns, runtime_ns, restore_ns, download_ns


def build_mod_release(instance, mods, mod_release_id, task_id, download_ns):
    stage_root = instance / "staging" / "mod-apply" / task_id / "release"
    safe_dir(stage_root.parent, create=True)
    for shard in ("Master", "Caves") if (instance / "data" / "Caves").exists() else ("Master",):
        shard_root = stage_root / shard
        workshop_root = shard_root / "steamapps" / "workshop" / "content" / "322330"
        workshop_root.mkdir(mode=0o700, parents=True)
        for item in mods:
            if not item["enabled"]: continue
            source = ROOT / "mods" / "prepared" / item["workshopId"] / item["artifactDigest"] / "content"
            _, _, actual_digest = download_ns["content_files"](source)
            if actual_digest != item["artifactDigest"]:
                reject("MOD_ARTIFACT_INVALID")
            target = workshop_root / item["workshopId"]
            shutil.copytree(source, target, symlinks=False)
        for current, directories, files in os.walk(shard_root):
            os.chown(current, 1000, 1000); os.chmod(current, 0o755)
            for filename in files:
                path = Path(current) / filename
                if path.is_symlink() or not path.is_file(): reject("MOD_ARTIFACT_INVALID")
                os.chown(path, 1000, 1000); os.chmod(path, 0o644)
        setup = setup_script(mods)
        atomic_bytes(shard_root / "dedicated_server_mods_setup.lua", setup, 0o644)
        os.chown(shard_root / "dedicated_server_mods_setup.lua", 1000, 1000)
    return stage_root


def verify_mod_release(release_root, mods, roles, download_ns):
    enabled = {item["workshopId"]: item for item in mods if item["enabled"]}
    expected_setup = setup_script(mods)
    for shard in roles:
        shard_root = release_root / shard.title()
        workshop_root = shard_root / "steamapps" / "workshop" / "content" / "322330"
        safe_dir(shard_root, runtime=True)
        safe_dir(workshop_root, runtime=True)
        actual_ids = {entry.name for entry in workshop_root.iterdir()}
        if actual_ids != set(enabled):
            reject("MOD_RELEASE_CONTENT_INVALID")
        for workshop_id, item in enabled.items():
            content = workshop_root / workshop_id
            safe_dir(content, runtime=True)
            _, _, actual_digest = download_ns["content_files"](content)
            if actual_digest != item["artifactDigest"]:
                reject("MOD_RELEASE_CONTENT_INVALID")
        setup_path = release_root / "dedicated_server_mods_setup.lua"
        info = setup_path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024
                or setup_path.read_bytes() != expected_setup):
            reject("MOD_RELEASE_CONTENT_INVALID")


def backup_current(instance, manifest, request, roles, archive_ns, backup_id):
    backup_root = ROOT / "backups" / request["instanceId"]
    safe_dir(ROOT / "backups", create=True); safe_dir(backup_root, create=True)
    backup = backup_root / backup_id
    backup.mkdir(mode=0o700)
    active = read_json(instance / "active-release.json", 65536, missing={})
    roots = ["config/Cluster_1", "desired/mods.json",
             "active-release.json", "manifest.json", "compose.yaml"]
    if (instance / "deployed-mods.json").exists(): roots.append("deployed-mods.json")
    if active.get("modsReleaseId"):
        roots.append("mods/releases/" + active["modsReleaseId"])
    roots.extend("data/" + role.title() for role in roles)
    world_desired = instance / "desired" / "world.json"
    if world_desired.exists(): roots.append("desired/world.json")
    if (instance / "deployed-world.json").exists(): roots.append("deployed-world.json")
    identity = {"schemaVersion": 1, "hostId": request["hostId"], "instanceId": request["instanceId"],
        "backupId": backup_id, "captureMode": "cold", "runtimeStatus": "stopped", "runtimeVerified": True,
        "configRevision": request["expectedRevision"], "dstReleaseId": active.get("dstReleaseId"), "reason": "MOD 应用前保护点"}
    created = archive_ns["create_archive"](instance, backup / "archive.tar.gz", identity, roots)
    archive_ns["inspect_archive"](backup / "archive.tar.gz", request["hostId"], request["instanceId"], created["checksum"])
    catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
    release = next((item for item in catalog.get("releases", []) if isinstance(item, dict)
                    and item.get("releaseId") == active.get("dstReleaseId") and item.get("verified") is True), None)
    atomic_json(backup / "metadata.json", {"schemaVersion": 1, "backupId": backup_id,
        "hostId": request["hostId"], "instanceId": request["instanceId"], "createdAt": now(),
        "reason": "MOD 应用前保护点", "captureMode": "cold", "captureSemantics": "stoppedBeforeRead",
        "runtimeStatusAtCapture": "stopped", "sizeBytes": created["archiveSizeBytes"],
        "checksum": created["checksum"], "verification": "verified",
        "restorability": "restorable" if release else "incompatible",
        "dstBuild": release.get("dstBuild") if release else None,
        "dstReleaseId": active.get("dstReleaseId"), "modsReleaseId": active.get("modsReleaseId"),
        "configRevision": request["expectedRevision"], "containsSecrets": False})
    return backup_id


def restore_paths_from_backup(instance, archive_ns, restore_ns, backup_path, request, backup_id, roles,
                               remove_mod_release_id=None):
    metadata = read_json(backup_path / "metadata.json", 262144)
    stage = instance / "staging" / "mod-apply" / request["operationId"] / "rollback"
    safe_dir(stage.parent, create=True)
    archive_ns["extract_to_staging"](backup_path / "archive.tar.gz", stage, request["hostId"], request["instanceId"], metadata["checksum"])
    relative = ["desired/mods.json", "deployed-mods.json", "active-release.json", "manifest.json", "compose.yaml"]
    relative.extend("config/Cluster_1/" + role.title() + "/modoverrides.lua" for role in roles)
    mods_release_id = metadata.get("modsReleaseId")
    if isinstance(mods_release_id, str) and UUID.fullmatch(mods_release_id):
        relative.append("mods/releases/" + mods_release_id)
    if isinstance(remove_mod_release_id, str) and UUID.fullmatch(remove_mod_release_id):
        relative.append("mods/releases/" + remove_mod_release_id)
    optional_snapshots = {"deployed-mods.json"}
    for name in relative:
        source = stage / name
        destination = instance / name
        if not source.exists():
            if name in optional_snapshots and (destination.exists() or destination.is_symlink()):
                if destination.is_dir() and not destination.is_symlink(): reject("MOD_APPLY_ROLLBACK_FAILED")
                destination.unlink()
            continue
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink(): shutil.rmtree(destination)
            else: destination.unlink()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if source.is_dir(): shutil.copytree(source, destination, symlinks=False)
        else: shutil.copy2(source, destination)
    restore_ns["assign_runtime_ownership"](instance / "config" / "Cluster_1")
    for role in roles:
        config = instance / "config" / "Cluster_1" / role.title() / "modoverrides.lua"
        if config.exists():
            os.chmod(config, 0o600)
    if isinstance(mods_release_id, str) and UUID.fullmatch(mods_release_id):
        release_root = instance / "mods" / "releases" / mods_release_id
        if release_root.exists():
            restore_ns["assign_runtime_ownership"](release_root)
            os.chown(release_root, 0, 0)
            os.chmod(release_root, 0o755)
            release_manifest = release_root / "manifest.json"
            os.chown(release_manifest, 0, 0)
            os.chmod(release_manifest, 0o600)
    return stage

def acquire_lock(path, wait=False, cancel_path=None):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
        os.close(fd); reject("MOD_APPLY_LOCK_INVALID")
    while True:
        if cancel_path is not None and cancel_path.exists():
            os.close(fd)
            raise Cancelled()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if not wait:
                os.close(fd)
                reject("MOD_APPLY_BUSY")
            time.sleep(0.5)


def controller_active():
    result = subprocess.run(["systemctl", "is-active", "--quiet", "famine-queue-controller"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=5, check=False)
    return result.returncode == 0


CONTROLLER_TASKS = {
    "modApply": ROOT / "mod-apply-tasks",
    "backupCreate": ROOT / "backup-tasks",
    "backupRestore": ROOT / "restore-tasks",
    "hostModPrepare": ROOT / "host-mod-tasks",
}


def controller_main():
    safe_dir(ROOT / "locks")
    lock_fd = os.open(ROOT / "locks" / "queue-controller.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        return
    try:
        idle = 0
        while True:
            try:
                registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                time.sleep(1)
                continue
            queue = registry.get("heavyQueue")
            if not isinstance(queue, list):
                return
            ordered = sorted((item for item in queue if isinstance(item, dict)),
                             key=lambda item: item.get("requestedAtUnixSeconds", 0))
            if not ordered:
                idle += 1
                if idle >= 3:
                    return
                time.sleep(1)
                continue
            idle = 0
            head = ordered[0]
            task_id = head.get("taskId")
            task_dir = CONTROLLER_TASKS.get(head.get("operation"))
            if not valid_uuid(task_id):
                return
            if task_dir is None:
                time.sleep(1)
                continue
            worker = task_dir / task_id / "worker.py"
            if not worker.exists():
                dequeue(task_id)
                release_queue(task_id)
                continue
            try:
                state = read_json(task_dir / task_id / "status.json", 262144)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                dequeue(task_id)
                release_queue(task_id)
                continue
            if state.get("status") in ("succeeded", "failed", "cancelled", "interrupted"):
                dequeue(task_id)
                release_queue(task_id)
                continue
            request = read_json(task_dir / task_id / "request.json")
            instance_id = request.get("instanceId")
            instance = ROOT / "instances" / instance_id if isinstance(instance_id, str) else None
            heavy = maintenance = instance_lock = None
            try:
                heavy = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                fcntl.flock(heavy, fcntl.LOCK_EX)
                if instance is not None:
                    safe_dir(instance / "locks")
                    maintenance = os.open(instance / "locks" / "world-config.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    fcntl.flock(maintenance, fcntl.LOCK_EX)
                    instance_lock = os.open(instance / "locks" / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    fcntl.flock(instance_lock, fcntl.LOCK_EX)
                env = dict(os.environ)
                env["FAMINE_CONTROLLER_LEASE"] = task_id
                subprocess.run([sys.executable, "-I", str(worker), "worker", task_id],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env, check=False)
            finally:
                for fd in (instance_lock, maintenance, heavy):
                    if fd is not None:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)
            try:
                final = read_json(task_dir / task_id / "status.json", 262144)
                if final.get("status") in ("succeeded", "failed", "cancelled", "interrupted"):
                    release_queue(task_id)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def worker(task_id):
    global CURRENT_STAGE
    directory = TASKS / task_id
    state = status_load(task_id)
    if state["status"] in ("succeeded", "failed", "interrupted"): return state
    request = read_json(directory / "request.json")
    instance, manifest = verify_identity(request)
    archive_ns, release_ns, runtime_ns, restore_ns, download_ns = load_task_sources(directory)
    heavy = maintenance = mods_lock = None
    protection_id = str(uuid.uuid4())
    backup_id = None
    stopped = False
    published = False
    stage_root = instance / "staging" / "mod-apply" / task_id
    mod_release_id = str(uuid.uuid4())
    cancel_path = directory / "cancel.request"
    lease = os.environ.get("FAMINE_CONTROLLER_LEASE") == task_id
    try:
        CURRENT_STAGE = "preflight"
        if not lease:
            heavy = acquire_lock(ROOT / "locks" / "heavy.lock", wait=True, cancel_path=cancel_path)
            dequeue(task_id)
            maintenance = acquire_lock(instance / "locks" / "world-config.lock", wait=True, cancel_path=cancel_path)
            mods_lock = acquire_lock(instance / "locks" / "instance.lock", wait=True, cancel_path=cancel_path)
        if cancel_path.exists(): raise Cancelled()
        instance, manifest = verify_identity(request)
        desired, mods, desired_digest = validate_desired(instance, request)
        if desired_digest != request["desiredDigest"]: reject("REVISION_CONFLICT")
        roles, runtime_before = container_roles(request["instanceId"], manifest)
        original_active = read_json(instance / "active-release.json", 65536)
        transaction = {"schemaVersion": 1, "taskId": task_id, "runtimeBefore": runtime_before,
            "manifest": manifest, "activeRelease": original_active,
            "compose": (instance / "compose.yaml").read_text(encoding="utf-8"),
            "protectionBackupId": None, "newModsReleaseId": mod_release_id}
        atomic_json(directory / "transaction.json", transaction)
        set_status(directory, "running", "preflight")
        CURRENT_STAGE = "prepare"
        stage_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        release_stage = build_mod_release(instance, mods, mod_release_id, task_id, download_ns)
        release_destination = instance / "mods" / "releases" / mod_release_id
        if release_destination.exists(): reject("MOD_RELEASE_CONFLICT")
        setup_release = release_stage.parent / "release-publish"
        setup_release.mkdir(mode=0o700)
        for shard in roles:
            shutil.copytree(release_stage / shard.title(), setup_release / shard.title(), symlinks=False)
        setup = setup_script(mods)
        atomic_bytes(setup_release / "dedicated_server_mods_setup.lua", setup, 0o644)
        for current, dirs, files in os.walk(setup_release):
            os.chown(current, 1000, 1000); os.chmod(current, 0o755)
            for filename in files:
                path = Path(current) / filename
                os.chown(path, 1000, 1000); os.chmod(path, 0o644)
        os.chown(setup_release, 0, 0); os.chmod(setup_release, 0o755)
        staged_config = stage_root / "config" / "Cluster_1"
        shutil.copytree(instance / "config" / "Cluster_1", staged_config, symlinks=False)
        for shard in roles:
            shard_config = staged_config / shard.title()
            safe_dir(shard_config, runtime=True)
            atomic_bytes(shard_config / "modoverrides.lua", modoverrides(mods), 0o600)
            os.chown(shard_config / "modoverrides.lua", 1000, 1000)
        release_metadata = {"schemaVersion": 1, "modsReleaseId": mod_release_id,
            "hostId": request["hostId"], "instanceId": request["instanceId"],
            "desiredDigest": desired_digest,
            "mods": [{"workshopId": item["workshopId"], "enabled": item["enabled"],
                      "artifactDigest": item["artifactDigest"]} for item in mods],
            "createdAt": now(),
            "sourceDigests": sorted({item["artifactDigest"] for item in mods if item["enabled"]})}
        atomic_json(setup_release / "manifest.json", release_metadata)
        verify_mod_release(setup_release, mods, roles, download_ns)

        if cancel_path.exists(): raise Cancelled()
        CURRENT_STAGE = "stop"
        if runtime_before == "running":
            compose(instance, manifest, "stop", *roles)
            stopped = True
            _, stopped_status = container_roles(request["instanceId"], manifest)
            if stopped_status != "stopped": reject("MOD_APPLY_STOP_FAILED")
        completed_stage = "stop" if runtime_before == "running" else "prepare"
        if request["createProtectionBackup"]:
            CURRENT_STAGE = "backup"
            backup_id = backup_current(instance, manifest, request, roles, archive_ns, protection_id)
            transaction["protectionBackupId"] = backup_id
            atomic_json(directory / "transaction.json", transaction)
            set_status(directory, "running", "backup", completed=completed_stage,
                result={"protectionBackupId": backup_id})
        else:
            set_status(directory, "running", "publish", completed=completed_stage,
                result={"protectionBackupId": None})

        CURRENT_STAGE = "publish"
        release_destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.rename(setup_release, release_destination)
        published = True
        for shard in roles:
            destination = instance / "config" / "Cluster_1" / shard.title() / "modoverrides.lua"
            candidate = staged_config / shard.title() / "modoverrides.lua"
            os.replace(candidate, destination)
            os.chown(destination, 1000, 1000); os.chmod(destination, 0o600)
        active_path = instance / "active-release.json"
        active = read_json(active_path, 65536)
        new_active = dict(active)
        new_active.update({"modsReleaseId": mod_release_id, "modsDigest": desired_digest,
                           "modsAppliedRevision": request["expectedRevision"], "verifiedAt": now()})
        new_manifest = dict(manifest)
        new_manifest["modsReleaseId"] = mod_release_id
        new_manifest.setdefault("desiredDigests", {})["mods"] = desired_digest
        new_manifest["modsAppliedRevision"] = request["expectedRevision"]
        deployed = {"schemaVersion": 1, "instanceId": request["instanceId"],
            "revision": request["expectedRevision"], "modsDigest": desired_digest,
            "mods": mods, "modsReleaseId": mod_release_id, "appliedAt": now()}
        atomic_json(instance / "active-release.json", new_active)
        atomic_json(instance / "manifest.json", new_manifest)
        atomic_json(instance / "deployed-mods.json", deployed)
        compose_path = instance / "compose.yaml"
        old_compose = compose_path.read_bytes()
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
        dst_release_id = new_active.get("dstReleaseId")
        matches = [item for item in catalog.get("releases", []) if isinstance(item, dict)
                   and item.get("releaseId") == dst_release_id and item.get("verified") is True]
        if len(matches) != 1: reject("MOD_RELEASE_INVALID")
        new_compose = release_ns["render_compose"](new_manifest, dict(matches[0], schemaVersion=1), str(instance))
        atomic_bytes(compose_path, new_compose.encode(), 0o600)
        compose(instance, new_manifest, "create", "--force-recreate", *roles)
        CURRENT_STAGE = "verify"
        runtime_verified = False
        if runtime_before == "running":
            compose(instance, new_manifest, "start", *roles)
            runtime_verified = restore_ns["wait_for_runtime_processes"](
                instance, request["instanceId"], new_manifest, roles)
            if not runtime_verified: reject("MOD_APPLY_VERIFY_FAILED")
        result = {"protectionBackupId": backup_id, "modsReleaseId": mod_release_id,
            "desiredDigest": desired_digest, "configRevision": request["expectedRevision"],
            "runtimeVerified": runtime_verified, "runningShards": roles if runtime_verified else [],
            "applied": True, "publicConnectivityVerified": False}
        shutil.rmtree(stage_root, ignore_errors=True)
        return set_status(directory, "succeeded", "finalize", completed="verify", result=result)
    except Cancelled:
        return set_status(directory, "cancelled", "cancelled")
    except Exception as failure:
        code = getattr(failure, "code", "MOD_APPLY_FAILED")
        rollback_ok = True
        if backup_id is not None:
            try:
                CURRENT_STAGE = "rollback"
                if runtime_before == "running":
                    compose(instance, manifest, "stop", *roles)
                backup_path = ROOT / "backups" / request["instanceId"] / backup_id
                restore_stage = restore_paths_from_backup(instance, archive_ns, restore_ns, backup_path, request,
                    backup_id, roles, remove_mod_release_id=mod_release_id)
                restored_active = read_json(restore_stage / "active-release.json", 65536)
                if restored_active.get("modsReleaseId") != mod_release_id:
                    orphan_release = instance / "mods" / "releases" / mod_release_id
                    if orphan_release.exists(): shutil.rmtree(orphan_release)
                previous_manifest = read_json(restore_stage / "manifest.json", 65536)
                compose(instance, previous_manifest, "create", "--force-recreate", *roles)
                if runtime_before == "running":
                    compose(instance, previous_manifest, "start", *roles)
                    if not restore_ns["wait_for_runtime_processes"](instance, request["instanceId"], previous_manifest, roles):
                        rollback_ok = False
                shutil.rmtree(restore_stage, ignore_errors=True)
            except Exception:
                rollback_ok = False
        elif stopped:
            try:
                compose(instance, manifest, "start", *roles)
            except Exception:
                rollback_ok = False
        if not rollback_ok: code = "MOD_APPLY_ROLLBACK_FAILED"
        return set_status(directory, "failed", "rollback" if backup_id else CURRENT_STAGE,
            error=error_payload(code, request, task_id, bool(backup_id) or published, bool(backup_id)))
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        for fd in (mods_lock, maintenance, heavy):
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def valid_request(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or request.get("action") not in ("preview", "start", "status", "list", "cancel")
            or not all(valid_uuid(request.get(key)) for key in ("requestId", "serverId", "hostId", "instanceId"))):
        reject("MOD_APPLY_INPUT_INVALID")
    if request["action"] in ("preview", "start"):
        if type(request.get("expectedRevision")) is not int or not 0 <= request["expectedRevision"] <= MAX_REVISION:
            reject("MOD_APPLY_INPUT_INVALID")
        if request["action"] == "start" and (not valid_uuid(request.get("operationId"))
                or not isinstance(request.get("desiredDigest"), str)
                or re.fullmatch(r"[0-9a-f]{64}", request["desiredDigest"]) is None
                or type(request.get("createProtectionBackup")) is not bool):
            reject("MOD_APPLY_INPUT_INVALID")
    elif request["action"] in ("status", "cancel") and not valid_uuid(request.get("operationId")):
        reject("MOD_APPLY_INPUT_INVALID")


def task_envelope(state, request):
    state = dict(state)
    state["target"] = {"serverId": request["serverId"], "hostId": request["hostId"], "instanceId": request["instanceId"]}
    state["requestId"] = request["requestId"]
    return {"schemaVersion": 1, "requestId": request["requestId"], "target": state["target"], "task": state}


def preview(request):
    instance, manifest = verify_identity(request)
    desired, mods, desired_digest = validate_desired(instance, request)
    deployed = read_json(instance / "deployed-mods.json", 2 * 1024 * 1024, missing={"mods": []})
    active = read_json(instance / "active-release.json", 65536)
    roles, runtime_status = container_roles(request["instanceId"], manifest)
    blockers = []
    previous = deployed.get("mods") if isinstance(deployed, dict) else []
    if not isinstance(previous, list): previous = []
    previous_map = {item.get("workshopId"): item for item in previous if isinstance(item, dict)}
    next_map = {item["workshopId"]: item for item in mods}
    changes = []
    for workshop_id in sorted(set(previous_map) | set(next_map)):
        old, new = previous_map.get(workshop_id), next_map.get(workshop_id)
        if old == new: continue
        if old is None and new is not None and not new["enabled"]: continue
        if new is None: action = "remove"
        elif new["enabled"] and (old is None or not old.get("enabled")): action = "add"
        elif new["enabled"] and old.get("artifactDigest") != new.get("artifactDigest"): action = "update"
        elif not new["enabled"] and old and old.get("enabled"): action = "disable"
        else: action = "configure"
        changes.append({"workshopId": workshop_id, "action": action})
    return {"schemaVersion": 1, "requestId": request["requestId"],
        "target": {"serverId": request["serverId"], "hostId": request["hostId"], "instanceId": request["instanceId"]},
        "configRevision": request["expectedRevision"], "desiredDigest": desired_digest,
        "modsReleaseId": active.get("modsReleaseId"), "runtimeStatus": runtime_status,
        "shards": roles, "changes": changes, "blockers": blockers, "allowed": bool(changes) and not blockers}


def start(request, worker_source, archive_source, release_source, runtime_source, restore_source, download_source):
    if os.geteuid() != 0: reject("ROOT_REQUIRED")
    safe_dir(ROOT); safe_dir(TASKS, create=True)
    admission = os.open(TASKS / "admit.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(admission, fcntl.LOCK_EX)
        directory = TASKS / request["operationId"]
        if directory.exists():
            saved = read_json(directory / "request.json", 262144)
            if saved.get("desiredDigest") != request["desiredDigest"]: reject("OPERATION_ID_REUSED")
            return status_load(directory.name)
        if any(path.is_dir() and valid_uuid(path.name) and status_load(path.name)["status"] in ("queued", "running")
               for path in TASKS.iterdir()): reject("MOD_APPLY_BUSY")
        result = preview(dict(request, action="preview"))
        if result["desiredDigest"] != request["desiredDigest"]: reject("REVISION_CONFLICT")
        if result["blockers"]: reject(result["blockers"][0])
        if not result["allowed"]: reject("MOD_APPLY_NO_CHANGES")
        directory.mkdir(mode=0o700)
        atomic_json(directory / "request.json", request)
        created = now()
        target = {"serverId": request["serverId"], "hostId": request["hostId"], "instanceId": request["instanceId"]}
        atomic_json(directory / "status.json", {"schemaVersion": 1, "taskId": request["operationId"],
            "operationId": request["operationId"], "target": target, "operation": "modApply",
            "status": "queued", "stage": "queued", "lastCompletedStage": None,
            "progress": {"kind": "indeterminate"}, "sequence": 1, "createdAt": created,
            "startedAt": None, "finishedAt": None, "cancelAllowed": True, "observation": "live",
            "result": None, "error": None})
        atomic_bytes(directory / "journal.jsonl", canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": created}) + b"\n")
        for filename, source in (("worker.py", worker_source), ("archive-source.py", archive_source),
                                 ("release-source.py", release_source), ("runtime-source.py", runtime_source),
                                 ("restore-source.py", restore_source), ("download-source.py", download_source)):
            atomic_bytes(directory / filename, source.encode(), 0o700 if filename == "worker.py" else 0o600)
        unit = "famine-queue-controller"
        admit_queue(request)
        atomic_bytes(ROOT / "queue-controller.py", worker_source.encode(), 0o700)
        launch = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect", "--unit", unit,
            "--property=Type=exec", "/usr/bin/python3", "-I", str(ROOT / "queue-controller.py"), "controller"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if launch.returncode != 0 and not controller_active():
            return set_status(directory, "failed", "queued", error=error_payload("MOD_APPLY_CONTROLLER_FAILED", request, request["operationId"], False))
        return status_load(request["operationId"])
    finally:
        fcntl.flock(admission, fcntl.LOCK_UN); os.close(admission)


def cancel(request):
    state = status_load(request["operationId"])
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        return state
    atomic_json(TASKS / request["operationId"] / "cancel.request", {"requestedAt": now()})
    return state


def status(request):
    state = status_load(request["operationId"])
    if state["status"] == "queued":
        position = queue_position(request["operationId"])
        if position is not None:
            state = dict(state, queuePosition=position)
    if state["target"] != {"serverId": request["serverId"], "hostId": request["hostId"], "instanceId": request["instanceId"]}:
        reject("MOD_APPLY_TARGET_MISMATCH")
    if state["status"] in ("queued", "running"):
        unit = "famine-queue-controller"
        active = subprocess.run(["systemctl", "show", "--property=ActiveState", "--value", unit],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=5, check=False)
        if active.returncode == 0 and active.stdout.strip() in ("active", "activating", "reloading"):
            return state
        try:
            created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
            admission_grace = (datetime.now(timezone.utc) - created).total_seconds() < 15
        except (KeyError, ValueError, TypeError):
            admission_grace = False
        if admission_grace:
            return state
        if active.returncode != 0 or active.stdout.strip() not in ("active", "activating", "reloading"):
            state = reconcile_interrupted(request, state)
    return state


def reconcile_interrupted(request, state):
    task_id = request["operationId"]
    directory = TASKS / task_id
    transaction = read_json(directory / "transaction.json", 262144, missing=None)
    if not isinstance(transaction, dict) or transaction.get("schemaVersion") != 1 \
            or transaction.get("taskId") != task_id:
        return set_status(directory, "interrupted", state["stage"],
            error=error_payload("MOD_APPLY_RECOVERY_REQUIRED", request, task_id))
    instance = ROOT / "instances" / request["instanceId"]
    heavy = maintenance = mods_lock = None
    try:
        heavy = acquire_lock(ROOT / "locks" / "heavy.lock", wait=True)
        dequeue(task_id)
        maintenance = acquire_lock(instance / "locks" / "world-config.lock", wait=True)
        mods_lock = acquire_lock(instance / "locks" / "instance.lock", wait=True)
        instance, current_manifest = verify_identity(request)
        roles, _ = container_roles(request["instanceId"], current_manifest)
        backup_id = transaction.get("protectionBackupId")
        if backup_id:
            archive_ns, _, _, restore_ns, _ = load_task_sources(directory)
            if transaction.get("runtimeBefore") == "running":
                compose(instance, current_manifest, "stop", *roles)
            backup_path = ROOT / "backups" / request["instanceId"] / backup_id
            stage = restore_paths_from_backup(instance, archive_ns, restore_ns, backup_path, request, backup_id,
                roles, remove_mod_release_id=transaction.get("newModsReleaseId"))
            previous_manifest = read_json(stage / "manifest.json", 65536)
            previous_active = read_json(stage / "active-release.json", 65536)
            if current_manifest.get("modsReleaseId") != previous_active.get("modsReleaseId"):
                candidate_release = current_manifest.get("modsReleaseId")
                candidate_path = instance / "mods" / "releases" / str(candidate_release)
                if candidate_release and candidate_path.exists(): shutil.rmtree(candidate_path)
            compose(instance, previous_manifest, "create", "--force-recreate", *roles)
            if transaction.get("runtimeBefore") == "running":
                compose(instance, previous_manifest, "start", *roles)
                if not restore_ns["wait_for_runtime_processes"](instance, request["instanceId"], previous_manifest, roles):
                    reject("MOD_APPLY_ROLLBACK_FAILED")
            else:
                _, restored_state = container_roles(request["instanceId"], previous_manifest)
                if restored_state != "stopped": reject("MOD_APPLY_ROLLBACK_FAILED")
            shutil.rmtree(stage, ignore_errors=True)
        elif transaction.get("runtimeBefore") == "running":
            compose(instance, transaction["manifest"], "start", *roles)
        return set_status(directory, "interrupted", state["stage"],
            error=error_payload("MOD_APPLY_INTERRUPTED_RECOVERED", request, task_id, bool(backup_id),
                bool(backup_id)),
            completed=state.get("lastCompletedStage"),
            result={"recovered": True, "protectionBackupId": backup_id,
                "runtimeStatus": transaction.get("runtimeBefore")})
    except Exception:
        return set_status(directory, "interrupted", state["stage"],
            error=error_payload("MOD_APPLY_RECOVERY_REQUIRED", request, task_id, True))
    finally:
        for fd in (mods_lock, maintenance, heavy):
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)


def main(request, worker_source=None, archive_source=None, release_source=None, runtime_source=None,
         restore_source=None, download_source=None):
    global CURRENT_STAGE
    try:
        valid_request(request)
        action = request["action"]
        if action == "preview": data = preview(request)
        elif action == "start": data = {"task": start(request, worker_source, archive_source, release_source,
            runtime_source, restore_source, download_source)}
        elif action == "status": data = {"task": status(request)}
        elif action == "cancel": data = {"task": cancel(request)}
        elif action == "list":
            safe_dir(TASKS, create=True)
            data = [status_load(path.name) for path in TASKS.iterdir()
                    if path.is_dir() and valid_uuid(path.name)
                    and status_load(path.name).get("target") == {"serverId": request["serverId"], "hostId": request["hostId"], "instanceId": request["instanceId"]}]
            data.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
        else: reject("MOD_APPLY_INPUT_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code, "details": failure.details}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"ok": False, "code": "MOD_APPLY_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "controller":
    controller_main()
elif __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "worker":
    try:
        result = worker(sys.argv[2])
        print(json.dumps({"ok": True, "data": result}, separators=(",", ":")), flush=True)
    except Exception as failure:
        try:
            directory = TASKS / sys.argv[2]
            request = read_json(directory / "request.json")
            result = set_status(directory, "failed", CURRENT_STAGE,
                error=error_payload(getattr(failure, "code", "MOD_APPLY_FAILED"), request, sys.argv[2]))
            print(json.dumps({"ok": True, "data": result}, separators=(",", ":")), flush=True)
        except Exception:
            raise SystemExit(1)
