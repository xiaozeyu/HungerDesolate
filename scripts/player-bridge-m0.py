#!/usr/bin/env python3
"""M0 installer and read-only state validator for the player bridge.

This script is a validation tool, not a production remote action. It runs on a
Linux amd64 host as root, installs the bridge into one test instance, and reads
the TheSim state key without touching the rest of the app.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
INSTANCES = ROOT / "instances"
BRIDGE_VERSION = "1.0.0"
STATE_LIMIT = 256 * 1024
MAX_PLAYERS = 128
MAX_TEXT = 64
SHARDS = ("Master", "Caves")
KLEI_HEADER = b"KLEI     1 "
STATE_KEY = "hungerhub_bridge_state"
COMMAND_KEY = "hungerhub_bridge_command"
RESULT_KEY = "hungerhub_bridge_result"
PROBE_KEY = "hungerhub_bridge_probe"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BRIDGE_OVERRIDE_LINE = '  ["hungerhub_bridge"]={enabled=true}'
BRIDGE_OVERRIDE_KEY = '["hungerhub_bridge"]'
COMPOSE_MOUNT = "/srv/dst/mods/hungerhub_bridge:rw"
M0_MARKER = ".m0-install.json"


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise Rejected(code)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def owners():
    return (0, os.geteuid(), 1000)


def safe_dir(path, create=False, mode=0o700):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            reject("BRIDGE_PATH_MISSING")
        path.mkdir(mode=mode)
        info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in owners() or info.st_mode & 0o022):
        reject("BRIDGE_PATH_UNSAFE")
    return info


def read_regular(path, limit, allowed_owners=None):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    allowed = owners() if allowed_owners is None else allowed_owners
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_nlink != 1 or info.st_size > limit
            or (allowed is not None and info.st_uid not in allowed)
            or info.st_mode & 0o022):
        reject("BRIDGE_FILE_UNSAFE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        reject("BRIDGE_FILE_UNREADABLE")
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit):
            reject("BRIDGE_FILE_UNSAFE")
        chunks = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        reject("BRIDGE_FILE_TOO_LARGE")
    return data


def atomic_write(path, data, mode=0o644, uid=None):
    path = Path(path)
    safe_dir(path.parent, create=True)
    try:
        target = path.lstat()
    except FileNotFoundError:
        target = None
    if target is not None and (stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode)):
        reject("BRIDGE_FILE_UNSAFE")
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(descriptor)
        os.chmod(temporary, mode)
        if uid is not None and os.geteuid() == 0:
            os.chown(temporary, uid, uid)
        os.replace(temporary, path)
        try:
            parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            parent_fd = None
        if parent_fd is not None:
            try:
                os.fsync(parent_fd)
            except OSError:
                pass
            finally:
                os.close(parent_fd)
    finally:
        os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def decode_json(data, limit, code):
    if len(data) > limit:
        reject(code)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        reject(code)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        reject(code)


def text_value(value, maximum, allow_empty, code):
    if not isinstance(value, str):
        reject(code)
    if not allow_empty and value == "":
        reject(code)
    if len(value) > maximum:
        reject(code)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        reject(code)
    return value


def valid_timestamp(value):
    return isinstance(value, str) and TIMESTAMP_RE.fullmatch(value) is not None


def player_entry(item):
    if not isinstance(item, dict):
        reject("PLAYER_INVALID")
    user_id = item.get("userId")
    if not isinstance(user_id, str) or ID_RE.fullmatch(user_id) is None:
        reject("PLAYER_INVALID")
    name = text_value(item.get("name", ""), MAX_TEXT, True, "PLAYER_INVALID")
    character = text_value(item.get("character", ""), MAX_TEXT, True, "PLAYER_INVALID")
    return {"userId": user_id, "name": name, "character": character}


def validate_state(data):
    value = decode_json(data, STATE_LIMIT, "STATE_INVALID")
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        reject("STATE_INVALID")
    shard = value.get("shard")
    if shard not in SHARDS:
        reject("STATE_INVALID")
    sequence = value.get("sequence")
    if type(sequence) is not int or sequence < 0:
        reject("STATE_INVALID")
    captured_at = value.get("capturedAt")
    if not valid_timestamp(captured_at):
        reject("STATE_INVALID")
    try:
        datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        reject("STATE_INVALID")
    players = value.get("players")
    if not isinstance(players, list) or len(players) > MAX_PLAYERS:
        reject("STATE_INVALID")
    normalized = []
    seen = set()
    skipped = 0
    for item in players:
        try:
            entry = player_entry(item)
        except Rejected:
            skipped += 1
            continue
        if entry["userId"] in seen:
            skipped += 1
            continue
        seen.add(entry["userId"])
        normalized.append(entry)
    warnings = []
    if skipped:
        warnings.append("PLAYER_ENTRY_INVALID")
    return {
        "schemaVersion": 1,
        "shard": shard,
        "sequence": sequence,
        "capturedAt": captured_at,
        "players": normalized,
        "warnings": warnings,
        "diag": value.get("diag") if isinstance(value.get("diag"), dict) else None,
    }


def save_dir(instance, shard):
    return instance / "config" / "Cluster_1" / shard / "save"


def key_path(instance, shard, key):
    return save_dir(instance, shard) / key


def read_key_file(path, limit):
    data = read_regular(path, limit + len(KLEI_HEADER))
    if data is None:
        return None
    if not data.startswith(KLEI_HEADER):
        reject("KEY_FILE_INVALID")
    return data[len(KLEI_HEADER):]


def write_key_file(path, payload, mode=0o644):
    atomic_write(path, KLEI_HEADER + payload, mode=mode, uid=1000)


def remove_key(instance, shard, key):
    path = key_path(instance, shard, key)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        reject("BRIDGE_FILE_UNSAFE")
    path.unlink()


def freshness(captured_at, now=None):
    try:
        captured = datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "invalid"
    current = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    age = (current - captured).total_seconds()
    if age < -5:
        return "invalid"
    if age <= 30:
        return "fresh"
    if age <= 120:
        return "stale"
    return "unavailable"


def bridge_digest(source_dir):
    # sha256("modinfo.lua\0" + bytes + "modmain.lua\0" + bytes); production must match.
    source = Path(source_dir)
    digest = hashlib.sha256()
    for name in ("modinfo.lua", "modmain.lua"):
        path = source / name
        try:
            data = path.read_bytes()
        except OSError:
            reject("BRIDGE_SOURCE_INVALID")
        if len(data) > 256 * 1024:
            reject("BRIDGE_SOURCE_INVALID")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            reject("BRIDGE_SOURCE_INVALID")
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
    return "sha256:" + digest.hexdigest()


def validate_bridge_source(source_dir):
    source = Path(source_dir)
    try:
        modinfo = (source / "modinfo.lua").read_text(encoding="utf-8")
        modmain = (source / "modmain.lua").read_text(encoding="utf-8")
    except OSError:
        reject("BRIDGE_SOURCE_INVALID")
    except UnicodeDecodeError:
        reject("BRIDGE_SOURCE_INVALID")
    if "server_only_mod = true" not in modinfo or "all_clients_require_mod = false" not in modinfo:
        reject("BRIDGE_SOURCE_INVALID")
    for marker in ("AllPlayers", "SetPersistentString", "STATE_KEY", "pcall"):
        if marker not in modmain:
            reject("BRIDGE_SOURCE_INVALID")
    for forbidden in ("io.popen", "os.execute", "loadstring"):
        if forbidden in modmain:
            reject("BRIDGE_SOURCE_INVALID")


def load_manifest(instance):
    data = read_regular(instance / "manifest.json", 65536)
    if data is None:
        reject("MANIFEST_NOT_FOUND")
    manifest = decode_json(data, 65536, "MANIFEST_INVALID")
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        reject("MANIFEST_INVALID")
    if manifest.get("instanceId") != instance.name:
        reject("INSTANCE_IDENTITY_MISMATCH")
    if not isinstance(manifest.get("composeProject"), str):
        reject("MANIFEST_INVALID")
    return manifest


def enabled_shards(manifest):
    shards = ["Master"]
    if manifest.get("cavesEnabled") is True:
        shards.append("Caves")
    return shards


def instance_path(value):
    instance = Path(value).resolve()
    if instance.parent != INSTANCES.resolve() or UUID_RE.fullmatch(instance.name) is None:
        reject("INSTANCE_PATH_INVALID")
    safe_dir(INSTANCES)
    safe_dir(instance)
    return instance


def insert_bridge_mounts(compose_text, instance, shard_names):
    lines = compose_text.splitlines()
    mounts = {}
    for shard in shard_names:
        shard_name = "Master" if shard == "master" else "Caves"
        mount = "      - '" + str(instance) + "/bridge/" + shard_name + ":" + COMPOSE_MOUNT + "'"
        mounts[shard] = (mount, any(line.strip() == mount.strip() for line in lines))
    output = []
    current = None
    inserted = set()
    service_re = re.compile(r"^  (master|caves):\s*$")
    for line in lines:
        match = service_re.match(line)
        if match:
            current = match.group(1)
        output.append(line)
        if current in shard_names and "/srv/data:rw" in line and current not in inserted:
            mount, present = mounts[current]
            if not present:
                output.append(mount)
            inserted.add(current)
    if inserted != set(shard_names):
        reject("COMPOSE_UNSUPPORTED")
    return "\n".join(output) + "\n"


def append_bridge_override(text):
    if text is None:
        return "return {\n" + BRIDGE_OVERRIDE_LINE + "\n}\n", True
    if BRIDGE_OVERRIDE_KEY in text:
        return text, False
    if not text.startswith("return {\n") or not text.endswith("}\n"):
        reject("MODOVERRIDES_UNSUPPORTED")
    body = text[len("return {\n"):-len("}\n")]
    if body.strip() == "":
        return "return {\n" + BRIDGE_OVERRIDE_LINE + "\n}\n", True
    return "return {\n" + BRIDGE_OVERRIDE_LINE + ",\n" + body + "}\n", True


def overrides_have_user_mods(text):
    keys = re.findall(r'\["([^"]+)"\]', text)
    return any(key != "hungerhub_bridge" for key in keys)


def install(instance_value, source_value, allow_existing_mods=False, dry_run=False):
    instance = instance_path(instance_value)
    source = Path(source_value)
    manifest = load_manifest(instance)
    shards = enabled_shards(manifest)
    lower_shards = {"master" if shard == "Master" else "caves" for shard in shards}
    compose_data = read_regular(instance / "compose.yaml", 65536)
    if compose_data is None:
        reject("COMPOSE_NOT_FOUND")
    try:
        compose_text = compose_data.decode("utf-8")
    except UnicodeDecodeError:
        reject("COMPOSE_INVALID")
    compose_m0 = insert_bridge_mounts(compose_text, instance, lower_shards)
    validate_bridge_source(source)
    digest = bridge_digest(source)
    if dry_run:
        return {
            "schemaVersion": 1,
            "instanceId": instance.name,
            "composeProject": manifest["composeProject"],
            "digest": digest,
            "shards": shards,
            "compose": compose_m0,
        }
    if (instance / "bridge" / M0_MARKER).exists():
        reject("M0_ALREADY_INSTALLED")

    plan = {}
    safe_dir(instance / "config")
    for shard in shards:
        config = instance / "config" / "Cluster_1" / shard
        safe_dir(config.parent, create=True)
        safe_dir(config, create=True)
        if os.geteuid() == 0:
            os.chown(config.parent, 1000, 1000)
            os.chown(config, 1000, 1000)
        overrides_path = config / "modoverrides.lua"
        existing = read_regular(overrides_path, 65536)
        if existing is not None:
            try:
                existing_text = existing.decode("utf-8")
            except UnicodeDecodeError:
                reject("MODOVERRIDES_UNSUPPORTED")
            backup = config / "modoverrides.lua.m0-backup"
            if BRIDGE_OVERRIDE_KEY in existing_text and not backup.exists():
                reject("M0_ALREADY_INSTALLED")
            if overrides_have_user_mods(existing_text) and not allow_existing_mods:
                reject("MODOVERRIDES_EXISTING_MODS")
        plan[shard] = (config, existing)

    for shard in shards:
        bridge = instance / "bridge" / shard
        safe_dir(instance / "bridge", create=True)
        safe_dir(bridge, create=True)
        if os.geteuid() == 0:
            for path in (instance / "bridge", bridge):
                os.chown(path, 1000, 1000)
        for name in ("modinfo.lua", "modmain.lua"):
            atomic_write(bridge / name, (source / name).read_bytes(), mode=0o644, uid=0)
        for legacy in (bridge / "commands", bridge / "results"):
            if legacy.exists():
                safe_dir(legacy)
                shutil.rmtree(legacy)
        save = save_dir(instance, shard)
        if save.exists():
            safe_dir(save)
            for key in (STATE_KEY, COMMAND_KEY, RESULT_KEY, PROBE_KEY):
                remove_key(instance, shard, key)

    marker_shards = {}
    for shard in shards:
        config, existing = plan[shard]
        overrides_path = config / "modoverrides.lua"
        backup = None
        if existing is not None:
            backup = config / "modoverrides.lua.m0-backup"
            if not backup.exists():
                atomic_write(backup, existing, mode=0o600, uid=0)
            try:
                text = existing.decode("utf-8")
            except UnicodeDecodeError:
                reject("MODOVERRIDES_UNSUPPORTED")
        else:
            text = None
        next_text, created = append_bridge_override(text)
        atomic_write(overrides_path, next_text.encode("utf-8"), mode=0o600, uid=1000)
        marker_shards[shard] = {
            "backup": str(backup) if backup is not None else None,
            "created": created,
        }

    atomic_write(instance / "compose.m0.yaml", compose_m0.encode("utf-8"), mode=0o644, uid=0)
    marker = {
        "schemaVersion": 1,
        "bridgeVersion": BRIDGE_VERSION,
        "digest": digest,
        "installedAt": now_iso(),
        "instanceId": instance.name,
        "composeProject": manifest["composeProject"],
        "shards": marker_shards,
    }
    atomic_write(
        instance / "bridge" / M0_MARKER,
        json.dumps(marker, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        mode=0o600,
        uid=0,
    )
    return marker


def load_marker(instance):
    data = read_regular(instance / "bridge" / M0_MARKER, 65536)
    if data is None:
        reject("M0_MARKER_MISSING")
    marker = decode_json(data, 65536, "M0_MARKER_INVALID")
    if (not isinstance(marker, dict) or marker.get("schemaVersion") != 1
            or marker.get("instanceId") != instance.name or not isinstance(marker.get("shards"), dict)):
        reject("M0_MARKER_INVALID")
    return marker


def uninstall(instance_value):
    instance = instance_path(instance_value)
    marker = load_marker(instance)
    for shard, info in marker["shards"].items():
        if shard not in SHARDS or not isinstance(info, dict):
            reject("M0_MARKER_INVALID")
        config = instance / "config" / "Cluster_1" / shard
        overrides_path = config / "modoverrides.lua"
        backup_value = info.get("backup")
        if backup_value:
            backup = Path(backup_value)
            data = read_regular(backup, 65536)
            if data is None:
                reject("M0_BACKUP_MISSING")
            atomic_write(overrides_path, data, mode=0o600, uid=1000)
            backup.unlink()
        elif info.get("created") is True:
            try:
                overrides_path.unlink()
            except FileNotFoundError:
                pass
        else:
            reject("M0_MARKER_INVALID")
        save = save_dir(instance, shard)
        if save.exists():
            safe_dir(save)
            for key in (STATE_KEY, COMMAND_KEY, RESULT_KEY, PROBE_KEY):
                remove_key(instance, shard, key)
    bridge = instance / "bridge"
    if bridge.exists():
        safe_dir(bridge)
        shutil.rmtree(bridge)
    compose_m0 = instance / "compose.m0.yaml"
    try:
        info = compose_m0.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            reject("BRIDGE_FILE_UNSAFE")
        compose_m0.unlink()
    return marker


def status(instance_value):
    instance = instance_path(instance_value)
    manifest = load_manifest(instance)
    result = []
    for shard in enabled_shards(manifest):
        save = save_dir(instance, shard)
        try:
            safe_dir(save)
        except Rejected:
            result.append({"shard": shard, "freshness": "unavailable", "players": 0, "warnings": []})
            continue
        try:
            data = read_key_file(key_path(instance, shard, STATE_KEY), STATE_LIMIT)
        except Rejected as error:
            result.append({"shard": shard, "freshness": "invalid", "players": 0,
                           "warnings": [error.code]})
            continue
        if data is None:
            result.append({"shard": shard, "freshness": "unavailable", "players": 0, "warnings": []})
            continue
        try:
            state = validate_state(data)
        except Rejected as error:
            result.append({"shard": shard, "freshness": "invalid", "players": 0,
                           "warnings": [error.code]})
            continue
        result.append({
            "shard": shard,
            "sequence": state["sequence"],
            "capturedAt": state["capturedAt"],
            "freshness": freshness(state["capturedAt"]),
            "players": len(state["players"]),
            "warnings": state["warnings"],
            "entries": state["players"],
            "diag": state.get("diag") if isinstance(state.get("diag"), dict) else None,
        })
    return result


def compose_command(project, compose_path):
    return "docker compose -p " + project + " -f " + str(compose_path) + " up -d --force-recreate"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Player bridge M0 helper")
    sub = parser.add_subparsers(dest="command", required=True)

    install_parser = sub.add_parser("install", help="install the bridge into one test instance")
    install_parser.add_argument("--instance", required=True)
    install_parser.add_argument("--source", required=True)
    install_parser.add_argument("--allow-existing-mods", action="store_true")
    install_parser.add_argument("--dry-run", action="store_true")

    status_parser = sub.add_parser("status", help="read TheSim key state")
    status_parser.add_argument("--instance", required=True)
    status_parser.add_argument("--names", action="store_true")

    uninstall_parser = sub.add_parser("uninstall", help="restore the M0 changes")
    uninstall_parser.add_argument("--instance", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install(args.instance, args.source, args.allow_existing_mods, args.dry_run)
            if args.dry_run:
                print(result["compose"])
                print("digest: " + result["digest"])
                return 0
            instance = Path(args.instance).resolve()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("start: " + compose_command(result["composeProject"], instance / "compose.m0.yaml"))
            print("cleanup: " + compose_command(result["composeProject"], instance / "compose.yaml"))
            print("warning: this recreates shards; do not use the app's start or MOD apply during M0")
            return 0
        if args.command == "status":
            entries = status(args.instance)
            for entry in entries:
                line = "{} freshness={} players={}".format(
                    entry["shard"], entry["freshness"], entry["players"])
                if "sequence" in entry:
                    line += " sequence={} capturedAt={}".format(entry["sequence"], entry["capturedAt"])
                if entry.get("warnings"):
                    line += " warnings=" + ",".join(entry["warnings"])
                print(line)
                if entry.get("diag"):
                    print("  diag=" + json.dumps(entry["diag"], ensure_ascii=False, separators=(",", ":")))
                if args.names:
                    for player in entry.get("entries", []):
                        print("  name={} character={}".format(player["name"], player["character"]))
            return 0
        if args.command == "uninstall":
            marker = uninstall(args.instance)
            instance = Path(args.instance).resolve()
            print(json.dumps(marker, ensure_ascii=False, indent=2))
            print("cleanup: " + compose_command(marker["composeProject"], instance / "compose.yaml"))
            return 0
        return 2
    except Rejected as error:
        print("ERROR: " + error.code, file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("ERROR: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
