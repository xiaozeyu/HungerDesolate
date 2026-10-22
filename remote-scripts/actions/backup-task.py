"""Persistent, identity-bound backup task admission and worker."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
TASKS = ROOT / "backup-tasks"
MAX_REVISION = 9007199254740991
MAX_TASKS = 200


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


def operation_digest(request):
    body = {key: value for key, value in request.items()
            if key not in ("action", "requestId", "requestDigest")}
    return __import__("hashlib").sha256(canonical(body)).hexdigest()


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
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("BACKUP_TASK_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("BACKUP_TASK_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("BACKUP_TASK_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("BACKUP_TASK_STATE_INVALID")


def atomic_json(path, value):
    safe_dir(path.parent, create=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(canonical(value))
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
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_bytes(path, payload, mode=0o600):
    safe_dir(path.parent, create=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
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


def load_status(task_id):
    if not valid_uuid(task_id):
        reject("BACKUP_TASK_INPUT_INVALID")
    directory = TASKS / task_id
    safe_dir(directory)
    state = read_json(directory / "status.json")
    if (state.get("schemaVersion") != 1 or state.get("taskId") != task_id
            or state.get("operationId") != task_id or state.get("operation") != "backupCreate"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "interrupted")
            or type(state.get("sequence")) is not int or state["sequence"] < 1):
        reject("BACKUP_TASK_STATE_INVALID")
    return state


def set_status(directory, status, stage, *, completed=None, result=None, error=None, fields=None):
    state = load_status(directory.name)
    sequence = state["sequence"] + 1
    state.update({"status": status, "stage": stage, "sequence": sequence,
                  "observation": "live", "result": result, "error": error})
    if completed is not None:
        state["lastCompletedStage"] = completed
    if fields:
        state.update(fields)
    if status == "running" and state.get("startedAt") is None:
        state["startedAt"] = now()
    if status in ("succeeded", "failed", "interrupted"):
        state["finishedAt"] = now()
    atomic_json(directory / "status.json", state)
    journal_path = directory / "journal.jsonl"
    previous = journal_path.read_bytes() if journal_path.exists() else b""
    entry = canonical({"sequence": sequence, "status": status, "stage": stage, "at": now()}) + b"\n"
    if len(previous) + len(entry) > 1024 * 1024:
        reject("BACKUP_TASK_JOURNAL_LIMIT")
    temporary = directory / (".journal." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(previous + entry)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, journal_path)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)
    state["journal"] = journal_tail(directory)
    return state


def journal_tail(directory):
    try:
        rows = (directory / "journal.jsonl").read_text(encoding="utf-8").splitlines()[-100:]
        return [json.loads(row) for row in rows]
    except (OSError, ValueError):
        reject("BACKUP_TASK_STATE_INVALID")


def safe_error(code, task_id, host_id, instance_id, remote_changed=True):
    return {"schemaVersion": 1, "code": code, "category": "backupCreate",
            "operation": "backupCreate", "requestId": None,
            "target": {"hostId": host_id, "instanceId": instance_id},
            "message": "备份任务未能完成，已保留任务现场", "retryable": True,
            "remoteChanged": remote_changed, "taskId": task_id,
            "recoverySteps": ["查看任务阶段和备份列表，再核对专服运行状态"], "details": {}}


def response(state):
    state = dict(state)
    state["journal"] = journal_tail(TASKS / state["taskId"])
    return {"ok": True, "data": state}


def docker_env():
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def backup_restorability(instance, request, release_id):
    desired_path = instance / "desired" / "world.json"
    deployed_path = instance / "deployed-world.json"
    if not desired_path.is_file() or not deployed_path.is_file():
        return "unknown", None
    try:
        desired = read_json(desired_path, 262144)
        deployed = read_json(deployed_path, 262144)
    except (OSError, Rejected):
        return "incompatible", None
    settings = desired.get("settings") if isinstance(desired, dict) else None
    digest = "sha256:" + __import__("hashlib").sha256(canonical(settings)).hexdigest()
    if (desired.get("schemaVersion") != 1 or desired.get("instanceId") != request["instanceId"]
            or desired.get("revision") != request["expectedRevision"] or not isinstance(settings, dict)
            or not isinstance(desired.get("secrets"), dict)
            or deployed.get("schemaVersion") != 1 or deployed.get("instanceId") != request["instanceId"]
            or deployed.get("revision") != request["expectedRevision"]
            or deployed.get("digest") != digest or deployed.get("settings") != settings):
        return "incompatible", None
    try:
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
        release = next((item for item in catalog.get("releases", [])
                        if isinstance(item, dict) and item.get("releaseId") == release_id
                        and item.get("verified") is True), None)
        if not release:
            return "missingArtifacts", None
        release_dir = ROOT / "staging" / release_id
        safe_dir(release_dir)
        release_metadata = read_json(release_dir / "release-metadata.json", 262144)
        release_metadata.pop("schemaVersion", None)
        if release_metadata != release:
            return "missingArtifacts", None
        image = subprocess.run(["docker", "image", "inspect", release["imageDigest"], "--format", "{{.Id}}"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=10, check=False, env=docker_env())
        if image.returncode != 0 or image.stdout.strip() != release["imageDigest"]:
            return "missingArtifacts", None
        return "restorable", release
    except (OSError, Rejected, KeyError, TypeError, subprocess.SubprocessError):
        return "missingArtifacts", None


def verify_instance(request, allow_unknown=False, allow_degraded=False):
    host_id, instance_id = request["hostId"], request["instanceId"]
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json", 1024 * 1024)
    if host.get("hostId") != host_id or registry.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    entries = [entry for entry in registry.get("instances", [])
               if isinstance(entry, dict) and entry.get("instanceId") == instance_id]
    if len(entries) != 1 or entries[0].get("lifecycle") != "ready":
        reject("INSTANCE_NOT_FOUND")
    instance = ROOT / "instances" / instance_id
    safe_dir(instance)
    manifest = read_json(instance / "manifest.json", 65536)
    if (manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id
            or manifest.get("configRevision") != request["expectedRevision"]
            or manifest.get("composeProject") != "fh_" + instance_id.replace("-", "")):
        reject("REVISION_CONFLICT")
    roles = ["master"] + (["caves"] if manifest.get("cavesEnabled") is True else [])
    try:
        ids = subprocess.run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                              "--filter", "label=com.hungerdesolate.instanceId=" + instance_id],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, timeout=15, check=False, env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        if allow_unknown:
            return instance, manifest, roles, "unknown"
        reject("BACKUP_RUNTIME_INVALID")
    values = ids.stdout.splitlines()
    if ids.returncode != 0 or len(values) != len(roles) or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in values):
        if allow_unknown:
            return instance, manifest, roles, "unknown"
        reject("BACKUP_RUNTIME_INVALID")
    try:
        inspected = subprocess.run(["docker", "container", "inspect", "--format",
                                    "{{json .Config.Labels}}\t{{.State.Status}}", *values],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, timeout=15, check=False, env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        if allow_unknown:
            return instance, manifest, roles, "unknown"
        reject("BACKUP_RUNTIME_INVALID")
    rows = inspected.stdout.splitlines()
    if inspected.returncode != 0 or len(rows) != len(roles):
        if allow_unknown:
            return instance, manifest, roles, "unknown"
        reject("BACKUP_RUNTIME_INVALID")
    states = {}
    for row in rows:
        labels_text, separator, status = row.partition("\t")
        try:
            labels = json.loads(labels_text)
        except ValueError:
            reject("BACKUP_RUNTIME_INVALID")
        role = labels.get("com.hungerdesolate.role") if isinstance(labels, dict) else None
        if (not separator or labels.get("com.hungerdesolate.hostId") != host_id
                or labels.get("com.hungerdesolate.instanceId") != instance_id
                or labels.get("com.hungerdesolate.schemaVersion") != "1"
                or role not in roles or role in states
                or status not in ("created", "running", "exited", "paused", "restarting", "dead")):
            reject("BACKUP_RUNTIME_INVALID")
        states[role] = status
    if all(value == "running" for value in states.values()):
        runtime = "running"
    elif all(value in ("created", "exited") for value in states.values()):
        runtime = "stopped"
    elif allow_unknown or allow_degraded:
        runtime = "degraded"
    else:
        reject("BACKUP_RUNTIME_INVALID")
    return instance, manifest, roles, runtime


def _worker_impl(task_id):
    archive_module = {}
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    directory = TASKS / task_id
    state = load_status(task_id)
    if state["status"] in ("succeeded", "failed", "interrupted"):
        return state
    request = read_json(directory / "request.json")
    request_digest = request.get("requestDigest")
    if operation_digest(request) != request_digest:
        reject("BACKUP_TASK_STATE_INVALID")
    source = read_json(directory / "archive-source.json", 1024 * 1024)
    archive_module = {}
    exec(compile(source["source"], "<backup-archive>", "exec"), archive_module)
    stopped = False
    backup_dir = None
    backup_stage = None
    backup_created = False
    heavy_fd = None
    lock_fd = None
    try:
        allow_unknown = request["captureMode"] == "quasiHot"
        allow_degraded = request["captureMode"] == "cold"
        instance, manifest, roles, runtime_before = verify_instance(request, allow_unknown, allow_degraded)
        safe_dir(ROOT / "locks")
        heavy_fd = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        heavy_info = os.fstat(heavy_fd)
        if not stat.S_ISREG(heavy_info.st_mode) or heavy_info.st_nlink != 1 or heavy_info.st_mode & 0o077:
            os.close(heavy_fd)
            reject("BACKUP_LOCK_FAILED")
        try:
            fcntl.flock(heavy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(heavy_fd)
            heavy_fd = None
            reject("BACKUP_TASK_BUSY")
        safe_dir(instance / "locks")
        lock_path = instance / "locks" / "world-config.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except Rejected as failure:
        if lock_fd is not None:
            os.close(lock_fd)
        if heavy_fd is not None:
            os.close(heavy_fd)
        current = load_status(task_id)
        return set_status(directory, "failed", current["stage"], error=task_error(
            failure.code, task_id, request))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        if lock_fd is not None:
            os.close(lock_fd)
        if heavy_fd is not None:
            os.close(heavy_fd)
        current = load_status(task_id)
        return set_status(directory, "failed", current["stage"], error=task_error(
            "BACKUP_TASK_PREFLIGHT_FAILED", task_id, request))
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
            reject("BACKUP_LOCK_FAILED")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            reject("INSTANCE_BUSY")
        instance, manifest, roles, runtime_before = verify_instance(request, allow_unknown, allow_degraded)
        if request["captureMode"] == "cold" and runtime_before not in ("running", "stopped", "degraded"):
            reject("BACKUP_RUNTIME_INVALID")
        set_status(directory, "running", "preflight")
        roots = ["data/Master", "config/Cluster_1"]
        if manifest.get("cavesEnabled") is True:
            roots.append("data/Caves")
        for relative in ("desired/world.json", "deployed-world.json"):
            path = instance / relative
            if path.exists() or path.is_symlink():
                roots.append(relative)
        files, source_size = archive_module["checked_files"](instance, roots)
        filesystem = os.statvfs(ROOT)
        available = filesystem.f_bavail * filesystem.f_frsize
        estimated = source_size + max(source_size // 10, 1024 * 1024)
        if estimated >= available:
            reject("BACKUP_SPACE_INSUFFICIENT")
        set_status(directory, "running", "stop" if request["captureMode"] == "cold" else "backup",
                   completed="preflight")
        stop_attempted = False
        backup_id = request["backupId"]
        backup_dir = ROOT / "backups" / request["instanceId"] / backup_id
        safe_dir(ROOT / "backups", create=True)
        safe_dir(ROOT / "backups" / request["instanceId"], create=True)
        try:
            backup_dir.lstat()
            reject("BACKUP_ID_EXISTS")
        except FileNotFoundError:
            pass
        backup_stage = directory / "backup-staging"
        try:
            backup_stage.lstat()
            reject("BACKUP_STAGING_EXISTS")
        except FileNotFoundError:
            backup_stage.mkdir(mode=0o700)
            backup_created = True
        pending_metadata = {"schemaVersion": 1, "backupId": backup_id,
            "hostId": request["hostId"], "instanceId": request["instanceId"],
            "createdAt": now(), "reason": request["reason"], "captureMode": request["captureMode"],
            "captureSemantics": "stoppedBeforeRead" if request["captureMode"] == "cold" else "onDiskBestEffort",
            "runtimeStatusAtCapture": runtime_before, "sizeBytes": 0, "checksum": None,
            "verification": "pending", "restorability": "unknown", "dstBuild": None,
            "dstReleaseId": None, "modsReleaseId": None,
            "configRevision": request["expectedRevision"], "containsSecrets": False}
        atomic_json(backup_stage / "metadata.json", pending_metadata)
        execution = {"schemaVersion": 1, "operationId": task_id,
                     "captureMode": request["captureMode"], "runtimeBefore": runtime_before,
                     "stopAttempted": False, "runtimeRestored": False}
        atomic_json(directory / "execution.json", execution)
        try:
            if request["captureMode"] == "cold" and runtime_before in ("running", "degraded"):
                stop_attempted = True
                stopped = True
                execution["stopAttempted"] = True
                atomic_json(directory / "execution.json", execution)
                result = subprocess.run(["docker", "compose", "-p", manifest["composeProject"],
                                         "-f", str(instance / "compose.yaml"), "stop", *roles],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=90, check=False, env=docker_env())
                if result.returncode != 0:
                    reject("BACKUP_STOP_FAILED")
                _, _, _, state_after_stop = verify_instance(request)
                if state_after_stop != "stopped":
                    reject("BACKUP_STOP_FAILED")
            set_status(directory, "running", "backup", completed="stop" if request["captureMode"] == "cold" else "preflight")
            active = read_json(instance / "active-release.json", 65536) if (instance / "active-release.json").exists() else {}
            release_id = active.get("dstReleaseId")
            capture_runtime = "stopped" if request["captureMode"] == "cold" else runtime_before
            backup_identity = {"schemaVersion": 1, "hostId": request["hostId"],
                "instanceId": request["instanceId"], "backupId": backup_id,
                "captureMode": request["captureMode"], "runtimeStatus": capture_runtime,
                "runtimeVerified": capture_runtime in ("running", "stopped"),
                "configRevision": request["expectedRevision"],
                "dstReleaseId": release_id, "reason": request["reason"]}
            archive_path = backup_stage / "archive.tar.gz"
            created = archive_module["create_archive"](instance, archive_path, backup_identity, roots)
            verified = archive_module["inspect_archive"](archive_path, request["hostId"],
                                                           request["instanceId"], created["checksum"])
            set_status(directory, "running", "backupVerify", completed="backup")
            restorability, release = backup_restorability(instance, request, release_id)
            metadata = {"schemaVersion": 1, "backupId": backup_id, "hostId": request["hostId"],
                "instanceId": request["instanceId"], "createdAt": verified["manifest"]["captureCompletedAt"],
                "reason": request["reason"], "captureMode": request["captureMode"],
                "captureSemantics": verified["manifest"]["captureSemantics"],
                "runtimeStatusAtCapture": capture_runtime, "sizeBytes": created["archiveSizeBytes"],
                "checksum": created["checksum"], "verification": "verified", "restorability": restorability,
                "dstBuild": release.get("dstBuild") if release else None, "dstReleaseId": release_id,
                "modsReleaseId": active.get("modsReleaseId") if isinstance(active, dict) else None,
                "configRevision": request["expectedRevision"], "containsSecrets": False}
            atomic_json(backup_stage / "metadata.json", metadata)
            if backup_dir.exists():
                reject("BACKUP_ID_EXISTS")
            os.rename(backup_stage, backup_dir)
            parent_fd = os.open(backup_dir.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            set_status(directory, "running", "start" if stopped else "verify", completed="backup")
        finally:
            if stop_attempted:
                restore_runtime(request, manifest, roles, runtime_before == "running")
                execution["runtimeRestored"] = True
                atomic_json(directory / "execution.json", execution)
        result = {"preparedHost": None, "verifiedRevision": request["expectedRevision"],
            "verification": "passed", "remoteChanged": request["captureMode"] == "cold" and stopped, "backupId": backup_id,
            "deploymentId": None, "invalidate": [{"hostId": request["hostId"],
                "instanceId": request["instanceId"], "domains": ["backups", "runtime"]}],
            "recoveryActions": []}
        return set_status(directory, "succeeded", "finalize", completed="verify", result=result)
    except Exception as failure:
        if isinstance(failure, Rejected):
            code = failure.code
        elif archive_module and isinstance(failure, archive_module["BackupRejected"]):
            code = failure.code
        else:
            code = "BACKUP_TASK_FAILED"
        if backup_created and backup_stage is not None:
            try:
                metadata = read_json(backup_stage / "metadata.json")
                if metadata.get("verification") == "pending":
                    metadata["verification"] = "failed"
                    atomic_json(backup_stage / "metadata.json", metadata)
            except (OSError, Rejected):
                pass
        current = load_status(task_id)
        return set_status(directory, "failed", current["stage"], error=safe_error(
            code, task_id, request["hostId"], request["instanceId"],
            request["captureMode"] == "cold" and stopped))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        fcntl.flock(heavy_fd, fcntl.LOCK_UN)
        os.close(heavy_fd)


def task_error(code, task_id, request):
    return safe_error(code, task_id, request.get("hostId"), request.get("instanceId"))


def restore_runtime(request, manifest, roles, should_run=True):
    instance, manifest, roles, state = verify_instance(request, allow_unknown=True)
    if should_run and state == "running":
        return
    if state not in ("stopped", "degraded"):
        reject("BACKUP_RESTART_STATE_UNKNOWN")
    if not should_run:
        if state != "stopped":
            reject("BACKUP_STOP_FAILED")
        return
    result = subprocess.run(["docker", "compose", "-p", manifest["composeProject"],
                             "-f", str(instance / "compose.yaml"), "start", *roles],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=90, check=False, env=docker_env())
    if result.returncode != 0:
        reject("BACKUP_RESTART_FAILED")
    _, _, _, restored = verify_instance(request)
    if restored != "running":
        reject("BACKUP_RESTART_FAILED")


def recover_interrupted(request, directory):
    execution_path = directory / "execution.json"
    try:
        execution = read_json(execution_path)
    except FileNotFoundError:
        return True, "BACKUP_WORKER_NOT_STARTED"
    if execution.get("schemaVersion") != 1 or execution.get("operationId") != request["operationId"]:
        return False, "BACKUP_TASK_STATE_INVALID"
    if (execution.get("captureMode") != "cold"
            or execution.get("runtimeBefore") not in ("running", "degraded")
            or execution.get("stopAttempted") is not True):
        return True, "BACKUP_WORKER_INTERRUPTED"
    if execution.get("runtimeRestored") is True:
        return True, "BACKUP_WORKER_INTERRUPTED"
    heavy_fd = None
    maintenance_fd = None
    try:
        instance, manifest, roles, _ = verify_instance(request, allow_unknown=True)
        safe_dir(ROOT / "locks")
        heavy_fd = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(heavy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        safe_dir(instance / "locks")
        maintenance_fd = os.open(instance / "locks" / "world-config.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(maintenance_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        restore_runtime(request, manifest, roles, execution["runtimeBefore"] == "running")
        execution["runtimeRestored"] = True
        atomic_json(execution_path, execution)
        return True, "BACKUP_WORKER_INTERRUPTED_RECOVERED"
    except Exception:
        return False, "BACKUP_RECOVERY_REQUIRED"
    finally:
        if maintenance_fd is not None:
            fcntl.flock(maintenance_fd, fcntl.LOCK_UN)
            os.close(maintenance_fd)
        if heavy_fd is not None:
            fcntl.flock(heavy_fd, fcntl.LOCK_UN)
            os.close(heavy_fd)


def worker(task_id):
    try:
        return _worker_impl(task_id)
    except Exception as failure:
        try:
            directory = TASKS / task_id
            state = load_status(task_id)
            request = read_json(directory / "request.json")
            code = getattr(failure, "code", "BACKUP_WORKER_FAILED")
            return set_status(directory, "failed", state.get("stage", "preflight"),
                              error=safe_error(code, task_id, request.get("hostId"), request.get("instanceId"), False))
        except Exception:
            return None


def start(request, archive_source, worker_source=None):
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    if (request.get("schemaVersion") != 1 or request.get("action") != "start"
            or not valid_uuid(request.get("requestId")) or not valid_uuid(request.get("operationId"))
            or not valid_uuid(request.get("backupId")) or not valid_uuid(request.get("hostId"))
            or not valid_uuid(request.get("instanceId"))
            or request.get("captureMode") not in ("cold", "quasiHot")
            or type(request.get("expectedRevision")) is not int
            or request["expectedRevision"] < 0 or request["expectedRevision"] > MAX_REVISION
            or not isinstance(request.get("reason"), str) or not request["reason"]
            or len(request["reason"]) > 96 or any(ord(character) < 32 for character in request["reason"])):
        reject("BACKUP_TASK_INPUT_INVALID")
    safe_dir(ROOT)
    safe_dir(TASKS, create=True)
    lock_fd = os.open(TASKS / "admit.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        directory = TASKS / request["operationId"]
        if directory.exists():
            saved = read_json(directory / "request.json")
            if saved.get("requestDigest") != operation_digest(request):
                reject("OPERATION_ID_REUSED")
            return load_status(directory.name)
        active = [child.name for child in TASKS.iterdir() if child.is_dir() and valid_uuid(child.name)
                  and load_status(child.name)["status"] in ("queued", "running")]
        if len(active) >= MAX_TASKS:
            reject("BACKUP_TASK_LIMIT")
        if active:
            reject("BACKUP_TASK_BUSY")
        verify_instance(request, request["captureMode"] == "quasiHot")
        directory.mkdir(mode=0o700)
        saved = dict(request)
        saved["requestDigest"] = operation_digest(request)
        atomic_json(directory / "request.json", saved)
        atomic_json(directory / "archive-source.json", {"source": archive_source})
        created = now()
        atomic_json(directory / "status.json", {"schemaVersion": 1,
            "taskId": request["operationId"], "operationId": request["operationId"],
            "target": {"hostId": request["hostId"], "instanceId": request["instanceId"]},
            "operation": "backupCreate", "status": "queued", "stage": "queued",
            "lastCompletedStage": None, "progress": {"kind": "indeterminate"},
            "sequence": 1, "createdAt": created, "startedAt": None, "finishedAt": None,
            "cancelAllowed": False, "observation": "live", "result": None, "error": None})
        journal = canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": created}) + b"\n"
        atomic_bytes(directory / "journal.jsonl", journal)
        worker_path = directory / "worker.py"
        if worker_source is None:
            source_path = globals().get("__file__")
            if not isinstance(source_path, str):
                reject("BACKUP_CONTROLLER_BUNDLE_INVALID")
            worker_source = Path(source_path).read_text(encoding="utf-8")
        if not isinstance(worker_source, str) or not worker_source:
            reject("BACKUP_CONTROLLER_BUNDLE_INVALID")
        atomic_bytes(worker_path, worker_source.encode("utf-8"), 0o700)
        unit = "famine-backup-" + request["operationId"].replace("-", "")
        launch = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect",
            "--unit", unit, "--property=Type=exec", "/usr/bin/python3", "-I",
            str(worker_path), "worker", request["operationId"]], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if launch.returncode != 0:
            return set_status(directory, "failed", "queued", error=task_error(
                "BACKUP_CONTROLLER_START_FAILED", request["operationId"], request))
        return load_status(request["operationId"])
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def status(request):
    task_id = request.get("operationId")
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("instanceId"))
            or not valid_uuid(task_id)):
        reject("BACKUP_TASK_INPUT_INVALID")
    state = load_status(task_id)
    if (state["target"].get("hostId") != request.get("hostId")
            or state["target"].get("instanceId") != request.get("instanceId")):
        reject("BACKUP_TARGET_MISMATCH")
    unit = "famine-backup-" + task_id.replace("-", "")
    active = subprocess.run(["systemctl", "is-active", "--quiet", unit], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    execution_path = TASKS / task_id / "execution.json"
    execution_pending = False
    if state["status"] == "failed":
        try:
            execution = read_json(execution_path)
            execution_pending = execution.get("stopAttempted") is True and execution.get("runtimeRestored") is not True
        except FileNotFoundError:
            execution_pending = False
    needs_recovery = state["status"] in ("queued", "running", "interrupted") or execution_pending
    if needs_recovery and active.returncode != 0:
        recovered, code = recover_interrupted(request, TASKS / task_id)
        if state["status"] == "failed":
            error = state.get("error")
        elif recovered and code == "BACKUP_WORKER_NOT_STARTED":
            error = safe_error(code, task_id, request["hostId"], request["instanceId"], False)
        else:
            error = task_error(code if recovered else "BACKUP_RECOVERY_REQUIRED", task_id, request)
        state = set_status(TASKS / task_id,
                           "failed" if code in ("BACKUP_WORKER_FAILED", "BACKUP_WORKER_NOT_STARTED") else
                           "interrupted" if state["status"] in ("queued", "running") else "failed",
                           state["stage"], error=error,
                           fields={"observation": "reconciling", "runtimeRecovery":
                                   "restored" if recovered and code.endswith("RECOVERED") else
                                   "notRequired" if recovered else "required"})
    return state


def list_tasks(request):
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("instanceId"))):
        reject("BACKUP_TASK_INPUT_INVALID")
    try:
        safe_dir(TASKS)
    except FileNotFoundError:
        return []
    states = []
    for child in TASKS.iterdir():
        if not child.is_dir() or not valid_uuid(child.name):
            continue
        state = load_status(child.name)
        if (state["target"].get("hostId") == request["hostId"]
                and state["target"].get("instanceId") == request["instanceId"]):
            state["journal"] = journal_tail(child)
            states.append(state)
    states.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return states[:100]


def main(request=None):
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "worker":
            worker(sys.argv[2])
            return
        if request is None:
            request = json.loads(sys.stdin.buffer.read(65537))
        if len(canonical(request)) > 65536:
            reject("BACKUP_TASK_INPUT_INVALID")
        if request.get("action") == "start":
            data = start(request, globals().get("ARCHIVE_SOURCE", ""),
                         globals().get("WORKER_SOURCE"))
        elif request.get("action") == "status":
            data = status(request)
        elif request.get("action") == "listTasks":
            data = list_tasks(request)
        else:
            reject("BACKUP_TASK_INPUT_INVALID")
        if isinstance(data, list):
            payload = data
        else:
            payload = dict(data, journal=journal_tail(TASKS / data["taskId"]))
        print(json.dumps({"ok": True, "data": payload}, separators=(",", ":")), flush=True)
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        print(json.dumps({"ok": False, "code": "BACKUP_TASK_STATE_INVALID"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"ok": False, "code": "BACKUP_TASK_HANDLER_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
