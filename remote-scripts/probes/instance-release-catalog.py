"""Read and validate the host-level DST release catalog without changing it."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

ROOT = Path("/var/lib/famine-hub")
CATALOG = ROOT / "releases" / "catalog.json"
MAX_BYTES = 1024 * 1024
MAX_RELEASES = 16


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
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def text(value, limit=256):
    return isinstance(value, str) and 0 < len(value) <= limit and all(ord(char) >= 32 for char in value)


def read_json(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit or opened.st_uid not in (0, os.geteuid())
                or opened.st_mode & 0o077):
            reject("CREATE_RELEASE_CATALOG_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("CREATE_RELEASE_CATALOG_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("CREATE_RELEASE_CATALOG_INVALID")


def validate_release(value):
    required = {"releaseId", "platform", "image", "imageDigest", "dstBuild", "artifactDigest",
                "scriptVersion", "verified", "runtimeVerified", "commands"}
    fields = set(value) if isinstance(value, dict) else set()
    if fields not in (required, required | {"createdAt"}):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    if "createdAt" in value and not text(value["createdAt"], 64):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    if (not valid_uuid(value["releaseId"]) or value["platform"] != "linux/amd64"
            or not text(value["image"]) or re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", value["image"]) is None
            or not digest(value["imageDigest"]) or not text(value["dstBuild"])
            or not digest(value["artifactDigest"]) or not text(value["scriptVersion"])
            or type(value["verified"]) is not bool or type(value["runtimeVerified"]) is not bool
            or not isinstance(value["commands"], dict) or set(value["commands"]) != {"master", "caves"}
            or not all(isinstance(value["commands"][role], list) and 1 <= len(value["commands"][role]) <= 32
                       and all(text(item) for item in value["commands"][role]) for role in ("master", "caves"))):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    return value


def image_sizes(releases):
    digests = sorted({item["imageDigest"] for item in releases})
    if not digests:
        return {}
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}} {{.Size}}", *digests],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=10, check=False, env=environment)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    sizes = {}
    for row in result.stdout.splitlines():
        parts = row.split()
        if len(parts) == 2 and parts[0].startswith("sha256:") and parts[1].isdigit():
            sizes[parts[0]] = int(parts[1])
    return sizes


def release_usage():
    used = set()
    try:
        registry = read_json(ROOT / "registry.json", 2 * 1024 * 1024)
    except (FileNotFoundError, Rejected):
        return used
    for entry in registry.get("instances", []):
        if isinstance(entry, dict) and valid_uuid(entry.get("dstReleaseId")):
            used.add(entry["dstReleaseId"])
    instances_root = ROOT / "instances"
    if not instances_root.is_dir():
        return used
    for instance_dir in instances_root.iterdir():
        if not instance_dir.is_dir() or not valid_uuid(instance_dir.name):
            continue
        for name in ("manifest.json", "active-release.json"):
            path = instance_dir / name
            try:
                value = read_json(path, 65536)
            except (FileNotFoundError, Rejected):
                continue
            if isinstance(value, dict) and valid_uuid(value.get("dstReleaseId")):
                used.add(value["dstReleaseId"])
        retained = instance_dir / "retained-releases.json"
        try:
            value = read_json(retained, 65536)
        except (FileNotFoundError, Rejected):
            continue
        release_ids = value.get("releaseIds") if isinstance(value, dict) else None
        if isinstance(release_ids, list):
            used.update(item for item in release_ids if valid_uuid(item))
    return used


def inspect(request):
    host_id = request.get("hostId")
    if not valid_uuid(host_id):
        reject("CREATE_RELEASE_INPUT_INVALID")
    host = read_json(ROOT / "host.json", 65536)
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    try:
        catalog = read_json(CATALOG, MAX_BYTES)
    except FileNotFoundError:
        return {"schemaVersion": 1, "hostId": host_id, "catalogDigest": None,
                "releases": [], "blockers": ["CREATE_RELEASE_REQUIRED"]}
    if (catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id
            or not isinstance(catalog.get("releases"), list) or len(catalog["releases"]) > MAX_RELEASES):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    releases = [validate_release(item) for item in catalog["releases"]]
    sizes = image_sizes(releases)
    used = release_usage()
    verified = [item for item in releases if item["verified"]]
    newest_id = max(verified, key=lambda item: (int(item["dstBuild"]) if item["dstBuild"].isdigit() else -1,
                                                item.get("createdAt") or ""),
                    default={}).get("releaseId")
    releases = [dict(item, imageSize=sizes.get(item["imageDigest"]),
                     inUse=item["releaseId"] in used,
                     reclaimable=item["releaseId"] not in used and item["releaseId"] != newest_id)
                for item in releases]
    return {"schemaVersion": 1, "hostId": host_id,
            "catalogDigest": "sha256:" + hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "releases": releases,
            "blockers": [] if any(item["verified"] for item in releases) else ["CREATE_RELEASE_REQUIRED"]}


def main(request):
    try:
        print(json.dumps({"ok": True, "data": inspect(request)}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "code": "CREATE_RELEASE_CATALOG_INVALID"}, separators=(",", ":")), flush=True)
