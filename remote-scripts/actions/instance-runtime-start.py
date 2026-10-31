"""Start one managed DST instance and verify shard processes and UDP listeners."""
import configparser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time
import uuid
from datetime import datetime, timezone

ROOT = Path("/var/lib/famine-hub")
SERVER_BINARY = "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64"
MAX_BYTES = 1024 * 1024
WAIT_SECONDS = 120
RUNTIME_UID = 1000
CURRENT_STAGE = "initialize"
RELEASE_HELPERS = globals().get("RELEASE_HELPERS", {})
OBSERVATION_DEADLINE = None


class Rejected(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code = code
        self.details = details if isinstance(details, dict) else {}


def reject(code):
    raise Rejected(code, {"failureStage": CURRENT_STAGE})


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def read_json(path, limit=MAX_BYTES):
    try:
        info = path.lstat()
    except OSError:
        reject("INSTANCE_START_STATE_INVALID")
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
        reject("INSTANCE_START_STATE_INVALID")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        reject("INSTANCE_START_STATE_INVALID")
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("INSTANCE_START_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
    except (OSError, ValueError, TypeError):
        reject("INSTANCE_START_STATE_INVALID")
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("INSTANCE_START_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("INSTANCE_START_STATE_INVALID")


def safe_directory(path, allow_runtime=False):
    try:
        info = path.lstat()
    except OSError:
        reject("INSTANCE_START_STATE_INVALID")
    owners = (0, os.geteuid(), RUNTIME_UID) if allow_runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or info.st_uid not in owners or info.st_mode & 0o022):
        reject("INSTANCE_START_STATE_INVALID")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def docker(arguments, timeout=30, capture=True):
    if OBSERVATION_DEADLINE is not None:
        remaining = OBSERVATION_DEADLINE - time.monotonic()
        if remaining <= 0:
            reject("INSTANCE_RUNTIME_READ_TIMEOUT")
        timeout = min(timeout, remaining)
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(["docker", *arguments], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, text=True, errors="replace",
                                timeout=timeout, check=False, env=environment)
    except FileNotFoundError:
        reject("INSTANCE_START_DOCKER_UNAVAILABLE")
    except subprocess.TimeoutExpired:
        reject("INSTANCE_START_TIMEOUT")
    if capture and len(result.stdout) > 65536:
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    return result.stdout if result.returncode == 0 else None


def compose(instance, project, arguments, timeout=30):
    output = docker(["compose", "-p", project, "-f", str(instance / "compose.yaml"), *arguments], timeout)
    if output is None:
        reject("INSTANCE_START_COMPOSE_FAILED")
    return output


def repair_runtime_layout(instance, manifest, release, project, shards):
    """Migrate only the exact generated legacy Compose; reject unrelated manual edits."""
    global CURRENT_STAGE
    CURRENT_STAGE = "repairRuntimeLayout"
    if not callable(RELEASE_HELPERS.get("render_compose")) or not callable(RELEASE_HELPERS.get("prepare_runtime_paths")):
        reject("INSTANCE_START_STATE_INVALID")
    try:
        desired = RELEASE_HELPERS["render_compose"](manifest, release, str(instance))
        previous = (instance / "compose.yaml").read_text(encoding="utf-8")
    except (OSError, ValueError, RELEASE_HELPERS["ReleaseRejected"]):
        reject("INSTANCE_START_STATE_INVALID")
    variants = {desired, desired.replace("    working_dir: '/srv/dst/bin64'", "    working_dir: '/srv'")}
    if manifest.get("modsReleaseId"):
        legacy_manifest = dict(manifest)
        legacy_manifest.pop("modsReleaseId", None)
        try:
            legacy_compose = RELEASE_HELPERS["render_compose"](legacy_manifest, release, str(instance))
            variants.add(legacy_compose)
            variants.add(legacy_compose.replace("    working_dir: '/srv/dst/bin64'", "    working_dir: '/srv'"))
        except (ValueError, RELEASE_HELPERS["ReleaseRejected"]):
            pass
    if previous not in variants:
        reject("INSTANCE_START_COMPOSE_DRIFT")
    if previous != desired:
        # Preserve the original file before replacing the generated working directory.
        backup = instance / "compose.before-runtime-fix.yaml"
        if not backup.exists():
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(previous.encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
        compose(instance, project, ["stop", *shards], timeout=60)
        temporary = instance / (".compose." + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(desired.encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, instance / "compose.yaml")
        finally:
            temporary.unlink(missing_ok=True)
    try:
        RELEASE_HELPERS["prepare_runtime_paths"](
            str(instance), manifest.get("modsReleaseId") or release["releaseId"], manifest["cavesEnabled"])
    except (OSError, RELEASE_HELPERS["ReleaseRejected"]):
        reject("INSTANCE_START_RUNTIME_PATH_INVALID")
    if previous != desired:
        compose(instance, project, ["create", "--force-recreate", *shards], timeout=60)


def shard_ports(config_root, shard, caves_enabled=False):
    path = config_root / shard.title() / "server.ini"
    safe_directory(path.parent, allow_runtime=True)
    try:
        info = path.lstat()
    except OSError:
        reject("INSTANCE_START_CONFIG_INVALID")
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid not in (0, os.geteuid(), RUNTIME_UID) or info.st_mode & 0o077
            or info.st_size > 16384):
        reject("INSTANCE_START_CONFIG_INVALID")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        ports = {"game": parser.getint("NETWORK", "server_port"),
                 "query": parser.getint("STEAM", "master_server_port")}
        if shard == "master" and caves_enabled:
            cluster_path = config_root / "cluster.ini"
            info = cluster_path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid not in (0, os.geteuid(), RUNTIME_UID)
                    or info.st_mode & 0o077 or info.st_size > 16384):
                reject("INSTANCE_START_CONFIG_INVALID")
            cluster = configparser.ConfigParser(interpolation=None)
            cluster.read(cluster_path, encoding="utf-8")
            if not cluster.getboolean("SHARD", "shard_enabled"):
                reject("INSTANCE_START_CONFIG_INVALID")
            ports["shard"] = cluster.getint("SHARD", "master_port")
    except (OSError, configparser.Error, UnicodeError, ValueError):
        reject("INSTANCE_START_CONFIG_INVALID")
    if any(port < 1 or port > 65535 for port in ports.values()) or len(set(ports.values())) != len(ports):
        reject("INSTANCE_START_CONFIG_INVALID")
    return ports


def verify_container(instance, project, host_id, instance_id, shard):
    ids = compose(instance, project, ["ps", "--all", "--quiet", shard]).splitlines()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        reject("INSTANCE_START_CONTAINER_MISSING")
    labels = docker(["inspect", "--format", "{{json .Config.Labels}}", ids[0]])
    if labels is None:
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    try:
        labels = json.loads(labels)
    except ValueError:
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    expected = {"com.hungerdesolate.hostId": host_id,
                "com.hungerdesolate.instanceId": instance_id,
                "com.hungerdesolate.role": shard}
    if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in expected.items()):
        reject("INSTANCE_START_RESOURCE_IDENTITY_INVALID")
    return ids[0]


def inspect_running(container_id):
    value = docker(["inspect", "--format", "{{.State.Running}}", container_id])
    if value is None or value.strip() not in ("true", "false"):
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    return value.strip() == "true"


def a2s_player_count(port):
    """Return the connected-player count from a local Steam A2S endpoint, or None."""
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.8)
    try:
        sock.sendto(b"\xff\xff\xff\xffU\xff\xff\xff\xff", ("127.0.0.1", port))
        data, _ = sock.recvfrom(8192)
        if len(data) >= 5 and data[:4] == b"\xff\xff\xff\xff":
            if data[4:5] == b"A":
                if len(data) >= 9:
                    sock.sendto(b"\xff\xff\xff\xffU" + data[5:9], ("127.0.0.1", port))
                    data, _ = sock.recvfrom(8192)
            if (len(data) >= 6 and data[:4] == b"\xff\xff\xff\xff"
                    and data[4:5] == b"D"):
                count = data[5]
                # Each player entry is at least 10 bytes; reject a truncated response.
                if len(data) >= 6 + count * 10:
                    return count
        # Some DST builds reject A2S_PLAYER but still expose the player count in A2S_INFO.
        sock.sendto(b"\xff\xff\xff\xffTSource Engine Query\x00", ("127.0.0.1", port))
        data, _ = sock.recvfrom(8192)
        if len(data) >= 5 and data[:4] == b"\xff\xff\xff\xff" and data[4:5] == b"A":
            if len(data) >= 9:
                sock.sendto(b"\xff\xff\xff\xffTSource Engine Query\x00" + data[5:9], ("127.0.0.1", port))
                data, _ = sock.recvfrom(8192)
        if len(data) < 7 or data[:4] != b"\xff\xff\xff\xff" or data[4:5] != b"I":
            return None
        offset = 6
        # Protocol byte is at offset 5; then name, map, folder and game strings.
        for _ in range(4):
            end = data.find(b"\x00", offset)
            if end < 0 or end + 1 > len(data):
                return None
            offset = end + 1
        if offset + 4 > len(data):
            return None
        return data[offset + 2]
    except (OSError, ValueError):
        return None
    finally:
        sock.close()


def runtime_running(container_id, shard):
    # Project only non-sensitive state; .State.Error and container logs may contain secrets.
    projection = ('{"status":{{json .State.Status}},"exitCode":{{json .State.ExitCode}},'
                  '"restartCount":{{json .RestartCount}},"oomKilled":{{json .State.OOMKilled}}}')
    output = docker(["inspect", "--format", projection, container_id])
    try:
        state = json.loads(output) if output is not None else None
    except ValueError:
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    if (not isinstance(state, dict)
            or state.get("status") not in ("created", "running", "paused", "restarting", "removing", "exited", "dead")
            or type(state.get("exitCode")) is not int or not 0 <= state["exitCode"] <= 255
            or type(state.get("restartCount")) is not int or not 0 <= state["restartCount"] <= 9007199254740991
            or type(state.get("oomKilled")) is not bool):
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    if state["status"] in ("restarting", "exited", "dead"):
        raise Rejected("INSTANCE_START_CONTAINER_EXITED", {
            "failureStage": CURRENT_STAGE, "shard": shard,
            "containerStatus": state["status"], "exitCode": state["exitCode"],
            "restartCount": state["restartCount"], "oomKilled": state["oomKilled"],
        })
    return state["status"] == "running"


def process_ready(container_id):
    # Compare executable paths inside the container; Docker top may fail on the host.
    probe = ("for p in /proc/[0-9]*/exe; do x=$(readlink \"$p\" 2>/dev/null) || continue; "
             "if [ \"$x\" = " + SERVER_BINARY + " ]; then printf DST_RUNNING; exit 0; fi; "
             "done; printf DST_NOT_FOUND")
    output = docker(["exec", container_id, "/bin/sh", "-c", probe])
    if output is None:
        return None
    if output.strip() not in ("DST_RUNNING", "DST_NOT_FOUND"):
        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
    return output.strip() == "DST_RUNNING"


def udp_ports(container_id):
    output = docker(["exec", container_id, "/bin/sh", "-c",
                     "cat /proc/net/udp && { if [ -r /proc/net/udp6 ]; then cat /proc/net/udp6; fi; }"])
    if output is None:
        return None
    observed = set()
    for line in output.splitlines():
        fields = line.split()
        if not fields or fields[0] == "sl":
            continue
        if (len(fields) < 2 or re.fullmatch(r"[0-9]+:", fields[0]) is None
                or re.fullmatch(r"[0-9A-Fa-f]{8,32}:[0-9A-Fa-f]{4}", fields[1]) is None):
            reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
        observed.add(int(fields[1].rsplit(":", 1)[1], 16))
    return observed


def start(request):
    global CURRENT_STAGE
    CURRENT_STAGE = "validateRequest"
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    action = request.get("action", "start")
    if (request.get("schemaVersion") != 1 or action not in ("start", "stop", "restart")
            or not valid_uuid(request.get("requestId"))
            or not valid_uuid(request.get("operationId")) or not valid_uuid(request.get("hostId"))
            or not valid_uuid(request.get("instanceId"))
            or type(request.get("expectedRevision")) is not int):
        reject("INSTANCE_START_INPUT_INVALID")
    host_id, instance_id = request["hostId"], request["instanceId"]
    CURRENT_STAGE = "readHostState"
    safe_directory(ROOT)
    safe_directory(ROOT / "instances")
    safe_directory(ROOT / "releases")
    host = read_json(ROOT / "host.json", 65536)
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    instance = ROOT / "instances" / instance_id
    safe_directory(instance)
    safe_directory(instance / "locks")
    lock_path = instance / "locks" / "world-config.lock"
    CURRENT_STAGE = "acquireInstanceLock"
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(lock_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
            os.close(lock_fd)
            reject("INSTANCE_START_LOCK_FAILED")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except OSError:
        reject("INSTANCE_START_LOCK_FAILED")
    try:
        if action == "stop":
            return _stop_locked(request, host_id, instance_id, instance)
        return _start_locked(request, host_id, instance_id, instance)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _locked_manifest(request, host_id, instance_id, instance):
    global CURRENT_STAGE
    CURRENT_STAGE = "validateManifest"
    manifest = read_json(instance / "manifest.json", 65536)
    if manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id:
        reject("INSTANCE_IDENTITY_MISMATCH")
    revision = manifest.get("configRevision")
    project = "fh_" + instance_id.replace("-", "")
    if (revision != request["expectedRevision"] or manifest.get("composeProject") != project
            or type(manifest.get("cavesEnabled")) is not bool):
        reject("REVISION_CONFLICT")
    compose_path = instance / "compose.yaml"
    try:
        compose_info = compose_path.lstat()
    except OSError:
        reject("WORLD_DEPLOYMENT_MISSING")
    if (not stat.S_ISREG(compose_info.st_mode) or compose_info.st_nlink != 1
            or compose_info.st_uid not in (0, os.geteuid()) or compose_info.st_mode & 0o022
            or compose_info.st_size > 65536):
        reject("WORLD_DEPLOYMENT_MISSING")
    return manifest, revision, project, ["master"] + (["caves"] if manifest["cavesEnabled"] else [])


def _stop_locked(request, host_id, instance_id, instance):
    global CURRENT_STAGE
    manifest, revision, project, shards = _locked_manifest(request, host_id, instance_id, instance)
    CURRENT_STAGE = "validateContainers"
    containers = {shard: verify_container(instance, project, host_id, instance_id, shard) for shard in shards}
    CURRENT_STAGE = "stopServices"
    compose(instance, project, ["stop", *shards], timeout=60)
    for shard, container in containers.items():
        if inspect_running(container):
            reject("INSTANCE_STOP_RUNTIME_NOT_STOPPED")
    return {"schemaVersion": 1, "requestId": request["requestId"], "operationId": request["operationId"],
            "hostId": host_id, "instanceId": instance_id, "revision": revision,
            "status": "stopped", "verificationLevel": "unverified", "startedShards": [],
            "runningShards": [], "listeners": {}, "publicConnectivityVerified": False}


def _start_locked(request, host_id, instance_id, instance):
    global CURRENT_STAGE
    safe_directory(instance / "config", allow_runtime=True)
    safe_directory(instance / "config" / "Cluster_1", allow_runtime=True)
    manifest, revision, project, shards = _locked_manifest(request, host_id, instance_id, instance)
    CURRENT_STAGE = "validateAppliedConfig"
    safe_directory(instance / "desired")
    desired = read_json(instance / "desired" / "world.json", 262144)
    deployed = read_json(instance / "deployed-world.json", 262144)
    settings = desired.get("settings") if isinstance(desired, dict) else None
    if (desired.get("revision") != deployed.get("revision")
            or deployed.get("digest") != "sha256:" + hashlib.sha256(canonical(settings)).hexdigest()
            or desired.get("settings") != deployed.get("settings")):
        reject("INSTANCE_START_CONFIG_NOT_APPLIED")
    CURRENT_STAGE = "validateActiveRelease"
    active = read_json(instance / "active-release.json", 65536)
    release_id = active.get("dstReleaseId") if isinstance(active, dict) else None
    if not valid_uuid(release_id):
        reject("WORLD_RELEASE_MISSING")
    catalog = read_json(ROOT / "releases" / "catalog.json")
    releases = [item for item in catalog.get("releases", []) if isinstance(item, dict)
                and item.get("releaseId") == release_id and item.get("verified") is True]
    if catalog.get("hostId") != host_id or len(releases) != 1:
        reject("WORLD_RELEASE_INVALID")
    release = dict(releases[0], schemaVersion=1)
    CURRENT_STAGE = "validateToken"
    config_root = instance / "config" / "Cluster_1"
    token = config_root / "cluster_token.txt"
    try:
        token_info = token.lstat()
        token_fd = os.open(token, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        reject("WORLD_TOKEN_MISSING")
    try:
        token_opened = os.fstat(token_fd)
        if (token_opened.st_dev, token_opened.st_ino) != (token_info.st_dev, token_info.st_ino):
            reject("WORLD_TOKEN_MISSING")
        with os.fdopen(token_fd, "rb", closefd=False) as stream:
            token_bytes = stream.read(8193)
    except OSError:
        reject("WORLD_TOKEN_MISSING")
    finally:
        os.close(token_fd)
    if (not stat.S_ISREG(token_info.st_mode) or token_info.st_nlink != 1
            or token_info.st_uid not in (0, os.geteuid(), RUNTIME_UID) or token_info.st_mode & 0o077
            or not token_bytes or len(token_bytes) > 8192
            or any(byte < 0x20 or byte > 0x7e for byte in token_bytes)):
        reject("WORLD_TOKEN_MISSING")
    CURRENT_STAGE = "validateShardConfig"
    port_plan = {shard: shard_ports(config_root, shard, manifest["cavesEnabled"]) for shard in shards}
    CURRENT_STAGE = "validateContainers"
    containers = {shard: verify_container(instance, project, host_id, instance_id, shard) for shard in shards}
    before = {shard for shard, container in containers.items() if inspect_running(container)}
    if request.get("action", "start") == "restart" and before != set(shards):
        reject("INSTANCE_RESTART_NOT_RUNNING")
    repair_runtime_layout(instance, manifest, release, project, shards)
    containers = {shard: verify_container(instance, project, host_id, instance_id, shard) for shard in shards}
    action = request.get("action", "start")
    CURRENT_STAGE = "restartServices" if action == "restart" else "startServices"
    compose(instance, project, ["restart", *shards] if action == "restart"
            else ["start", *shards], timeout=60)
    CURRENT_STAGE = "waitForRuntime"
    deadline = time.monotonic() + WAIT_SECONDS
    ready = set()
    observed_listeners = {shard: [] for shard in shards}
    observations = {}
    while time.monotonic() < deadline:
        ready.clear()
        for shard, container in containers.items():
            if not runtime_running(container, shard):
                continue
            running = process_ready(container)
            ports = udp_ports(container)
            observations[shard] = {"processRunning": running, "ports": ports}
            if running is True and ports is not None and set(port_plan[shard].values()).issubset(ports):
                ready.add(shard)
                observed_listeners[shard] = sorted(port_plan[shard].values())
        if ready == set(shards):
            return {"schemaVersion": 1, "requestId": request["requestId"],
                    "operationId": request["operationId"], "hostId": host_id,
                    "instanceId": instance_id, "revision": revision,
                    "status": "running", "verificationLevel": "localVerified",
                    "startedShards": sorted(ready if request.get("action") == "restart" else set(shards) - before),
                    "runningShards": sorted(ready), "listeners": observed_listeners,
                    "publicConnectivityVerified": False}
        time.sleep(2)
    details = {"failureStage": CURRENT_STAGE}
    for shard in shards:
        observation = observations.get(shard, {})
        ports = observation.get("ports")
        details[shard + "ProcessRunning"] = observation.get("processRunning")
        if ports is not None:
            details[shard + "MissingPorts"] = sorted(set(port_plan[shard].values()) - ports)
            details[shard + "ObservedPorts"] = sorted(ports)[:64]
    raise Rejected("INSTANCE_START_RUNTIME_NOT_READY", details)


def runtime_snapshot(host_id, instance_id, manifest, containers, releases=None):
    """Read-only evidence; never read cached runtime.json or write instance files."""
    instance = ROOT / "instances" / instance_id
    captured = int(time.time())
    result = {"hostId": host_id, "instanceId": instance_id, "capturedAtUnixSeconds": captured,
              "capturedAt": datetime.fromtimestamp(captured, timezone.utc).isoformat().replace("+00:00", "Z"),
              "configRevision": manifest.get("configRevision", 0) if manifest else 0,
              "status": "unknown", "verificationLevel": "unverified", "installedBuild": None,
              "buildSource": "unknown", "releaseId": None, "maxPlayers": None, "players": None,
              "gameMode": None, "pvp": None, "desiredVsDeployed": "unknown",
              "shards": [], "ports": [], "warnings": [], "publicConnectivityVerified": False}
    if manifest is None:
        result["warnings"].append("MANIFEST_MISSING")
        return result
    active_path = instance / "active-release.json"
    if active_path.exists():
        active = read_json(active_path, 65536)
        if valid_uuid(active.get("dstReleaseId")):
            result["releaseId"] = active["dstReleaseId"]
    deployed_path = instance / "deployed-world.json"
    if deployed_path.exists():
        deployed = read_json(deployed_path, 262144)
        settings = deployed.get("settings", {})
        if (deployed.get("instanceId") != instance_id or not isinstance(settings, dict)
                or deployed.get("digest") != "sha256:" + hashlib.sha256(canonical(settings)).hexdigest()):
            reject("INSTANCE_START_STATE_INVALID")
        result["maxPlayers"] = settings.get("maxPlayers")
        result["gameMode"] = settings.get("gameMode")
        result["pvp"] = settings.get("pvp")
        desired_path = instance / "desired" / "world.json"
        if desired_path.exists():
            desired = read_json(desired_path, 262144)
            desired_settings = desired.get("settings", {})
            desired_digest = "sha256:" + hashlib.sha256(canonical(desired_settings)).hexdigest()
            if (desired.get("schemaVersion") != 1 or desired.get("instanceId") != instance_id
                    or type(desired.get("revision")) is not int
                    or desired.get("revision") > manifest.get("configRevision", 0)
                    or not isinstance(desired_settings, dict)):
                reject("INSTANCE_START_STATE_INVALID")
            result["desiredVsDeployed"] = "inSync" if desired_digest == deployed.get("digest") else "pending"
    roles = ["master"] + (["caves"] if manifest["cavesEnabled"] else [])
    port_plan = manifest.get("ports", [])
    if not isinstance(port_plan, list) or len(port_plan) != (6 if manifest["cavesEnabled"] else 3):
        reject("INSTANCE_START_STATE_INVALID")
    for item in port_plan:
        if (not isinstance(item, dict) or item.get("shard") not in roles
                or item.get("purpose") not in ("game", "steamMaster", "steamAuth")
                or item.get("protocol") != "udp"
                or type(item.get("hostPort")) is not int or not 1 <= item["hostPort"] <= 65535
                or item.get("containerPort") != item["hostPort"] or item.get("bindAddress") != "0.0.0.0"):
            reject("INSTANCE_START_CONFIG_INVALID")
        result["ports"].append({"shard": item["shard"], "purpose": item["purpose"],
            "protocol": "udp", "containerPort": item["containerPort"], "hostPort": item["hostPort"],
            "bindAddress": item["bindAddress"], "published": None, "listening": None,
            "required": item["purpose"] != "steamAuth"})
    config_root = instance / "config" / "Cluster_1"
    required = {}
    try:
        safe_directory(instance / "config", allow_runtime=True)
        safe_directory(config_root, allow_runtime=True)
        for role in roles:
            required[role] = shard_ports(config_root, role, manifest["cavesEnabled"])
        if "shard" in required.get("master", {}):
            result["ports"].append({"shard": "master", "purpose": "shard", "protocol": "udp",
                "containerPort": required["master"]["shard"], "hostPort": None, "bindAddress": None,
                "published": False, "listening": None, "required": True})
    except (Rejected, OSError):
        result["warnings"].append("WORLD_CONFIG_UNAVAILABLE")
        required = {}
    if containers is None:
        result["warnings"].append("DOCKER_UNAVAILABLE")
    seen = set()
    builds = set()
    metadata_builds = set()
    release = [item for item in releases or [] if isinstance(item, dict)
               and item.get("releaseId") == result["releaseId"] and item.get("verified") is True]
    release = release[0] if len(release) == 1 else None
    for container in containers or []:
        labels = container.get("labels")
        if not isinstance(labels, dict) or labels.get("com.hungerdesolate.instanceId") != instance_id:
            continue
        role = labels.get("com.hungerdesolate.role")
        if (labels.get("com.hungerdesolate.hostId") != host_id
                or labels.get("com.hungerdesolate.schemaVersion") != "1"
                or labels.get("com.docker.compose.project") != manifest["composeProject"]
                or role not in roles or role in seen):
            result["warnings"].append("RESOURCE_IDENTITY_INVALID")
            continue
        seen.add(role)
        if release is not None:
            if container.get("imageId") != release.get("imageDigest"):
                result["warnings"].append("BUILD_MISMATCH")
            elif isinstance(release.get("dstBuild"), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", release["dstBuild"]):
                metadata_builds.add(release["dstBuild"])
        state = container.get("status")
        if state not in ("created", "running", "paused", "restarting", "removing", "exited", "dead"):
            reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
        evidence = {"shard": role, "containerStatus": state, "processRunning": None,
                    "ready": False, "installedBuild": None, "missingPorts": []}
        observed = None
        if state == "running":
            try:
                evidence["processRunning"] = process_ready(container["id"])
                observed = udp_ports(container["id"])
                build = docker(["exec", container["id"], "cat", "/srv/dst/version.txt"])
                if build is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", build.strip()):
                    evidence["installedBuild"] = build.strip()
                    builds.add(build.strip())
            except Rejected:
                result["warnings"].append("RUNTIME_PROBE_UNAVAILABLE")
        bindings = container.get("bindings")
        if not isinstance(bindings, dict):
            reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
        public_ready = True
        for port in result["ports"]:
            if port["shard"] != role:
                continue
            if port["hostPort"] is not None:
                entries = bindings.get(str(port["containerPort"]) + "/udp", []) or []
                if not isinstance(entries, list):
                    reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
                port["published"] = any(isinstance(entry, dict) and entry.get("HostPort") == str(port["hostPort"])
                    and entry.get("HostIp") == port["bindAddress"] for entry in entries)
                if port["required"] and not port["published"]:
                    public_ready = False
            if observed is not None:
                port["listening"] = port["containerPort"] in observed
            elif state in ("created", "exited", "dead"):
                port["listening"] = False
        if role in required and observed is not None:
            evidence["missingPorts"] = sorted(set(required[role].values()) - observed)
            evidence["ready"] = evidence["processRunning"] is True and not evidence["missingPorts"] and public_ready
        if state == "running" and (evidence["processRunning"] is None or observed is None):
            result["warnings"].append("RUNTIME_PROBE_UNAVAILABLE")
        result["shards"].append(evidence)
    if containers is not None and seen != set(roles):
        result["warnings"].append("CONTAINERS_MISSING")
    if len(builds) == 1:
        result["installedBuild"] = next(iter(builds))
        result["buildSource"] = "containerFile"
    elif len(builds) > 1:
        result["warnings"].append("BUILD_MISMATCH")
    elif len(metadata_builds) == 1 and len(seen) == len(roles):
        result["installedBuild"] = next(iter(metadata_builds))
        result["buildSource"] = "releaseMetadata"
    if builds and metadata_builds and builds != metadata_builds:
        result["warnings"].append("BUILD_MISMATCH")
    shards = result["shards"]
    if (len(shards) == len(roles) and all(shard["ready"] for shard in shards)
            and not set(result["warnings"]) & {"RESOURCE_IDENTITY_INVALID", "BUILD_MISMATCH"}):
        result["status"] = "running"
        result["verificationLevel"] = "localVerified"
    elif len(shards) == len(roles) and all(shard["containerStatus"] in ("created", "exited") for shard in shards):
        result["status"] = "stopped"
    elif any(shard["containerStatus"] in ("restarting", "dead") for shard in shards):
        result["status"] = "degraded" if any(shard["ready"] for shard in shards) else "failed"
    elif any(shard["ready"] for shard in shards):
        result["status"] = "degraded"
    elif shards and all(shard["containerStatus"] == "running" for shard in shards) and "RUNTIME_PROBE_UNAVAILABLE" not in result["warnings"]:
        result["status"] = "starting"
    if result["verificationLevel"] != "localVerified" and any(shard["processRunning"] is True for shard in shards):
        result["verificationLevel"] = "processOnly"
    if result["status"] == "running":
        counts = [a2s_player_count(required.get(role, {}).get("query")) for role in roles]
        observed = [count for count in counts if count is not None]
        if observed:
            result["players"] = sum(observed)
    result["warnings"] = sorted(set(result["warnings"]))
    return result


def inspect_instances(request):
    global OBSERVATION_DEADLINE
    ids = request.get("instanceIds")
    host_id = request.get("hostId")
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or not valid_uuid(host_id) or not isinstance(ids, list) or len(ids) > 200
            or len(set(ids)) != len(ids) or any(not valid_uuid(item) for item in ids)):
        reject("INSTANCE_START_INPUT_INVALID")
    safe_directory(ROOT)
    safe_directory(ROOT / "instances")
    host = read_json(ROOT / "host.json", 65536)
    registry = read_json(ROOT / "registry.json")
    if host.get("hostId") != host_id or registry.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    if registry.get("registryRevision") != request.get("expectedRegistryRevision"):
        reject("REVISION_CONFLICT")
    entries = registry.get("instances")
    if not isinstance(entries, list):
        reject("INSTANCE_START_STATE_INVALID")
    OBSERVATION_DEADLINE = time.monotonic() + 20
    try:
        releases = []
        catalog_path = ROOT / "releases" / "catalog.json"
        if catalog_path.exists():
            catalog = read_json(catalog_path)
            if catalog.get("hostId") != host_id or not isinstance(catalog.get("releases"), list):
                reject("HOST_IDENTITY_MISMATCH")
            releases = catalog["releases"]
        containers = None
        try:
            listed = docker(["container", "ls", "--all", "--quiet", "--no-trunc",
                             "--filter", "label=com.hungerdesolate.hostId=" + host_id])
            if listed is not None:
                container_ids = listed.splitlines()
                if len(container_ids) > 400 or any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in container_ids):
                    reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
                containers = []
                if container_ids:
                    projection = ('{"id":{{json .Id}},"labels":{{json .Config.Labels}},"imageId":{{json .Image}},'
                        '"status":{{json .State.Status}},"bindings":{{json .HostConfig.PortBindings}}}')
                    inspected = docker(["inspect", "--format", projection, *container_ids])
                    containers = [json.loads(line) for line in inspected.splitlines()] if inspected is not None else None
                    if containers is not None and len(containers) != len(container_ids):
                        reject("INSTANCE_START_DOCKER_RESPONSE_INVALID")
        except Rejected:
            containers = None
        snapshots = []
        for instance_id in ids:
            matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("instanceId") == instance_id]
            if len(matches) != 1 or matches[0].get("lifecycle") == "retired":
                reject("INSTANCE_NOT_FOUND")
            instance = ROOT / "instances" / instance_id
            manifest = None
            path = instance / "manifest.json"
            if path.exists():
                safe_directory(instance)
                manifest = read_json(path, 65536)
                if (manifest.get("hostId") != host_id or manifest.get("instanceId") != instance_id
                        or manifest.get("composeProject") != "fh_" + instance_id.replace("-", "")
                        or type(manifest.get("cavesEnabled")) is not bool):
                    reject("INSTANCE_IDENTITY_MISMATCH")
            snapshot = runtime_snapshot(host_id, instance_id, manifest, containers, releases)
            if manifest is not None and read_json(path, 65536) != manifest:
                reject("REVISION_CONFLICT")
            snapshots.append(snapshot)
        if read_json(ROOT / "registry.json") != registry or read_json(ROOT / "host.json", 65536) != host:
            reject("REVISION_CONFLICT")
        return {"schemaVersion": 1, "requestId": request["requestId"], "hostId": host_id, "instances": snapshots}
    finally:
        OBSERVATION_DEADLINE = None


def main(request):
    global CURRENT_STAGE
    try:
        data = inspect_instances(request) if request.get("action") == "inspect" else start(request)
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        details = dict(error.details)
        details.setdefault("failureStage", CURRENT_STAGE)
        print(json.dumps({"ok": False, "code": error.code, "details": details}, separators=(",", ":")), flush=True)
    except Exception as error:
        known_types = {"OSError", "PermissionError", "FileNotFoundError", "NotADirectoryError",
                       "ValueError", "TypeError", "KeyError", "AttributeError", "RuntimeError", "Error"}
        failure_type = type(error).__name__
        details = {"failureStage": CURRENT_STAGE,
                   "failureType": failure_type if failure_type in known_types else "Exception"}
        print(json.dumps({"ok": False, "code": "INSTANCE_START_STATE_INVALID",
                          "details": details}, separators=(",", ":")), flush=True)

