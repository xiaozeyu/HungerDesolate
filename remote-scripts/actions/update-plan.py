"""Validate and persist one instance DST switch plan."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import uuid

ROOT = Path("/var/lib/famine-hub")
PLANS = ROOT / "update-plans"
WORLD_ROOTS = ("config/Cluster_1/Master/save", "config/Cluster_1/Caves/save")
EXCLUDED_NAMES = {"cluster_token.txt", "adminlist.txt", "blocklist.txt", "whitelist.txt",
                  "server_log.txt", "server_chat_log.txt", "client_log.txt"}


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


def read_json(path, limit=1024 * 1024, missing=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("UPDATE_PLAN_STATE_INVALID")
    data = path.read_bytes()
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("UPDATE_PLAN_STATE_INVALID")


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def docker_env():
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def verify_instance(request):
    target = request.get("target")
    if (not isinstance(target, dict) or not valid_uuid(target.get("serverId"))
            or not valid_uuid(target.get("hostId")) or not valid_uuid(target.get("instanceId"))):
        reject("UPDATE_PLAN_INPUT_INVALID")
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json", 1024 * 1024)
    if host.get("hostId") != target["hostId"] or registry.get("hostId") != target["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    entries = [entry for entry in registry.get("instances", [])
               if isinstance(entry, dict) and entry.get("instanceId") == target["instanceId"]]
    if len(entries) != 1 or entries[0].get("lifecycle") != "ready":
        reject("UPDATE_TARGET_NOT_APPLICABLE")
    instance = ROOT / "instances" / target["instanceId"]
    manifest = read_json(instance / "manifest.json", 65536)
    if (manifest.get("hostId") != target["hostId"] or manifest.get("instanceId") != target["instanceId"]
            or manifest.get("composeProject") != "fh_" + target["instanceId"].replace("-", "")):
        reject("UPDATE_TARGET_NOT_APPLICABLE")
    return target, instance, manifest, entries[0]


def runtime_state(instance_id, manifest):
    shards = ["master"] + (["caves"] if manifest.get("cavesEnabled") else [])
    result = subprocess.run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                             "--filter", "label=com.hungerdesolate.instanceId=" + instance_id],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, timeout=10, check=False, env=docker_env())
    if result.returncode != 0 or len(result.stdout.splitlines()) != len(shards):
        return "unknown", []
    ids = result.stdout.splitlines()
    inspected = subprocess.run(["docker", "container", "inspect", "--format",
                                "{{json .Config.Labels}}\t{{.State.Status}}", *ids],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=10, check=False, env=docker_env())
    rows = inspected.stdout.splitlines()
    if inspected.returncode != 0 or len(rows) != len(shards):
        return "unknown", []
    states = {}
    for row in rows:
        labels_raw, separator, state = row.partition("\t")
        try:
            labels = json.loads(labels_raw)
        except ValueError:
            return "unknown", []
        role = labels.get("com.hungerdesolate.role") if isinstance(labels, dict) else None
        if (not separator or labels.get("com.hungerdesolate.hostId") != manifest["hostId"]
                or labels.get("com.hungerdesolate.instanceId") != instance_id
                or role not in shards or role in states
                or state not in ("created", "running", "paused", "restarting", "exited", "dead")):
            return "unknown", []
        states[role] = state
    if all(value == "running" for value in states.values()):
        return "running", shards
    if all(value in ("created", "exited") for value in states.values()):
        return "stopped", []
    return "degraded", sorted(states)


def catalog():
    value = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
    if value.get("schemaVersion") != 1 or not isinstance(value.get("releases"), list):
        reject("UPDATE_RELEASE_UNAVAILABLE")
    return value["releases"]


def release_by_id(releases, release_id):
    return next((item for item in releases
                 if isinstance(item, dict) and item.get("releaseId") == release_id), None)


def release_artifacts_valid(release):
    staging = ROOT / "staging" / release["releaseId"]
    try:
        info = staging.lstat()
        metadata = read_json(staging / "release-metadata.json", 262144)
    except (FileNotFoundError, Rejected):
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077:
        return False
    metadata.pop("schemaVersion", None)
    comparable = {key: value for key, value in release.items() if key != "createdAt"}
    if metadata != comparable:
        return False
    dst_dir = ROOT / "dst" / release["releaseId"]
    if dst_dir.exists():
        try:
            info = dst_dir.lstat()
            version = (dst_dir / "version.txt").read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid(), 1000)
                or info.st_mode & 0o022 or version != release.get("dstBuild")):
            return False
    try:
        image = subprocess.run(["docker", "image", "inspect", release["imageDigest"], "--format", "{{.Id}}"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=10, check=False, env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return image.returncode == 0 and image.stdout.strip() == release["imageDigest"]


def data_size(instance, caves):
    total = 0
    for relative in WORLD_ROOTS[:1] + (WORLD_ROOTS[1:] if caves else ()):
        root = instance / relative
        if not root.exists():
            continue
        for current, _, files in os.walk(root, followlinks=False):
            for name in files:
                if name in EXCLUDED_NAMES:
                    continue
                info = (Path(current) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
    return total


def preview(request):
    target, instance, manifest, entry = verify_instance(request)
    expected_revision = request.get("expectedRevision")
    if type(expected_revision) is not int or expected_revision < 0:
        reject("UPDATE_PLAN_INPUT_INVALID")
    if manifest.get("configRevision") != expected_revision:
        reject("UPDATE_REVISION_CONFLICT")
    target_release_id = request.get("targetReleaseId")
    if not valid_uuid(target_release_id):
        reject("UPDATE_PLAN_INPUT_INVALID")
    releases = catalog()
    active = read_json(instance / "active-release.json", 65536, missing={})
    current_release_id = active.get("dstReleaseId") if isinstance(active, dict) else None
    current_release = release_by_id(releases, current_release_id) if valid_uuid(current_release_id) else None
    target_release = release_by_id(releases, target_release_id)
    runtime, running_shards = runtime_state(target["instanceId"], manifest)
    blockers = []
    warnings = []
    if runtime not in ("running", "stopped"):
        blockers.append("UPDATE_RUNTIME_DEGRADED" if runtime == "degraded" else "UPDATE_RUNTIME_UNKNOWN")
    if entry.get("pendingTaskId"):
        blockers.append("UPDATE_INSTANCE_BUSY")
    if target_release is None:
        blockers.append("UPDATE_TARGET_RELEASE_NOT_FOUND")
    elif target_release.get("verified") is not True:
        blockers.append("UPDATE_TARGET_RELEASE_NOT_VERIFIED")
    elif not release_artifacts_valid(target_release):
        blockers.append("UPDATE_TARGET_RELEASE_IMAGE_MISSING")
    if current_release is None:
        blockers.append("UPDATE_TARGET_NOT_APPLICABLE")
    elif target_release is not None:
        if target_release.get("releaseId") == current_release.get("releaseId"):
            blockers.append("UPDATE_SAME_RELEASE")
        target_build = target_release.get("dstBuild")
        current_build = current_release.get("dstBuild")
        if not isinstance(target_build, str) or not target_build.isdigit() or not isinstance(current_build, str) or not current_build.isdigit():
            blockers.append("UPDATE_TARGET_BUILD_UNKNOWN")
        elif int(target_build) < int(current_build):
            blockers.append("UPDATE_TARGET_BUILD_OLDER")
        elif int(target_build) == int(current_build):
            blockers.append("UPDATE_SAME_RELEASE")
    estimated = data_size(instance, manifest.get("cavesEnabled") is True)
    estimated = estimated + max(estimated // 10, 1024 * 1024)
    filesystem = os.statvfs(ROOT)
    available = filesystem.f_bavail * filesystem.f_frsize
    if available < estimated + 1024 * 1024 * 1024:
        blockers.append("UPDATE_SPACE_INSUFFICIENT")
    rollback_available = current_release is not None and release_artifacts_valid(current_release)
    rollback_reason = None if rollback_available else "旧 Release 产物或镜像不可用"
    if not rollback_available:
        warnings.append("UPDATE_ROLLBACK_UNAVAILABLE")
    if target_release is not None and target_release.get("runtimeVerified") is not True:
        warnings.append("DST_RELEASE_RUNTIME_UNVERIFIED")
    if blockers:
        reject("DST_UPDATE_BLOCKED", {"blockers": blockers})
    plan_id = str(uuid.uuid4())
    created = datetime.now(timezone.utc)
    expires = created + timedelta(minutes=10)
    plan = {"schemaVersion": 1, "planId": plan_id, "target": target,
            "baseRevision": expected_revision, "currentReleaseId": current_release_id,
            "targetReleaseId": target_release_id, "currentBuild": current_release.get("dstBuild"),
            "targetBuild": target_release.get("dstBuild"), "rollbackAvailable": rollback_available,
            "createdAt": created.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "expiresAt": expires.isoformat(timespec="seconds").replace("+00:00", "Z")}
    atomic_json(PLANS / (plan_id + ".json"), plan)
    return {"schemaVersion": 1, "requestId": request["requestId"], "target": target,
            "planId": plan_id, "operation": "instanceUpdate", "baseRevision": expected_revision,
            "current": {"releaseId": current_release_id, "build": current_release.get("dstBuild"),
                        "imageDigest": current_release.get("imageDigest"), "runtimeStatus": runtime,
                        "runningShards": running_shards},
            "targetRelease": {"releaseId": target_release_id, "build": target_release.get("dstBuild"),
                              "imageDigest": target_release.get("imageDigest"),
                              "runtimeVerified": target_release.get("runtimeVerified") is True,
                              "createdAt": target_release.get("createdAt")},
            "shards": ["master"] + (["caves"] if manifest.get("cavesEnabled") else []),
            "stopRequired": runtime == "running",
            "backup": {"required": True, "captureMode": "cold", "scope": "worldSaves",
                       "estimatedBytes": estimated, "availableBytes": available},
            "rollback": {"available": rollback_available, "reason": rollback_reason},
            "warnings": warnings, "blockers": [], "allowed": True,
            "expiresAt": plan["expiresAt"]}


def main(request):
    if request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId")):
        reject("UPDATE_PLAN_INPUT_INVALID")
    data = preview(request)
    print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(json.loads(sys.stdin.buffer.read(65537)))
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code, "details": failure.details},
                         separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print(json.dumps({"ok": False, "code": "UPDATE_PLAN_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
