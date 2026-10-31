"""Preview and clean host-level Docker build cache without touching Release images."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
BUILD_DIR = ROOT / "image-build"
CONTEXT = BUILD_DIR / "dst-runtime-build"
LOCK = BUILD_DIR / "build.lock"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise Rejected(code)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path, limit=65536):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("BUILD_CACHE_STATE_INVALID")
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        reject("BUILD_CACHE_STATE_INVALID")
    try:
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError):
        reject("BUILD_CACHE_STATE_INVALID")


def host_id():
    value = read_json(ROOT / "host.json")
    if not isinstance(value, dict) or not UUID.fullmatch(str(value.get("hostId", ""))):
        reject("HOST_IDENTITY_MISMATCH")
    return value["hostId"]


def directory_bytes(path):
    if not path.exists():
        return 0
    if path.is_symlink() or not path.is_dir():
        reject("BUILD_CACHE_PATH_UNSAFE")
    total = 0
    for current, directories, files in os.walk(path, followlinks=False):
        for name in directories:
            info = (Path(current) / name).lstat()
            if info.st_mode & 0o022:
                reject("BUILD_CACHE_PATH_UNSAFE")
        for name in files:
            info = (Path(current) / name).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                reject("BUILD_CACHE_PATH_UNSAFE")
            total += info.st_size
    return total


def docker(arguments, timeout=300):
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(["docker", *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            timeout=timeout, check=False, env=environment)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        reject("BUILD_CACHE_DOCKER_UNAVAILABLE")
    if result.returncode != 0:
        reject("BUILD_CACHE_DOCKER_FAILED")
    return result.stdout


def docker_cache():
    output = docker(["system", "df", "--format", "{{json .}}"], timeout=30)
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("Type") == "Build Cache":
            return {"size": row.get("Size"), "reclaimable": row.get("Reclaimable")}
    return {"size": None, "reclaimable": None}


def check_target(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or not UUID.fullmatch(str(request.get("requestId", "")))
            or not UUID.fullmatch(str(request.get("hostId", "")))
            or not UUID.fullmatch(str(request.get("serverId", "")))):
        reject("BUILD_CACHE_INPUT_INVALID")
    if request["hostId"] != host_id():
        reject("HOST_IDENTITY_MISMATCH")


def acquire_build_lock():
    BUILD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def preview(request):
    check_target(request)
    fd = acquire_build_lock()
    active = fd is None
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    try:
        cache = docker_cache()
        docker_available = True
    except Rejected:
        cache = {"size": None, "reclaimable": None}
        docker_available = False
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "serverId": request["serverId"], "hostId": request["hostId"],
            "recordVersion": request.get("expectedRecordVersion"), "capturedAt": now(),
            "contextBytes": directory_bytes(CONTEXT), "buildActive": active,
            "dockerAvailable": docker_available, "dockerCache": cache,
            "allowed": not active and docker_available}


def cleanup(request):
    check_target(request)
    fd = acquire_build_lock()
    if fd is None:
        reject("BUILD_CACHE_BUSY")
    try:
        context_bytes = directory_bytes(CONTEXT)
        if CONTEXT.exists():
            import shutil
            shutil.rmtree(CONTEXT)
        output = docker(["builder", "prune", "-f"])
        return {"schemaVersion": 1, "requestId": request["requestId"],
                "serverId": request["serverId"], "hostId": request["hostId"],
                "recordVersion": request.get("expectedRecordVersion"), "capturedAt": now(),
                "contextBytes": context_bytes, "dockerOutput": output[-4096:]}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main(request):
    if not isinstance(request, dict) or request.get("action") not in ("preview", "cleanup"):
        reject("BUILD_CACHE_INPUT_INVALID")
    data = preview(request) if request["action"] == "preview" else cleanup(request)
    print(json.dumps(data, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(json.loads(input()))
    except Rejected as failure:
        print(json.dumps({"schemaVersion": 1, "ok": False, "code": failure.code}, separators=(",", ":")), flush=True)
        raise SystemExit(2)
