"""List, import, delete and clean host-level Workshop artifacts."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

ROOT = Path("/var/lib/famine-hub")
MODS = ROOT / "mods"
PREPARED = MODS / "prepared"
WORKSHOP = re.compile(r"^[0-9]{1,20}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
MAX_JSON = 4 * 1024 * 1024


class Rejected(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def reject(code, details=None):
    raise Rejected(code, details)


def read_json(path, limit=MAX_JSON, missing=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return missing
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("HOST_MOD_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit):
            reject("HOST_MOD_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("HOST_MOD_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError, UnicodeError):
        reject("HOST_MOD_STATE_INVALID")


def safe_dir(path, create=False, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in owners or info.st_mode & 0o022):
        reject("HOST_MOD_PATH_UNSAFE")
    return info


def read_manifest(path):
    manifest = read_json(path / "manifest.json")
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1
            or not WORKSHOP.fullmatch(str(manifest.get("workshopId", "")))
            or not DIGEST.fullmatch(str(manifest.get("contentDigest", "")))):
        reject("HOST_MOD_ARTIFACT_INVALID")
    content = path / "content"
    if not content.is_dir() or not (content / "modinfo.lua").is_file():
        reject("HOST_MOD_ARTIFACT_INVALID")
    return manifest


def modinfo_fields(content):
    try:
        text = (content / "modinfo.lua").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values = {}
    for name in ("name", "author", "version"):
        match = re.search(r"(?mi)^\s*" + name + r"\s*=\s*\"([^\"]*)\"", text)
        if match:
            values[name] = match.group(1).strip()[:200]
    return values


def artifact_key(path):
    return path.parent.name, path.name


def target_from(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or not UUID.fullmatch(str(request.get("requestId", "")))
            or not UUID.fullmatch(str(request.get("serverId", "")))
            or not UUID.fullmatch(str(request.get("hostId", "")))
            or type(request.get("expectedRecordVersion")) is not int):
        reject("HOST_MOD_INPUT_INVALID")
    host = read_json(ROOT / "host.json", 65536)
    if host.get("hostId") != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    return request["serverId"], request["hostId"]


def instances():
    registry = read_json(ROOT / "registry.json", 1024 * 1024, missing={})
    entries = registry.get("instances") if isinstance(registry, dict) else None
    if not isinstance(entries, list):
        entries = []
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or not UUID.fullmatch(str(entry.get("instanceId", ""))):
            continue
        if entry.get("lifecycle") != "ready":
            continue
        instance_id = entry["instanceId"]
        path = ROOT / "instances" / instance_id
        try:
            safe_dir(path)
            manifest = read_json(path / "manifest.json", 65536, missing={})
        except Rejected:
            continue
        result.append((instance_id, path, manifest.get("displayName") if isinstance(manifest, dict) else None))
    return result


def backup_references():
    refs = set()
    root = ROOT / "backups"
    if not root.is_dir():
        return refs
    for instance_dir in root.iterdir():
        if not instance_dir.is_dir() or not UUID.fullmatch(instance_dir.name):
            continue
        for backup in instance_dir.iterdir():
            if not backup.is_dir() or not UUID.fullmatch(backup.name):
                continue
            try:
                metadata = read_json(backup / "metadata.json", 262144, missing=None)
            except Rejected:
                continue
            mods = metadata.get("mods") if isinstance(metadata, dict) else None
            if not isinstance(mods, list):
                continue
            for item in mods:
                if not isinstance(item, dict):
                    continue
                workshop_id = str(item.get("workshopId"))
                digest = str(item.get("artifactDigest"))
                if WORKSHOP.fullmatch(workshop_id) and DIGEST.fullmatch(digest):
                    refs.add((workshop_id, digest))
    return refs


def usage(workshop_id, content_digest, backup_refs=None):
    used = []
    for instance_id, path, display_name in instances():
        selected = False
        for name in ("desired/mods.json", "deployed-mods.json"):
            state = read_json(path / name, 2 * 1024 * 1024, missing=None)
            if not isinstance(state, dict) or not isinstance(state.get("mods"), list):
                continue
            for item in state["mods"]:
                if (isinstance(item, dict) and str(item.get("workshopId")) == workshop_id
                        and item.get("artifactDigest") == content_digest):
                    selected = True
        if not selected:
            active = read_json(path / "active-release.json", 65536, missing={})
            mods_release = active.get("modsReleaseId") if isinstance(active, dict) else None
            if isinstance(mods_release, str):
                release = read_json(path / "mods" / "releases" / mods_release / "manifest.json", 4 * 1024 * 1024, missing={})
                for item in release.get("mods", []) if isinstance(release, dict) and isinstance(release.get("mods"), list) else []:
                    if (isinstance(item, dict) and str(item.get("workshopId")) == workshop_id
                            and item.get("artifactDigest") == content_digest):
                        selected = True
        if selected:
            used.append({"instanceId": instance_id, "displayName": display_name})
    if backup_refs is not None and (workshop_id, content_digest) in backup_refs:
        used.append({"instanceId": "backup", "displayName": "备份引用"})
    return used


def collect_items():
    items = []
    references = backup_references()
    if PREPARED.is_dir():
        for workshop in sorted(PREPARED.iterdir(), key=lambda item: item.name):
            if not workshop.is_dir() or not WORKSHOP.fullmatch(workshop.name):
                continue
            for artifact in sorted(workshop.iterdir(), key=lambda item: item.name):
                if not artifact.is_dir() or not DIGEST.fullmatch(artifact.name):
                    continue
                manifest = read_manifest(artifact)
                if str(manifest.get("workshopId")) != workshop.name or str(manifest.get("contentDigest")) != artifact.name:
                    reject("HOST_MOD_ARTIFACT_INVALID")
                info = modinfo_fields(artifact / "content")
                items.append({
                    "workshopId": workshop.name,
                    "contentDigest": artifact.name,
                    "sourceHandle": manifest.get("sourceHandle"),
                    "preparedAt": manifest.get("preparedAt"),
                    "sizeBytes": manifest.get("sizeBytes"),
                    "fileCount": manifest.get("fileCount"),
                    "usedBy": usage(workshop.name, artifact.name, references),
                    "name": manifest.get("name") or info.get("name"),
                    "author": manifest.get("author") or info.get("author"),
                    "previewUrl": manifest.get("previewUrl"),
                    "version": manifest.get("version") or info.get("version"),
                })
    return items


def list_items(request):
    target_from(request)
    return {"schemaVersion": 1, "items": collect_items(), "target": {"serverId": request["serverId"], "hostId": request["hostId"]},
            "requestId": request["requestId"], "recordVersion": request["expectedRecordVersion"]}


def reclaimable_items(items, keep=()):
    grouped = {}
    for item in items:
        grouped.setdefault(item["workshopId"], []).append(item)
    result = []
    for versions in grouped.values():
        versions.sort(key=lambda item: (item.get("preparedAt") or "", item["contentDigest"]), reverse=True)
        for index, item in enumerate(versions):
            if index == 0 or item["usedBy"] or (item["workshopId"], item["contentDigest"]) in keep:
                continue
            result.append(item)
    result.sort(key=lambda item: (item["workshopId"], item.get("preparedAt") or ""))
    return result


def cleanup(request):
    target_from(request)
    keep = set()
    requested = request.get("keep") if isinstance(request.get("keep"), list) else []
    for item in requested:
        if (isinstance(item, dict) and WORKSHOP.fullmatch(str(item.get("workshopId")))
                and DIGEST.fullmatch(str(item.get("contentDigest")))):
            keep.add((item["workshopId"], item["contentDigest"]))
    lock_path = MODS / "library.lock"
    safe_dir(MODS, create=True)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        items = reclaimable_items(collect_items(), keep)
        deleted = []
        for item in items:
            artifact = PREPARED / item["workshopId"] / item["contentDigest"]
            safe_dir(artifact)
            read_manifest(artifact)
            shutil.rmtree(artifact)
            deleted.append({"workshopId": item["workshopId"], "contentDigest": item["contentDigest"]})
        return {"schemaVersion": 1, "target": {"serverId": request["serverId"], "hostId": request["hostId"]},
                "requestId": request["requestId"], "recordVersion": request["expectedRecordVersion"],
                "deleted": deleted}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def delete_item(request):
    target_from(request)
    workshop_id = request.get("workshopId")
    content_digest = request.get("contentDigest")
    if (not isinstance(workshop_id, str) or not WORKSHOP.fullmatch(workshop_id)
            or not isinstance(content_digest, str) or not DIGEST.fullmatch(content_digest)):
        reject("HOST_MOD_INPUT_INVALID")
    artifact = PREPARED / workshop_id / content_digest
    safe_dir(artifact)
    read_manifest(artifact)
    safe_dir(MODS, create=True)
    lock_fd = os.open(MODS / "library.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        used = usage(workshop_id, content_digest, backup_references())
        if used:
            reject("HOST_MOD_IN_USE", {"usedBy": used})
        shutil.rmtree(artifact)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return {"schemaVersion": 1, "target": {"serverId": request["serverId"], "hostId": request["hostId"]},
            "requestId": request["requestId"], "recordVersion": request["expectedRecordVersion"],
            "deleted": {"workshopId": workshop_id, "contentDigest": content_digest}}


def main(request):
    try:
        if not isinstance(request, dict):
            reject("HOST_MOD_INPUT_INVALID")
        action = request.get("action")
        if action == "list":
            data = list_items(request)
        elif action == "delete":
            data = delete_item(request)
        elif action == "cleanup":
            data = cleanup(request)
        else:
            reject("HOST_MOD_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code, "details": failure.details},
                         separators=(",", ":")), flush=True)
    except Exception:
        print(json.dumps({"ok": False, "code": "HOST_MOD_FAILED"}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main(json.load(sys.stdin))
