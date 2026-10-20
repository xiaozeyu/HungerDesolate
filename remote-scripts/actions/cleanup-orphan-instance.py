"""Remove an instance from the active list only after checking its resources."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

ROOT = Path("/var/lib/famine-hub")
REGISTRY = ROOT / "registry.json"
LOCK = ROOT / "locks" / "registry.lock"
INSTANCES = ROOT / "instances"
TASKS = ROOT / "tasks"
MAX_BYTES = 1024 * 1024
MAX_REVISION = 9007199254740991


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
        reject("ORPHAN_INSTANCE_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("ORPHAN_INSTANCE_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("ORPHAN_INSTANCE_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("ORPHAN_INSTANCE_STATE_INVALID")


def atomic_json(path, value):
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
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


def docker_ids(kind, instance_id):
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    noun = "container" if kind == "container" else "network"
    command = ["docker", noun, "ls"]
    if kind == "container":
        command.append("--all")
    command.extend(["--quiet", "--no-trunc", "--filter", "label=com.hungerdesolate.instanceId=" + instance_id])
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=10, check=False, env=environment,
    )
    if result.returncode != 0:
        reject("ORPHAN_INSTANCE_DOCKER_UNAVAILABLE")
    ids = result.stdout.splitlines()
    if len(ids) > 32 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids):
        reject("ORPHAN_INSTANCE_DOCKER_RESPONSE_INVALID")
    return ids


def check_task(instance_id, entry):
    task_ids = set()
    pending = entry.get("pendingTaskId")
    if pending is not None:
        if not valid_uuid(pending):
            reject("ORPHAN_INSTANCE_STATE_INVALID")
        task_ids.add(pending)
    if TASKS.exists():
        info = TASKS.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            reject("ORPHAN_INSTANCE_STATE_INVALID")
        for task_dir in TASKS.iterdir():
            if not task_dir.is_dir() or task_dir.is_symlink() or not valid_uuid(task_dir.name):
                continue
            admission_path = task_dir / "admission.json"
            if admission_path.exists():
                admission = read_json(admission_path, 65536)
                if admission.get("instanceId") == instance_id:
                    task_ids.add(task_dir.name)
    environment = dict(os.environ)
    for task_id in task_ids:
        unit = "famine-instance-" + task_id.replace("-", "")
        active = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=5, check=False, env=environment)
        if active.returncode == 0:
            reject("ORPHAN_INSTANCE_TASK_ACTIVE")
        status_path = TASKS / task_id / "status.json"
        if not status_path.exists():
            reject("ORPHAN_INSTANCE_STATE_INVALID")
        status = read_json(status_path, 65536).get("status")
        if status in ("queued", "running") or status not in ("succeeded", "failed", "cancelled", "interrupted"):
            reject("ORPHAN_INSTANCE_TASK_ACTIVE")


def instance_directory_for_retirement(instance_id):
    try:
        parent_info = INSTANCES.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in (0, os.geteuid())
            or parent_info.st_mode & 0o022):
        reject("ORPHAN_INSTANCE_DATA_PRESENT")
    path = INSTANCES / instance_id
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022):
        reject("ORPHAN_INSTANCE_DATA_PRESENT")
    for child in path.rglob("*"):
        child_info = child.lstat()
        if (stat.S_ISLNK(child_info.st_mode) or child_info.st_uid not in (0, os.geteuid())
                or child_info.st_mode & 0o022
                or not (stat.S_ISDIR(child_info.st_mode) or stat.S_ISREG(child_info.st_mode))):
            reject("ORPHAN_INSTANCE_DATA_PRESENT")
    return path


def remove(request):
    host_id = request.get("hostId")
    instance_id = request.get("instanceId")
    expected_revision = request.get("expectedRegistryRevision")
    typed_name = request.get("typedName")
    if (not valid_uuid(host_id) or not valid_uuid(instance_id)
            or type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION
            or not isinstance(typed_name, str) or not typed_name or len(typed_name) > 128):
        reject("ORPHAN_INSTANCE_INPUT_INVALID")
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1
                or lock_info.st_uid not in (0, os.geteuid()) or lock_info.st_mode & 0o077):
            reject("ORPHAN_INSTANCE_STATE_INVALID")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        host = read_json(ROOT / "host.json", 65536)
        if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
            reject("HOST_IDENTITY_MISMATCH")
        registry = read_json(REGISTRY)
        if (registry.get("schemaVersion") != 1 or registry.get("hostId") != host_id
                or type(registry.get("registryRevision")) is not int
                or not isinstance(registry.get("instances"), list)
                or not isinstance(registry.get("portReservations"), list)):
            reject("ORPHAN_INSTANCE_STATE_INVALID")
        if registry["registryRevision"] != expected_revision:
            reject("REGISTRY_CHANGED")
        matches = [item for item in registry["instances"]
                   if isinstance(item, dict) and item.get("instanceId") == instance_id]
        if len(matches) != 1:
            reject("INSTANCE_NOT_FOUND")
        entry = matches[0]
        if entry.get("displayName") != typed_name:
            reject("INSTANCE_CONFIRMATION_MISMATCH")
        if entry.get("lifecycle") in ("deleting", "deleteFailed", "retired"):
            reject("INSTANCE_CLEANUP_NOT_ALLOWED")
        check_task(instance_id, entry)
        if docker_ids("container", instance_id) or docker_ids("network", instance_id):
            reject("ORPHAN_INSTANCE_RESOURCES_PRESENT")
        instance_path = instance_directory_for_retirement(instance_id)
        has_instance_files = instance_path is not None and any(
            child.is_file() for child in instance_path.rglob("*")
        )
        if registry["registryRevision"] >= MAX_REVISION:
            reject("REGISTRY_REVISION_EXHAUSTED")
        retired_root = ROOT / "retired"
        if retired_root.exists():
            retired_info = retired_root.lstat()
            if (not stat.S_ISDIR(retired_info.st_mode) or retired_info.st_uid not in (0, os.geteuid())
                    or retired_info.st_mode & 0o077):
                reject("ORPHAN_INSTANCE_DATA_PRESENT")
        else:
            retired_root.mkdir(mode=0o700)
        retired_path = retired_root / instance_id
        try:
            retired_path.lstat()
            reject("ORPHAN_INSTANCE_DATA_PRESENT")
        except FileNotFoundError:
            pass

        moved_to_retired = bool(has_instance_files)
        if moved_to_retired:
            os.rename(instance_path, retired_path)
            entry["lifecycle"] = "retired"
            entry["relativePath"] = "retired/" + instance_id
            entry.pop("pendingTaskId", None)
        else:
            if instance_path is not None:
                shutil.rmtree(instance_path)
            registry["instances"] = [item for item in registry["instances"] if item is not entry]
        before = len(registry["portReservations"])
        registry["portReservations"] = [item for item in registry["portReservations"]
                                         if not (isinstance(item, dict) and item.get("hostId") == host_id
                                                 and item.get("instanceId") == instance_id)]
        released_reservations = before - len(registry["portReservations"])
        registry["registryRevision"] += 1
        try:
            atomic_json(REGISTRY, registry)
        except OSError:
            if moved_to_retired:
                os.rename(retired_path, instance_path)
            raise
        return {"hostId": host_id, "instanceId": instance_id,
                "registryRevision": registry["registryRevision"],
                "releasedReservations": released_reservations, "removed": True,
                "retired": moved_to_retired}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(request):
    try:
        print(json.dumps({"ok": True, "data": remove(request)}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "code": "ORPHAN_INSTANCE_CHECK_TIMEOUT"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "code": "ORPHAN_INSTANCE_CLEANUP_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__" and len(sys.argv) > 1:
    main(json.loads(os.read(0, 65536).decode("utf-8")))
