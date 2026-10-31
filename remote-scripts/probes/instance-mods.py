"""Read instance MOD desired/deployed state and bounded modinfo sources."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
MAX_JSON = 2 * 1024 * 1024
MAX_MODINFO = 2 * 1024 * 1024
MAX_MODS = 200
ID = re.compile(r"^[0-9a-fA-F-]{1,128}$")
WORKSHOP = re.compile(r"^[0-9]{1,20}$")


class ModProbeError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise ModProbeError(code)


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def read_json(path, default):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return default
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > MAX_JSON or info.st_mode & 0o077
            or info.st_uid not in (0, os.geteuid())):
        reject("MOD_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > MAX_JSON or opened.st_uid not in (0, os.geteuid())
                or opened.st_mode & 0o077):
            reject("MOD_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            reject("MOD_STATE_INVALID")
        return json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        reject("MOD_STATE_INVALID")
    finally:
        os.close(fd)


def safe_directory(path, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in owners or info.st_mode & 0o022):
        reject("MOD_ARTIFACT_INVALID")
    return True


def safe_artifact_path(root, path, runtime=False):
    if not safe_directory(root):
        return False
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if not safe_directory(current, runtime=runtime):
            return False
    return True


def read_manifest(instance_id, host_id):
    instance_path = ROOT / "instances" / instance_id
    if not safe_directory(ROOT / "instances") or not safe_directory(instance_path):
        reject("MANIFEST_NOT_FOUND")
    manifest = read_json(instance_path / "manifest.json", None)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        reject("MANIFEST_NOT_FOUND")
    if manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id:
        reject("INSTANCE_IDENTITY_MISMATCH")
    if not isinstance(manifest.get("configRevision"), int) or manifest["configRevision"] < 0:
        reject("MANIFEST_INVALID")
    return manifest


def normalize_state(value, instance_id):
    if value is None:
        return {}, None
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        reject("MOD_STATE_INVALID")
    if value.get("instanceId") != instance_id or not isinstance(value.get("mods"), list):
        reject("MOD_STATE_INVALID")
    if len(value["mods"]) > MAX_MODS:
        reject("MOD_READ_LIMIT")
    result = {}
    for item in value["mods"]:
        if not isinstance(item, dict) or not WORKSHOP.fullmatch(str(item.get("workshopId", ""))):
            reject("MOD_STATE_INVALID")
        workshop_id = str(item["workshopId"])
        enabled = item.get("enabled")
        if type(enabled) is not bool:
            reject("MOD_STATE_INVALID")
        configuration = item.get("configurationOptions", {})
        if not isinstance(configuration, dict) or len(configuration) > 256:
            reject("MOD_STATE_INVALID")
        artifact_digest = item.get("artifactDigest")
        if artifact_digest is not None and (not isinstance(artifact_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest)):
            reject("MOD_STATE_INVALID")
        result[workshop_id] = {
            "workshopId": workshop_id,
            "enabled": enabled,
            "configurationOptions": configuration,
            "artifactDigest": artifact_digest,
        }
    return result, value.get("revision")


def candidate_paths(instance_id, workshop_id, release_id, artifact_digest=None):
    root = ROOT / "instances" / instance_id
    names = [f"workshop-{workshop_id}", workshop_id]
    bases = []
    prepared_roots = [ROOT / "mods" / "prepared" / workshop_id]
    for parent in (ROOT / "mods", ROOT / "mods" / "prepared"):
        if parent.exists() or parent.is_symlink():
            safe_directory(parent)
    if artifact_digest is not None:
        if not isinstance(artifact_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
            reject("MOD_STATE_INVALID")
        directory = None
        for prepared_root in prepared_roots:
            candidate = prepared_root / artifact_digest
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
                reject("MOD_ARTIFACT_INVALID")
            directory = candidate
            break
        if directory is None:
            return
        metadata = read_json(directory / "manifest.json", None)
        if (isinstance(metadata, dict) and metadata.get("workshopId") == workshop_id
                and metadata.get("contentDigest") == artifact_digest):
            yield directory / "content" / "modinfo.lua"
        return
    for prepared_root in prepared_roots:
        try:
            if not safe_directory(prepared_root):
                continue
            candidates = []
            for item in prepared_root.iterdir():
                if not re.fullmatch(r"[0-9a-f]{64}", item.name):
                    continue
                metadata = read_json(item / "manifest.json", None)
                if isinstance(metadata, dict) and metadata.get("workshopId") == workshop_id:
                    candidates.append((metadata.get("preparedAt", ""), item.name))
            candidates.sort(reverse=True)
            for _, content_digest in candidates[:20]:
                if not isinstance(content_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", content_digest):
                    continue
                directory = prepared_root / content_digest
                metadata = read_json(directory / "manifest.json", None)
                if (isinstance(metadata, dict) and metadata.get("workshopId") == workshop_id
                        and metadata.get("contentDigest") == content_digest):
                    yield directory / "content" / "modinfo.lua"
        except FileNotFoundError:
            continue
    if isinstance(release_id, str) and release_id:
        bases.extend([root / "mods" / "releases" / release_id / "Master", root / "mods" / "releases" / release_id])
    bases.extend([root / "mods" / "releases" / "Master", root / "mods" / "releases"])
    for base in bases:
        for name in names:
            yield base / name / "modinfo.lua"
            yield base / "mods" / name / "modinfo.lua"


def read_modinfo(instance_id, workshop_id, release_id, artifact_digest=None):
    for path in candidate_paths(instance_id, workshop_id, release_id, artifact_digest):
        host_root = ROOT / "mods"
        artifact_root = host_root if host_root in path.parents else ROOT / "instances" / instance_id / "mods"
        release_root = artifact_root / "releases"
        runtime_owned = release_root in path.parents
        if not safe_artifact_path(artifact_root, path.parent, runtime=runtime_owned):
            continue
        owners = (0, os.geteuid(), 1000) if runtime_owned else (0, os.geteuid())
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_MODINFO or info.st_mode & 0o022
                or info.st_uid not in owners):
            reject("MOD_ARTIFACT_INVALID")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                    or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or opened.st_size > MAX_MODINFO or opened.st_mode & 0o022
                    or opened.st_uid not in owners):
                reject("MOD_ARTIFACT_INVALID")
            with os.fdopen(fd, "rb", closefd=False) as source_file:
                source = source_file.read(MAX_MODINFO + 1)
            if len(source) > MAX_MODINFO:
                reject("MOD_ARTIFACT_INVALID")
        except OSError:
            reject("MOD_ARTIFACT_READ_FAILED")
        finally:
            os.close(fd)
        if not source or source.startswith(b"\x1b"):
            reject("MOD_ARTIFACT_INVALID")
        prepared_at = None
        content_digest = None
        if path.parent.name == "content":
            content_digest = path.parents[1].name
            metadata = read_json(path.parents[1] / "manifest.json", None)
            if isinstance(metadata, dict) and metadata.get("contentDigest") == content_digest:
                prepared_at = metadata.get("preparedAt") if isinstance(metadata.get("preparedAt"), str) else None
        return {
            "folderName": f"workshop-{workshop_id}" if path.parent.name == "content" else path.parent.name,
            "sourceBase64": base64.b64encode(source).decode("ascii"),
            "sourceSha256": hashlib.sha256(source).hexdigest(),
            "contentDigest": content_digest,
            "preparedAt": prepared_at,
        }
    return None


def read_prepared_artifact(instance_id, workshop_id, content_digest):
    if not isinstance(content_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", content_digest):
        return None
    roots = [(ROOT / "mods" / "prepared" / workshop_id, ROOT / "mods")]
    for parent in (ROOT / "mods", ROOT / "mods" / "prepared"):
        if parent.exists() or parent.is_symlink():
            safe_directory(parent)
    directory = None
    for root, safe_root in roots:
        candidate = root / content_digest
        try:
            if not safe_directory(root) or not safe_directory(candidate):
                continue
        except FileNotFoundError:
            continue
        directory = candidate
        break
    if directory is None:
        return None
    metadata = read_json(directory / "manifest.json", None)
    if (not isinstance(metadata, dict) or metadata.get("workshopId") != workshop_id
            or metadata.get("contentDigest") != content_digest):
        reject("MOD_ARTIFACT_INVALID")
    path = directory / "content" / "modinfo.lua"
    if not safe_artifact_path(safe_root, path.parent):
        reject("MOD_ARTIFACT_INVALID")
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_MODINFO
                or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
            reject("MOD_ARTIFACT_INVALID")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                    or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or opened.st_size > MAX_MODINFO or opened.st_mode & 0o022):
                reject("MOD_ARTIFACT_INVALID")
            with os.fdopen(fd, "rb", closefd=False) as source_file:
                source = source_file.read(MAX_MODINFO + 1)
        finally:
            os.close(fd)
        if not source or len(source) > MAX_MODINFO or source.startswith(b"\x1b"):
            reject("MOD_ARTIFACT_INVALID")
    except FileNotFoundError:
        return None
    return {
        "folderName": f"workshop-{workshop_id}",
        "sourceBase64": base64.b64encode(source).decode("ascii"),
        "sourceSha256": hashlib.sha256(source).hexdigest(),
        "contentDigest": content_digest,
        "preparedAt": metadata.get("preparedAt") if isinstance(metadata.get("preparedAt"), str) else None,
        "sourceHandle": metadata.get("sourceHandle") if isinstance(metadata.get("sourceHandle"), str) else None,
    }


def read_candidate_artifact(instance_id, workshop_id, desired_digest):
    root = ROOT / "instances" / instance_id
    candidate_root = root / "mods" / "candidates"
    if not all(safe_directory(path) for path in (root, root / "mods", candidate_root)):
        return None
    pointer = read_json(candidate_root / (workshop_id + ".json"), None)
    if (not isinstance(pointer, dict) or pointer.get("schemaVersion") != 1
            or pointer.get("workshopId") != workshop_id):
        return None
    digest = pointer.get("contentDigest")
    if not isinstance(digest, str):
        return None
    artifact = read_prepared_artifact(instance_id, workshop_id, digest)
    if artifact is None:
        return None
    artifact["sourceHandle"] = pointer.get("sourceHandle")
    artifact["preparedAt"] = pointer.get("preparedAt")
    return artifact


def main(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or not valid_id(request.get("hostId")) or not valid_id(request.get("instanceId"))
            or not valid_id(request.get("serverId")) or not valid_id(request.get("requestId"))):
        reject("MOD_INPUT_INVALID")
    host_id = request["hostId"]
    instance_id = request["instanceId"]
    manifest = read_manifest(instance_id, host_id)
    desired, desired_revision = normalize_state(
        read_json(ROOT / "instances" / instance_id / "desired" / "mods.json", None), instance_id)
    deployed, deployed_revision = normalize_state(
        read_json(ROOT / "instances" / instance_id / "deployed-mods.json", None), instance_id)
    if desired_revision is not None and desired_revision > manifest["configRevision"]:
        reject("MOD_STATE_REVISION_INVALID")
    active = read_json(ROOT / "instances" / instance_id / "active-release.json", {})
    release_id = active.get("modsReleaseId") if isinstance(active, dict) else None
    items = []
    for workshop_id in sorted(set(desired) | set(deployed)):
        wanted = desired.get(workshop_id) or deployed.get(workshop_id) or {}
        artifact = read_modinfo(instance_id, workshop_id, release_id, wanted.get("artifactDigest"))
        deployed_state = deployed.get(workshop_id)
        deployed_artifact = None
        if isinstance(deployed_state, dict) and isinstance(deployed_state.get("artifactDigest"), str):
            deployed_artifact = read_prepared_artifact(instance_id, workshop_id, deployed_state["artifactDigest"])
        candidate_artifact = read_candidate_artifact(
            instance_id, workshop_id,
            wanted.get("artifactDigest") if isinstance(wanted, dict) else None)
        items.append({
            "workshopId": workshop_id,
            "desired": desired.get(workshop_id),
            "deployed": deployed_state,
            "artifact": artifact,
            "deployedArtifact": deployed_artifact,
            "candidateArtifact": candidate_artifact,
        })
    response = {
        "schemaVersion": 1,
        "requestId": request["requestId"],
        "target": {"serverId": request["serverId"], "hostId": host_id, "instanceId": instance_id},
        "snapshotId": str(uuid.uuid4()),
        "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "remote",
        "freshness": "fresh",
        "configRevision": manifest["configRevision"],
        "items": items,
    }
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main(json.loads(input()))
    except ModProbeError as error:
        print(json.dumps({"schemaVersion": 1, "ok": False, "code": error.code}, separators=(",", ":")))
        raise SystemExit(2)
    except Exception:
        print(json.dumps({"schemaVersion": 1, "ok": False, "code": "MOD_PROBE_FAILED"}, separators=(",", ":")))
        raise SystemExit(2)
