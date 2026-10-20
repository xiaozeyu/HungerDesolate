"""Read and CAS-save desired DST world settings for one managed instance."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
MAX_BYTES = 1024 * 1024
MAX_REVISION = 9007199254740991
DST_RUNTIME_UID = 1000
OPTION_SCHEMA = globals().get("OPTION_SCHEMA", [])
GAME_MODES = {"relaxed", "survival", "endless", "wilderness", "lightsout"}


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


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def safe_directory(path, create=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o077):
        reject("WORLD_CONFIG_PATH_UNSAFE")
    return info


def read_bytes(path, limit=MAX_BYTES):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("WORLD_CONFIG_FILE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("WORLD_CONFIG_FILE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(limit + 1)
    finally:
        os.close(fd)
    if len(value) > limit:
        reject("WORLD_CONFIG_FILE_INVALID")
    return value


def read_json(path, limit=MAX_BYTES):
    try:
        return json.loads(read_bytes(path, limit))
    except (ValueError, TypeError):
        reject("WORLD_CONFIG_FILE_INVALID")


def atomic_bytes(path, data, mode=0o600):
    safe_directory(path.parent, create=True)
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        os.chmod(path, mode)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_json(path, value):
    atomic_bytes(path, canonical(value))


def default_shard(shard):
    return {"preset": "SURVIVAL_TOGETHER" if shard == "master" else "DST_CAVE",
            "worldgenOverrides": {}, "worldSettingsOverrides": {}}


def default_settings(caves_enabled, room_name=""):
    return {"roomName": room_name, "description": "", "gameMode": "survival", "maxPlayers": 6,
            "pvp": False, "language": "zh", "pauseWhenEmpty": True,
            "cavesEnabled": caves_enabled,
            "advanced": {"voteEnabled": True, "voteKickPercentage": 50,
                         "adminList": [], "whitelist": [], "whitelistSlots": 0, "blocklist": []},
            "worldgen": {"master": default_shard("master"), "caves": default_shard("caves")}}


def validate_string(value, limit, allow_empty=False):
    return (isinstance(value, str) and len(value) <= limit
            and (allow_empty or bool(value.strip()))
            and not any(ord(char) < 32 for char in value))


def validate_player_ids(value, maximum=128):
    return (isinstance(value, list) and len(value) <= maximum
            and all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item)
                    for item in value)
            and len(value) == len(set(value)))


def option_index():
    index = {}
    for item in OPTION_SCHEMA:
        if (not isinstance(item, dict) or item.get("category") not in ("worldgen", "worldSettings")
                or not isinstance(item.get("key"), str) or not isinstance(item.get("worlds"), list)):
            reject("WORLD_SCHEMA_INVALID")
        index[(item["category"], item["key"])] = item
    if not index:
        reject("WORLD_SCHEMA_INVALID")
    return index


def validate_shard(shard, shard_name, schema):
    if not isinstance(shard, dict) or set(shard) != {"preset", "worldgenOverrides", "worldSettingsOverrides"}:
        reject("WORLD_CONFIG_INVALID")
    preset = "SURVIVAL_TOGETHER" if shard_name == "master" else "DST_CAVE"
    if shard["preset"] != preset:
        reject("WORLD_CONFIG_INVALID")
    for category, field in (("worldgen", "worldgenOverrides"), ("worldSettings", "worldSettingsOverrides")):
        values = shard[field]
        if not isinstance(values, dict) or len(values) > 512:
            reject("WORLD_CONFIG_INVALID")
        for key, value in values.items():
            item = schema.get((category, key))
            if (item is None or shard_name not in item["worlds"] or not isinstance(value, str)
                    or len(value) > 128 or any(ord(char) < 32 for char in value)):
                reject("WORLD_OPTION_INVALID")
            allowed = item.get("shardValues", {}).get(shard_name, item["values"])
            if value not in [option.get("value") for option in allowed]:
                reject("WORLD_OPTION_INVALID")


def validate_settings(settings, manifest, schema):
    expected = {"roomName", "description", "gameMode", "maxPlayers", "pvp", "language",
                "pauseWhenEmpty", "cavesEnabled", "advanced", "worldgen"}
    if not isinstance(settings, dict) or set(settings) != expected:
        reject("WORLD_CONFIG_INVALID")
    if (not validate_string(settings["roomName"], 64)
            or not validate_string(settings["description"], 256, allow_empty=True)
            or settings["gameMode"] not in GAME_MODES
            or type(settings["maxPlayers"]) is not int or not 1 <= settings["maxPlayers"] <= 128
            or type(settings["pvp"]) is not bool or settings["language"] not in ("zh", "en")
            or type(settings["pauseWhenEmpty"]) is not bool
            or type(settings["cavesEnabled"]) is not bool
            or settings["cavesEnabled"] != manifest.get("cavesEnabled")):
        reject("WORLD_CONFIG_INVALID")
    advanced = settings["advanced"]
    expected_advanced = {"voteEnabled", "voteKickPercentage", "adminList", "whitelist", "whitelistSlots", "blocklist"}
    if (not isinstance(advanced, dict) or set(advanced) != expected_advanced
            or type(advanced["voteEnabled"]) is not bool
            or type(advanced["voteKickPercentage"]) is not int
            or not 0 <= advanced["voteKickPercentage"] <= 100
            or type(advanced["whitelistSlots"]) is not int
            or not 0 <= advanced["whitelistSlots"] <= settings["maxPlayers"]
            or not validate_player_ids(advanced["adminList"])
            or not validate_player_ids(advanced["whitelist"])
            or not validate_player_ids(advanced["blocklist"])):
        reject("WORLD_CONFIG_INVALID")
    if advanced["whitelistSlots"] and not advanced["whitelist"]:
        reject("WORLD_CONFIG_INVALID")
    worldgen = settings["worldgen"]
    if not isinstance(worldgen, dict) or set(worldgen) != {"master", "caves"}:
        reject("WORLD_CONFIG_INVALID")
    validate_shard(worldgen["master"], "master", schema)
    validate_shard(worldgen["caves"], "caves", schema)


def secret_change(value):
    if value is None or value == {"action": "keep"}:
        return None
    if (not isinstance(value, dict) or set(value) != {"action", "value"}
            or value.get("action") != "replace" or not validate_string(value.get("value"), 8192)):
        reject("WORLD_SECRET_INPUT_INVALID")
    return value["value"]


def verify_identity(request):
    host_id, instance_id = request.get("hostId"), request.get("instanceId")
    if not valid_uuid(host_id) or not valid_uuid(instance_id):
        reject("WORLD_TARGET_INVALID")
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    safe_directory(ROOT)
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json")
    if (host.get("schemaVersion") != 1 or host.get("hostId") != host_id
            or registry.get("schemaVersion") != 1 or registry.get("hostId") != host_id
            or type(registry.get("registryRevision")) is not int
            or not isinstance(registry.get("instances"), list)):
        reject("HOST_IDENTITY_MISMATCH")
    matches = [entry for entry in registry["instances"]
               if isinstance(entry, dict) and entry.get("instanceId") == instance_id]
    if len(matches) != 1 or matches[0].get("lifecycle") not in ("ready", "creating"):
        reject("INSTANCE_NOT_FOUND")
    instance = ROOT / "instances" / instance_id
    safe_directory(instance)
    manifest = read_json(instance / "manifest.json", 65536)
    if manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id:
        reject("INSTANCE_IDENTITY_MISMATCH")
    if type(manifest.get("configRevision")) is not int or not 0 <= manifest["configRevision"] <= MAX_REVISION:
        reject("WORLD_CONFIG_REVISION_INVALID")
    return host_id, instance_id, manifest


def lock_instance(instance_id):
    locks = ROOT / "instances" / instance_id / "locks"
    safe_directory(locks)
    path = locks / "world-config.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        os.close(fd)
        reject("WORLD_CONFIG_PATH_UNSAFE")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def transaction_paths(instance_id):
    directory = ROOT / "instances" / instance_id
    return directory, directory / "desired" / "world.json", directory / "desired-secrets", directory / "tasks" / "world-config-pending.json"


def finish_pending_transaction(instance_id):
    directory, desired_path, _, journal_path = transaction_paths(instance_id)
    if not journal_path.exists():
        return
    transaction = read_json(journal_path, 262144)
    if (transaction.get("schemaVersion") != 1 or transaction.get("instanceId") != instance_id
            or not isinstance(transaction.get("desired"), dict)
            or not isinstance(transaction.get("manifest"), dict)
            or not isinstance(transaction.get("receipt"), dict)):
        reject("WORLD_CONFIG_TRANSACTION_INVALID")
    manifest = transaction["manifest"]
    desired = transaction["desired"]
    receipt = transaction["receipt"]
    if (manifest.get("instanceId") != instance_id or not valid_uuid(manifest.get("hostId"))
            or desired.get("instanceId") != instance_id
            or desired.get("revision") != manifest.get("configRevision")
            or type(desired.get("revision")) is not int
            or receipt.get("revision") != desired.get("revision")
            or not valid_uuid(receipt.get("operationId"))
            or not isinstance(receipt.get("result"), dict)):
        reject("WORLD_CONFIG_TRANSACTION_INVALID")
    validate_settings(desired.get("settings"), manifest, option_index())
    if not isinstance(desired.get("secrets"), dict):
        reject("WORLD_CONFIG_TRANSACTION_INVALID")
    for key in ("tokenRef", "serverPasswordRef"):
        if desired["secrets"].get(key) is not None and not valid_uuid(desired["secrets"][key]):
            reject("WORLD_CONFIG_TRANSACTION_INVALID")
    atomic_json(desired_path, transaction["desired"])
    atomic_json(directory / "manifest.json", transaction["manifest"])
    receipt_path = directory / "operations" / (transaction["receipt"]["operationId"] + ".json")
    atomic_json(receipt_path, transaction["receipt"])
    journal_path.unlink()


def secret_exists(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("WORLD_SECRET_FILE_INVALID")
    return True


def load_desired(instance_id, manifest, schema):
    _, desired_path, secrets_dir, _ = transaction_paths(instance_id)
    if desired_path.exists():
        desired = read_json(desired_path, 262144)
        if (desired.get("schemaVersion") != 1 or desired.get("instanceId") != instance_id
                or desired.get("revision") != manifest["configRevision"]
                or not isinstance(desired.get("settings"), dict)
                or not isinstance(desired.get("secrets"), dict)):
            reject("WORLD_CONFIG_STATE_INVALID")
    else:
        if manifest.get("configRevision") != 0:
            reject("WORLD_CONFIG_STATE_INVALID")
        desired = {"schemaVersion": 1, "instanceId": instance_id,
                   "revision": manifest["configRevision"],
                   "settings": default_settings(manifest.get("cavesEnabled") is True,
                                                manifest.get("displayName", "")),
                   "secrets": {"tokenRef": None, "serverPasswordRef": None}}
    validate_settings(desired["settings"], manifest, schema)
    token_ref = desired["secrets"].get("tokenRef")
    password_ref = desired["secrets"].get("serverPasswordRef")
    if ((token_ref is not None and not valid_uuid(token_ref))
            or (password_ref is not None and not valid_uuid(password_ref))):
        reject("WORLD_CONFIG_STATE_INVALID")
    token_present = token_ref is not None and secret_exists(secrets_dir / (token_ref + ".token"))
    password_present = password_ref is not None and secret_exists(secrets_dir / (password_ref + ".password"))
    if (token_ref is not None and not token_present) or (password_ref is not None and not password_present):
        reject("WORLD_SECRET_FILE_INVALID")
    return desired, token_present, password_present


def config_response(request, manifest, desired, token_present, password_present):
    settings = desired["settings"]
    return {"schemaVersion": 1, "requestId": request["requestId"],
            "target": {"serverId": request["serverId"], "hostId": request["hostId"],
                       "instanceId": request["instanceId"]},
            "snapshotId": str(uuid.uuid4()), "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "remote", "freshness": "fresh", "configRevision": manifest["configRevision"],
            "data": {"revision": manifest["configRevision"], "desired": settings,
                     "deployed": None, "desiredDigest": digest(settings), "deployedDigest": None,
                     "tokenStatus": "configured" if token_present else "missing",
                     "serverPasswordStatus": "configured" if password_present else "missing",
                     "validationWarnings": []}}


def get_config(request):
    host_id, instance_id, manifest = verify_identity(request)
    lock_fd = lock_instance(instance_id)
    try:
        finish_pending_transaction(instance_id)
        manifest = read_json(ROOT / "instances" / instance_id / "manifest.json", 65536)
        if manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id:
            reject("INSTANCE_IDENTITY_MISMATCH")
        desired, token_present, password_present = load_desired(instance_id, manifest, option_index())
        return config_response(request, manifest, desired, token_present, password_present)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def save_config(request):
    host_id, instance_id, manifest = verify_identity(request)
    expected_revision = request.get("expectedRevision")
    operation_id = request.get("operationId")
    request_id = request.get("requestId")
    if (type(expected_revision) is not int or expected_revision < 0 or expected_revision > MAX_REVISION
            or not valid_uuid(operation_id) or not valid_uuid(request_id)):
        reject("WORLD_CONFIG_INPUT_INVALID")
    schema = option_index()
    validate_settings(request.get("settings"), manifest, schema)
    token_value = secret_change(request.get("tokenChange"))
    password_value = secret_change(request.get("serverPasswordChange"))
    settings = request["settings"]
    settings_digest = digest(settings)
    lock_fd = lock_instance(instance_id)
    try:
        finish_pending_transaction(instance_id)
        manifest = read_json(ROOT / "instances" / instance_id / "manifest.json", 65536)
        directory, desired_path, secrets_dir, _ = transaction_paths(instance_id)
        operations_dir = directory / "operations"
        safe_directory(operations_dir, create=True)
        receipt_path = operations_dir / (operation_id + ".json")
        if receipt_path.exists():
            receipt = read_json(receipt_path, 262144)
            if (receipt.get("settingsDigest") != settings_digest
                    or receipt.get("tokenChanged") is not (token_value is not None)
                    or receipt.get("serverPasswordChanged") is not (password_value is not None)):
                reject("OPERATION_ID_REUSED")
            for secret_value, secret_ref_key, extension in (
                (token_value, "tokenRef", "token"),
                (password_value, "serverPasswordRef", "password"),
            ):
                if secret_value is None:
                    continue
                secret_ref = receipt.get(secret_ref_key)
                if not valid_uuid(secret_ref):
                    reject("WORLD_CONFIG_STATE_INVALID")
                saved_secret = read_bytes(secrets_dir / (secret_ref + "." + extension), 8192)
                if saved_secret != (secret_value + "\n").encode():
                    reject("OPERATION_ID_REUSED")
            saved_result = receipt.get("result")
            if not isinstance(saved_result, dict):
                reject("WORLD_CONFIG_STATE_INVALID")
            replay = dict(saved_result)
            replay["requestId"] = request_id
            return replay
        if expected_revision != manifest["configRevision"]:
            reject("REVISION_CONFLICT")
        if manifest["configRevision"] >= MAX_REVISION:
            reject("WORLD_CONFIG_REVISION_EXHAUSTED")
        prior, _, _ = load_desired(instance_id, manifest, schema)
        refs = dict(prior["secrets"])
        safe_directory(secrets_dir, create=True)
        for key, secret_value, extension in (
            ("tokenRef", token_value, "token"),
            ("serverPasswordRef", password_value, "password"),
        ):
            if secret_value is not None:
                secret_ref = str(uuid.uuid4())
                atomic_bytes(secrets_dir / (secret_ref + "." + extension),
                             (secret_value + "\n").encode(), 0o600)
                refs[key] = secret_ref
        new_revision = manifest["configRevision"] + 1
        desired = {"schemaVersion": 1, "instanceId": instance_id, "revision": new_revision,
                   "settings": settings, "secrets": refs}
        new_manifest = dict(manifest)
        new_manifest["configRevision"] = new_revision
        transaction = {"schemaVersion": 1, "instanceId": instance_id,
                       "desired": desired, "manifest": new_manifest,
                       "receipt": {"operationId": operation_id, "settingsDigest": settings_digest,
                                   "tokenChanged": token_value is not None,
                                   "serverPasswordChanged": password_value is not None,
                                   "tokenRef": refs.get("tokenRef"),
                                   "serverPasswordRef": refs.get("serverPasswordRef"),
                                   "revision": new_revision}}
        _, _, _, journal_path = transaction_paths(instance_id)
        token_present = refs.get("tokenRef") is not None
        password_present = refs.get("serverPasswordRef") is not None
        data = {"revision": new_revision, "desired": settings, "deployed": None,
                "desiredDigest": digest(settings), "deployedDigest": None,
                "tokenStatus": "configured" if token_present else "missing",
                "serverPasswordStatus": "configured" if password_present else "missing",
                "validationWarnings": []}
        result = {"schemaVersion": 1, "requestId": request_id,
                  "target": {"serverId": request["serverId"], "hostId": host_id, "instanceId": instance_id},
                  "operationId": operation_id, "verifiedRevision": new_revision, "data": data,
                  "invalidate": [{"hostId": host_id, "instanceId": instance_id, "domains": ["world", "overview"]}]}
        transaction["receipt"]["result"] = result
        atomic_json(journal_path, transaction)
        finish_pending_transaction(instance_id)
        return result
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def preview_apply(request):
    host_id, instance_id, manifest = verify_identity(request)
    expected_revision = request.get("expectedRevision")
    if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
        reject("WORLD_CONFIG_INPUT_INVALID")
    schema = option_index()
    lock_fd = lock_instance(instance_id)
    try:
        finish_pending_transaction(instance_id)
        manifest = read_json(ROOT / "instances" / instance_id / "manifest.json", 65536)
        if manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id:
            reject("INSTANCE_IDENTITY_MISMATCH")
        if manifest["configRevision"] != expected_revision:
            reject("REVISION_CONFLICT")
        desired, token_present, password_present = load_desired(instance_id, manifest, schema)
        renderer = globals().get("render_config")
        if not callable(renderer):
            reject("WORLD_CONFIG_RENDERER_UNAVAILABLE")
        try:
            rendered = renderer(desired["settings"], b"preview-token", b"", "0" * 32,
                                manifest.get("ports", []))
        except (ValueError, TypeError, KeyError, AttributeError):
            rendered = None
        data_root = ROOT / "instances" / instance_id / "data"
        world_files = []
        scanned_entries = 0
        try:
            for shard in ("Master", "Caves") if manifest.get("cavesEnabled") else ("Master",):
                shard_root = data_root / shard
                if not shard_root.exists():
                    continue
                for directory in (data_root, shard_root):
                    info = directory.lstat()
                    if (not stat.S_ISDIR(info.st_mode)
                            or info.st_uid not in (0, DST_RUNTIME_UID)
                            or info.st_mode & 0o022):
                        reject("WORLD_CONFIG_PATH_UNSAFE")
                for child in shard_root.rglob("*"):
                    scanned_entries += 1
                    if scanned_entries > 20_000:
                        reject("WORLD_CONFIG_DATA_LIMIT")
                    info = child.lstat()
                    if (stat.S_ISLNK(info.st_mode)
                            or info.st_uid not in (0, DST_RUNTIME_UID)
                            or info.st_mode & 0o022
                            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                        reject("WORLD_CONFIG_PATH_UNSAFE")
                    if stat.S_ISREG(info.st_mode):
                        world_files.append(shard + "/" + child.relative_to(shard_root).as_posix())
                        if len(world_files) > 20000:
                            reject("WORLD_CONFIG_DATA_LIMIT")
        except PermissionError:
            reject("WORLD_CONFIG_PATH_UNSAFE")
        except OSError:
            reject("WORLD_CONFIG_DATA_READ_FAILED")
        new_world_options = []
        for shard_name, shard_config in desired["settings"]["worldgen"].items():
            for option_key in shard_config["worldgenOverrides"]:
                option = schema.get(("worldgen", option_key))
                if option is not None:
                    new_world_options.append({"shard": shard_name, "key": option_key, "label": option["label"]})
        blockers = ["WORLD_APPLY_NOT_IMPLEMENTED"]
        if rendered is None:
            blockers.append("WORLD_CONFIG_RENDER_UNSUPPORTED")
        if world_files:
            blockers.append("PROTECTION_BACKUP_REQUIRED")
        if not token_present:
            blockers.append("WORLD_TOKEN_MISSING")
        return {"schemaVersion":1,"requestId":request["requestId"],
                "target":{"serverId":request["serverId"],"hostId":host_id,"instanceId":instance_id},
                "recordVersion":request["expectedRecordVersion"],"revision":manifest["configRevision"],
                "desiredDigest":digest(desired["settings"]),"worldDataPresent":bool(world_files),
                "worldDataFileCount":len(world_files),"newWorldOptions":new_world_options,
                "tokenStatus":"configured" if token_present else "missing",
                "serverPasswordStatus":"configured" if password_present else "missing",
                "backupRequirement":"required" if world_files else "none",
                "warnings":[],"blockers":blockers,"allowed":False}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(request):
    try:
        action = request.get("action")
        if request.get("schemaVersion") != 1:
            reject("SCHEMA_UNSUPPORTED")
        if action == "get":
            data = get_config(request)
        elif action == "save":
            data = save_config(request)
        elif action == "previewApply":
            data = preview_apply(request)
        else:
            reject("WORLD_CONFIG_ACTION_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        failure = {"ok": False, "code": "WORLD_CONFIG_FAILED",
                   "failureType": type(error).__name__}
        if isinstance(error, KeyError) and error.args and isinstance(error.args[0], str):
            key = error.args[0]
            if len(key) <= 64 and key.isascii() and key.replace("_", "a").isalnum():
                failure["failureKey"] = key
        print(json.dumps(failure, separators=(",", ":")), flush=True)
        raise SystemExit(1)
