"""Read the player bridge state written by the server-only MOD.

The probe never scans directories and never returns player data for a shard
whose state is older than the stale window. Player names stay out of logs.
"""
import json
import os
from pathlib import Path
import re
import stat
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
MAX_JSON = 65536
MAX_STATE = 256 * 1024
MAX_PLAYERS = 128
FRESH_SECONDS = 30
STALE_SECONDS = 120
KLEI_HEADER = b"KLEI     1 "
STATE_KEY = "hungerhub_bridge_state"
SHARDS = ("Master", "Caves")
USER_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ProbeRejected(Exception):
    pass


def reject(code):
    raise ProbeRejected(code)


def now_unix():
    return int(datetime.now(timezone.utc).timestamp())


def captured_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def safe_directory(path, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in owners or info.st_mode & 0o022):
        reject("PLAYER_STATE_INVALID")
    return True


def read_regular(path, limit, runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    owners = (0, os.geteuid(), 1000) if runtime else (0, os.geteuid())
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in owners or (not runtime and info.st_mode & 0o077)):
        reject("PLAYER_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit):
            reject("PLAYER_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            return source.read(limit + 1)
    finally:
        os.close(fd)


def read_json(path, limit):
    raw = read_regular(path, limit)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        reject("PLAYER_STATE_INVALID")


def safe_text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and all(ord(char) >= 32 for char in value)


def parse_timestamp(value):
    if not isinstance(value, str) or len(value) > 32 or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp())


def load_manifest(instance):
    value = read_json(instance / "manifest.json", MAX_JSON)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        reject("MANIFEST_NOT_FOUND")
    return value


def read_state(instance, shard):
    directory = instance / "config" / "Cluster_1" / shard
    save = directory / "save"
    if not safe_directory(save, runtime=True):
        return None
    raw = read_regular(save / STATE_KEY, MAX_STATE, runtime=True)
    if raw is None or not raw.startswith(KLEI_HEADER):
        return None
    try:
        value = json.loads(raw[len(KLEI_HEADER):])
    except (ValueError, UnicodeDecodeError):
        reject("PLAYER_STATE_INVALID")
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1
            or value.get("shard") != shard or type(value.get("sequence")) is not int
            or value["sequence"] < 0 or not isinstance(value.get("players"), list)
            or len(value["players"]) > MAX_PLAYERS):
        reject("PLAYER_STATE_INVALID")
    stamp = parse_timestamp(value.get("capturedAt"))
    if stamp is None:
        reject("PLAYER_STATE_INVALID")
    players = []
    warnings = []
    for item in value["players"]:
        if not isinstance(item, dict):
            warnings.append("PLAYER_ENTRY_INVALID")
            continue
        user_id = item.get("userId")
        name = item.get("name")
        character = item.get("character")
        if (not isinstance(user_id, str) or USER_ID.fullmatch(user_id) is None
                or not isinstance(name, str) or not name or len(name) > 64
                or not isinstance(character, str) or len(character) > 64
                or any(ord(char) < 32 for char in name + character)):
            warnings.append("PLAYER_ENTRY_INVALID")
            continue
        players.append({"userId": user_id, "name": name, "character": character,
                        "shard": shard, "capturedAtUnixSeconds": stamp})
    return {"shard": shard, "capturedAtUnixSeconds": stamp, "players": players,
            "warnings": warnings}


def query(request):
    if (not isinstance(request, dict) or request.get("schemaVersion") != 1
            or not isinstance(request.get("requestId"), str)
            or not valid_uuid(request.get("serverId"))
            or not valid_uuid(request.get("hostId"))
            or not valid_uuid(request.get("instanceId"))):
        reject("PLAYER_INPUT_INVALID")
    target = {"serverId": request["serverId"], "hostId": request["hostId"],
              "instanceId": request["instanceId"]}
    instance = ROOT / "instances" / request["instanceId"]
    if not safe_directory(ROOT / "instances") or not safe_directory(instance):
        reject("MANIFEST_NOT_FOUND")
    if not safe_directory(instance / "config", runtime=True):
        reject("MANIFEST_NOT_FOUND")
    manifest = load_manifest(instance)
    if manifest.get("hostId") != request["hostId"] or manifest.get("instanceId") != request["instanceId"]:
        reject("INSTANCE_IDENTITY_MISMATCH")
    revision = manifest.get("configRevision")
    if type(revision) is not int or revision < 0:
        reject("MANIFEST_NOT_FOUND")
    bridge = manifest.get("playerBridge")
    if not isinstance(bridge, dict) or bridge.get("enabled") is not True:
        return {"schemaVersion": 1, "requestId": request["requestId"], "target": target,
                "snapshotId": str(uuid.uuid4()), "capturedAt": captured_at(),
                "source": "remote", "freshness": "disabled", "configRevision": revision,
                "data": {"bridge": None, "runtimeStatus": "unknown",
                         "onlineCount": 0, "players": [], "warnings": []}}
    if (not isinstance(bridge.get("version"), str) or not safe_text(bridge.get("version"), 32)
            or not isinstance(bridge.get("digest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", bridge["digest"]) is None):
        reject("PLAYER_STATE_INVALID")
    states = []
    invalid_shards = []
    for shard in SHARDS:
        if shard == "Caves" and manifest.get("cavesEnabled") is not True:
            continue
        directory = instance / "config" / "Cluster_1" / shard
        if not safe_directory(directory, runtime=True):
            continue
        try:
            state = read_state(instance, shard)
        except ProbeRejected:
            invalid_shards.append(shard)
            continue
        if state is not None:
            states.append(state)
    current = now_unix()
    usable = [state for state in states if current - state["capturedAtUnixSeconds"] <= STALE_SECONDS]
    if not states:
        freshness = "unavailable"
    elif any(current - state["capturedAtUnixSeconds"] <= FRESH_SECONDS for state in states):
        freshness = "fresh"
    elif usable:
        freshness = "stale"
    else:
        freshness = "unavailable"
    merged = {}
    warnings = ["PLAYER_STATE_INVALID"] if invalid_shards else []
    for state in sorted(usable, key=lambda item: item["capturedAtUnixSeconds"]):
        warnings.extend(state["warnings"])
        for player in state["players"]:
            merged[player["userId"]] = player
    players = sorted(merged.values(), key=lambda item: (item["name"], item["userId"]))
    if freshness == "unavailable":
        players = []
    return {"schemaVersion": 1, "requestId": request["requestId"], "target": target,
            "snapshotId": str(uuid.uuid4()), "capturedAt": captured_at(),
            "source": "remote", "freshness": freshness, "configRevision": revision,
            "data": {"bridge": {"enabled": True, "version": bridge["version"],
                                "digest": bridge["digest"],
                                "installedAt": bridge.get("installedAt")},
                     "runtimeStatus": "running" if freshness == "fresh" else "unknown",
                     "onlineCount": len(players), "players": players,
                     "warnings": sorted(set(warnings))}}


def main(request):
    try:
        value = query(request)
        print(json.dumps({"ok": True, "data": value}, separators=(",", ":")))
    except ProbeRejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print(json.dumps({"ok": False, "code": "PLAYER_QUERY_FAILED"}, separators=(",", ":")))


if __name__ == "__main__":
    import sys
    main(json.loads(sys.stdin.read()))
