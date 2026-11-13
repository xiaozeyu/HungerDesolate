"""Prepare host-level Workshop artifacts in an isolated, persistent Docker worker."""
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
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
TASKS = ROOT / "host-mod-tasks"
MODS = ROOT / "mods"
PREPARED = MODS / "prepared"
MAX_FILES = 50_000
MAX_BYTES = 8 * 1024 * 1024 * 1024
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
WORKSHOP_ID = re.compile(r"^[0-9]{1,20}$")
CURRENT_STAGE = "initialize"


class Rejected(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def reject(code, details=None):
    raise Rejected(code, details)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def request_digest(request):
    body = {key: value for key, value in request.items() if key not in ("action", "requestId")}
    return hashlib.sha256(canonical(body)).hexdigest()


def valid_uuid(value):
    return isinstance(value, str) and UUID.fullmatch(value) is not None


def safe_dir(path, create=False, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or info.st_uid not in owners or info.st_mode & 0o022:
        reject("HOST_MOD_TASK_PATH_UNSAFE")
    return info


def read_json(path, limit=1024 * 1024, missing=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return missing
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("HOST_MOD_TASK_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > limit):
            reject("HOST_MOD_TASK_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("HOST_MOD_TASK_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError, UnicodeError):
        reject("HOST_MOD_TASK_STATE_INVALID")


def atomic_json(path, value, mode=0o600):
    safe_dir(path.parent, create=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(canonical(value))
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


def atomic_bytes(path, value, mode=0o600):
    safe_dir(path.parent, create=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(value)
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


def lock(path, blocking=True):
    safe_dir(path.parent, create=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        os.close(fd)
        reject("HOST_MOD_TASK_LOCK_INVALID")
    fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def task_dir(task_id):
    if not valid_uuid(task_id):
        reject("HOST_MOD_TASK_INPUT_INVALID")
    path = TASKS / task_id
    safe_dir(path)
    return path


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
        if registry.get("schemaVersion") != 1 or registry.get("hostId") != request["hostId"]:
            reject("HOST_MOD_REGISTRY_INVALID")
        if not isinstance(registry.get("heavyQueue"), list):
            registry["heavyQueue"] = []
        queue = [item for item in registry["heavyQueue"] if item.get("taskId") != task_id]
        queue.append({"taskId": task_id, "operation": "hostModPrepare", "hostId": request["hostId"],
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


def load_status(task_id):
    directory = task_dir(task_id)
    state = read_json(directory / "status.json", 262144)
    if (state.get("schemaVersion") != 1 or state.get("taskId") != task_id
            or state.get("operationId") != task_id or state.get("operation") != "hostModPrepare"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")
            or type(state.get("sequence")) is not int or state["sequence"] < 1):
        reject("HOST_MOD_TASK_STATE_INVALID")
    return state


def journal_tail(directory):
    path = directory / "journal.jsonl"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            reject("HOST_MOD_TASK_STATE_INVALID")
        return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()[-100:]]
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        reject("HOST_MOD_TASK_STATE_INVALID")


def set_status(directory, status, stage, *, completed=None, result=None, code=None, progress=None, details=None):
    state = load_status(directory.name)
    sequence = state["sequence"] + 1
    state.update({"status": status, "stage": stage, "sequence": sequence, "observation": "live",
                  "cancelAllowed": status in ("queued", "running")})
    if completed is not None:
        state["lastCompletedStage"] = completed
    if progress is not None:
        state["progress"] = progress
    if result is not None:
        state["result"] = result
    if code is not None:
        state["error"] = {"schemaVersion": 1, "code": code, "category": "modPrepare", "operation": "hostModPrepare",
            "requestId": None, "target": state["target"], "message": "主机 MOD 内容准备未能完成，任务现场已保留",
            "retryable": True, "remoteChanged": bool(state.get("result", {}).get("remoteChanged")), "taskId": directory.name,
            "recoverySteps": ["查看任务阶段和错误代码，再重试"], "details": details or {}}
    if status == "running" and state.get("startedAt") is None:
        state["startedAt"] = now()
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        state["finishedAt"] = now()
    atomic_json(directory / "status.json", state)
    entries = journal_tail(directory)
    entries.append({"sequence": sequence, "status": status, "stage": stage, "at": now()})
    atomic_bytes(directory / "journal.jsonl", b"\n".join(canonical(item) for item in entries[-100:]) + b"\n")
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        release_queue(directory.name)
    return state


def task_result(request, prepared):
    return {"remoteChanged": bool(prepared), "invalidate": [{"hostId": request["hostId"], "domains": ["mods"]}],
            "recoveryActions": [], "requestedWorkshopIds": request["workshopIds"], "preparedMods": prepared}


def valid_request(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or any(not valid_uuid(request.get(field)) for field in ("requestId", "operationId", "serverId", "hostId"))
            or type(request.get("expectedRecordVersion")) is not int or request["expectedRecordVersion"] < 1
            or not isinstance(request.get("workshopIds"), list) or not request["workshopIds"] or len(request["workshopIds"]) > 50):
        reject("HOST_MOD_TASK_INPUT_INVALID")
    ids = request["workshopIds"]
    if any(not isinstance(value, str) or not WORKSHOP_ID.fullmatch(value) for value in ids) or len(set(ids)) != len(ids):
        reject("HOST_MOD_TASK_INPUT_INVALID")
    sources = request.get("sourceHandles", {})
    if (not isinstance(sources, dict) or set(sources) - set(ids)
            or any(not isinstance(value, str) or not value.isdigit() or len(value) > 64
                   for value in sources.values())):
        reject("HOST_MOD_TASK_INPUT_INVALID")


def docker_environment():
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def docker_failure_kind(output):
    message = output.lower() if isinstance(output, str) else ""
    if "cannot connect to the docker daemon" in message or "is the docker daemon running" in message:
        return "daemon"
    if any(token in message for token in ("invalid mount config", "bind source path", "mounts denied", "error mounting")):
        return "mount"
    if any(token in message for token in ("failed to create endpoint", "network is not found", "network not found",
                                         "failed to set up container networking", "failed to create network")):
        return "network"
    if any(token in message for token in ("no such image", "pull access denied", "manifest unknown")):
        return "image"
    if any(token in message for token in ("permission denied", "operation not permitted", "apparmor", "seccomp")):
        return "permission"
    if any(token in message for token in ("oci runtime", "failed to create shim", "containerd task")):
        return "runtime"
    return "unknown"


def find_fetch_image(required_image=None):
    catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
    releases = catalog.get("releases") if isinstance(catalog, dict) else None
    if not isinstance(releases, list):
        reject("HOST_MOD_RELEASE_INVALID")
    candidates = [item for item in reversed(releases) if isinstance(item, dict) and item.get("verified") is True]
    if required_image is not None:
        candidates = [item for item in candidates
            if isinstance(item.get("image"), str) and isinstance(item.get("imageDigest"), str)
            and item["image"] + "@" + item["imageDigest"] == required_image]
    environment = docker_environment()
    image_found = False
    for candidate in candidates:
        image, image_digest = candidate.get("image"), candidate.get("imageDigest")
        if (not isinstance(image, str) or not re.fullmatch(r"[a-zA-Z0-9._/:@-]{1,256}", image)
                or not isinstance(image_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)):
            continue
        candidate_ref = image + "@" + image_digest
        inspected = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image_digest],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=20, check=False, env=environment)
        if inspected.returncode != 0 or inspected.stdout.strip() != image_digest:
            continue
        image_found = True
        capability = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh", candidate_ref,
            "-ec", "test -x /usr/local/bin/famine-workshop-fetch && grep -Fxq steamcmd-workshop-v1 /srv/capabilities/workshop-fetch"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=45, check=False, env=environment)
        if capability.returncode == 0:
            return candidate_ref, candidate.get("releaseId")
    reject("HOST_MOD_FETCH_CAPABILITY_UNAVAILABLE" if image_found or required_image else "HOST_MOD_RELEASE_IMAGE_MISSING")


def verify_scope(request, required_image=None):
    host = read_json(ROOT / "host.json", 65536)
    if host.get("hostId") != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    image_ref, release_id = find_fetch_image(required_image)
    return image_ref, release_id


def run_library_cleanup(directory, request, prepared):
    cleanup_request = {key: request[key] for key in
                       ("schemaVersion", "requestId", "serverId", "hostId", "expectedRecordVersion")}
    cleanup_request.update({"action": "cleanup",
        "keep": [{"workshopId": item["workshopId"], "contentDigest": item["contentDigest"]}
                 for item in prepared]})
    try:
        result = subprocess.run([sys.executable, "-I", str(directory / "probe.py")],
            input=json.dumps(cleanup_request), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=120, check=False, env=dict(os.environ))
    except (OSError, subprocess.TimeoutExpired):
        reject("HOST_MOD_CLEANUP_FAILED")
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        reject("HOST_MOD_CLEANUP_FAILED")
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        reject(str(envelope.get("code") if isinstance(envelope, dict) else "HOST_MOD_CLEANUP_FAILED"))
    data = envelope.get("data")
    return data.get("deleted") if isinstance(data, dict) and isinstance(data.get("deleted"), list) else []


def start(request, worker_source, probe_source=None):
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    valid_request(request)
    safe_dir(ROOT); safe_dir(TASKS, create=True)
    admission = lock(TASKS / "admit.lock")
    try:
        directory = TASKS / request["operationId"]
        if directory.exists():
            saved = read_json(directory / "request.json", 262144)
            if saved.get("requestDigest") != request_digest(request):
                reject("OPERATION_ID_REUSED")
            return load_status(directory.name)
        active = [entry.name for entry in TASKS.iterdir() if entry.is_dir() and UUID.fullmatch(entry.name)
                  and load_status(entry.name)["status"] in ("queued", "running")]
        if len(active) >= 16:
            reject("HOST_MOD_TASK_LIMIT")
        image_ref, target_release_id = verify_scope(request)
        directory.mkdir(mode=0o700)
        saved = dict(request); saved["requestDigest"] = request_digest(request)
        atomic_json(directory / "request.json", saved)
        created = now()
        target = {"serverId": request["serverId"], "hostId": request["hostId"]}
        atomic_json(directory / "status.json", {"schemaVersion": 1, "taskId": request["operationId"],
            "operationId": request["operationId"], "target": target, "operation": "hostModPrepare",
            "status": "queued", "stage": "queued", "lastCompletedStage": None,
            "progress": {"kind": "determinate", "completed": 0, "total": len(request["workshopIds"])},
            "sequence": 1, "createdAt": created, "startedAt": None, "finishedAt": None,
            "cancelAllowed": True, "observation": "live", "result": task_result(request, []), "error": None})
        atomic_json(directory / "journal.jsonl", {"sequence": 1, "status": "queued", "stage": "queued", "at": created})
        atomic_json(directory / "worker-context.json", {"schemaVersion": 1, "image": image_ref,
            "dstReleaseId": target_release_id})
        worker_path = directory / "worker.py"
        fd = os.open(worker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        try:
            with os.fdopen(fd, "wb", closefd=False) as output:
                output.write(worker_source.encode()); output.flush(); os.fsync(fd)
        finally:
            os.close(fd)
        if probe_source is not None:
            probe_path = directory / "probe.py"
            fd = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
            try:
                with os.fdopen(fd, "wb", closefd=False) as output:
                    output.write(probe_source.encode()); output.flush(); os.fsync(fd)
            finally:
                os.close(fd)
        unit = "famine-queue-controller"
        admit_queue(request)
        atomic_bytes(ROOT / "queue-controller.py", worker_source.encode(), 0o700)
        launch = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect", "--unit", unit,
            "--property=Type=exec", "/usr/bin/python3", "-I", str(ROOT / "queue-controller.py"), "controller"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if launch.returncode != 0 and not controller_active():
            set_status(directory, "failed", "queued", code="HOST_MOD_CONTROLLER_START_FAILED")
        return load_status(request["operationId"])
    finally:
        fcntl.flock(admission, fcntl.LOCK_UN); os.close(admission)


def content_files(root):
    entries = []; total = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            info = (current_path / name).lstat()
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or info.st_uid not in (0, 1000, os.geteuid()) or info.st_mode & 0o022): reject("HOST_MOD_CONTENT_UNSAFE")
        for name in filenames:
            path = current_path / name; info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
                    or info.st_uid not in (0, 1000, os.geteuid()) or info.st_mode & 0o022): reject("HOST_MOD_CONTENT_UNSAFE")
            relative = path.relative_to(root).as_posix()
            if not relative or relative.startswith("../") or "\\" in relative: reject("HOST_MOD_CONTENT_UNSAFE")
            digest = hashlib.sha256()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(fd)
                if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                        or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1): reject("HOST_MOD_CONTENT_UNSAFE")
                with os.fdopen(fd, "rb", closefd=False) as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk: break
                        digest.update(chunk)
            finally:
                os.close(fd)
            total += info.st_size
            if total > MAX_BYTES or len(entries) >= MAX_FILES: reject("HOST_MOD_CONTENT_LIMIT")
            entries.append({"path": relative, "sizeBytes": info.st_size, "sha256": digest.hexdigest()})
    entries.sort(key=lambda value: value["path"])
    if not any(entry["path"] == "modinfo.lua" and entry["sizeBytes"] <= 2 * 1024 * 1024 for entry in entries):
        reject("HOST_MOD_CONTENT_INVALID")
    digest = hashlib.sha256(canonical(entries)).hexdigest()
    return entries, total, digest


def modinfo_fields(content):
    try:
        text = (content / "modinfo.lua").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values = {}
    for name in ("name", "author", "version"):
        match = re.search(r"(?mi)^\s*" + name + r"\s*=\s*\"([^\"]*)\"", text)
        if match:
            values[name] = match.group(1).strip()[:200]
    return values


def wait_lock(path, cancel_path):
    safe_dir(path.parent, create=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        os.close(fd)
        reject("HOST_MOD_TASK_LOCK_INVALID")
    while True:
        if cancel_path.exists():
            os.close(fd)
            reject("HOST_MOD_CANCELLED")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
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
    directory = task_dir(task_id); request = read_json(directory / "request.json", 262144)
    context = read_json(directory / "worker-context.json", 65536)
    cancel_path = directory / "cancel.request"
    lease = os.environ.get("FAMINE_CONTROLLER_LEASE") == task_id
    heavy = None
    if not lease:
        heavy = wait_lock(ROOT / "locks" / "heavy.lock", cancel_path)
        dequeue(task_id)
    if cancel_path.exists(): reject("HOST_MOD_CANCELLED")
    try:
        image_ref, target_release_id = verify_scope(request, context.get("image"))
        if image_ref != context.get("image"):
            reject("HOST_MOD_RELEASE_CHANGED")
        if target_release_id != context.get("dstReleaseId"):
            reject("HOST_MOD_RELEASE_CHANGED")
        CURRENT_STAGE = "prepare"
        safe_dir(MODS, create=True); safe_dir(PREPARED, create=True)
        staging_root = MODS / "staging" / task_id
        safe_dir(MODS / "staging", create=True); safe_dir(staging_root, create=True)
        set_status(directory, "running", "prepare", progress={"kind": "determinate", "completed": 0, "total": len(request["workshopIds"])})
        result = task_result(request, [])
        environment = docker_environment()
        source_handles = request.get("sourceHandles", {})
        for index, workshop_id in enumerate(request["workshopIds"]):
            CURRENT_STAGE = "download"
            set_status(directory, "running", "download", completed="prepare",
                progress={"kind": "determinate", "completed": index, "total": len(request["workshopIds"])}, result=result)
            work = staging_root / workshop_id
            safe_dir(work, create=True, runtime=True); os.chown(work, 1000, 1000)
            if os.statvfs(MODS).f_bavail * os.statvfs(MODS).f_frsize < 1024 * 1024 * 1024:
                reject("HOST_MOD_SPACE_INSUFFICIENT")
            run = subprocess.run(["docker", "run", "--rm", "--network", "bridge", "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=1g", "--cpus=2",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--user", "1000:1000",
                "--mount", "type=bind,src=" + str(work) + ",dst=/work", image_ref,
                "/usr/local/bin/famine-workshop-fetch", workshop_id], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=1320, check=False, env=environment)
            if run.returncode != 0:
                diagnostic = run.stderr if isinstance(run.stderr, str) else ""
                if run.returncode == 124:
                    code = "HOST_MOD_STEAMCMD_TIMEOUT"
                elif "WORKSHOP_STEAMCMD_CAPABILITY_MISSING" in diagnostic:
                    code = "HOST_MOD_WORKSHOP_STEAMCMD_MISSING"
                elif "WORKSHOP_CONTENT_INVALID" in diagnostic:
                    code = "HOST_MOD_WORKSHOP_CONTENT_INVALID"
                elif "WORKSHOP_OUTPUT_EXISTS" in diagnostic:
                    code = "HOST_MOD_WORKSHOP_OUTPUT_CONFLICT"
                elif "WORKSHOP_DOWNLOAD_FAILED" in diagnostic:
                    code = "HOST_MOD_STEAMCMD_DOWNLOAD_FAILED"
                elif run.returncode in (125, 126, 127):
                    code = "HOST_MOD_WORKSHOP_CONTAINER_FAILED"
                else:
                    code = "HOST_MOD_WORKSHOP_DOWNLOAD_FAILED"
                details = {"exitCode": run.returncode}
                if code == "HOST_MOD_WORKSHOP_CONTAINER_FAILED":
                    details["failureKind"] = docker_failure_kind(diagnostic)
                reject(code, details)
            CURRENT_STAGE = "verify"
            downloaded = work / "output" / workshop_id
            safe_dir(downloaded, runtime=True)
            files, size_bytes, content_digest = content_files(downloaded)
            final = PREPARED / workshop_id / content_digest
            safe_dir(PREPARED / workshop_id, create=True)
            if final.exists() or final.is_symlink():
                safe_dir(final)
                old = read_json(final / "manifest.json", 4 * 1024 * 1024)
                if old.get("contentDigest") != content_digest: reject("HOST_MOD_PREPARED_ARTIFACT_CONFLICT")
                _, old_size, old_digest = content_files(final / "content")
                if old_digest != content_digest or old_size != old.get("sizeBytes"):
                    reject("HOST_MOD_PREPARED_ARTIFACT_INVALID")
                info = modinfo_fields(final / "content")
                supplied = request.get("metadata", {}).get(workshop_id, {}) if isinstance(request.get("metadata"), dict) else {}
                refreshed = dict(old)
                for field in ("name", "author"):
                    value = info.get(field) or supplied.get(field)
                    if value and value != refreshed.get(field):
                        refreshed[field] = value
                if supplied.get("previewUrl") and supplied.get("previewUrl") != refreshed.get("previewUrl"):
                    refreshed["previewUrl"] = supplied.get("previewUrl")
                handle = source_handles.get(workshop_id)
                if handle and handle != refreshed.get("sourceHandle"):
                    refreshed["sourceHandle"] = handle
                if refreshed != old:
                    atomic_json(final / "manifest.json", refreshed)
                shutil.rmtree(work, ignore_errors=True)
            else:
                CURRENT_STAGE = "publish"
                candidate = staging_root / ("publish-" + workshop_id)
                candidate.mkdir(mode=0o700)
                os.replace(downloaded, candidate / "content")
                info = modinfo_fields(candidate / "content")
                supplied = request.get("metadata", {}).get(workshop_id, {}) if isinstance(request.get("metadata"), dict) else {}
                atomic_json(candidate / "manifest.json", {"schemaVersion": 1, "workshopId": workshop_id,
                    "contentDigest": content_digest, "dstReleaseId": context["dstReleaseId"],
                    "fetcherImage": context["image"],
                    "preparedAt": now(), "fileCount": len(files), "sizeBytes": size_bytes,
                    "sourceHandle": source_handles.get(workshop_id),
                    "name": info.get("name") or supplied.get("name"),
                    "author": info.get("author") or supplied.get("author"),
                    "version": info.get("version"),
                    "previewUrl": supplied.get("previewUrl")})
                for current, _, names in os.walk(candidate / "content"):
                    os.chown(current, 0, 0)
                    os.chmod(current, 0o555)
                    for name in names:
                        path = Path(current) / name
                        os.chown(path, 0, 0)
                        os.chmod(path, 0o444)
                os.chmod(candidate / "manifest.json", 0o600)
                os.rename(candidate, final)
            result["preparedMods"].append({"workshopId": workshop_id, "contentDigest": content_digest,
                "dstReleaseId": context["dstReleaseId"], "fetcherImage": context["image"], "sizeBytes": size_bytes,
                "sourceHandle": source_handles.get(workshop_id)})
            result["remoteChanged"] = True
            set_status(directory, "running", "verify", completed="download",
                progress={"kind": "determinate", "completed": index + 1, "total": len(request["workshopIds"])}, result=result)
        shutil.rmtree(staging_root, ignore_errors=True)
        CURRENT_STAGE = "cleanup"
        set_status(directory, "running", "cleanup", completed="verify", result=result)
        try:
            result["autoCleanupDeleted"] = len(run_library_cleanup(directory, request, result["preparedMods"]))
        except Rejected as failure:
            result["autoCleanupError"] = failure.code
        except Exception:
            result["autoCleanupError"] = "HOST_MOD_CLEANUP_FAILED"
        CURRENT_STAGE = "finalize"
        return set_status(directory, "succeeded", "finalize", completed="cleanup", result=result,
            progress={"kind": "determinate", "completed": len(request["workshopIds"]), "total": len(request["workshopIds"])})
    except Exception as failure:
        if getattr(failure, "code", None) == "HOST_MOD_CANCELLED":
            return set_status(directory, "cancelled", "cancelled")
        try: return set_status(directory, "failed", CURRENT_STAGE, code=getattr(failure, "code", "HOST_MOD_WORKER_FAILED"),
            details=getattr(failure, "details", None))
        except Exception: return None
    finally:
        if heavy is not None:
            fcntl.flock(heavy, fcntl.LOCK_UN); os.close(heavy)


def cancel(request):
    task_id = request.get("operationId")
    if request.get("schemaVersion") != 1 or not valid_uuid(task_id):
        reject("HOST_MOD_TASK_INPUT_INVALID")
    state = load_status(task_id)
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        return state
    atomic_json(TASKS / task_id / "cancel.request", {"requestedAt": now()})
    return state


def status(request):
    task_id = request.get("operationId")
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or any(not valid_uuid(request.get(key)) for key in ("serverId", "hostId")) or not valid_uuid(task_id)):
        reject("HOST_MOD_TASK_INPUT_INVALID")
    state = load_status(task_id)
    if state["status"] == "queued":
        position = queue_position(task_id)
        if position is not None:
            state = dict(state, queuePosition=position)
    expected = {"serverId": request["serverId"], "hostId": request["hostId"]}
    if state["target"] != expected: reject("HOST_MOD_TASK_TARGET_MISMATCH")
    if state["status"] in ("queued", "running"):
        unit = "famine-queue-controller"
        active = subprocess.run(["systemctl", "show", "--property=ActiveState", "--value", unit], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
        if active.returncode != 0 or active.stdout.strip() not in ("active", "activating", "reloading"):
            state = set_status(TASKS / task_id, "interrupted", state["stage"], code="HOST_MOD_WORKER_INTERRUPTED")
    return state


def list_tasks(request):
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or any(not valid_uuid(request.get(key)) for key in ("serverId", "hostId"))):
        reject("HOST_MOD_TASK_INPUT_INVALID")
    result = []
    if TASKS.exists():
        for child in TASKS.iterdir():
            if child.is_dir() and UUID.fullmatch(child.name):
                state = load_status(child.name)
                if state.get("target") == {"serverId": request["serverId"], "hostId": request["hostId"]}:
                    result.append(state)
    result.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return result[:100]


def main(request, worker_source, probe_source=None):
    try:
        action = request.get("action")
        if action == "start": data = {"task": start(request, worker_source, probe_source)}
        elif action == "status": data = {"task": status(request)}
        elif action == "cancel": data = {"task": cancel(request)}
        elif action == "list": data = list_tasks(request)
        else: reject("HOST_MOD_TASK_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "controller":
        controller_main()
    elif len(sys.argv) == 3 and sys.argv[1] == "worker":
        worker(sys.argv[2])
    else:
        main(json.load(sys.stdin), Path(__file__).read_text())
