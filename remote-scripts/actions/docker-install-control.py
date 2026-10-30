"""Plan, admit and reconcile the root-owned Docker installation worker."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

BOOTSTRAP = Path("/var/lib/famine-hub.bootstrap")
TASKS = BOOTSTRAP / "docker-install"
MAX_TASKS = 100


class Rejected(Exception):
    pass


def reject(code):
    raise Rejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (ValueError, TypeError, AttributeError):
        return False


def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def safe_directory(path, create=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        os.mkdir(path, 0o700)
        info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        reject("INSTALL_TASK_PATH_UNSAFE")
    return info


def read_status(task_id):
    task_dir = TASKS / task_id
    if task_dir.parent != TASKS:
        reject("INSTALL_TASK_ID_INVALID")
    safe_directory(task_dir)
    path = task_dir / "status.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > 65536:
        reject("INSTALL_TASK_STATE_INVALID")
    return json.loads(path.read_text(encoding="utf-8"))


def list_tasks():
    try:
        safe_directory(BOOTSTRAP)
        safe_directory(TASKS)
    except FileNotFoundError:
        return []
    entries = []
    for child in TASKS.iterdir():
        if not valid_uuid(child.name):
            continue
        try:
            state = read_status(child.name)
            if state.get("taskId") != child.name:
                reject("INSTALL_TASK_STATE_INVALID")
            entries.append(state)
        except FileNotFoundError:
            continue
    entries.sort(key=lambda state: state.get("updatedAtUnixSeconds", 0), reverse=True)
    return entries[:MAX_TASKS]


def start(request, worker_source, preflight_source):
    operation_id = request.get("operationId")
    if not valid_uuid(operation_id):
        reject("INPUT_INVALID")
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    safe_directory(BOOTSTRAP, create=True)
    safe_directory(TASKS, create=True)
    acceptance_lock = os.open(TASKS / "admit.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(acceptance_lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            reject("INSTALL_TASK_PATH_UNSAFE")
        fcntl.flock(acceptance_lock, fcntl.LOCK_EX)
        current = inspect()
        if current["status"] != "ready":
            reject("PLAN_STALE")
        if current["snapshotDigest"] != request.get("snapshotDigest"):
            reject("PLAN_STALE")
        expiry = request.get("expiresAtUnixSeconds")
        if type(expiry) is not int or expiry < int(time.time()) or expiry > int(time.time()) + 600:
            reject("PLAN_STALE")
        existing = TASKS / operation_id
        if existing.exists():
            state = read_status(operation_id)
            config = json.loads((existing / "plan.json").read_text(encoding="utf-8"))
            if config.get("snapshotDigest") != request["snapshotDigest"]:
                reject("OPERATION_ID_REUSED")
            return state
        for state in list_tasks():
            if state.get("status") in ("queued", "running"):
                reject("INSTALL_ALREADY_RUNNING")
        task_dir = TASKS / operation_id
        os.mkdir(task_dir, 0o700)
        directory_fd = os.open(task_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            plan = {"taskId": operation_id, "snapshotDigest": current["snapshotDigest"]}
            write_new(task_dir / "plan.json", json.dumps(plan, separators=(",", ":")).encode())
            write_new(task_dir / "preflight.py", preflight_source.encode())
            write_new(task_dir / "worker.py", worker_source.encode())
            state = {"schemaVersion": 1, "taskId": operation_id, "sequence": 1,
                     "status": "queued", "phase": "accepted", "messageCode": None,
                     "startedAtUnixSeconds": int(time.time()), "updatedAtUnixSeconds": int(time.time()),
                     "installedVersions": {}}
            write_new(task_dir / "status.json", json.dumps(state, separators=(",", ":")).encode())
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        unit = "famine-docker-" + operation_id.replace("-", "")
        result = subprocess.run(
            ["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect", "--unit", unit,
             "--property=Type=exec", "/usr/bin/python3", "-I", str(task_dir / "worker.py"), str(task_dir)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        if result.returncode != 0:
            state["status"] = "failed"
            state["phase"] = "failed"
            state["messageCode"] = "INSTALL_CONTROLLER_START_FAILED"
            state["sequence"] += 1
            state["updatedAtUnixSeconds"] = int(time.time())
            temporary = task_dir / (".status-" + str(uuid.uuid4()))
            write_new(temporary, json.dumps(state, separators=(",", ":")).encode())
            os.replace(temporary, task_dir / "status.json")
            return state
        return state
    finally:
        os.close(acceptance_lock)


def main(request):
    try:
        action = request.get("action")
        if action == "preview":
            data = inspect()
        elif action == "status":
            task_id = request.get("taskId")
            data = read_status(task_id) if task_id else (list_tasks()[0] if list_tasks() else None)
        elif action == "start":
            data = start(request, WORKER_SOURCE, PREFLIGHT_SOURCE)
        else:
            reject("ACTION_INVALID")
        response = {"ok": True, "data": data}
    except Rejected as error:
        response = {"ok": False, "code": str(error)}
    except subprocess.TimeoutExpired:
        response = {"ok": False, "code": "INSTALL_CONTROLLER_START_TIMEOUT"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        response = {"ok": False, "code": "INSTALL_TASK_STATE_INVALID"}
    print(json.dumps(response, separators=(",", ":")), flush=True)
"""Persistent Docker install task admission and reconciliation controller."""
import base64
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import uuid

BOOTSTRAP = Path("/var/lib/famine-hub.bootstrap")
TASKS = BOOTSTRAP / "docker-install"
MAX_TASKS = 100


class Rejected(Exception):
    pass


def reject(code):
    raise Rejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (ValueError, TypeError, AttributeError):
        return False


def safe_directory(path, create=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        os.mkdir(path, 0o700)
        info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        reject("INSTALL_TASK_PATH_UNSAFE")
    return info


def write_new(path, content, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(content)
            output.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_state(task_dir, state):
    state["sequence"] = state.get("sequence", 0) + 1
    state["updatedAtUnixSeconds"] = int(time.time())
    temporary = task_dir / (".state-" + str(uuid.uuid4()))
    write_new(temporary, json.dumps(state, separators=(",", ":")).encode())
    os.replace(temporary, task_dir / "status.json")
    directory = os.open(task_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return state


def read_state(task_id):
    if not valid_uuid(task_id):
        reject("INSTALL_TASK_ID_INVALID")
    task_dir = TASKS / task_id
    info = task_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        reject("INSTALL_TASK_PATH_UNSAFE")
    path = task_dir / "status.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 65536:
        reject("INSTALL_TASK_STATE_INVALID")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("taskId") != task_id or value.get("status") not in {"queued", "running", "succeeded", "failed", "interrupted"}:
        reject("INSTALL_TASK_STATE_INVALID")
    return value


def list_tasks():
    try:
        safe_directory(BOOTSTRAP)
        safe_directory(TASKS)
    except FileNotFoundError:
        return []
    entries = []
    for child in TASKS.iterdir():
        if not valid_uuid(child.name):
            continue
        try:
            entries.append(read_state(child.name))
        except FileNotFoundError:
            continue
    entries.sort(key=lambda item: item.get("updatedAtUnixSeconds", 0), reverse=True)
    return entries[:MAX_TASKS]


def start(request, worker_source, preflight_source):
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    task_id = request.get("operationId")
    plan_id = request.get("planId")
    if not valid_uuid(task_id) or not valid_uuid(plan_id):
        reject("INPUT_INVALID")
    safe_directory(BOOTSTRAP)
    safe_directory(TASKS, create=True)
    admission = os.open(TASKS / "admission.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(admission)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            reject("INSTALL_TASK_PATH_UNSAFE")
        fcntl.flock(admission, fcntl.LOCK_EX)
        if task_id in {item["taskId"] for item in list_tasks()}:
            state = read_state(task_id)
            config = json.loads((TASKS / task_id / "plan.json").read_text(encoding="utf-8"))
            if config.get("snapshotDigest") != request.get("snapshotDigest") or config.get("planId") != plan_id:
                reject("OPERATION_ID_REUSED")
            return state
        # Revalidate identity, packages and APT state without another transient network gate.
        current = inspect(check_repository_access=False)
        expires = request.get("expiresAtUnixSeconds")
        if type(expires) is not int or expires <= int(time.time()) or expires > int(time.time()) + 600:
            reject("PLAN_EXPIRED")
        if current.get("hostId") != request.get("hostId"):
            reject("INSTALL_HOST_CHANGED")
        if current["status"] != "ready":
            reject("INSTALL_PREFLIGHT_BLOCKED")
        if current["snapshotDigest"] != request.get("snapshotDigest"):
            reject("INSTALL_SNAPSHOT_CHANGED")
        if any(item["status"] in {"queued", "running"} for item in list_tasks()):
            reject("INSTALL_ALREADY_RUNNING")
        task_dir = TASKS / task_id
        os.mkdir(task_dir, 0o700)
        directory = os.open(task_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            plan = {"planId": plan_id, "taskId": task_id, "hostId": current["hostId"], "snapshotDigest": current["snapshotDigest"]}
            state = {"schemaVersion": 1, "taskId": task_id, "sequence": 1, "status": "queued",
                     "phase": "accepted", "messageCode": None, "startedAtUnixSeconds": int(time.time()),
                     "updatedAtUnixSeconds": int(time.time()), "installedVersions": {}, "hostId": current["hostId"]}
            write_new(task_dir / "plan.json", json.dumps(plan, separators=(",", ":")).encode())
            write_new(task_dir / "preflight.py", preflight_source.encode())
            write_new(task_dir / "worker.py", worker_source.encode())
            write_new(task_dir / "status.json", json.dumps(state, separators=(",", ":")).encode())
            os.fsync(directory)
        finally:
            os.close(directory)
        unit = "famine-docker-" + task_id.replace("-", "")
        result = subprocess.run(
            ["systemd-run", "--quiet", "--no-block", "--collect", "--unit", unit,
             "--property=Type=exec", "/usr/bin/python3", "-I", str(task_dir / "worker.py"), str(task_dir)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        if result.returncode != 0:
            state.update({"status": "failed", "phase": "failed", "messageCode": "INSTALL_CONTROLLER_START_FAILED"})
            write_state(task_dir, state)
        return read_state(task_id)
    finally:
        os.close(admission)


def status(task_id=None):
    if task_id is not None:
        if not valid_uuid(task_id):
            reject("INSTALL_TASK_ID_INVALID")
        try:
            safe_directory(BOOTSTRAP)
            safe_directory(TASKS)
            safe_directory(TASKS / task_id)
        except FileNotFoundError:
            return None
        # A missing task is distinct from a task directory with a missing/corrupt status.
        try:
            state = read_state(task_id)
        except FileNotFoundError:
            reject("INSTALL_TASK_STATE_INVALID")
    else:
        values = list_tasks()
        if not values:
            return None
        state = values[0]
    if state["status"] in {"queued", "running"}:
        unit = "famine-docker-" + state["taskId"].replace("-", "") + ".service"
        result = subprocess.run(["systemctl", "show", "--property=ActiveState", "--value", unit],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
        if result.returncode != 0 or (result.stdout.strip() not in {"active", "activating", "deactivating", "reloading"}
                                      and int(time.time()) - state.get("updatedAtUnixSeconds", 0) > 20):
            task_dir = TASKS / state["taskId"]
            state.update({"status": "interrupted", "phase": "interrupted", "messageCode": "INSTALL_WORKER_INTERRUPTED"})
            state = write_state(task_dir, state)
    return state


def main(request):
    try:
        action = request.get("action")
        if action == "preview":
            data = inspect()
        elif action == "start":
            data = start(request, WORKER_SOURCE, PREFLIGHT_SOURCE)
        elif action == "status":
            data = status(request.get("taskId"))
        else:
            reject("ACTION_INVALID")
        result = {"ok": True, "data": data}
    except Rejected as error:
        result = {"ok": False, "code": str(error)}
    except subprocess.TimeoutExpired:
        result = {"ok": False, "code": "INSTALL_CONTROLLER_START_TIMEOUT"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result = {"ok": False, "code": "INSTALL_TASK_STATE_INVALID"}
    print(json.dumps(result, separators=(",", ":")), flush=True)
