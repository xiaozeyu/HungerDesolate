"""Prepare and validate a DST release staging before importing into catalog."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

ROOT = Path("/var/lib/famine-hub")
STAGING = ROOT / "staging"
RELEASES = ROOT / "releases"
CATALOG = RELEASES / "catalog.json"
MAX_BYTES = 1024 * 1024


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


def inspect(request):
    host_id = request.get("hostId")
    release_id = request.get("releaseId")
    expected_catalog_digest = request.get("catalogDigest")

    if not valid_uuid(host_id) or not valid_uuid(release_id):
        reject("CREATE_RELEASE_INPUT_INVALID")

    # 验证主机身份
    host = json.loads((ROOT / "host.json").read_bytes())
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")

    # 检查 catalog 是否存在
    catalog_digest = None
    blockers = []

    if CATALOG.exists():
        catalog_content = CATALOG.read_bytes()
        catalog = json.loads(catalog_content)
        if catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id:
            reject("CREATE_RELEASE_CATALOG_INVALID")
        catalog_digest = "sha256:" + hashlib.sha256(
            json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        # 检查 release 是否已存在
        for item in catalog.get("releases", []):
            if item.get("releaseId") == release_id:
                return {"releaseId": release_id, "catalogDigest": catalog_digest,
                        "status": "exists", "blockers": [], "artifactPath": None}

    # 检查 staging 区域是否有待导入的 release
    staging_metadata = STAGING / release_id / "release-metadata.json"
    staging_artifact = STAGING / release_id / "artifact.tar"

    if not staging_metadata.exists():
        blockers.append("CREATE_RELEASE_STAGING_MISSING")
        return {"releaseId": release_id, "catalogDigest": catalog_digest,
                "status": "blocked", "blockers": blockers, "artifactPath": None}

    if not staging_artifact.exists():
        blockers.append("CREATE_RELEASE_ARTIFACT_MISSING")

    # 验证元数据
    try:
        metadata = json.loads(staging_metadata.read_bytes())
        if (metadata.get("schemaVersion") != 1
                or metadata.get("releaseId") != release_id
                or metadata.get("platform") != "linux/amd64"
                or not isinstance(metadata.get("image"), str)
                or not isinstance(metadata.get("imageDigest"), str)
                or not isinstance(metadata.get("dstBuild"), str)
                or not isinstance(metadata.get("commands"), dict)):
            blockers.append("CREATE_RELEASE_METADATA_INVALID")
    except (json.JSONDecodeError, OSError):
        blockers.append("CREATE_RELEASE_METADATA_INVALID")

    status = "ready" if not blockers else "blocked"
    artifact_path = str(staging_artifact) if staging_artifact.exists() else None

    return {"releaseId": release_id, "catalogDigest": catalog_digest,
            "status": status, "blockers": blockers, "artifactPath": artifact_path}


def main(request):
    try:
        result = inspect(request)
        print(json.dumps({"ok": True, "data": result}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"ok": False, "code": "CREATE_RELEASE_PREPARE_FAILED"}, separators=(",", ":")), flush=True)
