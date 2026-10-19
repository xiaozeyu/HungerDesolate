"""Read and validate the host-level DST release catalog without changing it."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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
    if not isinstance(value, dict) or set(value) != required:
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
    return {"schemaVersion": 1, "hostId": host_id,
            "catalogDigest": "sha256:" + hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "releases": releases,
            "blockers": [] if any(item["verified"] and item["runtimeVerified"] for item in releases) else ["CREATE_RELEASE_REQUIRED"]}


def main(request):
    try:
        print(json.dumps({"ok": True, "data": inspect(request)}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "code": "CREATE_RELEASE_CATALOG_INVALID"}, separators=(",", ":")), flush=True)
