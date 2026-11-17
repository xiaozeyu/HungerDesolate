"""Install the read-only player bridge into an existing stopped or running instance.

The next instance start recreates the Compose file from the manifest, so this
controller only publishes MOD files, modoverrides and the manifest marker.
"""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
MAX_JSON = 65536
MAX_BRIDGE_FILE = 256 * 1024
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class BridgeRejected(Exception):
    pass


def reject(code):
    raise BridgeRejected(code)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def safe_directory(path, create=False, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            return False
        path.mkdir(mode=0o700)
        info = path.lstat()
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    forbidden = 0o022 if runtime else 0o077
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in owners or info.st_mode & forbidden):
        reject("PLAYER_BRIDGE_PATH_UNSAFE")
    return True


def read_json(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_JSON
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("PLAYER_BRIDGE_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > MAX_JSON or opened.st_uid not in (0, os.geteuid())
                or opened.st_mode & 0o077):
            reject("PLAYER_BRIDGE_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            reject("PLAYER_BRIDGE_STATE_INVALID")
        return json.loads(data)
    except (OSError, ValueError, UnicodeDecodeError):
        reject("PLAYER_BRIDGE_STATE_INVALID")
    finally:
        os.close(fd)


def atomic_bytes(path, payload, mode=0o600, runtime=False):
    safe_directory(path.parent, create=True, runtime=runtime)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(payload)
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        os.chmod(path, mode)
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


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def bridge_build(payload):
    if (not isinstance(payload, dict) or set(payload) != {"version", "digest", "files"}
            or not isinstance(payload.get("version"), str)
            or VERSION.fullmatch(payload["version"]) is None
            or not isinstance(payload.get("digest"), str)
            or DIGEST.fullmatch(payload["digest"]) is None
            or not isinstance(payload.get("files"), dict)
            or set(payload["files"]) != {"modinfo.lua", "modmain.lua"}):
        reject("PLAYER_BRIDGE_PAYLOAD_INVALID")
    decoded = {}
    for name in ("modinfo.lua", "modmain.lua"):
        value = payload["files"][name]
        if not isinstance(value, str):
            reject("PLAYER_BRIDGE_PAYLOAD_INVALID")
        try:
            data = base64.b64decode(value, validate=True)
            data.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            reject("PLAYER_BRIDGE_PAYLOAD_INVALID")
        if not data or len(data) > MAX_BRIDGE_FILE:
            reject("PLAYER_BRIDGE_PAYLOAD_INVALID")
        decoded[name] = data
    hasher = hashlib.sha256()
    for name in ("modinfo.lua", "modmain.lua"):
        hasher.update(name.encode("ascii") + b"\0" + decoded[name])
    if "sha256:" + hasher.hexdigest() != payload["digest"]:
        reject("PLAYER_BRIDGE_DIGEST_MISMATCH")
    return payload["version"], payload["digest"], decoded


def deployed_mods(instance):
    value = read_json(instance / "deployed-mods.json")
    if value is None:
        return []
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1
            or not isinstance(value.get("mods"), list) or len(value["mods"]) > 200):
        reject("PLAYER_BRIDGE_STATE_INVALID")
    for item in value["mods"]:
        if (not isinstance(item, dict) or not isinstance(item.get("workshopId"), str)
                or not item["workshopId"].isdigit() or not isinstance(item.get("enabled"), bool)
                or not isinstance(item.get("configurationOptions"), dict)):
            reject("PLAYER_BRIDGE_STATE_INVALID")
    return value["mods"]


def overrides_renderer(source):
    namespace = {"__name__": "mods_overrides"}
    exec(compile(source, "<mods-overrides>", "exec"), namespace)
    return namespace["modoverrides"]


def shards_for(manifest):
    return ("Master", "Caves") if manifest.get("cavesEnabled") is True else ("Master",)


def check_overrides(instance, mods, bridge_enabled, render, shards):
    expected = render(mods, bridge_enabled)
    for shard in shards:
        directory = instance / "config" / "Cluster_1" / shard
        if not safe_directory(directory, runtime=True):
            continue
        path = directory / "modoverrides.lua"
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BRIDGE_FILE
                or info.st_uid not in (0, os.geteuid(), 1000) or info.st_mode & 0o077):
            reject("PLAYER_BRIDGE_OVERRIDES_UNMANAGED")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            current = os.read(fd, MAX_BRIDGE_FILE + 1)
        finally:
            os.close(fd)
        if len(current) > MAX_BRIDGE_FILE or current not in (expected, render(mods, not bridge_enabled)):
            reject("PLAYER_BRIDGE_OVERRIDES_UNMANAGED")
    return expected


def load(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or not isinstance(request.get("requestId"), str)
            or not valid_uuid(request.get("serverId")) or not valid_uuid(request.get("hostId"))
            or not valid_uuid(request.get("instanceId"))):
        reject("PLAYER_BRIDGE_INPUT_INVALID")
    if not safe_directory(ROOT / "instances") or not safe_directory(ROOT / "instances" / request["instanceId"]):
        reject("MANIFEST_NOT_FOUND")
    instance = ROOT / "instances" / request["instanceId"]
    manifest = read_json(instance / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        reject("MANIFEST_NOT_FOUND")
    if manifest.get("hostId") != request["hostId"] or manifest.get("instanceId") != request["instanceId"]:
        reject("INSTANCE_IDENTITY_MISMATCH")
    revision = manifest.get("configRevision")
    if type(revision) is not int or revision < 0:
        reject("MANIFEST_NOT_FOUND")
    return instance, manifest


def layout_blockers(instance, manifest):
    active = read_json(instance / "active-release.json") or {}
    release_id = manifest.get("dstReleaseId") or active.get("dstReleaseId")
    if not valid_uuid(release_id):
        return ["PLAYER_BRIDGE_RELEASE_REQUIRED"]
    dst_root = ROOT / "dst" / release_id
    if not dst_root.is_dir() or dst_root.is_symlink():
        return ["PLAYER_BRIDGE_LAYOUT_UNSUPPORTED"]
    mods = dst_root / "mods"
    if mods.exists() and (not mods.is_dir() or mods.is_symlink()):
        return ["PLAYER_BRIDGE_LAYOUT_UNSUPPORTED"]
    return []


def describe(instance, manifest, render):
    mods = deployed_mods(instance)
    try:
        check_overrides(instance, mods, manifest.get("playerBridge", {}).get("enabled") is True,
                        render, shards_for(manifest))
        overrides_ok = True
    except BridgeRejected:
        overrides_ok = False
    bridge = manifest.get("playerBridge")
    enabled = isinstance(bridge, dict) and bridge.get("enabled") is True
    blockers = layout_blockers(instance, manifest)
    if not overrides_ok:
        blockers.append("PLAYER_BRIDGE_OVERRIDES_UNMANAGED")
    return {"enabled": enabled, "bridge": bridge if enabled else None,
            "blockers": blockers, "allowed": not blockers, "mods": mods}


def preview(request, renderer):
    instance, manifest = load(request)
    if request.get("expectedRevision") != manifest["configRevision"]:
        reject("REVISION_CONFLICT")
    described = describe(instance, manifest, renderer)
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": request["hostId"],
                       "instanceId": request["instanceId"]},
            "configRevision": manifest["configRevision"],
            "bridge": described["bridge"], "blockers": described["blockers"],
            "allowed": described["allowed"], "restartRequired": False,
            "runtimeStatus": "unknown",
            "shards": ["master"] + (["caves"] if manifest.get("cavesEnabled") is True else [])}


def apply(request, renderer):
    instance, manifest = load(request)
    expected_revision = request.get("expectedRevision")
    if type(expected_revision) is not int or expected_revision != manifest["configRevision"]:
        reject("REVISION_CONFLICT")
    version, digest, files = bridge_build(request.get("bridge"))
    mods = deployed_mods(instance)
    expected_overrides = check_overrides(instance, mods, True, renderer, shards_for(manifest))
    locks = instance / "locks"
    safe_directory(locks, create=True)
    lock_fd = os.open(locks / "player-bridge.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            reject("PLAYER_BRIDGE_BUSY")
        current = read_json(instance / "manifest.json")
        if current.get("configRevision") != expected_revision:
            reject("REVISION_CONFLICT")
        blockers = layout_blockers(instance, current)
        if blockers:
            reject(blockers[0])
        previous = current.get("playerBridge")
        bridge_root = instance / "bridge"
        safe_directory(bridge_root, create=True, runtime=True)
        os.chown(bridge_root, 1000, 1000)
        os.chmod(bridge_root, 0o700)
        for shard in shards_for(manifest):
            directory = bridge_root / shard
            safe_directory(directory, create=True, runtime=True)
            os.chown(directory, 1000, 1000)
            os.chmod(directory, 0o700)
            for name in ("modinfo.lua", "modmain.lua"):
                path = directory / name
                atomic_bytes(path, files[name], 0o644, runtime=True)
                os.chown(path, 0, 0)
                os.chmod(path, 0o644)
            config = instance / "config" / "Cluster_1" / shard
            safe_directory(config, create=True, runtime=True)
            os.chown(config, 1000, 1000)
            os.chmod(config, 0o700)
            atomic_bytes(config / "modoverrides.lua", expected_overrides, 0o600, runtime=True)
            os.chown(config / "modoverrides.lua", 1000, 1000)
            os.chmod(config / "modoverrides.lua", 0o600)
        installed_at = None
        if (isinstance(previous, dict) and previous.get("version") == version
                and previous.get("digest") == digest and isinstance(previous.get("installedAt"), str)):
            installed_at = previous["installedAt"]
        current["playerBridge"] = {"enabled": True, "version": version, "digest": digest,
                                   "installedAt": installed_at or now()}
        write_json(instance / "manifest.json", current)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    described = describe(instance, current, renderer)
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": request["hostId"],
                       "instanceId": request["instanceId"]},
            "configRevision": current["configRevision"],
            "bridge": current["playerBridge"], "blockers": described["blockers"],
            "allowed": described["allowed"],
            "restartRequired": False,
            "runtimeStatus": "unknown",
            "shards": ["master"] + (["caves"] if manifest.get("cavesEnabled") is True else [])}


def main(request, overrides_source):
    try:
        renderer = overrides_renderer(overrides_source)
        if request.get("action") == "preview":
            data = preview(request, renderer)
        elif request.get("action") == "apply":
            data = apply(request, renderer)
        else:
            reject("PLAYER_BRIDGE_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=True, separators=(",", ":")))
    except BridgeRejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"ok": False, "code": "PLAYER_BRIDGE_FAILED"}, separators=(",", ":")))
