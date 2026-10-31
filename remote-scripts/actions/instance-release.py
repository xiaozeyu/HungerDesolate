"""Validation and deterministic Compose rendering for a verified DST release."""
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath


class ReleaseRejected(Exception):
    pass


def reject(code):
    raise ReleaseRejected(code)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def safe_text(value, limit=128):
    return isinstance(value, str) and 0 < len(value) <= limit and all(ord(char) >= 32 for char in value)


def command(value):
    return (isinstance(value, list) and 1 <= len(value) <= 32
            and all(safe_text(item, 256) for item in value))


def validate_release(value):
    if not isinstance(value, dict):
        reject("CREATE_RELEASE_INVALID")
    required = {"schemaVersion", "releaseId", "platform", "image", "imageDigest",
                "dstBuild", "artifactDigest", "scriptVersion", "verified", "runtimeVerified", "commands"}
    if set(value) not in (required, required | {"createdAt"}):
        reject("CREATE_RELEASE_INVALID")
    if (value["schemaVersion"] != 1 or not valid_uuid(value["releaseId"])
            or value["platform"] != "linux/amd64" or not value["verified"]
            or type(value["runtimeVerified"]) is not bool
            or not safe_text(value["image"], 256)
            or re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", value["image"]) is None
            or not digest(value["imageDigest"]) or not safe_text(value["dstBuild"])
            or not digest(value["artifactDigest"]) or not safe_text(value["scriptVersion"])
            or not isinstance(value["commands"], dict)
            or set(value["commands"]) != {"master", "caves"}
            or not command(value["commands"]["master"])
            or not command(value["commands"]["caves"])
            or ("createdAt" in value and (not isinstance(value["createdAt"], str) or not value["createdAt"]))):
        reject("CREATE_RELEASE_INVALID")
    return value


def quote(value):
    if not safe_text(value, 512) or "'" in value:
        reject("CREATE_COMPOSE_INVALID")
    return "'" + value + "'"


def prepare_runtime_paths(instance_root, release_id, caves_enabled):
    """Grant UID 1000 access to bind mount roots, without traversing or changing saves."""
    if not valid_uuid(release_id) or type(caves_enabled) is not bool:
        reject("CREATE_PATH_INVALID")
    instance = Path(instance_root)
    if not instance.is_absolute() or not valid_uuid(instance.name) or instance.parent.name != "instances":
        reject("CREATE_PATH_INVALID")
    mods_release_id = release_id
    relative_roots = ["data", "mods", "mods/releases", "mods/releases/" + mods_release_id]
    for shard in ("Master", "Caves") if caves_enabled else ("Master",):
        relative_roots.extend(["data/" + shard, "mods/releases/" + mods_release_id + "/" + shard])
    for relative in [".", *relative_roots]:
        path = instance / relative
        if relative != ".":
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, 1000)
                    or info.st_mode & 0o022):
                reject("CREATE_PATH_INVALID")
            if relative.startswith("data/") or relative.endswith(("/Master", "/Caves")):
                os.fchown(fd, 1000, 1000)
        finally:
            os.close(fd)


def port_lines(manifest, shard):
    rows = []
    for item in manifest["ports"]:
        if item["shard"] != shard:
            continue
        if (item.get("protocol") != "udp" or item.get("bindAddress") != "0.0.0.0"
                or item.get("hostPort") != item.get("containerPort")):
            reject("CREATE_PORT_STATE_INVALID")
        rows.append("      - " + quote("0.0.0.0:%d:%d/udp" % (item["hostPort"], item["containerPort"])))
    return rows


def render_compose(manifest, release, instance_root):
    release = validate_release(release)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        reject("CREATE_MANIFEST_INVALID")
    instance_id = manifest.get("instanceId")
    if not valid_uuid(instance_id) or manifest.get("composeProject") != "fh_" + instance_id.replace("-", ""):
        reject("CREATE_MANIFEST_INVALID")
    root_path = PurePosixPath(instance_root) if isinstance(instance_root, str) else None
    if (root_path is None or not root_path.is_absolute() or str(root_path) != instance_root
            or ".." in root_path.parts or root_path.name != instance_id
            or root_path.parent.name != "instances"):
        reject("CREATE_PATH_INVALID")
    caves = manifest.get("cavesEnabled") is True
    mods_release_id = manifest.get("modsReleaseId") or release["releaseId"]
    if not valid_uuid(mods_release_id):
        reject("CREATE_MANIFEST_INVALID")
    services = ["master"] + (["caves"] if caves else [])
    lines = ["services:"]
    for shard in services:
        command_value = release["commands"][shard]
        if manifest.get("modsReleaseId"):
            command_value = command_value + ["-ugc_directory", "/srv/ugc"]
        lines.extend(["  " + shard + ":", "    image: " + quote(release["image"] + "@" + release["imageDigest"]),
                      "    init: true", "    restart: " + quote("unless-stopped" if manifest.get("autostart") else "no"),
                      "    command:"])
        lines.extend("      - " + quote(item) for item in command_value)
        lines.extend(["    working_dir: '/srv/dst/bin64'", "    volumes:",
                      "      - " + quote(instance_root + "/data/" + ("Caves" if shard == "caves" else "Master") + ":/srv/data:rw"),
                      "      - " + quote(instance_root + "/config:/srv/data/config:rw"),
                      "      - " + quote(instance_root + "/mods/releases/" + mods_release_id + "/" + ("Caves" if shard == "caves" else "Master") + ":/srv/ugc:rw")])
        if manifest.get("modsReleaseId"):
            lines.append("      - " + quote(instance_root + "/mods/releases/" + mods_release_id + "/dedicated_server_mods_setup.lua:/srv/dst/mods/dedicated_server_mods_setup.lua:ro"))
        ports = port_lines(manifest, shard)
        if ports:
            lines.append("    ports:")
            lines.extend(ports)
        lines.extend(["    networks:", "      - instance"])
        lines.extend(["    labels:", "      com.hungerdesolate.schemaVersion: '1'",
                      "      com.hungerdesolate.hostId: " + quote(manifest["hostId"]),
                      "      com.hungerdesolate.instanceId: " + quote(instance_id),
                      "      com.hungerdesolate.role: " + quote(shard)])
        if shard == "caves":
            lines.extend(["    depends_on:", "      - master"])
    lines.extend(["networks:", "  instance:", "    driver: bridge", "x-hunger-desolate:",
                  "  schemaVersion: 1", "  instanceId: " + quote(instance_id),
                  "  releaseId: " + quote(release["releaseId"]),
                  "  modsReleaseId: " + quote(mods_release_id)])
    return "\n".join(lines) + "\n"
