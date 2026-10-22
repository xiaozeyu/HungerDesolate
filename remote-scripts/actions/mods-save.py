"""CAS-save desired MOD entries for one managed instance."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

ROOT = Path("/var/lib/famine-hub")
MAX_REVISION = 9007199254740991
MAX_MODS = 200
WORKSHOP_ID = re.compile(r"^[0-9]{1,20}$")


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code): raise Rejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value); return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError): return False


def canonical(value): return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def safe_dir(path, create=False):
    try: info = path.lstat()
    except FileNotFoundError:
        if not create: raise
        path.mkdir(mode=0o700); info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077: reject("MOD_PATH_UNSAFE")
    return info


def read_json(path, limit=2 * 1024 * 1024, missing=None):
    try: info = path.lstat()
    except FileNotFoundError: return missing
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077): reject("MOD_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit or opened.st_uid not in (0, os.geteuid())
                or opened.st_mode & 0o077): reject("MOD_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source: payload = source.read(limit + 1)
    finally: os.close(fd)
    if len(payload) > limit: reject("MOD_STATE_INVALID")
    try: return json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, ValueError): reject("MOD_STATE_INVALID")


def atomic_json(path, value):
    safe_dir(path.parent, create=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(canonical(value)); output.flush(); os.fsync(fd)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        try: temporary.unlink()
        except FileNotFoundError: pass


def validate_mods(value):
    if not isinstance(value, list) or len(value) > MAX_MODS: reject("MOD_INPUT_INVALID")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or not WORKSHOP_ID.fullmatch(str(item.get("workshopId", ""))): reject("MOD_INPUT_INVALID")
        workshop_id = str(item["workshopId"])
        if workshop_id in seen: reject("MOD_DUPLICATE")
        seen.add(workshop_id)
        if type(item.get("enabled")) is not bool: reject("MOD_INPUT_INVALID")
        options = item.get("configurationOptions", {})
        if not isinstance(options, dict) or len(options) > 256: reject("MOD_INPUT_INVALID")
        result.append({"workshopId": workshop_id, "enabled": item["enabled"], "configurationOptions": options, "artifactDigest": item.get("artifactDigest") if isinstance(item.get("artifactDigest"), str) else None})
    return result


def finish_pending(instance_id):
    root = ROOT / "instances" / instance_id
    pending = read_json(root / "desired" / "mods-pending.json", missing=None)
    if pending is None: return
    if not isinstance(pending, dict) or pending.get("schemaVersion") != 1: reject("MOD_TRANSACTION_INVALID")
    atomic_json(root / "desired" / "mods.json", pending["desired"])
    atomic_json(root / "manifest.json", pending["manifest"])
    atomic_json(root / "operations" / (pending["operationId"] + ".json"), pending["receipt"])
    (root / "desired" / "mods-pending.json").unlink()


def main(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1 or not valid_uuid(request.get("hostId"))
            or not valid_uuid(request.get("instanceId")) or not valid_uuid(request.get("serverId"))
            or not valid_uuid(request.get("requestId")) or not valid_uuid(request.get("operationId"))
            or type(request.get("expectedRevision")) is not int): reject("MOD_INPUT_INVALID")
    instance_id = request["instanceId"]
    safe_dir(ROOT); safe_dir(ROOT / "instances")
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json", 1024 * 1024)
    if host.get("hostId") != request["hostId"] or registry.get("hostId") != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    entries = [item for item in registry.get("instances", [])
               if isinstance(item, dict) and item.get("instanceId") == instance_id]
    if (len(entries) != 1 or entries[0].get("lifecycle") != "ready"
            or entries[0].get("relativePath") != "instances/" + instance_id):
        reject("INSTANCE_NOT_FOUND")
    root = ROOT / "instances" / instance_id
    safe_dir(root); safe_dir(root / "locks"); safe_dir(root / "desired", create=True); safe_dir(root / "operations", create=True)
    lock_fd = os.open(root / "locks" / "instance.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1
                or lock_info.st_uid not in (0, os.geteuid()) or lock_info.st_mode & 0o077):
            reject("MOD_LOCK_INVALID")
        fcntl.flock(lock_fd, fcntl.LOCK_EX); finish_pending(instance_id)
        manifest = read_json(root / "manifest.json", 65536)
        if manifest.get("hostId") != request["hostId"] or manifest.get("instanceId") != instance_id: reject("INSTANCE_IDENTITY_MISMATCH")
        revision = manifest.get("configRevision")
        if type(revision) is not int: reject("MOD_STATE_INVALID")
        mods = validate_mods(request.get("mods")); payload_digest = hashlib.sha256(canonical(mods)).hexdigest()
        operation_path = root / "operations" / (request["operationId"] + ".json")
        previous = read_json(operation_path, 262144, missing=None)
        if previous is not None:
            if previous.get("payloadDigest") != payload_digest: reject("OPERATION_ID_REUSED")
            result = dict(previous["result"]); result["requestId"] = request["requestId"]; print(json.dumps(result, separators=(",", ":"))); return
        if revision != request["expectedRevision"]: reject("REVISION_CONFLICT")
        if revision >= MAX_REVISION: reject("MOD_REVISION_EXHAUSTED")
        new_revision = revision + 1
        desired = {"schemaVersion": 1, "instanceId": instance_id, "revision": new_revision, "mods": mods}
        new_manifest = dict(manifest); new_manifest["configRevision"] = new_revision
        result = {"schemaVersion": 1, "requestId": request["requestId"], "target": {"serverId": request["serverId"], "hostId": request["hostId"], "instanceId": instance_id}, "operationId": request["operationId"], "verifiedRevision": new_revision, "data": {"revision": new_revision, "mods": mods}, "invalidate": [{"hostId": request["hostId"], "instanceId": instance_id, "domains": ["mods", "world", "overview", "updates"]}]}
        receipt = {"schemaVersion": 1, "operationId": request["operationId"], "payloadDigest": payload_digest, "result": result}
        atomic_json(root / "desired" / "mods-pending.json", {"schemaVersion": 1, "instanceId": instance_id, "operationId": request["operationId"], "desired": desired, "manifest": new_manifest, "receipt": receipt})
        finish_pending(instance_id); print(json.dumps(result, separators=(",", ":")))
    finally: os.close(lock_fd)


if __name__ == "__main__":
    try: main(json.loads(input()))
    except Rejected as error: print(json.dumps({"schemaVersion": 1, "ok": False, "code": error.code}, separators=(",", ":"))); raise SystemExit(2)
