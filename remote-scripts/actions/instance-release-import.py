"""Persistent validation/import of a staged, catalog-selected release.

This action deliberately does not download software. It validates an artifact
already staged by an approved preparation process and atomically marks the
catalog entry runtimeVerified only when evidence matches the catalog.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

ROOT = Path("/var/lib/famine-hub")
CATALOG = ROOT / "releases" / "catalog.json"
STAGING = ROOT / "releases" / "staging"
TASKS = ROOT / "releases" / "tasks"


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


def digest(value):
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def safe_dir(path, create=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        reject("CREATE_RELEASE_TASK_PATH_UNSAFE")
    return info


def read_json(path, limit=1024 * 1024):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        reject("CREATE_RELEASE_ARTIFACT_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("CREATE_RELEASE_ARTIFACT_CHANGED")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("CREATE_RELEASE_ARTIFACT_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("CREATE_RELEASE_ARTIFACT_INVALID")


def atomic_json(path, value):
    safe_dir(path.parent, create=True)
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
            output.flush(); os.fsync(fd)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        try: temporary.unlink()
        except FileNotFoundError: pass


def catalog_digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def status_path(operation_id):
    if not valid_uuid(operation_id): reject("CREATE_RELEASE_OPERATION_INVALID")
    return TASKS / operation_id / "status.json"


def write_status(operation_id, status, stage, code=None, result=None):
    directory = status_path(operation_id).parent
    safe_dir(directory, create=True)
    current = None
    try: current = read_json(status_path(operation_id), 65536)
    except FileNotFoundError: pass
    body = {"schemaVersion": 1, "taskId": operation_id, "operationId": operation_id,
            "operation": "releaseImport", "status": status, "stage": stage,
            "sequence": (current.get("sequence", 0) + 1 if current else 1),
            "createdAt": current.get("createdAt", int(time.time())) if current else int(time.time()),
            "updatedAt": int(time.time()), "error": {"code": code} if code else None,
            "result": result}
    atomic_json(status_path(operation_id), body)
    return body


def run(request):
    operation_id = request.get("operationId")
    release_id = request.get("releaseId")
    host_id = request.get("hostId")
    expected_digest = request.get("catalogDigest")
    if not valid_uuid(operation_id) or not valid_uuid(release_id) or not valid_uuid(host_id) or not isinstance(expected_digest, str):
        reject("CREATE_RELEASE_INPUT_INVALID")
    safe_dir(ROOT); safe_dir(TASKS, create=True)
    lock_fd = os.open(TASKS / "admit.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        existing = TASKS / operation_id / "status.json"
        if existing.exists(): return read_json(existing, 65536)
        catalog = read_json(CATALOG)
        if catalog_digest(catalog) != expected_digest: reject("RELEASE_CATALOG_CHANGED")
        if catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id: reject("HOST_IDENTITY_MISMATCH")
        release = next((item for item in catalog.get("releases", []) if item.get("releaseId") == release_id), None)
        if release is None or release.get("verified") is not True: reject("CREATE_RELEASE_NOT_VERIFIED")
        write_status(operation_id, "running", "stagingVerify")
        artifact = STAGING / release_id
        if not artifact.is_dir() or artifact.is_symlink(): reject("CREATE_RELEASE_ARTIFACT_REQUIRED")
        try:
            metadata = read_json(artifact / "release.json", 65536)
            evidence = read_json(artifact / "runtime.json", 65536)
        except FileNotFoundError:
            reject("CREATE_RELEASE_ARTIFACT_REQUIRED")
        if metadata != release or evidence.get("runtimeVerified") is not True or evidence.get("verification") != "passed":
            reject("CREATE_RELEASE_ARTIFACT_INVALID")
        updated = dict(catalog)
        updated["releases"] = [dict(item, runtimeVerified=True) if item.get("releaseId") == release_id else item for item in catalog["releases"]]
        atomic_json(CATALOG, updated)
        return write_status(operation_id, "succeeded", "finalize", result={"releaseId": release_id, "catalogDigest": catalog_digest(updated), "runtimeVerified": True})
    except Rejected as error:
        try: return write_status(operation_id, "failed", "stagingVerify", code=str(error))
        except Exception: raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN); os.close(lock_fd)


def main(request):
    try:
        action = request.get("action")
        if action == "start": data = run(request)
        elif action == "status": data = read_json(status_path(request.get("operationId")), 65536) if status_path(request.get("operationId")).exists() else None
        else: reject("CREATE_RELEASE_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "code": "CREATE_RELEASE_TASK_INVALID"}, separators=(",", ":")), flush=True)
