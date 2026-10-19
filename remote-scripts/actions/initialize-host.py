"""Bounded metadata initialization. Invoked only as a bundled script over verified SSH.

No dependency installation, privilege escalation, detached process or game operation.
The private bootstrap receipt is written before publishing either managed file.
"""
import base64
import fcntl
import hashlib
import json
import os
import stat
import sys
import time
import uuid

ROOT = "/var/lib/famine-hub"
LIMIT = 1048576


class Rejected(Exception):
    pass


def reject(code):
    raise Rejected(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def identifier(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (ValueError, TypeError, AttributeError):
        return False


def directory(parent, name, private=False):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    info = os.fstat(fd)
    if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
        os.close(fd)
        reject("INITIALIZATION_PATH_UNSAFE")
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        os.close(fd)
        reject("INITIALIZATION_PATH_UNSAFE")
    return fd


def read(parent, name, limit=LIMIT, allowed_links=(1,)):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_nlink not in allowed_links:
            reject("INITIALIZATION_FILE_INVALID")
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            reject("INITIALIZATION_PATH_UNSAFE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            reject("INITIALIZATION_FILE_INVALID")
        return data
    finally:
        os.close(fd)


def identity(root):
    host = json.loads(read(root, "host.json", 65536))
    registry = json.loads(read(root, "registry.json"))
    if (host.get("schemaVersion") != 1 or not identifier(host.get("hostId"))
            or registry.get("schemaVersion") != 1 or registry.get("hostId") != host["hostId"]
            or type(registry.get("registryRevision")) is not int
            or not 0 <= registry["registryRevision"] <= 9007199254740991
            or not isinstance(registry.get("instances"), list)):
        reject("INITIALIZATION_IDENTITY_INVALID")
    return host["hostId"]


def snapshot(parent, name):
    try:
        root = directory(parent, name)
    except FileNotFoundError:
        state = {"kind": "missing"}
        host_id = None
    else:
        try:
            info = os.fstat(root)
            # Only an empty directory or a valid existing installation is accepted.
            names = os.listdir(root)
            if len(names) > 64:
                reject("INITIALIZATION_EXISTING_DATA")
            host_id = identity(root) if names else None
            state = {"kind": "ready" if names else "empty", "inode": info.st_ino,
                     "device": info.st_dev, "uid": info.st_uid, "mode": info.st_mode,
                     "names": sorted(names)}
            if names:
                state["host"] = hashlib.sha256(read(root, "host.json", 65536)).hexdigest()
                state["registry"] = hashlib.sha256(read(root, "registry.json")).hexdigest()
            if not os.access(name, os.W_OK | os.X_OK, dir_fd=parent):
                reject("INITIALIZATION_PERMISSION_DENIED")
        finally:
            os.close(root)
    if not os.access(".", os.W_OK | os.X_OK, dir_fd=parent):
        reject("INITIALIZATION_PERMISSION_DENIED")
    return {"snapshot": hashlib.sha256(encoded(state)).hexdigest(), "hostId": host_id,
            "state": state["kind"]}


def atomic_json(parent, name, value):
    temporary = ".write-" + str(uuid.uuid4())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(fd)
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def publish(root, name, value, operation_id):
    expected = encoded(value)
    temporary = ".initialize-" + operation_id + "-" + name
    try:
        staged = read(root, temporary, allowed_links=(1, 2))
    except FileNotFoundError:
        pass
    else:
        staged_info = os.stat(temporary, dir_fd=root, follow_symlinks=False)
        if staged != expected:
            # A crash while writing staging can leave partial bytes. Only an
            # unlinked private staging inode belonging to this operation may go.
            if staged_info.st_nlink != 1 or staged_info.st_uid != os.geteuid() or stat.S_IMODE(staged_info.st_mode) != 0o600:
                reject("INITIALIZATION_FILE_INVALID")
        if staged_info.st_nlink == 2:
            target = os.stat(name, dir_fd=root, follow_symlinks=False)
            if (target.st_dev, target.st_ino) != (staged_info.st_dev, staged_info.st_ino):
                reject("INITIALIZATION_FILE_INVALID")
        os.unlink(temporary, dir_fd=root)
        os.fsync(root)
    try:
        existing = read(root, name)
    except FileNotFoundError:
        # Stage complete bytes, then publish with a no-overwrite hard link.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=root)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(expected)
                stream.flush()
                os.fsync(fd)
            os.link(temporary, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            os.unlink(temporary, dir_fd=root)
            os.fsync(root)
        finally:
            os.close(fd)
        return
    if existing != expected:
        reject("INITIALIZATION_EXISTING_DATA")


def locked(parent, boot_name, create):
    if create:
        try:
            os.mkdir(boot_name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    boot = directory(parent, boot_name, private=True)
    lock = os.open("initialize.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                   0o600, dir_fd=boot)
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        reject("INITIALIZATION_PATH_UNSAFE")
    # Never wait unboundedly on a remote process.
    deadline = time.monotonic() + 5
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                reject("INITIALIZATION_BUSY")
            time.sleep(0.05)
    return boot, lock


def run(request):
    if sys.platform != "linux":
        reject("INITIALIZATION_LINUX_REQUIRED")
    parent_path, name = os.path.split(ROOT)
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        # Walk every ancestor without following symlinks. Only the final parent
        # must be non-writable by other accounts; /tmp is allowed in test fixtures.
        for component in parent_path.split("/"):
            if not component:
                continue
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = following
        info = os.fstat(parent)
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            reject("INITIALIZATION_PATH_UNSAFE")
        boot_name = name + ".bootstrap"
        if request["action"] == "preview":
            try:
                boot = directory(parent, boot_name, private=True)
            except FileNotFoundError:
                pass
            else:
                os.close(boot)
            return snapshot(parent, name)
        if not identifier(request.get("operationId")):
            reject("INPUT_INVALID")
        try:
            boot, lock = locked(parent, boot_name, request["action"] == "initialize")
        except FileNotFoundError:
            return {"status": "notFound"}
        try:
            receipt_name = request["operationId"] + ".json"
            try:
                receipt = json.loads(read(boot, receipt_name, 65536))
            except FileNotFoundError:
                receipt = None
            if receipt is not None and receipt.get("payload") != request["payload"]:
                reject("OPERATION_ID_REUSED")
            if request["action"] == "query":
                if receipt is None:
                    return {"status": "notFound"}
                if receipt["status"] == "succeeded":
                    root = directory(parent, name)
                    try:
                        if identity(root) != receipt["payload"]["hostId"]:
                            reject("HOST_IDENTITY_MISMATCH")
                    finally:
                        os.close(root)
                return {"status": receipt["status"], "hostId": receipt["payload"]["hostId"]}
            if receipt is None:
                payload = request["payload"]
                if not identifier(payload.get("hostId")):
                    reject("INPUT_INVALID")
                if time.time() > payload["expiresAtUnixSeconds"]:
                    reject("PLAN_STALE")
                current = snapshot(parent, name)
                if current["hostId"] is not None or current["snapshot"] != payload["snapshot"]:
                    reject("PLAN_STALE")
                receipt = {"schemaVersion": 1, "status": "incomplete", "payload": payload}
                atomic_json(boot, receipt_name, receipt)
            if receipt["status"] != "succeeded":
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    pass
                root = directory(parent, name)
                try:
                    if set(os.listdir(root)) - {"host.json", "registry.json", ".initialize-" + request["operationId"] + "-host.json", ".initialize-" + request["operationId"] + "-registry.json"}:
                        reject("INITIALIZATION_EXISTING_DATA")
                    host_id = receipt["payload"]["hostId"]
                    publish(root, "host.json", {"schemaVersion": 1, "hostId": host_id}, request["operationId"])
                    publish(root, "registry.json", {"schemaVersion": 1, "hostId": host_id,
                            "registryRevision": 0, "instances": [], "portReservations": [], "heavyQueue": []}, request["operationId"])
                    if identity(root) != host_id:
                        reject("HOST_IDENTITY_MISMATCH")
                finally:
                    os.close(root)
                receipt["status"] = "succeeded"
                atomic_json(boot, receipt_name, receipt)
            root = directory(parent, name)
            try:
                if identity(root) != receipt["payload"]["hostId"]:
                    reject("HOST_IDENTITY_MISMATCH")
            finally:
                os.close(root)
            return {"status": "succeeded", "hostId": receipt["payload"]["hostId"]}
        finally:
            os.close(lock)
            os.close(boot)
    finally:
        os.close(parent)


def main(request):
    try:
        result = {"ok": True, "data": run(request)}
    except Rejected as error:
        result = {"ok": False, "code": str(error)}
    except PermissionError:
        result = {"ok": False, "code": "INITIALIZATION_PERMISSION_DENIED"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result = {"ok": False, "code": "INITIALIZATION_FILE_INVALID"}
    print(json.dumps(result, separators=(",", ":")), flush=True)
