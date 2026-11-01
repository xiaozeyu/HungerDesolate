"""Preview and remove one stopped, identity-verified instance deployment."""
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
import traceback
import uuid

ROOT = Path("/var/lib/famine-hub")
REGISTRY = ROOT / "registry.json"
LOCK = ROOT / "locks" / "registry.lock"
INSTANCES = ROOT / "instances"
TASKS = ROOT / "tasks"
MAX_BYTES = 1024 * 1024
MAX_REVISION = 9007199254740991
DST_RUNTIME_UID = 1000


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


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def read_json(path, limit=MAX_BYTES):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
        reject("INSTANCE_DELETE_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("INSTANCE_DELETE_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("INSTANCE_DELETE_STATE_INVALID")
    try:
        return json.loads(data)
    except (TypeError, ValueError):
        reject("INSTANCE_DELETE_STATE_INVALID")


def atomic_json(path, value):
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
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


def safe_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022):
        reject("INSTANCE_DELETE_STATE_INVALID")
    return info


def docker_environment():
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def docker_run(command, timeout=10):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=timeout,
                                check=False, env=docker_environment())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        reject("INSTANCE_DELETE_DOCKER_UNAVAILABLE")
    if result.returncode != 0:
        reject("INSTANCE_DELETE_DOCKER_UNAVAILABLE")
    return result.stdout


def docker_ids(kind, instance_id):
    noun = "container" if kind == "container" else "network"
    command = ["docker", noun, "ls"]
    if kind == "container":
        command.append("--all")
    command.extend(["--quiet", "--no-trunc", "--filter",
                    "label=com.hungerdesolate.instanceId=" + instance_id])
    values = docker_run(command).splitlines()
    if len(values) > 32 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
        reject("INSTANCE_DELETE_STATE_INVALID")
    return values


def inspect_containers(ids, host_id, instance_id):
    if not ids:
        return []
    rows = docker_run(["docker", "container", "inspect", "--format",
                       "{{json .Config.Labels}}\t{{.State.Status}}", *ids]).splitlines()
    if len(rows) != len(ids):
        reject("INSTANCE_DELETE_STATE_INVALID")
    resources = []
    roles = set()
    for container_id, row in zip(ids, rows):
        parts = row.split("\t")
        if len(parts) != 2:
            reject("INSTANCE_DELETE_STATE_INVALID")
        try:
            labels = json.loads(parts[0])
        except ValueError:
            reject("INSTANCE_DELETE_STATE_INVALID")
        role, status = labels.get("com.hungerdesolate.role"), parts[1]
        if (labels.get("com.hungerdesolate.hostId") != host_id
                or labels.get("com.hungerdesolate.instanceId") != instance_id
                or labels.get("com.hungerdesolate.schemaVersion") != "1"
                or role not in ("master", "caves") or role in roles
                or status not in ("created", "exited", "dead", "running", "paused", "restarting", "removing")):
            reject("INSTANCE_DELETE_RESOURCE_IDENTITY_INVALID")
        roles.add(role)
        resources.append({"id": container_id, "role": role, "status": status})
    return resources


def inspect_networks(ids, host_id, instance_id, container_ids):
    if not ids:
        return []
    rows = docker_run(["docker", "network", "inspect", "--format",
                       "{{.Name}}\t{{json .Labels}}\t{{json .Containers}}", *ids]).splitlines()
    if len(rows) != len(ids):
        reject("INSTANCE_DELETE_STATE_INVALID")
    resources = []
    for network_id, row in zip(ids, rows):
        parts = row.split("\t")
        if len(parts) != 3 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", parts[0]):
            reject("INSTANCE_DELETE_STATE_INVALID")
        try:
            labels, endpoints = json.loads(parts[1]), json.loads(parts[2]) or {}
        except ValueError:
            reject("INSTANCE_DELETE_STATE_INVALID")
        if (labels.get("com.hungerdesolate.hostId") != host_id
                or labels.get("com.hungerdesolate.instanceId") != instance_id
                or labels.get("com.hungerdesolate.schemaVersion") != "1"
                or labels.get("com.hungerdesolate.role") != "network"
                or not isinstance(endpoints, dict)
                or any(container_id not in container_ids for container_id in endpoints)):
            reject("INSTANCE_DELETE_RESOURCE_IDENTITY_INVALID")
        resources.append({"id": network_id, "name": parts[0], "endpointIds": sorted(endpoints)})
    return resources


def task_blockers(instance_id, entry):
    task_ids = set()
    pending = entry.get("pendingTaskId")
    if pending is not None:
        if not valid_uuid(pending):
            reject("INSTANCE_DELETE_STATE_INVALID")
        task_ids.add(pending)
    if TASKS.exists():
        safe_directory(TASKS)
        for task_dir in TASKS.iterdir():
            if not task_dir.is_dir() or task_dir.is_symlink() or not valid_uuid(task_dir.name):
                continue
            admission_path = task_dir / "admission.json"
            if admission_path.exists() and read_json(admission_path, 65536).get("instanceId") == instance_id:
                task_ids.add(task_dir.name)
    blockers = []
    for task_id in task_ids:
        try:
            active = subprocess.run(["systemctl", "is-active", "--quiet",
                                     "famine-instance-" + task_id.replace("-", "")],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=5, check=False,
                                    env=docker_environment())
        except (FileNotFoundError, subprocess.TimeoutExpired):
            reject("INSTANCE_DELETE_TASK_STATE_UNKNOWN")
        if active.returncode == 0:
            blockers.append("ACTIVE_TASK")
        status_path = TASKS / task_id / "status.json"
        if not status_path.exists():
            blockers.append("TASK_STATUS_MISSING")
            continue
        status = read_json(status_path, 65536).get("status")
        if status not in ("succeeded", "failed", "cancelled", "interrupted"):
            blockers.append("ACTIVE_TASK")
    return sorted(set(blockers))


def inspect_instance(request, registry):
    host_id, instance_id = request["hostId"], request["instanceId"]
    entries = [item for item in registry["instances"]
               if isinstance(item, dict) and item.get("instanceId") == instance_id]
    if len(entries) != 1:
        reject("INSTANCE_NOT_FOUND")
    entry = entries[0]
    if entry.get("displayName") != request.get("typedName", entry.get("displayName")):
        reject("INSTANCE_CONFIRMATION_MISMATCH")
    if entry.get("relativePath") not in (None, "instances/" + instance_id):
        reject("INSTANCE_DELETE_STATE_INVALID")
    if entry.get("lifecycle") not in ("creating", "ready", "createFailed", "deleteFailed"):
        reject("INSTANCE_DELETE_NOT_ALLOWED")
    blockers = task_blockers(instance_id, entry)
    container_ids = docker_ids("container", instance_id)
    containers = inspect_containers(container_ids, host_id, instance_id)
    network_ids = docker_ids("network", instance_id)
    networks = inspect_networks(network_ids, host_id, instance_id, set(container_ids))
    if any(item["status"] not in ("created", "exited", "dead") for item in containers):
        blockers.append("CONTAINER_RUNNING")
    instance_path = None
    data_snapshot = None
    has_files = False
    world_data = False
    if INSTANCES.exists():
        safe_directory(INSTANCES)
        instance_path = INSTANCES / instance_id
        if instance_path.exists():
            safe_directory(instance_path)
            entries_snapshot = []
            for child in sorted(instance_path.rglob("*")):
                if len(entries_snapshot) >= 20000:
                    reject("INSTANCE_DELETE_DATA_LIMIT")
                info = child.lstat()
                if (stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.geteuid(), DST_RUNTIME_UID)
                        or info.st_mode & 0o022
                        or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                    reject("INSTANCE_DELETE_DATA_UNSAFE")
                relative = child.relative_to(instance_path).as_posix()
                if relative == "locks" or relative.startswith("locks/"):
                    continue
                entries_snapshot.append([relative, "d" if stat.S_ISDIR(info.st_mode) else "f",
                                         info.st_size, info.st_mtime_ns])
                has_files = has_files or stat.S_ISREG(info.st_mode)
                world_data = world_data or (stat.S_ISREG(info.st_mode)
                                            and (relative.startswith("data/Master/")
                                                 or relative.startswith("data/Caves/")))
            data_snapshot = hashlib.sha256(canonical(entries_snapshot)).hexdigest()
    if registry["registryRevision"] >= MAX_REVISION:
        reject("REGISTRY_REVISION_EXHAUSTED")
    blockers = sorted(set(blockers))
    snapshot = {"hostId": host_id, "instanceId": instance_id,
                "registryRevision": registry["registryRevision"], "displayName": entry.get("displayName"),
                "lifecycle": entry.get("lifecycle"), "containers": containers, "networks": networks,
                "dataSnapshot": data_snapshot, "hasFiles": has_files, "worldData": world_data}
    return {"hostId": host_id, "instanceId": instance_id,
            "registryRevision": registry["registryRevision"], "displayName": entry.get("displayName"),
            "lifecycle": entry.get("lifecycle"), "containers": containers, "networks": networks,
            "dataPresent": has_files, "archived": False, "blockers": blockers,
            "worldData": world_data,
            "allowed": not blockers, "planId": hashlib.sha256(canonical(snapshot)).hexdigest(),
            "snapshot": snapshot, "instancePath": instance_path}


def request_values(request):
    host_id, instance_id = request.get("hostId"), request.get("instanceId")
    revision = request.get("expectedRegistryRevision")
    if (not valid_uuid(host_id) or not valid_uuid(instance_id) or type(revision) is not int
            or revision < 0 or revision > MAX_REVISION):
        reject("INSTANCE_DELETE_INPUT_INVALID")
    return host_id, instance_id, revision


def operation_digest(request):
    operation_id = request.get("operationId")
    if not valid_uuid(operation_id):
        reject("INSTANCE_DELETE_INPUT_INVALID")
    payload = {key: request.get(key) for key in
               ("hostId", "instanceId", "expectedRegistryRevision", "planId", "typedName")}
    return operation_id, "sha256:" + hashlib.sha256(canonical(payload)).hexdigest()


def open_delete_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        os.close(fd)
        reject("INSTANCE_DELETE_STATE_INVALID")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        reject("INSTANCE_DELETE_BUSY")
    return fd


def acquire_delete_locks(instance_id):
    safe_directory(ROOT / "locks")
    locks = []
    try:
        locks.append(open_delete_lock(ROOT / "locks" / "heavy.lock"))
        instance = ROOT / "instances" / instance_id
        if instance.exists():
            safe_directory(instance)
            locks_dir = instance / "locks"
            if locks_dir.exists():
                safe_directory(locks_dir)
                locks.append(open_delete_lock(instance / "locks" / "world-config.lock"))
                locks.append(open_delete_lock(instance / "locks" / "instance.lock"))
        return locks
    except Exception:
        for fd in reversed(locks):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        raise


def locked_operation(request, commit=False):
    if request.get("schemaVersion") != 1:
        reject("SCHEMA_UNSUPPORTED")
    host_id, instance_id, expected_revision = request_values(request)
    if commit and (not isinstance(request.get("planId"), str)
                   or not re.fullmatch(r"[0-9a-f]{64}", request["planId"])
                   or not isinstance(request.get("typedName"), str)
                   or not 1 <= len(request["typedName"]) <= 128):
        reject("INSTANCE_DELETE_INPUT_INVALID")
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    safe_directory(ROOT)
    safe_directory(ROOT / "locks")
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    extra_locks = []
    try:
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1
                or lock_info.st_uid not in (0, os.geteuid()) or lock_info.st_mode & 0o077):
            reject("INSTANCE_DELETE_STATE_INVALID")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        host = read_json(ROOT / "host.json", 65536)
        registry = read_json(REGISTRY)
        if (host.get("schemaVersion") != 1 or host.get("hostId") != host_id
                or registry.get("schemaVersion") != 1 or registry.get("hostId") != host_id
                or type(registry.get("registryRevision")) is not int
                or not isinstance(registry.get("instances"), list)
                or not isinstance(registry.get("portReservations"), list)):
            reject("INSTANCE_DELETE_STATE_INVALID")
        if commit:
            operation_id, payload_digest = operation_digest(request)
            operations = registry.get("operations", [])
            if not isinstance(operations, list):
                reject("INSTANCE_DELETE_STATE_INVALID")
            for operation in operations:
                if isinstance(operation, dict) and operation.get("operationId") == operation_id:
                    saved_digest = operation.get("payloadDigest")
                    if (not isinstance(saved_digest, str)
                            or re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", saved_digest) is None):
                        reject("INSTANCE_DELETE_STATE_INVALID")
                    if saved_digest.removeprefix("sha256:") != payload_digest.removeprefix("sha256:"):
                        reject("OPERATION_ID_REUSED")
                    if not isinstance(operation.get("result"), dict):
                        reject("INSTANCE_DELETE_STATE_INVALID")
                    return operation["result"]
        if registry["registryRevision"] != expected_revision:
            reject("REGISTRY_CHANGED")
        view = inspect_instance(request, registry)
        if not commit:
            view.pop("snapshot")
            view.pop("instancePath")
            return view
        extra_locks = acquire_delete_locks(instance_id)
        if request.get("typedName") != view["displayName"]:
            reject("INSTANCE_CONFIRMATION_MISMATCH")
        if request.get("planId") != view["planId"]:
            reject("INSTANCE_DELETE_PLAN_STALE")
        if not view["allowed"]:
            reject(view["blockers"][0])

        for container in view["containers"]:
            docker_run(["docker", "container", "rm", container["id"]])
        for network in view["networks"]:
            docker_run(["docker", "network", "rm", network["id"]])
        if docker_ids("container", instance_id) or docker_ids("network", instance_id):
            reject("INSTANCE_DELETE_RESOURCES_REMAIN")
        current = read_json(REGISTRY)
        if current.get("registryRevision") != expected_revision:
            reject("REGISTRY_CHANGED")
        confirmed = inspect_instance(request, current)
        if confirmed["containers"] or confirmed["networks"] or confirmed["blockers"]:
            reject("INSTANCE_DELETE_RESOURCES_REMAIN")
        if confirmed["snapshot"]["dataSnapshot"] != view["snapshot"]["dataSnapshot"]:
            reject("INSTANCE_DELETE_PLAN_STALE")

        instance_path = view["instancePath"]
        trash_root = ROOT / "trash"
        if trash_root.exists():
            safe_directory(trash_root)
        else:
            trash_root.mkdir(mode=0o700)
        trash_path = trash_root / operation_id
        if trash_path.exists():
            reject("INSTANCE_DELETE_STATE_INVALID")
        trash_path.mkdir(mode=0o700)
        moved = []

        def move_to_trash(source, name):
            if not source.exists():
                return
            safe_directory(source)
            target = trash_path / name
            os.rename(source, target)
            moved.append((target, source))

        try:
            if instance_path is not None:
                move_to_trash(instance_path, "instance")
            move_to_trash(ROOT / "backups" / instance_id, "backups")
            move_to_trash(ROOT / "retired" / instance_id, "retired")
        except Exception:
            for target, source in reversed(moved):
                os.rename(target, source)
            shutil.rmtree(trash_path, ignore_errors=True)
            raise

        current["instances"] = [item for item in current["instances"]
                                if not (isinstance(item, dict) and item.get("instanceId") == instance_id)]
        before = len(current["portReservations"])
        current["portReservations"] = [item for item in current["portReservations"]
                                        if not (isinstance(item, dict) and item.get("hostId") == host_id
                                                and item.get("instanceId") == instance_id)]
        if current["registryRevision"] >= MAX_REVISION:
            reject("REGISTRY_REVISION_EXHAUSTED")
        current["registryRevision"] += 1
        result = {"hostId": host_id, "instanceId": instance_id,
                  "registryRevision": current["registryRevision"],
                  "releasedReservations": before - len(current["portReservations"]),
                  "removed": True, "retired": False,
                  "removedContainers": len(view["containers"]), "removedNetworks": len(view["networks"])}
        operations = current.setdefault("operations", [])
        if not isinstance(operations, list):
            reject("INSTANCE_DELETE_STATE_INVALID")
        operations.append({"operationId": operation_id, "payloadDigest": payload_digest, "result": result})
        current["operations"] = operations[-200:]
        try:
            atomic_json(REGISTRY, current)
        except OSError:
            committed = False
            try:
                observed = read_json(REGISTRY)
                committed = any(isinstance(item, dict)
                                and item.get("operationId") == operation_id
                                and item.get("payloadDigest") == payload_digest
                                and item.get("result") == result
                                for item in observed.get("operations", []))
            except (OSError, Rejected, TypeError, ValueError, AttributeError):
                pass
            if not committed:
                for target, source in reversed(moved):
                    os.rename(target, source)
                shutil.rmtree(trash_path, ignore_errors=True)
            raise
        shutil.rmtree(trash_path)
        return result
    finally:
        for extra in reversed(extra_locks):
            fcntl.flock(extra, fcntl.LOCK_UN)
            os.close(extra)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(request):
    try:
        action = request.get("action")
        if action == "preview":
            data = locked_operation(request)
        elif action == "delete":
            data = locked_operation(request, commit=True)
        else:
            reject("INSTANCE_DELETE_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "code": "INSTANCE_DELETE_CHECK_TIMEOUT"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        frame = traceback.extract_tb(error.__traceback__)[-1] if error.__traceback__ else None
        details = {"exception": type(error).__name__}
        if frame is not None and type(frame.lineno) is int and frame.lineno > 0:
            details["line"] = frame.lineno
        if isinstance(error, OSError) and type(error.errno) is int:
            details["errno"] = error.errno
        print(json.dumps({"ok": False, "code": "INSTANCE_DELETE_FAILED", "details": details}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__" and len(sys.argv) > 1:
    main(json.loads(sys.stdin.read()))
