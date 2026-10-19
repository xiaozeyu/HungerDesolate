"""Persistent admission and reconciliation for instance-create tasks.

The short registry admission function is supplied by
instance-create-control.py when the scripts are bundled for SSH execution.
This controller owns task files and process supervision; it never reports a
successful deployment before the worker has produced verification evidence.
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid
from datetime import datetime, timezone

ROOT = globals().get("ROOT", Path("/var/lib/famine-hub"))
TASKS = ROOT / "tasks"
MAX_TASKS = 200
MAX_STATUS_BYTES = 65536


class TaskRejected(Exception):
    pass


def reject(code):
    raise TaskRejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def safe_directory(path, create=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o077):
        reject("CREATE_TASK_PATH_UNSAFE")
    return info


def read_bytes(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("CREATE_TASK_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit or opened.st_uid not in (0, os.geteuid())
                or opened.st_mode & 0o077):
            reject("CREATE_TASK_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            return source.read(limit + 1)
    finally:
        os.close(fd)


def read_json(path, limit):
    try:
        return json.loads(read_bytes(path, limit))
    except (TypeError, ValueError):
        reject("CREATE_TASK_STATE_INVALID")


def atomic_bytes(path, payload, mode=0o600):
    safe_directory(path.parent, create=True)
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        os.chmod(path, mode)
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


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def task_path(task_id):
    if not valid_uuid(task_id):
        reject("CREATE_TASK_ID_INVALID")
    return TASKS / task_id


def validate_status(state, task_id):
    if (not isinstance(state, dict) or state.get("schemaVersion") != 1
            or state.get("taskId") != task_id or state.get("operationId") != task_id
            or state.get("operation") != "instanceCreate"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")
            or not isinstance(state.get("sequence"), int) or state["sequence"] < 1
            or not isinstance(state.get("stage"), str) or len(state["stage"]) > 64):
        reject("CREATE_TASK_STATE_INVALID")
    return state


def load_status(task_id):
    directory = task_path(task_id)
    safe_directory(directory)
    return validate_status(read_json(directory / "status.json", MAX_STATUS_BYTES), task_id)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def initial_status(admission):
    return {
        "schemaVersion": 1,
        "taskId": admission["taskId"],
        "operationId": admission["operationId"],
        "target": {"hostId": admission["hostId"], "instanceId": admission["instanceId"]},
        "operation": "instanceCreate",
        "status": "queued",
        "stage": "queued",
        "lastCompletedStage": None,
        "progress": {"kind": "indeterminate"},
        "sequence": 1,
        "createdAt": now(),
        "startedAt": None,
        "finishedAt": None,
        "cancelAllowed": True,
        "observation": "live",
        "result": None,
        "error": None,
    }


def append_journal(directory, event):
    path = directory / "journal.jsonl"
    prior = b""
    if path.exists():
        prior = read_bytes(path, 1024 * 1024)
    line = json.dumps(event, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(prior) + len(line) > 1024 * 1024:
        reject("CREATE_TASK_JOURNAL_LIMIT")
    atomic_bytes(path, prior + line)


def update_status(directory, status, stage, *, last_completed=None, error=None,
                  result=None, observation="live", cancel_allowed=None):
    task_id = directory.name
    current = load_status(task_id)
    sequence = current["sequence"] + 1
    body = dict(current)
    body.update({"status": status, "stage": stage, "sequence": sequence,
                 "observation": observation, "error": error, "result": result})
    if last_completed is not None:
        body["lastCompletedStage"] = last_completed
    if cancel_allowed is not None:
        body["cancelAllowed"] = cancel_allowed
    if status == "running" and body.get("startedAt") is None:
        body["startedAt"] = now()
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        body["finishedAt"] = now()
        body["cancelAllowed"] = False
    write_json(directory / "status.json", body)
    append_journal(directory, {"sequence": sequence, "status": status, "stage": stage,
                                "observation": observation, "at": now()})
    return body


def request_digest(request):
    value = {key: request[key] for key in request if key not in ("action", "workerSource")}
    return __import__("hashlib").sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def task_error(task_id, code, message, retryable, remote_changed=True, target=None):
    return {"schemaVersion": 1, "code": code, "category": "instanceCreate",
            "operation": "instanceCreate", "requestId": None,
            "target": target, "message": message, "retryable": retryable,
            "remoteChanged": remote_changed, "taskId": task_id,
            "recoverySteps": ["核对任务状态和专服列表后再决定继续或清理"], "details": {}}


def recovery_result(host_id, instance_id):
    return {"preparedHost": None, "verifiedRevision": None, "verification": "notVerified",
            "remoteChanged": True, "backupId": None, "deploymentId": None,
            "invalidate": [{"hostId": host_id, "instanceId": None,
                            "domains": ["instances", "overview", "runtime"]}],
            "recoveryActions": [{"action": "resumeCreate", "label": "继续创建", "requiresPreview": True},
                                 {"action": "cleanupCreate", "label": "清理创建现场", "requiresPreview": True},
                                 {"action": "inspectLogs", "label": "查看任务日志", "requiresPreview": False}]}


def start(request, worker_source, runtime_source, release_source, docker_source):
    operation_id = request.get("operationId")
    if not valid_uuid(operation_id) or request.get("action") != "start":
        reject("CREATE_TASK_INPUT_INVALID")
    if not worker_source or not runtime_source or not release_source or not docker_source:
        reject("CREATE_CONTROLLER_BUNDLE_INVALID")
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    safe_directory(ROOT)
    safe_directory(TASKS, create=True)
    lock_fd = os.open(TASKS / "admit.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
            reject("CREATE_TASK_PATH_UNSAFE")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        directory = TASKS / operation_id
        if directory.exists():
            saved = read_json(directory / "request.json", MAX_STATUS_BYTES)
            if saved.get("requestDigest") != request_digest(request):
                reject("OPERATION_ID_REUSED")
            return load_status(operation_id)
        active = [child.name for child in TASKS.iterdir()
                  if child.is_dir() and valid_uuid(child.name)
                  and load_status(child.name).get("status") in ("queued", "running")]
        if len(active) >= 1:
            reject("INSTANCE_CREATE_BUSY")
        admission = globals().get("admit")
        if not callable(admission):
            reject("CREATE_ADMISSION_UNAVAILABLE")
        admitted = admission(request)
        if (admitted.get("operationId") != operation_id or not valid_uuid(admitted.get("instanceId"))
                or admitted.get("hostId") != request.get("hostId")):
            reject("CREATE_ADMISSION_INVALID")
        directory.mkdir(mode=0o700)
        request_record = {"schemaVersion": 1, "requestDigest": request_digest(request),
                          "operationId": operation_id, "planId": request.get("planId"),
                          "hostId": request.get("hostId"), "input": request.get("input"),
                          "admission": admitted}
        write_json(directory / "request.json", request_record)
        write_json(directory / "admission.json", admitted)
        write_json(directory / "status.json", initial_status(admitted))
        append_journal(directory, {"sequence": 1, "status": "queued", "stage": "queued", "at": now()})
        atomic_bytes(directory / "worker.py", worker_source.encode(), 0o700)
        atomic_bytes(directory / "controller-runtime.py", runtime_source.encode(), 0o600)
        atomic_bytes(directory / "instance-release.py", release_source.encode(), 0o600)
        atomic_bytes(directory / "instance-docker.py", docker_source.encode(), 0o600)
        unit = "famine-instance-" + operation_id.replace("-", "")
        launch = subprocess.run(
            ["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect", "--unit", unit,
             "--property=Type=exec", "/usr/bin/python3", "-I", str(directory / "worker.py"), str(directory)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        if launch.returncode != 0:
            return update_status(directory, "failed", "queued", error=task_error(
                operation_id, "CREATE_CONTROLLER_START_FAILED", "未能启动持久创建任务", True))
        return load_status(operation_id)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def worker_active(task_id):
    unit = "famine-instance-" + task_id.replace("-", "")
    result = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=5, check=False)
    return result.returncode == 0


def status(task_id):
    if task_id is None:
        safe_directory(TASKS)
        tasks = [child.name for child in TASKS.iterdir() if child.is_dir() and valid_uuid(child.name)]
        if not tasks:
            return None
        tasks.sort(key=lambda value: load_status(value).get("createdAt", 0), reverse=True)
        task_id = tasks[0]
    state = load_status(task_id)
    if state["status"] == "running" and not worker_active(task_id):
        directory = task_path(task_id)
        return update_status(directory, "interrupted", state["stage"], observation="reconciling",
                             error=task_error(task_id, "CREATE_RESULT_UNKNOWN", "创建任务进程已结束，结果待核对", True))
    return state


def main(request):
    try:
        action = request.get("action")
        if action == "start":
            data = start(request, globals().get("WORKER_SOURCE", ""), globals().get("RUNTIME_SOURCE", ""),
                         globals().get("RELEASE_SOURCE", ""), globals().get("DOCKER_SOURCE", ""))
        elif action == "status":
            data = status(request.get("taskId"))
        else:
            reject("CREATE_TASK_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except TaskRejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "code": "CREATE_CONTROLLER_START_TIMEOUT"}, separators=(",", ":")), flush=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"ok": False, "code": "CREATE_TASK_STATE_INVALID"}, separators=(",", ":")), flush=True)
