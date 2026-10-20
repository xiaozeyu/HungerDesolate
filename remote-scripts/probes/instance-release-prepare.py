"""Read-only preflight for preparing a catalog-selected DST release."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

ROOT = Path("/var/lib/famine-hub")
CATALOG = ROOT / "releases" / "catalog.json"
STAGING = ROOT / "releases" / "staging"


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
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("CREATE_RELEASE_CATALOG_INVALID")


def catalog_digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inspect(request):
    host_id = request.get("hostId")
    release_id = request.get("releaseId")
    expected_digest = request.get("catalogDigest")
    if not valid_uuid(host_id) or not valid_uuid(release_id) or not isinstance(expected_digest, str):
        reject("CREATE_RELEASE_INPUT_INVALID")
    host = read_json(ROOT / "host.json", 65536)
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    try:
        catalog = read_json(CATALOG, 1024 * 1024)
    except FileNotFoundError:
        return {"schemaVersion": 1, "hostId": host_id, "releaseId": release_id,
                "catalogDigest": None, "status": "blocked", "blockers": ["CREATE_RELEASE_REQUIRED"],
                "artifactPath": None}
    observed_digest = catalog_digest(catalog)
    if observed_digest != expected_digest:
        reject("RELEASE_CATALOG_CHANGED")
    releases = catalog.get("releases")
    if catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id or not isinstance(releases, list):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    release = next((item for item in releases if isinstance(item, dict) and item.get("releaseId") == release_id), None)
    if release is None:
        return {"schemaVersion": 1, "hostId": host_id, "releaseId": release_id,
                "catalogDigest": observed_digest, "status": "blocked", "blockers": ["CREATE_RELEASE_REQUIRED"],
                "artifactPath": None}
    if release.get("verified") is not True:
        return {"schemaVersion": 1, "hostId": host_id, "releaseId": release_id,
                "catalogDigest": observed_digest, "status": "blocked", "blockers": ["CREATE_RELEASE_NOT_VERIFIED"],
                "artifactPath": None}
    staging_dir = STAGING / release_id
    if not staging_dir.exists():
        return {"schemaVersion": 1, "hostId": host_id, "releaseId": release_id,
                "catalogDigest": observed_digest, "status": "blocked", "blockers": ["CREATE_RELEASE_STAGING_MISSING"],
                "artifactPath": None}
    info = staging_dir.lstat()
    if (not staging_dir.is_dir() or staging_dir.is_symlink() or info.st_mode & 0o077
            or info.st_uid not in (0, os.geteuid())):
        reject("CREATE_RELEASE_STAGING_INVALID")
    try:
        metadata = json.loads((staging_dir / "release-metadata.json").read_bytes())
        metadata_entry = dict(metadata)
        metadata_entry.pop("schemaVersion", None)
    except (OSError, ValueError, TypeError):
        return {"schemaVersion": 1, "hostId": host_id, "releaseId": release_id,
                "catalogDigest": observed_digest, "status": "blocked", "blockers": ["CREATE_RELEASE_METADATA_INVALID"],
                "artifactPath": None}
    if metadata_entry != release:
        reject("CREATE_RELEASE_METADATA_INVALID")
    return {"schemaVersion": 1, "hostId": host_id, "releaseId": release_id,
            "catalogDigest": observed_digest, "status": "ready", "blockers": [],
            "artifactPath": "/var/lib/famine-hub/staging/" + release_id}


def main(request):
    try:
        print(json.dumps({"ok": True, "data": inspect(request)}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "code": "CREATE_RELEASE_CATALOG_INVALID"}, separators=(",", ":")), flush=True)
