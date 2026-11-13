"""Identity-bound world restore task with protection point and rollback."""
import configparser
import fcntl
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
TASKS = ROOT / "restore-tasks"
RESTORE_ACCEPTANCE = ROOT / "restore-acceptance.json"
MAX_REVISION = 9007199254740991
DST_RUNTIME_UID = 1000
RUNTIME_READY_SECONDS = 120
MISSING = object()


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
        reject("BACKUP_PATH_UNSAFE", {"failurePath": str(path)})
    return info


def read_json(path, limit=262144, missing=MISSING):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing is not MISSING:
            return missing
        raise
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


def registry_lock():
    safe_dir(ROOT / "locks")
    fd = os.open(ROOT / "locks" / "registry.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def abandon_stale_pending(task_id):
    directory = TASKS / task_id
    try:
        state = read_json(directory / "status.json")
        request = read_json(directory / "request.json")
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if state.get("status") != "queued":
        return False
    state.update({"status": "interrupted", "stage": state.get("stage", "queued"),
                  "sequence": state.get("sequence", 0) + 1, "finishedAt": now(),
                  "cancelAllowed": False, "observation": "reconciling",
                  "error": error_payload("BACKUP_RESTORE_INTERRUPTED", request, task_id, False,
                                         {"stalePending": True})})
    atomic_json(directory / "status.json", state)
    journal = directory / "journal.jsonl"
    previous = journal.read_bytes() if journal.exists() else b""
    atomic_bytes(journal, previous + canonical({"sequence": state["sequence"], "status": "interrupted",
                                                 "stage": state["stage"], "at": now()}) + b"\n")
    return True


def admit_queue(request):
    task_id = request["operationId"]
    lock_fd = registry_lock()
    try:
        registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
        if (registry.get("schemaVersion") != 1 or registry.get("hostId") != request["hostId"]
                or not isinstance(registry.get("instances"), list)):
            reject("BACKUP_REGISTRY_INVALID")
        if not isinstance(registry.get("heavyQueue"), list):
            registry["heavyQueue"] = []
        entry = next((item for item in registry["instances"]
                      if isinstance(item, dict) and item.get("instanceId") == request["instanceId"]), None)
        if entry is None:
            reject("BACKUP_REGISTRY_INVALID")
        pending = entry.get("pendingTaskId")
        if pending and pending != task_id:
            pending_state = read_json(TASKS / pending / "status.json", missing=None)
            terminal = isinstance(pending_state, dict) and pending_state.get("status") in (
                "succeeded", "failed", "cancelled", "interrupted")
            stale_queued = (isinstance(pending_state, dict)
                            and pending_state.get("status") == "queued"
                            and not controller_active()
                            and not queued_recently(pending_state))
            if stale_queued:
                abandon_stale_pending(pending)
                entry["pendingTaskId"] = None
                registry["heavyQueue"] = [item for item in registry["heavyQueue"]
                                          if item.get("taskId") != pending]
            elif not terminal:
                reject("INSTANCE_BUSY")
            else:
                entry["pendingTaskId"] = None
        entry["pendingTaskId"] = task_id
        queue = [item for item in registry["heavyQueue"] if item.get("taskId") != task_id]
        queue.append({"taskId": task_id, "operation": "backupRestore", "instanceId": request["instanceId"],
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


def load(task_id):
    if not valid_uuid(task_id):
        reject("BACKUP_TASK_INPUT_INVALID")
    directory = TASKS / task_id
    safe_dir(directory)
    state = read_json(directory / "status.json")
    if (state.get("schemaVersion") != 1 or state.get("taskId") != task_id
            or state.get("operationId") != task_id or state.get("operation") != "backupRestore"
            or state.get("status") not in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")):
        reject("BACKUP_TASK_STATE_INVALID")
    state["journal"] = journal_tail(directory)
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        release_queue(directory.name)
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
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        state["finishedAt"] = now()
        state["cancelAllowed"] = False
    atomic_json(directory / "status.json", state)
    journal = directory / "journal.jsonl"
    previous = journal.read_bytes() if journal.exists() else b""
    atomic_bytes(journal, previous + canonical({"sequence": state["sequence"], "status": status, "stage": stage, "at": now()}) + b"\n")
    state["journal"] = journal_tail(directory)
    if status in ("succeeded", "failed", "cancelled", "interrupted"):
        release_queue(directory.name)
    return state


def error_payload(code, request, task_id, remote_changed=True, details=None):
    protected = request.get("createProtectionBackup", True)
    message = "恢复任务未能完成，已保留现场和保护点" if protected else "恢复任务未能完成，已保留现场"
    recovery_steps = ["核对保护点和恢复现场后重试"] if protected else ["核对恢复现场后重试"]
    return {"schemaVersion": 1, "code": code, "category": "backupRestore", "operation": "backupRestore",
            "requestId": None, "target": {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")},
            "message": message, "retryable": True,
            "remoteChanged": remote_changed, "taskId": task_id, "recoverySteps": recovery_steps,
            "details": details if isinstance(details, dict) else {}}


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


def verify_runtime_processes(instance, instance_id, manifest, roles, evidence=None):
    def fail(check, shard=None, missing_ports=None):
        if isinstance(evidence, dict):
            evidence.clear()
            evidence.update({"check": check})
            if shard in ("master", "caves"):
                evidence["shard"] = shard
            if isinstance(missing_ports, list):
                evidence["missingPorts"] = sorted(port for port in missing_ports
                    if type(port) is int and 1 <= port <= 65535)[:16]
        return False

    result = subprocess.run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                             "--filter", "label=com.hungerdesolate.instanceId=" + instance_id],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            timeout=15, check=False, env=docker_env())
    container_ids = result.stdout.splitlines()
    if result.returncode != 0 or len(container_ids) != len(roles):
        return fail("containerInventory")
    inspected = subprocess.run(["docker", "container", "inspect", "--format",
                                "{{json .Config.Labels}}\t{{.State.Status}}", *container_ids],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                               timeout=15, check=False, env=docker_env())
    if inspected.returncode != 0:
        return fail("containerInspection")
    by_role = {}
    try:
        for row in inspected.stdout.splitlines():
            labels_raw, separator, state = row.partition("\t")
            labels = json.loads(labels_raw)
            role = labels.get("com.hungerdesolate.role") if isinstance(labels, dict) else None
            if (not separator or labels.get("com.hungerdesolate.hostId") != manifest["hostId"]
                    or labels.get("com.hungerdesolate.instanceId") != instance_id
                    or role not in roles or role in by_role or state != "running"):
                return fail("containerIdentityOrState", role)
            by_role[role] = container_ids[len(by_role)]
    except (ValueError, KeyError, TypeError):
        return fail("containerLabels")
    if set(by_role) != set(roles):
        return fail("shardSet")

    config_root = instance / "config" / "Cluster_1"
    expected = {}
    try:
        for role in roles:
            settings = configparser.ConfigParser(interpolation=None)
            settings.read(config_root / role.title() / "server.ini", encoding="utf-8")
            ports = {settings.getint("NETWORK", "server_port"),
                     settings.getint("STEAM", "master_server_port")}
            if role == "master" and manifest.get("cavesEnabled") is True:
                cluster = configparser.ConfigParser(interpolation=None)
                cluster.read(config_root / "cluster.ini", encoding="utf-8")
                ports.add(cluster.getint("SHARD", "master_port"))
            if not ports or any(port < 1 or port > 65535 for port in ports):
                return fail("configuredPorts", role)
            expected[role] = ports
    except (OSError, configparser.Error, UnicodeError, ValueError):
        return fail("serverConfiguration")

    for role, container_id in by_role.items():
        process_probe = ('for p in /proc/[0-9]*/exe; do x=$(readlink "$p" 2>/dev/null) || continue; '
                         'if [ "$x" = /srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64 ]; '
                         "then printf DST_RUNNING; exit 0; fi; done; printf DST_NOT_FOUND")
        process = subprocess.run(["docker", "exec", container_id, "/bin/sh", "-c", process_probe],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 timeout=10, check=False, env=docker_env())
        sockets = subprocess.run(["docker", "exec", container_id, "/bin/sh", "-c",
                                  "cat /proc/net/udp && { if [ -r /proc/net/udp6 ]; then cat /proc/net/udp6; fi; }"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 timeout=10, check=False, env=docker_env())
        if process.returncode != 0 or process.stdout.strip() != "DST_RUNNING" or sockets.returncode != 0:
            return fail("dstProcessOrUdpProbe", role)
        observed = set()
        for line in sockets.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2 or ":" not in fields[1]:
                continue
            try:
                observed.add(int(fields[1].rsplit(":", 1)[1], 16))
            except ValueError:
                return fail("udpSocketData", role)
        missing_ports = expected[role] - observed
        if missing_ports:
            return fail("udpListeners", role, list(missing_ports))
    if isinstance(evidence, dict):
        evidence.clear()
        evidence.update({"check": "passed"})
    return True


def wait_for_runtime_processes(instance, instance_id, manifest, roles, evidence=None,
                               timeout=RUNTIME_READY_SECONDS):
    deadline = time.monotonic() + timeout
    while True:
        if verify_runtime_processes(instance, instance_id, manifest, roles, evidence):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(2)


def remove_path(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def restore_changed_live_state(stop_attempted, stopped, moved):
    return stop_attempted or stopped or bool(moved)


def same_runtime_degradation(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if left.get("check") != right.get("check"):
        return False
    if left.get("shard") != right.get("shard"):
        return False
    left_ports = left.get("missingPorts")
    right_ports = right.get("missingPorts")
    if isinstance(left_ports, list) or isinstance(right_ports, list):
        return sorted(left_ports or []) == sorted(right_ports or [])
    left_state = left.get("state")
    right_state = right.get("state")
    return left_state == right_state


def restore_transaction_snapshot(instance, recovery, transaction):
    rollback_paths = transaction.get("rollbackPaths")
    original_paths = transaction.get("originalPaths")
    if (not isinstance(rollback_paths, list) or not isinstance(original_paths, list)
            or any(not isinstance(path, str) for path in rollback_paths + original_paths)):
        reject("BACKUP_RECOVERY_REQUIRED")
    original_set = set(original_paths)
    for relative in reversed(rollback_paths):
        old = recovery / relative
        current = instance / relative
        if old.exists() or old.is_symlink():
            if current.exists() or current.is_symlink():
                remove_path(current)
            current.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(old, current)
        elif relative not in original_set and (current.exists() or current.is_symlink()):
            remove_path(current)
    atomic_json(instance / "active-release.json", transaction["activeRelease"])
    atomic_json(instance / "manifest.json", transaction["manifest"])
    atomic_json(instance / "desired" / "world.json", transaction["desiredWorld"])
    atomic_json(instance / "deployed-world.json", transaction["deployedWorld"])
    if transaction.get("desiredMods") is None:
        (instance / "desired" / "mods.json").unlink(missing_ok=True)
    else:
        atomic_json(instance / "desired" / "mods.json", transaction["desiredMods"])
    if transaction.get("deployedMods") is None:
        (instance / "deployed-mods.json").unlink(missing_ok=True)
    else:
        atomic_json(instance / "deployed-mods.json", transaction["deployedMods"])
    atomic_bytes(instance / "compose.yaml", transaction["compose"].encode("utf-8"), 0o600)


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


def compose(instance, manifest, action, *arguments):
    args = []
    for argument in arguments:
        args.extend(argument if isinstance(argument, (list, tuple)) else [argument])
    result = subprocess.run(["docker", "compose", "-p", manifest["composeProject"], "-f", str(instance / "compose.yaml"), action, *args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False, env=docker_env())
    if result.returncode != 0:
        reject("BACKUP_STOP_FAILED" if action == "stop" else "BACKUP_RESTART_FAILED")


def wait_lock(handle, cancel_path):
    while True:
        if cancel_path.exists():
            reject("BACKUP_CANCELLED")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(0.5)


def controller_active():
    try:
        result = subprocess.run(["systemctl", "is-active", "--quiet", "famine-queue-controller"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def wait_controller_active(seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if controller_active():
            return True
        time.sleep(0.1)
    return controller_active()


def launch_controller():
    try:
        result = subprocess.run(["/usr/bin/systemd-run", "--quiet", "--no-block", "--collect",
                                 "--unit", "famine-queue-controller", "--property=Type=exec",
                                 "/usr/bin/python3", "-I", str(ROOT / "queue-controller.py"), "controller"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def queued_recently(state, seconds=20):
    try:
        created = datetime.fromisoformat(state["createdAt"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() < seconds


def wait_controller_lock(fd, cancel_path):
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if cancel_path.exists():
                return False
            time.sleep(0.5)


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
            if not valid_uuid(task_id) or task_dir is None:
                dequeue(task_id)
                release_queue(task_id)
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
            cancel_path = task_dir / task_id / "cancel.request"
            instance_id = request.get("instanceId")
            instance = ROOT / "instances" / instance_id if isinstance(instance_id, str) else None
            heavy = maintenance = instance_lock = None
            try:
                heavy = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                if not wait_controller_lock(heavy, cancel_path):
                    set_status(task_dir / task_id, "cancelled", "cancelled")
                    dequeue(task_id)
                    release_queue(task_id)
                    continue
                if instance is not None:
                    safe_dir(instance / "locks")
                    maintenance = os.open(instance / "locks" / "world-config.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    if not wait_controller_lock(maintenance, cancel_path):
                        set_status(task_dir / task_id, "cancelled", "cancelled")
                        dequeue(task_id)
                        release_queue(task_id)
                        continue
                    instance_lock = os.open(instance / "locks" / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    if not wait_controller_lock(instance_lock, cancel_path):
                        set_status(task_dir / task_id, "cancelled", "cancelled")
                        dequeue(task_id)
                        release_queue(task_id)
                        continue
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


def mod_warnings(instance, metadata):
    desired_path = instance / "desired" / "mods.json"
    desired = read_json(desired_path, 2 * 1024 * 1024) if desired_path.exists() else {}
    current = {str(item.get("workshopId")): item for item in desired.get("mods", [])
               if isinstance(item, dict)} if isinstance(desired, dict) else {}
    warnings = []
    for item in metadata.get("mods", []):
        if not isinstance(item, dict) or item.get("enabled") is not True:
            continue
        workshop_id = str(item.get("workshopId"))
        current_item = current.get(workshop_id)
        if current_item is None or current_item.get("enabled") is not True:
            warnings.append("MOD_MISSING:" + workshop_id)
        elif current_item.get("artifactDigest") != item.get("artifactDigest"):
            warnings.append("MOD_VERSION_CHANGED:" + workshop_id)
    return warnings


def _worker_impl(task_id):
    directory = TASKS / task_id
    cancel_path = directory / "cancel.request"
    state = load(task_id)
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        return state
    request = read_json(directory / "request.json")
    set_status(directory, "running", "preflight")
    module = {}
    exec(compile((directory / "archive-source.py").read_text(), "<backup-archive>", "exec"), module)
    instance, manifest = verify_instance(request)
    runtime_before, roles = runtime(request["instanceId"], manifest)
    lease = os.environ.get("FAMINE_CONTROLLER_LEASE") == task_id
    heavy = maintenance = instance_lock = None
    if not lease:
        heavy = open(ROOT / "locks" / "heavy.lock", "a+b")
        maintenance = open(instance / "locks" / "world-config.lock", "a+b")
        instance_lock = open(instance / "locks" / "instance.lock", "a+b")
        wait_lock(heavy, cancel_path)
        dequeue(task_id)
        wait_lock(maintenance, cancel_path)
        wait_lock(instance_lock, cancel_path)
    if cancel_path.exists(): reject("BACKUP_CANCELLED")
    protection_id = str(uuid.uuid4())
    recovery = instance / (".restore-recovery-" + task_id)
    staging = instance / (".restore-staging-" + task_id)
    moved = []
    stopped = False
    runtime_verified = False
    restore_runtime_evidence = {}
    rollback_runtime_evidence = {}
    original_manifest = dict(manifest)
    active_path = instance / "active-release.json"
    original_active = read_json(active_path, 65536)
    failure_step = "verifyTarget"
    stop_attempted = False
    try:
        instance, manifest = verify_instance(request)
        runtime_before, roles = runtime(request["instanceId"], manifest)
        original_manifest = dict(manifest)
        original_active = read_json(active_path, 65536)
        backup_dir = ROOT / "backups" / request["instanceId"] / request["backupId"]
        failure_step = "readBackupMetadata"
        metadata = read_json(backup_dir / "metadata.json")
        if (metadata.get("schemaVersion") != 1 or metadata.get("backupId") != request["backupId"]
                or metadata.get("hostId") != request["hostId"] or metadata.get("instanceId") != request["instanceId"]
                or metadata.get("verification") != "verified"):
            reject("BACKUP_TARGET_MISMATCH")
        checksum = metadata.get("checksum")
        failure_step = "inspectBackupArchive"
        module["inspect_archive"](backup_dir / "archive.tar.gz", request["hostId"], request["instanceId"], checksum)
        set_status(directory, "running", "stagingVerify", completed="preflight")
        failure_step = "extractBackupArchive"
        module["extract_to_staging"](backup_dir / "archive.tar.gz", staging, request["hostId"], request["instanceId"], checksum)
        failure_step = "assignRuntimeOwnership"
        for relative in ("data/Master", "data/Caves"):
            if (staging / relative).exists():
                assign_runtime_ownership(staging / relative)
        failure_step = "writeRestoreTransaction"
        rollback_paths = ["data/Master", "data/Caves"]
        original_paths = [relative for relative in rollback_paths
                          if (instance / relative).exists() or (instance / relative).is_symlink()]
        runtime_before_evidence = {}
        if runtime_before == "running":
            try:
                if not verify_runtime_processes(instance, request["instanceId"], original_manifest, roles,
                                                runtime_before_evidence):
                    runtime_before_evidence = dict(runtime_before_evidence)
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                runtime_before_evidence = {"check": "baselineProbeUnavailable"}
        atomic_json(directory / "transaction.json", {"schemaVersion": 1, "operationId": task_id,
            "runtimeBefore": runtime_before, "manifest": original_manifest, "activeRelease": original_active,
            "desiredWorld": read_json(instance / "desired" / "world.json", 262144),
            "deployedWorld": read_json(instance / "deployed-world.json", 262144),
            "desiredMods": read_json(instance / "desired" / "mods.json", 2 * 1024 * 1024, missing=None),
            "deployedMods": read_json(instance / "deployed-mods.json", 2 * 1024 * 1024, missing=None),
            "compose": (instance / "compose.yaml").read_text(encoding="utf-8"),
            "rollbackPaths": rollback_paths, "originalPaths": original_paths,
            "runtimeBeforeEvidence": runtime_before_evidence,
            "recoveryPath": str(recovery), "stagingPath": str(staging), "protectionBackupId": None})
        if runtime_before in ("running", "degraded"):
            failure_step = "stopCurrentRuntime"
            stop_attempted = True
            compose(instance, manifest, "stop", roles)
            stopped = True
            runtime_after, _ = runtime(request["instanceId"], manifest)
            if runtime_after != "stopped":
                reject("BACKUP_STOP_FAILED")
        set_status(directory, "running", "stop", completed="stagingVerify")
        protection_created = request.get("createProtectionBackup", True)
        if protection_created:
            failure_step = "createProtectionPoint"
            protection_dir = ROOT / "backups" / request["instanceId"] / protection_id
            protection_dir.mkdir(mode=0o700, parents=False)
            protection_archive = protection_dir / "archive.tar.gz"
            identity = {"schemaVersion": 1, "hostId": request["hostId"], "instanceId": request["instanceId"],
                        "backupId": protection_id, "captureMode": "cold", "runtimeStatus": "stopped",
                        "runtimeVerified": True, "reason": "恢复前保护点"}
            protection_roots = ["data/Master"] + (["data/Caves"] if manifest.get("cavesEnabled") else [])
            created = module["create_archive"](instance, protection_archive, identity, protection_roots)
            atomic_json(protection_dir / "metadata.json", {"schemaVersion": 1, "backupId": protection_id,
                "hostId": request["hostId"], "instanceId": request["instanceId"], "createdAt": now(),
                "reason": "恢复前保护点", "captureMode": "cold", "captureSemantics": "stoppedBeforeRead",
                "runtimeStatusAtCapture": "stopped", "sizeBytes": created["archiveSizeBytes"],
                "checksum": created["checksum"], "verification": "verified", "restorability": "restorable",
                "dstBuild": metadata.get("dstBuild"), "mods": metadata.get("mods", []),
                "containsSecrets": False})
            transaction = read_json(directory / "transaction.json")
            transaction["protectionBackupId"] = protection_id
            atomic_json(directory / "transaction.json", transaction)
            set_status(directory, "running", "backup", completed="stop", result={"protectionBackupId": protection_id})
            set_status(directory, "running", "restoreCommit", completed="backup")
        else:
            set_status(directory, "running", "restoreCommit", completed="stop")
        recovery.mkdir(mode=0o700)
        relative_paths = ["data/Master", "data/Caves"]
        failure_step = "commitRestoredFiles"
        for relative in relative_paths:
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
        if runtime_before == "running":
            failure_step = "createRestoredContainers"
            compose(instance, manifest, "create", "--force-recreate", *roles)
            set_status(directory, "running", "start", completed="restoreCommit")
            failure_step = "startRestoredContainers"
            compose(instance, manifest, "start", roles)
            failure_step = "verifyRestoredRuntime"
            if not wait_for_runtime_processes(instance, request["instanceId"], manifest, roles,
                                              restore_runtime_evidence):
                running_after, _ = runtime(request["instanceId"], manifest)
                if running_after != "running":
                    restore_runtime_evidence.clear()
                    restore_runtime_evidence.update({"check": "containerRuntimeState", "state": running_after})
                reject("BACKUP_RESTART_FAILED")
            runtime_verified = True
        elif stopped:
            stopped_after, _ = runtime(request["instanceId"], manifest)
            if stopped_after != "stopped":
                reject("BACKUP_STOP_FAILED")
        set_status(directory, "running", "verify", completed="start" if runtime_before == "running" else "restoreCommit")
        result_payload = {"protectionBackupId": protection_id if protection_created else None,
                          "configRevision": manifest["configRevision"], "verification": "passed",
                          "runtimeVerified": runtime_verified, "restoreAcceptanceRecorded": False,
                          "modsWarnings": mod_warnings(instance, metadata)}
        result = set_status(directory, "succeeded", "finalize", completed="verify", result=result_payload)
        shutil.rmtree(recovery, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        if not runtime_verified:
            return result
        try:
            atomic_json(RESTORE_ACCEPTANCE, {
                "schemaVersion": 1,
                "hostId": request["hostId"],
                "instanceId": request["instanceId"],
                "taskId": task_id,
                "verifiedAt": now(),
                "runtimeVerified": True,
            })
        except (OSError, ValueError, TypeError):
            return result
        result_payload["restoreAcceptanceRecorded"] = True
        try:
            return set_status(directory, "succeeded", "finalize", completed="verify", result=result_payload)
        except (OSError, ValueError, TypeError):
            return result
    except Exception as failure:
        code = getattr(failure, "code", "BACKUP_RESTORE_FAILED")
        failure_cause = code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,80}", code) \
            else type(failure).__name__
        failed_state = load(task_id)
        failure_stage = failed_state.get("stage", "preflight")
        if not restore_changed_live_state(stop_attempted, stopped, moved):
            details = {"failureStep": failure_step, "failureCause": failure_cause}
            if isinstance(getattr(failure, "details", None), dict):
                details.update(failure.details)
            return set_status(directory, "failed", failure_stage,
                error=error_payload(code, request, task_id, False, details))
        rollback_stage = "readRestoreTransaction"
        rollback_failure = None
        rollback_health = None
        try:
            transaction = read_json(directory / "transaction.json")
            rollback_stage = "restoreFiles"
            restore_transaction_snapshot(instance, recovery, transaction)
            if runtime_before == "running":
                rollback_stage = "recreateOriginalContainers"
                compose(instance, original_manifest, "create", "--force-recreate", *roles)
                rollback_stage = "restartOriginalContainers"
                compose(instance, original_manifest, "start", roles)
                rollback_stage = "verifyOriginalRuntime"
                if not wait_for_runtime_processes(instance, request["instanceId"], original_manifest, roles,
                                                  rollback_runtime_evidence):
                    original_runtime, _ = runtime(request["instanceId"], original_manifest)
                    if original_runtime != "running":
                        rollback_runtime_evidence.clear()
                        rollback_runtime_evidence.update({"check": "containerRuntimeState", "state": original_runtime})
                        reject("BACKUP_RESTART_FAILED")
                    baseline = transaction.get("runtimeBeforeEvidence")
                    if (isinstance(baseline, dict) and baseline.get("check") != "passed"
                            and same_runtime_degradation(baseline, rollback_runtime_evidence)):
                        rollback_health = "sameDegradedStateAsBeforeRestore"
                    elif (not isinstance(baseline, dict) or baseline.get("check") != "passed"):
                        rollback_health = "degradedStateBaselineUnavailable"
                    else:
                        reject("BACKUP_RESTART_FAILED")
            elif stopped:
                rollback_stage = "verifyOriginalStoppedState"
                stopped_after, _ = runtime(request["instanceId"], original_manifest)
                if stopped_after != "stopped":
                    reject("BACKUP_STOP_FAILED")
            rollback_stage = "complete"
        except Exception as rollback_error:
            code = "BACKUP_ROLLBACK_FAILED"
            rollback_failure = getattr(rollback_error, "code", None)
            if not isinstance(rollback_failure, str) or re.fullmatch(r"[A-Z0-9_]{1,80}", rollback_failure) is None:
                rollback_failure = type(rollback_error).__name__
        details = {"rollbackStage": rollback_stage} if rollback_failure is None else {
            "rollbackStage": rollback_stage, "rollbackFailure": rollback_failure}
        details.update({"failureStep": failure_step, "failureCause": failure_cause,
                        "rollbackSucceeded": rollback_failure is None})
        if rollback_health:
            details["rollbackHealth"] = rollback_health
        if restore_runtime_evidence:
            details["restoreRuntimeCheck"] = restore_runtime_evidence
        if rollback_runtime_evidence:
            details["rollbackRuntimeCheck"] = rollback_runtime_evidence
        return set_status(directory, "failed", "rollback",
                          error=error_payload(code, request, task_id, True, details))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if instance_lock is not None:
            fcntl.flock(instance_lock.fileno(), fcntl.LOCK_UN); instance_lock.close()
        if maintenance is not None:
            fcntl.flock(maintenance.fileno(), fcntl.LOCK_UN); maintenance.close()
        if heavy is not None:
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
            if code == "BACKUP_CANCELLED":
                return set_status(directory, "cancelled", "cancelled")
            if state["status"] not in ("succeeded", "failed", "cancelled", "interrupted"):
                return set_status(directory, "failed", state.get("stage", "preflight"),
                                  error=error_payload(code, request, task_id, True))
        except Exception:
            return None
        return state


def write_worker_bundle(directory, worker_source, archive_source):
    if not all(isinstance(value, str) for value in (worker_source, archive_source)):
        reject("BACKUP_CONTROLLER_BUNDLE_INVALID")
    atomic_bytes(directory / "worker.py", worker_source.encode("utf-8"), 0o700)
    atomic_bytes(directory / "archive-source.py", archive_source.encode("utf-8"), 0o600)


def start(request):
    if os.geteuid() != 0 or request.get("schemaVersion") != 1 or request.get("action") != "restoreStart" or not valid_uuid(request.get("requestId")) or not valid_uuid(request.get("operationId")) or not valid_uuid(request.get("backupId")) or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("instanceId")) or type(request.get("expectedRevision")) is not int or request["expectedRevision"] < 0 or request["expectedRevision"] > MAX_REVISION or type(request.get("createProtectionBackup")) is not bool:
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
        "cancelAllowed": True, "observation": "live", "result": None, "error": None})
    atomic_bytes(directory / "journal.jsonl", canonical({"sequence": 1, "status": "queued", "stage": "queued", "at": created}) + b"\n")
    worker_path = directory / "worker.py"
    worker_source = globals().get("RESTORE_SOURCE")
    archive_source = globals().get("ARCHIVE_SOURCE")
    write_worker_bundle(directory, worker_source, archive_source)
    admit_queue(request)
    atomic_bytes(ROOT / "queue-controller.py", worker_source.encode(), 0o700)
    launch = launch_controller()
    if not launch or not wait_controller_active():
        return set_status(directory, "failed", "queued", error=error_payload("BACKUP_CONTROLLER_START_FAILED", request, request["operationId"], False))
    return load(request["operationId"])


def cancel(request):
    task_id = request.get("operationId")
    if request.get("schemaVersion") != 1 or not valid_uuid(task_id) or not valid_uuid(request.get("requestId")):
        reject("BACKUP_TASK_INPUT_INVALID")
    state = load(task_id)
    if state["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
        return state
    atomic_json(TASKS / task_id / "cancel.request", {"requestedAt": now()})
    if not controller_active():
        launch_controller()
    return state


def status(request):
    if not valid_uuid(request.get("operationId")) or request.get("schemaVersion") != 1:
        reject("BACKUP_TASK_INPUT_INVALID")
    state = load(request["operationId"])
    if state["status"] == "queued":
        position = queue_position(request["operationId"])
        if position is not None:
            state = dict(state, queuePosition=position)
    if state["target"] != {"hostId": request.get("hostId"), "instanceId": request.get("instanceId")}:
        reject("BACKUP_TARGET_MISMATCH")
    if state["status"] in ("queued", "running"):
        unit = "famine-queue-controller"
        active = subprocess.run(["systemctl", "is-active", "--quiet", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        if active.returncode != 0:
            if state["status"] == "queued":
                if queued_recently(state) or launch_controller():
                    return state
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
    has_recovery_contract = isinstance(transaction.get("rollbackPaths"), list) \
        and isinstance(transaction.get("originalPaths"), list)
    if not has_recovery_contract and state.get("stage") in ("restoreCommit", "start", "verify", "rollback"):
        return set_status(directory, "interrupted", state["stage"],
            error=error_payload("BACKUP_RECOVERY_REQUIRED", request, task_id, True),
            fields={"observation": "reconciling", "runtimeRecovery": "required"})
    instance = ROOT / "instances" / request["instanceId"]
    heavy = None
    maintenance = None
    instance_lock = None
    try:
        safe_dir(ROOT / "locks")
        heavy = os.open(ROOT / "locks" / "heavy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(heavy, fcntl.LOCK_EX | fcntl.LOCK_NB)
        safe_dir(instance / "locks")
        maintenance = os.open(instance / "locks" / "world-config.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
        instance_lock = os.open(instance / "locks" / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recovery = instance / (".restore-recovery-" + task_id)
        manifest = transaction["manifest"]
        current_state, roles = runtime(request["instanceId"], manifest)
        if current_state != "stopped":
            compose(instance, manifest, "stop", roles)
            current_state, _ = runtime(request["instanceId"], manifest)
            if current_state != "stopped":
                reject("BACKUP_STOP_FAILED")
        if has_recovery_contract:
            restore_transaction_snapshot(instance, recovery, transaction)
        else:
            atomic_json(instance / "active-release.json", transaction["activeRelease"])
            atomic_json(instance / "manifest.json", manifest)
            atomic_json(instance / "desired" / "world.json", transaction["desiredWorld"])
            atomic_json(instance / "deployed-world.json", transaction["deployedWorld"])
        runtime_before = transaction.get("runtimeBefore")
        runtime_health = None
        if runtime_before == "running":
            compose(instance, manifest, "create", "--force-recreate", *roles)
            compose(instance, manifest, "start", roles)
            recovered_evidence = {}
            if not wait_for_runtime_processes(instance, request["instanceId"], manifest, roles, recovered_evidence):
                baseline = transaction.get("runtimeBeforeEvidence")
                if (isinstance(baseline, dict) and baseline.get("check") != "passed"
                        and same_runtime_degradation(baseline, recovered_evidence)):
                    runtime_health = "sameDegradedStateAsBeforeRestore"
                else:
                    reject("BACKUP_RESTART_FAILED")
        elif runtime_before in ("stopped", "degraded"):
            stopped, _ = runtime(request["instanceId"], manifest)
            if stopped != "stopped":
                reject("BACKUP_STOP_FAILED")
        else:
            reject("BACKUP_RUNTIME_INVALID")
        details = {"runtimeHealth": runtime_health} if runtime_health else {}
        recovered = set_status(directory, "interrupted", state["stage"],
            error=error_payload("BACKUP_RESTORE_INTERRUPTED_RECOVERED", request, task_id, True, details),
            fields={"observation": "reconciling", "runtimeRecovery": "restored"})
        shutil.rmtree(recovery, ignore_errors=True)
        shutil.rmtree(instance / (".restore-staging-" + task_id), ignore_errors=True)
        return recovered
    except Exception:
        return set_status(directory, "interrupted", state["stage"], error=error_payload("BACKUP_RECOVERY_REQUIRED", request, task_id, True), fields={"observation": "reconciling", "runtimeRecovery": "required"})
    finally:
        if instance_lock is not None:
            fcntl.flock(instance_lock, fcntl.LOCK_UN); os.close(instance_lock)
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
                unit = "famine-queue-controller"
                active = subprocess.run(["systemctl", "is-active", "--quiet", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                if active.returncode != 0:
                    if state["status"] != "queued" or not (queued_recently(state) or launch_controller()):
                        state = reconcile_interrupted(read_json(child / "request.json"), state)
            result.append(state)
    result.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return result[:100]


def main(request=None):
    try:
        action = request.get("action")
        if action == "restoreStart": data = start(request)
        elif action == "restoreStatus": data = status(request)
        elif action == "restoreCancel": data = cancel(request)
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
    if len(sys.argv) > 1 and sys.argv[1] == "controller":
        controller_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(sys.argv[2])
