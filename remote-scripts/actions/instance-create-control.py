"""Admit an instance creation request under the remote registry lock.

This module only performs the short admission transaction. It does not create
the instance directory, write secrets, generate Compose files, or start Docker.
The future create controller must consume the returned admission record before
doing any long-running work.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid

ROOT = Path("/var/lib/famine-hub")
LOCKS = ROOT / "locks"
LOCK_PATH = LOCKS / "registry.lock"
OWNER_PATH = LOCKS / "registry-owner.json"
CATALOG = ROOT / "releases" / "catalog.json"
MAX_REVISION = 9007199254740991
MAX_INSTANCES = 200
MAX_RESERVATIONS = 2400
MAX_OPERATIONS = 200
PORT_RANGES = {
    "game": (11000, 19999),
    "steamAuth": (8766, 9765),
    "steamMaster": (27016, 28015),
}


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


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def safe_directory(path, create=False, mode=0o700):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=mode)
        info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022):
        reject("CREATE_PATH_UNSAFE")
    return info


def read_json(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > limit or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022):
        reject("CREATE_FILE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit or opened.st_uid not in (0, os.geteuid())
                or opened.st_mode & 0o022):
            reject("CREATE_FILE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("CREATE_FILE_INVALID")
    try:
        return json.loads(data)
    except (TypeError, ValueError):
        reject("CREATE_REGISTRY_INVALID")


def atomic_json(path, value):
    safe_directory(path.parent, create=True)
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    payload = canonical(value)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
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


def identity():
    host = read_json(ROOT / "host.json", 65536)
    if (host.get("schemaVersion") != 1 or not valid_uuid(host.get("hostId"))):
        reject("HOST_IDENTITY_INVALID")
    return host["hostId"]


def validate_registry(registry, host_id):
    if (registry.get("schemaVersion") != 1 or registry.get("hostId") != host_id
            or type(registry.get("registryRevision")) is not int
            or not 0 <= registry["registryRevision"] <= MAX_REVISION
            or not isinstance(registry.get("instances"), list)
            or len(registry["instances"]) > MAX_INSTANCES):
        reject("CREATE_REGISTRY_INVALID")
    if "portReservations" not in registry or not isinstance(registry["portReservations"], list):
        reject("CREATE_PORT_STATE_INVALID")
    if len(registry["portReservations"]) > MAX_RESERVATIONS:
        reject("CREATE_PORT_STATE_INVALID")
    if "operations" not in registry:
        registry["operations"] = []
    if not isinstance(registry["operations"], list) or len(registry["operations"]) > MAX_OPERATIONS:
        reject("CREATE_OPERATION_STATE_INVALID")
    for operation in registry["operations"]:
        if (not isinstance(operation, dict) or not valid_uuid(operation.get("operationId"))
                or not isinstance(operation.get("payloadDigest"), str)
                # Legacy deletion receipts used a bare SHA-256 digest.
                or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", operation["payloadDigest"])
                or not isinstance(operation.get("result"), dict)):
            reject("CREATE_OPERATION_STATE_INVALID")
    instance_ids = set()
    for entry in registry["instances"]:
        if (not isinstance(entry, dict) or not valid_uuid(entry.get("instanceId"))
                or entry["instanceId"] in instance_ids
                or entry.get("lifecycle") not in ("creating", "ready", "createFailed", "deleting", "deleteFailed", "retired")):
            reject("CREATE_REGISTRY_INVALID")
        instance_ids.add(entry["instanceId"])
    seen_ports = set()
    for reservation in registry["portReservations"]:
        validate_reservation(reservation, host_id, instance_ids)
        key = reservation["protocol"], reservation["hostPort"]
        if key in seen_ports:
            reject("CREATE_PORT_STATE_INVALID")
        seen_ports.add(key)
    return instance_ids


def validate_reservation(item, host_id, instance_ids):
    if (not isinstance(item, dict) or item.get("hostId") != host_id
            or item.get("instanceId") not in instance_ids
            or item.get("protocol") not in ("udp", "tcp")
            or item.get("shard") not in ("master", "caves")
            or item.get("purpose") not in PORT_RANGES
            or not isinstance(item.get("bindAddress"), str)
            or not re.fullmatch(r"(?:0\.0\.0\.0|::1|::|127\.0\.0\.1)", item["bindAddress"])
            or type(item.get("hostPort")) is not int
            or type(item.get("containerPort")) is not int
            or item["hostPort"] != item["containerPort"]
            or not 1 <= item["hostPort"] <= 65535):
        reject("CREATE_PORT_STATE_INVALID")


def validate_input(request):
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("operationId"))
            or not valid_uuid(request.get("planId")) or not valid_uuid(request.get("hostId"))):
        reject("CREATE_INPUT_INVALID")
    if type(request.get("expectedRegistryRevision")) is not int:
        reject("CREATE_INPUT_INVALID")
    if request["expectedRegistryRevision"] < 0 or request["expectedRegistryRevision"] > MAX_REVISION:
        reject("CREATE_INPUT_INVALID")
    settings = request.get("input")
    if not isinstance(settings, dict) or set(settings) != {"displayName", "autostart", "startAfterCreate", "portPolicy", "cavesEnabled", "expectedPorts", "releaseId", "releaseCatalogDigest"}:
        reject("CREATE_INPUT_INVALID")
    if (not isinstance(settings["displayName"], str) or not settings["displayName"].strip()
            or len(settings["displayName"]) > 64 or any(ord(char) < 32 for char in settings["displayName"])
            or type(settings["autostart"]) is not bool or type(settings["startAfterCreate"]) is not bool
            or (not settings["autostart"] and settings["startAfterCreate"])
            or settings["portPolicy"] != "auto"):
        reject("CREATE_INPUT_INVALID")
    if type(settings["cavesEnabled"]) is not bool:
        reject("CREATE_INPUT_INVALID")
    if (not valid_uuid(settings["releaseId"])
            or not isinstance(settings["releaseCatalogDigest"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", settings["releaseCatalogDigest"]) is None):
        reject("CREATE_RELEASE_REQUIRED")
    if not isinstance(settings["expectedPorts"], list) or len(settings["expectedPorts"]) not in (3, 6):
        reject("CREATE_INPUT_INVALID")


def socket_ports():
    used = set()
    for protocol in ("udp", "udp6", "tcp", "tcp6"):
        try:
            lines = (Path("/proc/net") / protocol).read_text(encoding="ascii").splitlines()
        except (FileNotFoundError, OSError):
            reject("PORT_EVIDENCE_UNAVAILABLE")
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 2:
                reject("CREATE_SOCKET_INVALID")
            try:
                number = int(fields[1].rsplit(":", 1)[1], 16)
            except (ValueError, IndexError):
                reject("CREATE_SOCKET_INVALID")
            if number:
                used.add(("tcp" if protocol.startswith("tcp") else "udp", number))
    return used


def docker_ports():
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    result = subprocess.run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=10, check=False,
                            env=environment)
    if result.returncode != 0:
        reject("DOCKER_UNAVAILABLE")
    ids = result.stdout.splitlines()
    if len(ids) > 400 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        reject("CREATE_DOCKER_RESPONSE_INVALID")
    if not ids:
        return set()
    bindings = subprocess.run(["docker", "container", "inspect", "--format", "{{json .HostConfig.PortBindings}}", *ids],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, timeout=10, check=False,
                              env=environment)
    rows = bindings.stdout.splitlines()
    if bindings.returncode != 0 or len(rows) != len(ids):
        reject("CREATE_DOCKER_RESPONSE_INVALID")
    used = set()
    for row in rows:
        try:
            values = json.loads(row)
        except ValueError:
            reject("CREATE_DOCKER_RESPONSE_INVALID")
        if values is None:
            continue
        if not isinstance(values, dict):
            reject("CREATE_DOCKER_RESPONSE_INVALID")
        for key, addresses in values.items():
            match = re.fullmatch(r"[0-9]{1,5}/(tcp|udp)", key)
            if not match or addresses is None:
                continue
            if not isinstance(addresses, list):
                reject("CREATE_DOCKER_RESPONSE_INVALID")
            for address in addresses:
                number = address.get("HostPort") if isinstance(address, dict) else None
                if not isinstance(number, str) or not number.isdigit() or not 1 <= int(number) <= 65535:
                    reject("CREATE_DOCKER_RESPONSE_INVALID")
                used.add((match[1], int(number)))
    return used


def allocate(used, caves):
    planned = []
    used = set(used)
    for shard in (["master", "caves"] if caves else ["master"]):
        for purpose in ("game", "steamAuth", "steamMaster"):
            first, last = PORT_RANGES[purpose]
            candidate = next((port for port in range(first, last + 1) if ("udp", port) not in used), None)
            if candidate is None:
                reject("PORT_PLAN_EXHAUSTED")
            used.add(("udp", candidate))
            planned.append({"shard": shard, "purpose": purpose, "protocol": "udp",
                            "bindAddress": "0.0.0.0", "hostPort": candidate,
                            "containerPort": candidate})
    return planned


def validate_plan(expected, actual):
    if expected != actual:
        reject("PLAN_STALE")


def lock_registry(operation_id):
    safe_directory(ROOT)
    safe_directory(LOCKS, create=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o077):
        os.close(fd)
        reject("CREATE_PATH_UNSAFE")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        reject("REGISTRY_BUSY")
    token = str(uuid.uuid4())
    try:
        previous = read_json(OWNER_PATH, 65536)
        generation = previous.get("generation", 0) + 1 if isinstance(previous, dict) else 1
    except (FileNotFoundError, Rejected, ValueError, TypeError, AttributeError):
        generation = 1
    try:
        now = int(time.time())
        atomic_json(OWNER_PATH, {"schemaVersion": 1, "status": "held", "operationId": operation_id,
                                 "ownerToken": token, "generation": generation, "pid": os.getpid(),
                                 "heartbeatAtUnixSeconds": now, "leaseUntilUnixSeconds": now + 30})
    except BaseException:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        raise
    return fd, token, generation


def release_registry(fd, token, generation, operation_id):
    try:
        try:
            owner = read_json(OWNER_PATH, 65536)
        except (FileNotFoundError, Rejected, ValueError, TypeError, AttributeError):
            owner = None
        if (isinstance(owner, dict) and owner.get("ownerToken") == token
                and owner.get("generation") == generation and owner.get("operationId") == operation_id):
            atomic_json(OWNER_PATH, {"schemaVersion": 1, "status": "released", "operationId": operation_id,
                                     "ownerToken": token, "generation": generation,
                                     "releasedAtUnixSeconds": int(time.time())})
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def admit(request):
    validate_input(request)
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    host_id = identity()
    if host_id != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    fd, token, generation = lock_registry(request["operationId"])
    try:
        registry = read_json(ROOT / "registry.json", 1024 * 1024)
        instance_ids = validate_registry(registry, host_id)
        payload = {"operation": "instanceCreate", "planId": request["planId"], "hostId": host_id,
                   "expectedRegistryRevision": request["expectedRegistryRevision"], "input": request["input"]}
        payload_digest = digest(payload)
        for operation in registry["operations"]:
            if operation.get("operationId") == request["operationId"]:
                if operation["payloadDigest"].removeprefix("sha256:") != payload_digest.removeprefix("sha256:"):
                    reject("OPERATION_ID_REUSED")
                return operation["result"]
        if registry["registryRevision"] != request["expectedRegistryRevision"]:
            reject("REGISTRY_CHANGED")
        catalog = read_json(CATALOG, 1024 * 1024)
        if (catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id
                or digest(catalog) != request["input"]["releaseCatalogDigest"]):
            reject("RELEASE_CATALOG_CHANGED")
        release = next((item for item in catalog.get("releases", [])
                        if isinstance(item, dict) and item.get("releaseId") == request["input"]["releaseId"]), None)
        if release is None or release.get("verified") is not True:
            reject("CREATE_RELEASE_REQUIRED")
        release = dict(release, schemaVersion=1)
        used = {("udp" if item["protocol"] == "udp" else "tcp", item["hostPort"])
                for item in registry["portReservations"]}
        used |= socket_ports()
        used |= docker_ports()
        settings = request["input"]
        if len(registry["instances"]) >= MAX_INSTANCES:
            reject("INSTANCE_LIMIT")
        if len(registry["portReservations"]) + len(settings["expectedPorts"]) > MAX_RESERVATIONS:
            reject("PORT_RESERVATION_LIMIT")
        ports = allocate(used, settings["cavesEnabled"])
        validate_plan(settings["expectedPorts"], ports)
        instance_id = str(uuid.uuid4())
        task_id = request["operationId"]
        now = int(time.time())
        entry = {"instanceId": instance_id, "lifecycle": "creating", "relativePath": "instances/" + instance_id,
                 "displayName": settings["displayName"].strip(), "pendingTaskId": task_id,
                 "dstReleaseId": release["releaseId"]}
        registry["instances"].append(entry)
        registry["portReservations"].extend({"hostId": host_id, "instanceId": instance_id, **port, "reservedAtUnixSeconds": now}
                                             for port in ports)
        result = {"schemaVersion": 1, "operationId": task_id, "hostId": host_id, "instanceId": instance_id,
                  "taskId": task_id, "registryRevision": registry["registryRevision"] + 1,
                  "lifecycle": "creating", "ports": ports, "reservedAtUnixSeconds": now,
                  "release": release, "releaseCatalogDigest": request["input"]["releaseCatalogDigest"]}
        registry["registryRevision"] += 1
        registry["operations"].append({"operationId": task_id, "payloadDigest": payload_digest, "result": result})
        registry["operations"] = registry["operations"][-MAX_OPERATIONS:]
        atomic_json(ROOT / "registry.json", registry)
        return result
    finally:
        release_registry(fd, token, generation, request["operationId"])


def main(request):
    try:
        if request.get("action") != "admit":
            reject("ACTION_INVALID")
        response = {"ok": True, "data": admit(request)}
    except Rejected as error:
        response = {"ok": False, "code": str(error)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        response = {"ok": False, "code": "CREATE_ADMISSION_FAILED"}
    print(json.dumps(response, separators=(",", ":")), flush=True)
