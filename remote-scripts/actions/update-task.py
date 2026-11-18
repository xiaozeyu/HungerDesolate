"""Persistent instance DST switch task admission and worker."""
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
TASKS = ROOT / "update-tasks"
PLANS = ROOT / "update-plans"
MAX_REVISION = 9007199254740991
MISSING = object()
WORLD_SAVE_ROOTS = {"Master": "config/Cluster_1/Master/save",
                    "Caves": "config/Cluster_1/Caves/save"}


class Rejected(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def reject(code, details=None):
    raise Rejected(code, details)


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
        reject("UPDATE_TASK_STATE_INVALID")
    return info


def read_json(path, limit=262144, missing=MISSING):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing is not MISSING:
            return missing
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("UPDATE_TASK_STATE_INVALID")
    data = path.read_bytes()
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("UPDATE_TASK_STATE_INVALID")


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(canonical(value))
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def atomic_bytes(path, payload, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def journal_tail(directory):
    try:
        return [json.loads(row) for row in (directory / "journal.jsonl").read_text().splitlines()[-100:]]
    except (OSError, ValueError):
        reject("UPDATE_TASK_STATE_INVALID")


def registry_lock():
    safe_dir(ROOT / "locks")
    fd = os.open(ROOT / "locks" / "registry.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def release_pending(task_id):
    path = ROOT / "registry.json"
    if not path.exists():
        return
    lock_fd = registry_lock()
    try:
        registry = read_json(path, 2 * 1024 * 1024)
        changed = False
        for entry in registry.get("instances", []):
            if isinstance(entry, dict) and entry.get("pendingTaskId") == task_id:
                entry["pendingTaskId"] = None
                changed = True
        if changed:
            registry["registryRevision"] = int(registry.get("registryRevision", 0)) + 1
            atomic_json(path, registry)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def admit(request):
    task_id = request["operationId"]
    lock_fd = registry_lock()
    try:
        registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
        entries = [entry for entry in registry.get("instances", [])
                   if isinstance(entry, dict) and entry.get("instanceId") == request["instanceId"]]
        if len(entries) != 1 or entries[0].get("lifecycle") != "ready":
            reject("UPDATE_TARGET_NOT_APPLICABLE")
        pending = entries[0].get("pendingTaskId")
        if pending and pending != task_id:
            reject("INSTANCE_BUSY")
        entries[0]["pendingTaskId"] = task_id
        registry["registryRevision"] = int(registry.get("registryRevision", 0)) + 1
        atomic_json(ROOT / "registry.json", registry)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def load(task_id):
    if not valid_uuid(task_id):
        reject("UPDATE_TASK_INPUT_INVALID")
    directory = TASKS / task_id
    safe_dir(directory)
    state = read_json(directory / "status.json")
    if (state.get("schemaVersion") != 1 or state.get("taskId") != task_id
            or state.get("operationId") != task_id or state.get("operation") != "instanceUpdate"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")
            or type(state.get("sequence")) is not int or state["sequence"] < 1):
        reject("UPDATE_TASK_STATE_INVALID")
    state["journal"] = journal_tail(directory)
    return state


def set_status(directory, status, stage, completed=None, result=None, error=None, fields=None):
    state = load(directory.name)
    sequence = state["sequence"] + 1
    state.update({"status": status, "stage": stage, "sequence": sequence,
                  "observation": "live", "result": result, "error": error})
    if completed is not None:
        state["lastCompletedStage"] = completed
    if fields:
        state.update(fields)
    if status == "running" and state.get("startedAt") is None:
        state["startedAt"] = now()
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        state["finishedAt"] = now()
        state["cancelAllowed"] = False
    atomic_json(directory / "status.json", state)
    journal = directory / "journal.jsonl"
    previous = journal.read_bytes() if journal.exists() else b""
    atomic_bytes(journal, previous + canonical({"sequence": sequence, "status": status, "stage": stage, "at": now()}) + b"\n")
    state["journal"] = journal_tail(directory)
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        release_pending(directory.name)
    return state


def set_registry_release(instance_id, release_id):
    lock_fd = registry_lock()
    try:
        registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
        for entry in registry.get("instances", []):
            if isinstance(entry, dict) and entry.get("instanceId") == instance_id:
                entry["dstReleaseId"] = release_id
        registry["registryRevision"] = int(registry.get("registryRevision", 0)) + 1
        atomic_json(ROOT / "registry.json", registry)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def error_payload(code, request, task_id, remote_changed=True, details=None):
    return {"schemaVersion": 1, "code": code, "category": "instanceUpdate", "operation": "instanceUpdate",
            "requestId": None, "target": {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")},
            "message": "DST 切换任务未能完成，已保留现场", "retryable": True,
            "remoteChanged": remote_changed, "taskId": task_id,
            "recoverySteps": ["核对任务阶段和保护点后再操作"], "details": details or {}}


def load_helpers(directory):
    namespace = {"__name__": "restore_helpers"}
    exec(compile((directory / "restore-source.py").read_text(encoding="utf-8"), "<restore-helpers>", "exec"), namespace)
    namespace["ROOT"] = ROOT
    namespace["TASKS"] = TASKS
    archive = {"__name__": "backup_archive"}
    exec(compile((directory / "archive-source.py").read_text(encoding="utf-8"), "<backup-archive>", "exec"), archive)
    release = {"__name__": "instance_release"}
    exec(compile((directory / "release-source.py").read_text(encoding="utf-8"), "<instance-release>", "exec"), release)
    return namespace, archive, release


def digest(value):
    return hashlib.sha256(value).hexdigest()


def instance_summary(instance):
    values = {}
    for name in ("active-release.json", "manifest.json", "compose.yaml"):
        path = instance / name
        values[name] = digest(path.read_bytes()) if path.exists() else None
    return values


def other_instances_summary(instance_id):
    result = {}
    root = ROOT / "instances"
    if not root.exists():
        return result
    for child in root.iterdir():
        if child.name != instance_id and child.is_dir() and valid_uuid(child.name):
            result[child.name] = instance_summary(child)
    return result


def plan_valid(plan, request):
    try:
        expires = datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        reject("UPDATE_PLAN_STALE")
    if expires <= datetime.now(timezone.utc):
        reject("UPDATE_PLAN_STALE")
    if (plan.get("target", {}).get("hostId") != request["hostId"]
            or plan.get("target", {}).get("instanceId") != request["instanceId"]
            or plan.get("baseRevision") != request["expectedRevision"]):
        reject("UPDATE_PLAN_STALE")
    if not valid_uuid(plan.get("targetReleaseId")):
        reject("UPDATE_PLAN_STALE")
    return plan


def load_plan(request):
    try:
        return plan_valid(read_json(PLANS / (request["planId"] + ".json"), 65536), request)
    except FileNotFoundError:
        reject("UPDATE_PLAN_STALE")


def restore_protection(instance, directory, archive, request, backup_id, caves_enabled):
    protection = directory / "protection" / backup_id
    metadata = read_json(protection / "metadata.json")
    staging = instance / (".update-rollback-" + directory.name)
    shutil.rmtree(staging, ignore_errors=True)
    extracted = archive["extract_to_staging"](protection / "archive.tar.gz", staging,
                                              request["hostId"], request["instanceId"],
                                              metadata["checksum"])
    save_roots = archive["world_save_roots"](extracted["manifest"], caves_enabled)
    if save_roots is not None:
        for shard, archive_root in save_roots.items():
            source = staging / archive_root
            if not source.exists():
                continue
            destination = instance / WORLD_SAVE_ROOTS[shard]
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(source, destination)
    shutil.rmtree(staging, ignore_errors=True)


def _worker_impl(task_id):
    directory = TASKS / task_id
    cancel_path = directory / "cancel.request"
    state = load(task_id)
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        return state
    request = read_json(directory / "request.json")
    helpers, archive, release = load_helpers(directory)
    set_status(directory, "running", "preflight")
    heavy = maintenance = instance_lock = None
    protection_id = None
    backup_created = False
    stopped = False
    runtime_before = "unknown"
    roles = []
    instance = None
    original_manifest = {}
    original_active = {}
    original_compose = ""
    target_release = None
    retained_path = None
    other_before = {}
    plan = None
    published = False
    failure_step = "preflight"
    try:
        heavy = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(heavy, fcntl.LOCK_EX)
        instance, manifest = helpers["verify_instance"](request)
        original_manifest = dict(manifest)
        runtime_before, roles = helpers["runtime"](request["instanceId"], manifest)
        if runtime_before not in ("running", "stopped"):
            reject("UPDATE_RUNTIME_INVALID")
        safe_dir(instance / "locks", create=True)
        maintenance = os.open(instance / "locks" / "world-config.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(maintenance, fcntl.LOCK_EX)
        instance_lock = os.open(instance / "locks" / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(instance_lock, fcntl.LOCK_EX)
        if cancel_path.exists():
            reject("UPDATE_CANCELLED")
        plan = load_plan(request)
        original_active = read_json(instance / "active-release.json", 65536)
        original_compose = (instance / "compose.yaml").read_text(encoding="utf-8")
        current_release_id = original_active.get("dstReleaseId")
        if valid_uuid(current_release_id):
            retained_path = instance / "retained-releases.json"
            atomic_json(retained_path, {"schemaVersion": 1, "taskId": task_id,
                                        "releaseIds": [current_release_id]})
        releases = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)["releases"]
        target_release = next((item for item in releases if item.get("releaseId") == plan["targetReleaseId"]), None)
        if target_release is None or target_release.get("verified") is not True:
            reject("UPDATE_TARGET_RELEASE_NOT_VERIFIED")
        if target_release.get("artifactDigest") != target_release.get("imageDigest"):
            dst_dir = ROOT / "dst" / target_release["releaseId"]
            try:
                version = (dst_dir / "version.txt").read_text(encoding="utf-8").strip()
            except OSError:
                reject("UPDATE_TARGET_RELEASE_IMAGE_MISSING")
            if version != target_release.get("dstBuild"):
                reject("UPDATE_TARGET_RELEASE_IMAGE_MISSING")
        if target_release.get("releaseId") == current_release_id:
            reject("UPDATE_SAME_RELEASE")
        current_release = next((item for item in releases if item.get("releaseId") == current_release_id), None)
        if current_release is not None:
            target_build = target_release.get("dstBuild")
            current_build = current_release.get("dstBuild")
            if (isinstance(target_build, str) and target_build.isdigit()
                    and isinstance(current_build, str) and current_build.isdigit()):
                if int(target_build) < int(current_build):
                    reject("UPDATE_TARGET_BUILD_OLDER")
                if int(target_build) == int(current_build):
                    reject("UPDATE_SAME_RELEASE")
        other_before = other_instances_summary(request["instanceId"])
        transaction = {"schemaVersion": 1, "operationId": task_id, "runtimeBefore": runtime_before,
                       "manifest": original_manifest, "activeRelease": original_active, "compose": original_compose,
                       "targetReleaseId": plan["targetReleaseId"], "protectionBackupId": None,
                       "otherInstances": other_before}
        atomic_json(directory / "transaction.json", transaction)
        set_status(directory, "running", "stagingVerify", completed="preflight")
        if runtime_before == "running":
            failure_step = "stop"
            set_status(directory, "running", "stop", completed="stagingVerify")
            stopped = True
            helpers["compose"](instance, manifest, "stop", roles)
            after, _ = helpers["runtime"](request["instanceId"], manifest)
            if after != "stopped":
                reject("UPDATE_STOP_FAILED")
        set_status(directory, "running", "backup", completed="stop" if runtime_before == "running" else "stagingVerify")
        protection_id = str(uuid.uuid4())
        protection = directory / "protection" / protection_id
        protection.mkdir(mode=0o700, parents=True)
        identity = {"schemaVersion": 1, "hostId": request["hostId"], "instanceId": request["instanceId"],
                    "backupId": protection_id, "captureMode": "cold", "runtimeStatus": "stopped",
                    "runtimeVerified": True, "reason": "DST 更新前保护点"}
        shards = ["Master"] + (["Caves"] if manifest.get("cavesEnabled") is True else [])
        roots = {WORLD_SAVE_ROOTS[shard]: "data/" + shard + "/save"
                 for shard in shards if (instance / WORLD_SAVE_ROOTS[shard]).is_dir()}
        created = archive["create_archive"](instance, protection / "archive.tar.gz", identity, roots)
        previous_release = next((item for item in releases if item.get("releaseId") == current_release_id), None)
        atomic_json(protection / "metadata.json", {"schemaVersion": 1, "backupId": protection_id,
            "hostId": request["hostId"], "instanceId": request["instanceId"], "createdAt": now(),
            "reason": "DST 更新前保护点", "captureMode": "cold", "captureSemantics": "stoppedBeforeRead",
            "runtimeStatusAtCapture": "stopped", "sizeBytes": created["archiveSizeBytes"],
            "checksum": created["checksum"], "verification": "verified", "restorability": "restorable",
            "dstBuild": previous_release.get("dstBuild") if previous_release else None,
            "containsSecrets": False})
        backup_created = True
        transaction["protectionBackupId"] = protection_id
        atomic_json(directory / "transaction.json", transaction)
        set_status(directory, "running", "publish", completed="backup")
        failure_step = "publish"
        deployment = str(uuid.uuid4())
        new_manifest = dict(manifest)
        new_manifest.update({"deploymentId": deployment, "dstReleaseId": target_release["releaseId"]})
        new_active = {"schemaVersion": 1, "deploymentId": deployment,
                      "dstReleaseId": target_release["releaseId"],
                      "modsReleaseId": original_active.get("modsReleaseId"),
                      "composeProject": manifest["composeProject"], "verifiedAt": None}
        compose_text = release["render_compose"](new_manifest, dict(target_release, schemaVersion=1), str(instance))
        release["prepare_runtime_paths"](str(instance), target_release["releaseId"],
                                        manifest.get("cavesEnabled") is True)
        atomic_json(instance / "active-release.json", new_active)
        atomic_json(instance / "manifest.json", new_manifest)
        atomic_bytes(instance / "compose.yaml", compose_text.encode("utf-8"), 0o600)
        published = True
        set_registry_release(request["instanceId"], target_release["releaseId"])
        set_status(directory, "running", "start", completed="publish")
        failure_step = "start"
        helpers["compose"](instance, new_manifest, "create", "--force-recreate", *roles)
        if runtime_before == "running":
            helpers["compose"](instance, new_manifest, "start", roles)
        set_status(directory, "running", "verify", completed="start")
        failure_step = "verify"
        runtime_verified = False
        if runtime_before == "running":
            runtime_verified = helpers["wait_for_runtime_processes"](instance, request["instanceId"],
                                                                     new_manifest, roles)
            if not runtime_verified:
                reject("UPDATE_VERIFY_FAILED")
        else:
            after, _ = helpers["runtime"](request["instanceId"], new_manifest)
            if after != "stopped":
                reject("UPDATE_VERIFY_FAILED")
        if other_instances_summary(request["instanceId"]) != other_before:
            reject("UPDATE_ISOLATION_CHANGED")
        new_active["verifiedAt"] = now()
        atomic_json(instance / "active-release.json", new_active)
        result = {"updated": True, "previousReleaseId": current_release_id,
                  "targetReleaseId": target_release["releaseId"],
                  "previousBuild": previous_release.get("dstBuild") if previous_release else None,
                  "targetBuild": target_release.get("dstBuild"),
                  "protectionBackupId": protection_id, "runtimeVerified": runtime_verified,
                  "runningShards": roles if runtime_before == "running" else [],
                  "otherInstancesUnchanged": True, "publicConnectivityVerified": False}
        return set_status(directory, "succeeded", "finalize", completed="verify", result=result)
    except Exception as failure:
        code = getattr(failure, "code", "UPDATE_TASK_FAILED")
        failure_cause = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code) else type(failure).__name__
        rollback_ok = False
        rollback_error = None
        rollback_available = isinstance(plan, dict) and plan.get("rollbackAvailable") is True
        if instance is not None and published and not rollback_available:
            rollback_error = "UPDATE_ROLLBACK_UNAVAILABLE"
        elif instance is not None:
            try:
                if protection_id and backup_created:
                    restore_protection(instance, directory, archive, request, protection_id,
                                       original_manifest.get("cavesEnabled") is True)
                    for relative in WORLD_SAVE_ROOTS.values():
                        if (instance / relative).exists():
                            helpers["assign_runtime_ownership"](instance / relative)
                atomic_json(instance / "active-release.json", original_active)
                atomic_json(instance / "manifest.json", original_manifest)
                atomic_bytes(instance / "compose.yaml", original_compose.encode("utf-8"), 0o600)
                if original_active:
                    set_registry_release(request["instanceId"], original_active.get("dstReleaseId"))
                if runtime_before == "running":
                    helpers["compose"](instance, original_manifest, "create", "--force-recreate", *roles)
                    helpers["compose"](instance, original_manifest, "start", roles)
                    if not helpers["wait_for_runtime_processes"](instance, request["instanceId"],
                                                                 original_manifest, roles):
                        raise RuntimeError("rollback verify failed")
                rollback_ok = True
            except Exception as rollback_failure:
                rollback_error = getattr(rollback_failure, "code", type(rollback_failure).__name__)
        details = {"failureStep": failure_step, "failureCause": failure_cause,
                   "rollbackPerformed": instance is not None and (not published or rollback_available),
                   "rollbackVerified": rollback_ok}
        if rollback_error:
            details["rollbackFailure"] = rollback_error
        target_status = "failed"
        if code == "UPDATE_CANCELLED":
            target_status = "cancelled"
        current = load(task_id)
        return set_status(directory, target_status, current["stage"],
                          error=error_payload(code, request, task_id, protection_id is not None or stopped, details))
    finally:
        if retained_path is not None:
            retained_path.unlink(missing_ok=True)
        if instance_lock is not None:
            fcntl.flock(instance_lock, fcntl.LOCK_UN)
            os.close(instance_lock)
        if maintenance is not None:
            fcntl.flock(maintenance, fcntl.LOCK_UN)
            os.close(maintenance)
        if heavy is not None:
            fcntl.flock(heavy, fcntl.LOCK_UN)
            os.close(heavy)


def worker(task_id):
    try:
        return _worker_impl(task_id)
    except Exception as failure:
        try:
            directory = TASKS / task_id
            state = load(task_id)
            request = read_json(directory / "request.json")
            code = getattr(failure, "code", "UPDATE_WORKER_FAILED")
            return set_status(directory, "failed", state.get("stage", "preflight"),
                              error=error_payload(code, request, task_id, False))
        except Exception:
            return None


def start(request):
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    if (request.get("schemaVersion") != 1 or request.get("action") != "updateStart"
            or not valid_uuid(request.get("requestId")) or not valid_uuid(request.get("operationId"))
            or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("instanceId"))
            or not valid_uuid(request.get("planId"))
            or type(request.get("expectedRevision")) is not int
            or not 0 <= request["expectedRevision"] <= MAX_REVISION):
        reject("UPDATE_TASK_INPUT_INVALID")
    sources = {"worker.py": globals().get("UPDATE_SOURCE"),
               "restore-source.py": globals().get("RESTORE_SOURCE"),
               "archive-source.py": globals().get("ARCHIVE_SOURCE"),
               "release-source.py": globals().get("RELEASE_SOURCE")}
    if not all(isinstance(value, str) and value for value in sources.values()):
        reject("UPDATE_CONTROLLER_BUNDLE_INVALID")
    safe_dir(ROOT)
    safe_dir(TASKS, create=True)
    directory = TASKS / request["operationId"]
    if directory.exists():
        saved = read_json(directory / "request.json")
        if saved != request:
            reject("OPERATION_ID_REUSED")
        return load(request["operationId"])
    load_plan(request)
    admit(request)
    directory.mkdir(mode=0o700)
    atomic_json(directory / "request.json", request)
    created = now()
    atomic_json(directory / "status.json", {"schemaVersion": 1, "taskId": request["operationId"],
        "operationId": request["operationId"], "target": {"hostId": request["hostId"], "instanceId": request["instanceId"]},
        "operation": "instanceUpdate", "status": "queued", "stage": "queued", "lastCompletedStage": None,
        "progress": {"kind": "indeterminate"}, "sequence": 1, "createdAt": created, "startedAt": None,
        "finishedAt": None, "cancelAllowed": True, "observation": "live", "result": None, "error": None})
    atomic_bytes(directory / "journal.jsonl", canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": created}) + b"\n")
    atomic_bytes(directory / "worker.py", sources["worker.py"].encode("utf-8"), 0o700)
    for name in ("restore-source.py", "archive-source.py", "release-source.py"):
        atomic_bytes(directory / name, sources[name].encode("utf-8"), 0o600)
    unit = "famine-update-" + request["operationId"]
    launch = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect",
                             "--unit", unit, "--property=Type=exec", "/usr/bin/python3", "-I",
                             str(directory / "worker.py"), "worker", request["operationId"]],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=10, check=False)
    if launch.returncode != 0:
        release_pending(request["operationId"])
        return set_status(directory, "failed", "queued",
                          error=error_payload("UPDATE_CONTROLLER_START_FAILED", request, request["operationId"], False))
    return load(request["operationId"])


def cancel(request):
    task_id = request.get("operationId")
    if request.get("schemaVersion") != 1 or not valid_uuid(task_id) or not valid_uuid(request.get("requestId")):
        reject("UPDATE_TASK_INPUT_INVALID")
    state = load(task_id)
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        return state
    atomic_json(TASKS / task_id / "cancel.request", {"requestedAt": now()})
    return state


def queued_recently(state, seconds=20):
    try:
        created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() < seconds


def status(request):
    task_id = request.get("operationId")
    if request.get("schemaVersion") != 1 or not valid_uuid(task_id):
        reject("UPDATE_TASK_INPUT_INVALID")
    state = load(task_id)
    if state["target"] != {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")}:
        reject("UPDATE_TARGET_MISMATCH")
    if state["status"] in ("queued", "running"):
        active = subprocess.run(["systemctl", "is-active", "--quiet", "famine-update-" + task_id],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=5, check=False)
        if active.returncode != 0 and not queued_recently(state) and not (TASKS / task_id / "cancel.request").exists():
            return set_status(TASKS / task_id, "interrupted", state["stage"],
                              error=error_payload("UPDATE_TASK_INTERRUPTED", request, task_id, False),
                              fields={"observation": "reconciling"})
    return state


def list_tasks(request):
    if request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId")):
        reject("UPDATE_TASK_INPUT_INVALID")
    try:
        safe_dir(TASKS)
    except FileNotFoundError:
        return []
    values = []
    for child in TASKS.iterdir():
        if not child.is_dir() or not valid_uuid(child.name):
            continue
        state = load(child.name)
        if (state["target"].get("hostId") == request.get("hostId")
                and state["target"].get("instanceId") == request.get("instanceId")):
            values.append(state)
    values.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return values[:100]


def main(request):
    action = request.get("action")
    if action == "updateStart":
        data = start(request)
    elif action == "updateStatus":
        data = status(request)
    elif action == "updateCancel":
        data = cancel(request)
    elif action == "updateList":
        data = list_tasks(request)
    else:
        reject("UPDATE_TASK_INPUT_INVALID")
    payload = dict(data, journal=journal_tail(TASKS / data["taskId"])) if isinstance(data, dict) else data
    print(json.dumps({"ok": True, "data": payload}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "worker":
            worker(sys.argv[2])
        else:
            main(json.loads(sys.stdin.buffer.read(65537)))
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code, "details": failure.details}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print(json.dumps({"ok": False, "code": "UPDATE_TASK_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
