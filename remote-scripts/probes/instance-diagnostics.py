"""Read bounded instance health, sanitized shard logs and failed-task evidence."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
MAX_JSON = 262144
MAX_LOG_BYTES = 64 * 1024
MAX_LINES = 200
MAX_TASKS = 20
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
WORKSHOP = re.compile(r"^[0-9]{1,20}$")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
REDACTIONS = (
    re.compile(r"(?i)\b(token|password|passwd|secret|authorization)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bpds-[A-Za-z0-9._-]{8,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
)


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise Rejected(code)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path, limit=MAX_JSON, missing=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return missing
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("DIAGNOSTICS_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit):
            reject("DIAGNOSTICS_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        reject("DIAGNOSTICS_STATE_INVALID")
    try:
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError):
        reject("DIAGNOSTICS_STATE_INVALID")


def safe_dir(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
        reject("DIAGNOSTICS_PATH_UNSAFE")
    return True


def valid_uuid(value):
    return isinstance(value, str) and UUID.fullmatch(value) is not None


def docker(arguments, timeout=15):
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(["docker", *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
            timeout=timeout, check=False, env=environment)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 512 * 1024:
        return None
    return result.stdout


def identity(request):
    target = request.get("target") if isinstance(request, dict) else None
    if (not isinstance(target, dict) or not all(valid_uuid(target.get(key))
            for key in ("serverId", "hostId", "instanceId"))):
        reject("DIAGNOSTICS_INPUT_INVALID")
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json", 1024 * 1024)
    if host.get("hostId") != target["hostId"] or registry.get("hostId") != target["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    entries = registry.get("instances")
    entry = next((item for item in entries if isinstance(item, dict)
                  and item.get("instanceId") == target["instanceId"]), None) if isinstance(entries, list) else None
    if entry is None or entry.get("lifecycle") != "ready" or entry.get("relativePath") != "instances/" + target["instanceId"]:
        reject("INSTANCE_NOT_FOUND")
    instance = ROOT / "instances" / target["instanceId"]
    if not safe_dir(instance):
        reject("INSTANCE_NOT_FOUND")
    manifest = read_json(instance / "manifest.json", 65536)
    if (manifest.get("hostId") != target["hostId"] or manifest.get("instanceId") != target["instanceId"]
            or not isinstance(manifest.get("configRevision"), int)):
        reject("INSTANCE_IDENTITY_MISMATCH")
    return instance, manifest


def release_build(active):
    release_id = active.get("dstReleaseId") if isinstance(active, dict) else None
    if not valid_uuid(release_id):
        return None, None
    catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024, missing={})
    releases = catalog.get("releases") if isinstance(catalog, dict) else None
    release = next((item for item in releases if isinstance(item, dict)
                    and item.get("releaseId") == release_id and item.get("verified") is True), None) if isinstance(releases, list) else None
    return (release.get("dstBuild") if release else None), release_id


def shard_containers(instance_id):
    output = docker(["ps", "-a", "--filter", "label=com.hungerdesolate.instanceId=" + instance_id,
                     "--format", "{{.ID}}\t{{.Label \"com.hungerdesolate.role\"}}\t{{.Status}}"])
    containers = {}
    if not output:
        return containers
    for row in output.splitlines():
        parts = row.split("\t")
        if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{12,64}", parts[0]) or parts[1] not in ("master", "caves"):
            continue
        containers[parts[1]] = {"containerId": parts[0], "status": parts[2][:128]}
    return containers


def redact(line):
    value = ANSI.sub("", line).replace("\x00", "").replace("\r", "")
    for pattern in REDACTIONS:
        value = pattern.sub(lambda match: (match.group(1) + "=<redacted>") if match.lastindex else "<redacted>", value)
    return value[:512]


def read_logs(container_id, limit):
    output = docker(["logs", "--tail", str(limit), "--timestamps", container_id], timeout=20)
    if output is None:
        return {"lines": [], "truncated": False, "available": False}
    raw = output
    truncated = len(raw.encode("utf-8")) > MAX_LOG_BYTES
    if truncated:
        raw = raw.encode("utf-8")[-MAX_LOG_BYTES:].decode("utf-8", "replace")
    lines = [redact(line)[:512] for line in raw.splitlines()[-limit:]]
    return {"lines": lines, "truncated": truncated, "available": True}


def failed_tasks(target):
    result = []
    for directory_name in ("tasks", "mod-apply-tasks", "backup-tasks", "restore-tasks"):
        directory = ROOT / directory_name
        if not safe_dir(directory):
            continue
        for child in directory.iterdir():
            if not child.is_dir() or not valid_uuid(child.name):
                continue
            try:
                state = read_json(child / "status.json", 262144)
            except Rejected:
                continue
            task_target = state.get("target") if isinstance(state, dict) else None
            if not isinstance(task_target, dict) or task_target.get("instanceId") != target["instanceId"]:
                continue
            if task_target.get("hostId") not in (None, target["hostId"]):
                continue
            if state.get("status") not in ("failed", "interrupted"):
                continue
            journal = []
            try:
                journal = [json.loads(line) for line in (child / "journal.jsonl").read_text(encoding="utf-8").splitlines()[-8:]]
            except (OSError, ValueError):
                journal = []
            error = state.get("error") if isinstance(state.get("error"), dict) else {}
            result.append({
                "taskId": child.name,
                "operation": state.get("operation"),
                "status": state.get("status"),
                "stage": state.get("stage"),
                "errorCode": error.get("code"),
                "recoverySteps": error.get("recoverySteps", []) if isinstance(error.get("recoverySteps"), list) else [],
                "createdAt": state.get("createdAt"),
                "finishedAt": state.get("finishedAt"),
                "journal": journal,
            })
    result.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    return result[:MAX_TASKS]


def main(request):
    if not isinstance(request, dict) or request.get("schemaVersion") != 1:
        reject("DIAGNOSTICS_INPUT_INVALID")
    instance, manifest = identity(request)
    target = request["target"]
    limit = request.get("logLimit", MAX_LINES)
    if type(limit) is not int or not 1 <= limit <= MAX_LINES:
        reject("DIAGNOSTICS_INPUT_INVALID")
    active = read_json(instance / "active-release.json", 65536, missing={})
    desired_world = read_json(instance / "desired" / "world.json", 262144, missing={})
    desired_mods = read_json(instance / "desired" / "mods.json", 2 * 1024 * 1024, missing={})
    containers = shard_containers(target["instanceId"])
    dst_build, dst_release_id = release_build(active)
    docker_version = (docker(["version", "--format", "{{.Server.Version}}"]) or "").strip() or None
    try:
        statvfs = os.statvfs(instance)
        disk_available = statvfs.f_bavail * statvfs.f_frsize
    except OSError:
        disk_available = None
    shards = []
    warnings = []
    for role in ("master", "caves"):
        if role == "caves" and manifest.get("cavesEnabled") is not True:
            continue
        container = containers.get(role)
        if container is None:
            warnings.append("SHARD_CONTAINER_MISSING:" + role)
        shards.append({"shard": role, "containerId": container["containerId"] if container else None,
                       "status": container["status"] if container else None})
    if not containers:
        warnings.append("DOCKER_CONTAINERS_UNAVAILABLE")
    statuses = [(containers.get(item["shard"]) or {}).get("status") for item in shards]
    if statuses and all(status is not None and status.startswith("Up") for status in statuses):
        runtime_status = "running"
    elif statuses and all(status is not None for status in statuses):
        runtime_status = "stopped"
    else:
        runtime_status = "unknown"
    logs = []
    for shard in shards:
        container = containers.get(shard["shard"])
        entry = read_logs(container["containerId"], limit) if container else {"lines": [], "truncated": False, "available": False}
        logs.append({"shard": shard["shard"], **entry})
    health = {
        "runtimeStatus": runtime_status,
        "configRevision": manifest.get("configRevision"),
        "worldRevision": desired_world.get("revision") if isinstance(desired_world, dict) else None,
        "modsRevision": desired_mods.get("revision") if isinstance(desired_mods, dict) else None,
        "dstBuild": dst_build,
        "dstReleaseId": dst_release_id,
        "modsReleaseId": active.get("modsReleaseId") if isinstance(active, dict) else None,
        "dockerVersion": docker_version,
        "diskAvailableBytes": disk_available,
        "shards": shards,
        "warnings": warnings,
    }
    print(json.dumps({"schemaVersion": 1, "requestId": request["requestId"], "target": target,
        "recordVersion": request["expectedRecordVersion"], "capturedAt": now(),
        "health": health, "logs": logs, "tasks": failed_tasks(target)}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(json.loads(input()))
    except Rejected as failure:
        print(json.dumps({"schemaVersion": 1, "ok": False, "code": failure.code}, separators=(",", ":")), flush=True)
        raise SystemExit(2)
