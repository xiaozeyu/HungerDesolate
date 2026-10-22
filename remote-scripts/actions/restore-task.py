"""Identity-bound world restore task with protection point and rollback."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
TASKS = ROOT / "restore-tasks"
MAX_REVISION = 9007199254740991
DST_RUNTIME_UID = 1000


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise Rejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def safe_dir(path, create=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        reject("BACKUP_PATH_UNSAFE")
    return info


def read_json(path, limit=262144):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        reject("BACKUP_TASK_STATE_INVALID")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        reject("BACKUP_TASK_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("BACKUP_TASK_STATE_INVALID")


def atomic_json(path, value):
    atomic_bytes(path, canonical(value))


def atomic_bytes(path, payload, mode=0o600):
    safe_dir(path.parent, create=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def journal_tail(directory):
    try:
        return [json.loads(row) for row in (directory / "journal.jsonl").read_text().splitlines()[-100:]]
    except (OSError, ValueError):
        reject("BACKUP_TASK_STATE_INVALID")


def load(task_id):
    if not valid_uuid(task_id):
        reject("BACKUP_TASK_INPUT_INVALID")
    directory = TASKS / task_id
    safe_dir(directory)
    state = read_json(directory / "status.json")
    if (state.get("schemaVersion") != 1 or state.get("taskId") != task_id
            or state.get("operationId") != task_id or state.get("operation") != "backupRestore"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "interrupted")):
        reject("BACKUP_TASK_STATE_INVALID")
    state["journal"] = journal_tail(directory)
    return state


def set_status(directory, status, stage, completed=None, result=None, error=None, fields=None):
    state = load(directory.name)
    state.update({"status": status, "stage": stage, "sequence": state.get("sequence", 0) + 1,
                  "result": result, "error": error, "observation": "live"})
    if completed is not None:
        state["lastCompletedStage"] = completed
    if fields:
        state.update(fields)
    if status == "running" and state.get("startedAt") is None:
        state["startedAt"] = now()
    if status in ("succeeded", "failed", "interrupted"):
        state["finishedAt"] = now()
    atomic_json(directory / "status.json", state)
    journal = directory / "journal.jsonl"
    previous = journal.read_bytes() if journal.exists() else b""
    atomic_bytes(journal, previous + canonical({"sequence": state["sequence"], "status": status, "stage": stage, "at": now()}) + b"\n")
    state["journal"] = journal_tail(directory)
    return state


def error_payload(code, request, task_id, remote_changed=True):
    return {"schemaVersion": 1, "code": code, "category": "backupRestore", "operation": "backupRestore",
            "requestId": None, "target": {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")},
            "message": "恢复任务未能完成，已保留现场和保护点", "retryable": True,
            "remoteChanged": remote_changed, "taskId": task_id, "recoverySteps": ["核对保护点和恢复现场后重试"], "details": {}}


def docker_env():
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def verify_instance(request):
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json", 1024 * 1024)
    if host.get("hostId") != request["hostId"] or registry.get("hostId") != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    matches = [item for item in registry.get("instances", []) if isinstance(item, dict) and item.get("instanceId") == request["instanceId"]]
    if len(matches) != 1 or matches[0].get("lifecycle") != "ready":
        reject("INSTANCE_NOT_FOUND")
    instance = ROOT / "instances" / request["instanceId"]
    safe_dir(instance)
    manifest = read_json(instance / "manifest.json", 65536)
    if (manifest.get("hostId") != request["hostId"] or manifest.get("instanceId") != request["instanceId"]
            or manifest.get("configRevision") != request["expectedRevision"]):
        reject("REVISION_CONFLICT")
    return instance, manifest


def runtime(instance_id, manifest):
    roles = ["master"] + (["caves"] if manifest.get("cavesEnabled") else [])
    result = subprocess.run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc", "--filter", "label=com.hungerdesolate.instanceId=" + instance_id],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15, check=False, env=docker_env())
    if result.returncode != 0 or len(result.stdout.splitlines()) != len(roles):
        reject("BACKUP_RUNTIME_INVALID")
    ids = result.stdout.splitlines()
    inspected = subprocess.run(["docker", "container", "inspect", "--format", "{{json .Config.Labels}}\t{{.State.Status}}", *ids],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15, check=False, env=docker_env())
    if inspected.returncode != 0:
        reject("BACKUP_RUNTIME_INVALID")
    states = []
    for row in inspected.stdout.splitlines():
        labels_raw, _, state = row.partition("\t")
        labels = json.loads(labels_raw)
        if labels.get("com.hungerdesolate.hostId") != manifest["hostId"] or labels.get("com.hungerdesolate.instanceId") != instance_id or state not in ("created", "running", "exited", "paused", "restarting", "dead"):
            reject("BACKUP_RUNTIME_INVALID")
        states.append(state)
    if all(item == "running" for item in states):
        return "running", roles
    if all(item in ("created", "exited") for item in states):
        return "stopped", roles
    return "degraded", roles


def remove_path(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def restore_cluster_token(instance, staging, desired):
    secrets = desired.get("secrets") if isinstance(desired, dict) else None
    token_ref = secrets.get("tokenRef") if isinstance(secrets, dict) else None
    if not valid_uuid(token_ref):
        reject("WORLD_TOKEN_MISSING")
    source = instance / "desired-secrets" / (token_ref + ".token")
    info = source.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8192
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("WORLD_SECRET_FILE_INVALID")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
            reject("WORLD_SECRET_FILE_INVALID")
        token = os.read(fd, 8193)
    finally:
        os.close(fd)
    if not token.endswith(b"\n") or len(token) > 8192 or any(byte < 0x20 and byte != 0x0a for byte in token):
        reject("WORLD_SECRET_FILE_INVALID")
    config_root = staging / "config" / "Cluster_1"
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = config_root / "cluster_token.txt"
    token_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(token_fd, "wb", closefd=False) as output:
            output.write(token)
            output.flush()
            os.fsync(token_fd)
    finally:
        os.close(token_fd)


def assign_runtime_ownership(root):
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        reject("BACKUP_RESTORE_STAGING_FAILED")
    for current, directories, files in os.walk(root, followlinks=False):
        directory = Path(current)
        os.chown(directory, DST_RUNTIME_UID, DST_RUNTIME_UID, follow_symlinks=False)
        os.chmod(directory, 0o700, follow_symlinks=False)
        for name in directories:
            child = directory / name
            if child.is_symlink() or not child.is_dir():
                reject("BACKUP_RESTORE_STAGING_FAILED")
        for name in files:
            child = directory / name
            info = child.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                reject("BACKUP_RESTORE_STAGING_FAILED")
            os.chown(child, DST_RUNTIME_UID, DST_RUNTIME_UID, follow_symlinks=False)
            os.chmod(child, 0o600, follow_symlinks=False)


def rebase_world_config(desired, deployed, instance_id, backup_revision, new_revision):
    settings = desired.get("settings") if isinstance(desired, dict) else None
    deployed_settings = deployed.get("settings") if isinstance(deployed, dict) else None
    settings_digest = "sha256:" + __import__("hashlib").sha256(canonical(settings)).hexdigest()
    if (desired.get("schemaVersion") != 1 or desired.get("instanceId") != instance_id
            or type(desired.get("revision")) is not int or desired.get("revision") > backup_revision
            or not isinstance(settings, dict)
            or not isinstance(desired.get("secrets"), dict)
            or deployed.get("schemaVersion") != 1 or deployed.get("instanceId") != instance_id
            or deployed.get("revision") != desired.get("revision") or deployed.get("digest") != settings_digest
            or deployed_settings != settings):
        reject("BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID")
    desired["revision"] = new_revision
    deployed["revision"] = new_revision
    return desired, deployed


def compose(instance, manifest, action, roles):
    result = subprocess.run(["docker", "compose", "-p", manifest["composeProject"], "-f", str(instance / "compose.yaml"), action, *roles],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False, env=docker_env())
    if result.returncode != 0:
        reject("BACKUP_STOP_FAILED" if action == "stop" else "BACKUP_RESTART_FAILED")


def _worker_impl(task_id):
    directory = TASKS / task_id
    state = load(task_id)
    if state["status"] in ("succeeded", "failed", "interrupted"):
        return state
    request = read_json(directory / "request.json")
    archive = (directory / "archive-source.py").read_text()
    module = {}
    exec(compile(archive, "<backup-archive>", "exec"), module)
    instance, manifest = verify_instance(request)
    runtime_before, roles = runtime(request["instanceId"], manifest)
    heavy = open(ROOT / "locks" / "heavy.lock", "a+b")
    maintenance = open(instance / "locks" / "world-config.lock", "a+b")
    fcntl.flock(heavy.fileno(), fcntl.LOCK_EX)
    fcntl.flock(maintenance.fileno(), fcntl.LOCK_EX)
    protection_id = str(uuid.uuid4())
    recovery = instance / (".restore-recovery-" + task_id)
    staging = instance / (".restore-staging-" + task_id)
    moved = []
    stopped = False
    original_manifest = dict(manifest)
    active_path = instance / "active-release.json"
    original_active = read_json(active_path, 65536)
    try:
        set_status(directory, "running", "preflight")
        instance, manifest = verify_instance(request)
        runtime_before, roles = runtime(request["instanceId"], manifest)
        original_manifest = dict(manifest)
        original_active = read_json(active_path, 65536)
        backup_dir = ROOT / "backups" / request["instanceId"] / request["backupId"]
        metadata = read_json(backup_dir / "metadata.json")
        if (metadata.get("schemaVersion") != 1 or metadata.get("backupId") != request["backupId"]
                or metadata.get("hostId") != request["hostId"] or metadata.get("instanceId") != request["instanceId"]
                or metadata.get("verification") != "verified"):
            reject("BACKUP_TARGET_MISMATCH")
        checksum = metadata.get("checksum")
        inspected = module["inspect_archive"](backup_dir / "archive.tar.gz", request["hostId"], request["instanceId"], checksum)
        backup_revision = inspected["manifest"].get("configRevision")
        archive_paths = {item.get("path") for item in inspected["manifest"].get("files", []) if isinstance(item, dict)}
        has_desired = "desired/world.json" in archive_paths
        has_deployed = "deployed-world.json" in archive_paths
        if has_desired != has_deployed:
            reject("BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID")
        set_status(directory, "running", "stagingVerify", completed="preflight")
        module["extract_to_staging"](backup_dir / "archive.tar.gz", staging, request["hostId"], request["instanceId"], checksum)
        if has_desired:
            restore_desired = read_json(staging / "desired" / "world.json", 262144)
            restore_deployed = read_json(staging / "deployed-world.json", 262144)
        else:
            restore_desired = read_json(instance / "desired" / "world.json", 262144)
            restore_deployed = read_json(instance / "deployed-world.json", 262144)
            if (type(restore_desired.get("revision")) is not int
                    or restore_desired.get("revision") != restore_deployed.get("revision")
                    or restore_desired.get("revision") > backup_revision):
                reject("BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING")
        new_revision = request["expectedRevision"] + 1
        restore_desired, restore_deployed = rebase_world_config(
            restore_desired, restore_deployed, request["instanceId"], backup_revision, new_revision)
        restore_cluster_token(instance, staging, restore_desired)
        for relative in ("data/Master", "data/Caves", "config/Cluster_1"):
            assign_runtime_ownership(staging / relative)
        active_release = read_json(active_path, 65536)
        restored_release = inspected["manifest"].get("dstReleaseId")
        if restored_release != active_release.get("dstReleaseId"):
            reject("DST_RELEASE_MISMATCH")
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
        release = next((item for item in catalog.get("releases", []) if item.get("releaseId") == restored_release and item.get("verified") is True), None)
        if not release:
            reject("DST_RELEASE_UNAVAILABLE")
        release_dir = ROOT / "staging" / restored_release
        safe_dir(release_dir)
        release_metadata = read_json(release_dir / "release-metadata.json", 262144)
        release_metadata.pop("schemaVersion", None)
        if release_metadata != release:
            reject("DST_RELEASE_UNAVAILABLE")
        atomic_json(directory / "transaction.json", {"schemaVersion": 1, "operationId": task_id,
            "runtimeBefore": runtime_before, "manifest": original_manifest, "activeRelease": original_active,
            "desiredWorld": read_json(instance / "desired" / "world.json", 262144),
            "deployedWorld": read_json(instance / "deployed-world.json", 262144),
            "recoveryPath": str(recovery), "stagingPath": str(staging), "protectionBackupId": None})
        if runtime_before in ("running", "degraded"):
            compose(instance, manifest, "stop", roles)
            stopped = True
            runtime_after, _ = runtime(request["instanceId"], manifest)
            if runtime_after != "stopped":
                reject("BACKUP_STOP_FAILED")
        set_status(directory, "running", "stop", completed="stagingVerify")
        protection_dir = ROOT / "backups" / request["instanceId"] / protection_id
        protection_dir.mkdir(mode=0o700, parents=False)
        protection_archive = protection_dir / "archive.tar.gz"
        identity = {"schemaVersion": 1, "hostId": request["hostId"], "instanceId": request["instanceId"], "backupId": protection_id,
                    "captureMode": "cold", "runtimeStatus": "stopped", "runtimeVerified": True, "configRevision": request["expectedRevision"], "dstReleaseId": active_release.get("dstReleaseId")}
        protection_roots = ["data/Master", "config/Cluster_1"] + (["data/Caves"] if manifest.get("cavesEnabled") else [])
        for relative in ("desired/world.json", "deployed-world.json"):
            if (instance / relative).exists():
                protection_roots.append(relative)
        created = module["create_archive"](instance, protection_archive, identity, protection_roots)
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
        release = next((item for item in catalog.get("releases", []) if item.get("releaseId") == active_release.get("dstReleaseId") and item.get("verified") is True), None)
        atomic_json(protection_dir / "metadata.json", {"schemaVersion": 1, "backupId": protection_id, "hostId": request["hostId"], "instanceId": request["instanceId"],
            "createdAt": now(), "reason": "恢复前保护点", "captureMode": "cold", "captureSemantics": "stoppedBeforeRead", "runtimeStatusAtCapture": "stopped",
            "sizeBytes": created["archiveSizeBytes"], "checksum": created["checksum"], "verification": "verified", "restorability": "restorable" if release else "incompatible",
            "dstBuild": release.get("dstBuild") if release else None, "dstReleaseId": active_release.get("dstReleaseId"), "modsReleaseId": active_release.get("modsReleaseId"),
            "configRevision": request["expectedRevision"], "containsSecrets": False})
        transaction = read_json(directory / "transaction.json")
        transaction["protectionBackupId"] = protection_id
        atomic_json(directory / "transaction.json", transaction)
        set_status(directory, "running", "backup", completed="stop", result={"protectionBackupId": protection_id})
        set_status(directory, "running", "restoreCommit", completed="backup")
        recovery.mkdir(mode=0o700)
        for relative in ("data/Master", "data/Caves", "config/Cluster_1", "desired/world.json", "deployed-world.json"):
            source = instance / relative
            if source.exists():
                destination = recovery / relative
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.replace(source, destination)
                moved.append(relative)
            restored = staging / relative
            if restored.exists():
                restored.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.replace(restored, source)
        atomic_json(instance / "desired" / "world.json", restore_desired)
        atomic_json(instance / "deployed-world.json", restore_deployed)
        manifest["configRevision"] = new_revision
        new_deployment = str(uuid.uuid4())
        manifest.update({"deploymentId": new_deployment, "dstReleaseId": restored_release})
        active_release = {"schemaVersion": 1, "deploymentId": new_deployment, "dstReleaseId": restored_release,
                          "modsReleaseId": inspected["manifest"].get("modsReleaseId"), "composeProject": manifest["composeProject"], "verifiedAt": now()}
        atomic_json(active_path, active_release)
        atomic_json(instance / "manifest.json", manifest)
        if runtime_before == "running":
            set_status(directory, "running", "start", completed="restoreCommit")
            compose(instance, manifest, "start", roles)
            running_after, _ = runtime(request["instanceId"], manifest)
            if running_after != "running":
                reject("BACKUP_RESTART_FAILED")
        elif stopped:
            stopped_after, _ = runtime(request["instanceId"], manifest)
            if stopped_after != "stopped":
                reject("BACKUP_STOP_FAILED")
        set_status(directory, "running", "verify", completed="start" if runtime_before == "running" else "restoreCommit")
        shutil.rmtree(recovery, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        return set_status(directory, "succeeded", "finalize", completed="verify", result={"protectionBackupId": protection_id, "configRevision": manifest["configRevision"], "verification": "passed"})
    except Exception as failure:
        code = getattr(failure, "code", "BACKUP_RESTORE_FAILED")
        try:
            for relative in reversed(moved):
                current = instance / relative
                old = recovery / relative
                if current.exists() or current.is_symlink(): remove_path(current)
                if old.exists():
                    current.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.replace(old, current)
            atomic_json(active_path, original_active)
            atomic_json(instance / "manifest.json", original_manifest)
            transaction = read_json(directory / "transaction.json")
            atomic_json(instance / "desired" / "world.json", transaction["desiredWorld"])
            atomic_json(instance / "deployed-world.json", transaction["deployedWorld"])
            if runtime_before == "running":
                compose(instance, manifest, "start", roles)
            elif stopped:
                stopped_after, _ = runtime(request["instanceId"], original_manifest)
                if stopped_after != "stopped":
                    reject("BACKUP_STOP_FAILED")
        except Exception:
            code = "BACKUP_ROLLBACK_FAILED"
        return set_status(directory, "failed", "rollback", error=error_payload(code, request, task_id, True))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        fcntl.flock(maintenance.fileno(), fcntl.LOCK_UN); maintenance.close()
        fcntl.flock(heavy.fileno(), fcntl.LOCK_UN); heavy.close()


def worker(task_id):
    try:
        return _worker_impl(task_id)
    except Exception as failure:
        try:
            directory = TASKS / task_id
            state = load(task_id)
            request = read_json(directory / "request.json")
            code = getattr(failure, "code", "BACKUP_RESTORE_WORKER_FAILED")
            if state["status"] not in ("succeeded", "failed", "interrupted"):
                return set_status(directory, "failed", state.get("stage", "preflight"),
                                  error=error_payload(code, request, task_id, True))
        except Exception:
            return None
        return state


def start(request):
    if os.geteuid() != 0 or request.get("schemaVersion") != 1 or request.get("action") != "restoreStart" or not valid_uuid(request.get("requestId")) or not valid_uuid(request.get("operationId")) or not valid_uuid(request.get("backupId")) or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("instanceId")) or type(request.get("expectedRevision")) is not int or request["expectedRevision"] < 0 or request["expectedRevision"] > MAX_REVISION:
        reject("BACKUP_TASK_INPUT_INVALID")
    safe_dir(ROOT); safe_dir(TASKS, create=True)
    directory = TASKS / request["operationId"]
    if directory.exists():
        saved = read_json(directory / "request.json")
        if saved != request: reject("OPERATION_ID_REUSED")
        return load(request["operationId"])
    verify_instance(request)
    directory.mkdir(mode=0o700)
    atomic_json(directory / "request.json", request)
    created = now()
    atomic_json(directory / "status.json", {"schemaVersion": 1, "taskId": request["operationId"], "operationId": request["operationId"],
        "target": {"hostId": request["hostId"], "instanceId": request["instanceId"]}, "operation": "backupRestore", "status": "queued", "stage": "queued",
        "lastCompletedStage": None, "progress": {"kind": "indeterminate"}, "sequence": 1, "createdAt": created, "startedAt": None, "finishedAt": None,
        "cancelAllowed": False, "observation": "live", "result": None, "error": None})
    atomic_bytes(directory / "journal.jsonl", canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": created}) + b"\n")
    unit = "famine-restore-" + request["operationId"].replace("-", "")
    worker_path = directory / "worker.py"
    worker_source = globals().get("RESTORE_SOURCE")
    archive_source = globals().get("ARCHIVE_SOURCE")
    if not isinstance(worker_source, str) or not isinstance(archive_source, str):
        reject("BACKUP_CONTROLLER_BUNDLE_INVALID")
    atomic_bytes(worker_path, worker_source.encode("utf-8"), 0o700)
    atomic_bytes(directory / "archive-source.py", archive_source.encode("utf-8"), 0o600)
    launch = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect", "--unit", unit, "--property=Type=exec", "/usr/bin/python3", "-I", str(worker_path), "worker", request["operationId"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if launch.returncode != 0:
        return set_status(directory, "failed", "queued", error=error_payload("BACKUP_CONTROLLER_START_FAILED", request, request["operationId"], False))
    return load(request["operationId"])


def status(request):
    if not valid_uuid(request.get("operationId")) or request.get("schemaVersion") != 1:
        reject("BACKUP_TASK_INPUT_INVALID")
    state = load(request["operationId"])
    if state["target"] != {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")}:
        reject("BACKUP_TARGET_MISMATCH")
    if state["status"] in ("queued", "running"):
        unit = "famine-restore-" + request["operationId"].replace("-", "")
        active = subprocess.run(["systemctl", "is-active", "--quiet", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        if active.returncode != 0:
            state = reconcile_interrupted(request, state)
    return state


def reconcile_interrupted(request, state):
    task_id = state["operationId"]
    directory = TASKS / task_id
    try:
        transaction = read_json(directory / "transaction.json")
    except FileNotFoundError:
        return set_status(directory, "interrupted", state["stage"], error=error_payload("BACKUP_RESTORE_INTERRUPTED", request, task_id, False), fields={"observation": "reconciling", "runtimeRecovery": "notRequired"})
    if transaction.get("schemaVersion") != 1 or transaction.get("operationId") != task_id:
        return set_status(directory, "interrupted", state["stage"], error=error_payload("BACKUP_RECOVERY_REQUIRED", request, task_id, True), fields={"observation": "reconciling", "runtimeRecovery": "required"})
    instance = ROOT / "instances" / request["instanceId"]
    heavy = None
    maintenance = None
    try:
        safe_dir(ROOT / "locks")
        heavy = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(heavy, fcntl.LOCK_EX | fcntl.LOCK_NB)
        safe_dir(instance / "locks")
        maintenance = os.open(instance / "locks" / "world-config.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recovery = instance / (".restore-recovery-" + task_id)
        manifest = transaction["manifest"]
        current_state, roles = runtime(request["instanceId"], manifest)
        if current_state != "stopped":
            compose(instance, manifest, "stop", roles)
            current_state, _ = runtime(request["instanceId"], manifest)
            if current_state != "stopped":
                reject("BACKUP_STOP_FAILED")
        for relative in ("data/Master", "data/Caves", "config/Cluster_1", "desired/world.json", "deployed-world.json"):
            old = recovery / relative
            current = instance / relative
            if old.exists():
                if current.exists() or current.is_symlink(): remove_path(current)
                current.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.replace(old, current)
        atomic_json(instance / "active-release.json", transaction["activeRelease"])
        atomic_json(instance / "desired" / "world.json", transaction["desiredWorld"])
        atomic_json(instance / "deployed-world.json", transaction["deployedWorld"])
        atomic_json(instance / "manifest.json", manifest)
        runtime_before = transaction.get("runtimeBefore")
        if runtime_before == "running":
            compose(instance, manifest, "start", roles)
            running, _ = runtime(request["instanceId"], manifest)
            if running != "running":
                reject("BACKUP_RESTART_FAILED")
        elif runtime_before in ("stopped", "degraded"):
            stopped, _ = runtime(request["instanceId"], manifest)
            if stopped != "stopped":
                reject("BACKUP_STOP_FAILED")
        else:
            reject("BACKUP_RUNTIME_INVALID")
        shutil.rmtree(recovery, ignore_errors=True)
        shutil.rmtree(instance / (".restore-staging-" + task_id), ignore_errors=True)
        return set_status(directory, "interrupted", state["stage"], error=error_payload("BACKUP_RESTORE_INTERRUPTED_RECOVERED", request, task_id, True), fields={"observation": "reconciling", "runtimeRecovery": "restored"})
    except Exception:
        return set_status(directory, "interrupted", state["stage"], error=error_payload("BACKUP_RECOVERY_REQUIRED", request, task_id, True), fields={"observation": "reconciling", "runtimeRecovery": "required"})
    finally:
        if maintenance is not None:
            fcntl.flock(maintenance, fcntl.LOCK_UN); os.close(maintenance)
        if heavy is not None:
            fcntl.flock(heavy, fcntl.LOCK_UN); os.close(heavy)


def list_tasks(request):
    if request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId")) or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("instanceId")):
        reject("BACKUP_TASK_INPUT_INVALID")
    try:
        safe_dir(TASKS)
    except FileNotFoundError:
        return []
    result = []
    for child in TASKS.iterdir():
        if not child.is_dir() or not valid_uuid(child.name):
            continue
        state = load(child.name)
        if state["target"].get("hostId") == request["hostId"] and state["target"].get("instanceId") == request["instanceId"]:
            if state["status"] in ("queued", "running"):
                unit = "famine-restore-" + child.name.replace("-", "")
                active = subprocess.run(["systemctl", "is-active", "--quiet", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                if active.returncode != 0:
                    state = reconcile_interrupted(read_json(child / "request.json"), state)
            result.append(state)
    result.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return result[:100]


def main(request=None):
    try:
        action = request.get("action")
        if action == "restoreStart": data = start(request)
        elif action == "restoreStatus": data = status(request)
        elif action == "restoreList": data = list_tasks(request)
        else: reject("BACKUP_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as failure:
        print(json.dumps({"ok": False, "code": "BACKUP_TASK_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "worker":
        worker(__import__("sys").argv[2])
