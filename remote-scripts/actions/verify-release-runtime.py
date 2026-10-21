"""Run an isolated Master/Caves smoke test for a digest-pinned DST release."""
import base64
import configparser
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile

ROOT = Path("/var/lib/famine-hub")
STAGING = ROOT / "staging"
CATALOG = ROOT / "releases" / "catalog.json"
RUNTIME_LOCK = ROOT / "releases" / "runtime-verify.lock"
TEMPORARY_ROOT = Path("/run/famine-hub/release-verify")
MAX_BUNDLE_BYTES = 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024
MAX_ZIP_ENTRIES = 96
SERVER_BINARY = "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64"
ROLES = ("master", "caves")
CURRENT_STAGE = "validateRequest"


class Rejected(Exception):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.details = details if isinstance(details, dict) else {}


def reject(code, details=None):
    raise Rejected(code, details)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def catalog_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def docker(command, timeout=30, stdout=subprocess.PIPE):
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=subprocess.DEVNULL, text=True, timeout=timeout,
                                check=False, env=environment)
    except FileNotFoundError:
        reject("CREATE_DOCKER_UNAVAILABLE")
    except subprocess.TimeoutExpired:
        reject("RELEASE_RUNTIME_DOCKER_TIMEOUT")
    if result.stdout is not None and len(result.stdout) > 65536:
        reject("RELEASE_RUNTIME_DOCKER_RESPONSE_INVALID")
    return result


def read_json(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("CREATE_RELEASE_CATALOG_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("CREATE_RELEASE_CATALOG_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("CREATE_RELEASE_CATALOG_INVALID")


def prepare_bundle(encoded, destination):
    if not isinstance(encoded, str) or len(encoded) > MAX_BUNDLE_BYTES * 2:
        reject("RELEASE_RUNTIME_BUNDLE_INVALID")
    try:
        bundle = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        reject("RELEASE_RUNTIME_BUNDLE_INVALID")
    if not bundle or len(bundle) > MAX_BUNDLE_BYTES:
        reject("RELEASE_RUNTIME_BUNDLE_INVALID")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle))
        entries = archive.infolist()
    except (OSError, ValueError, zipfile.BadZipFile):
        reject("RELEASE_RUNTIME_BUNDLE_INVALID")
    if not entries or len(entries) > MAX_ZIP_ENTRIES:
        reject("RELEASE_RUNTIME_BUNDLE_INVALID")

    files = {}
    roots = set()
    total = 0
    for entry in entries:
        name = entry.filename.replace("\\", "/")
        path = PurePosixPath(name)
        mode = (entry.external_attr >> 16) & 0o170000
        if (path.is_absolute() or ".." in path.parts or "" in path.parts
                or mode == stat.S_IFLNK or entry.flag_bits & 0x1):
            reject("RELEASE_RUNTIME_BUNDLE_INVALID")
        if entry.is_dir():
            continue
        total += entry.file_size
        if entry.file_size > MAX_EXPANDED_BYTES or total > MAX_EXPANDED_BYTES:
            reject("RELEASE_RUNTIME_BUNDLE_TOO_LARGE")
        parts = path.parts
        try:
            index = parts.index("cluster.ini")
        except ValueError:
            if "cluster_token.txt" in parts:
                index = parts.index("cluster_token.txt")
            elif len(parts) >= 2 and parts[-2].lower() in ROLES and parts[-1] == "server.ini":
                index = len(parts) - 2
            else:
                continue
        root = parts[:index]
        relative = parts[index:]
        roots.add(root)
        relative_name = "/".join(relative)
        allowed = (relative_name in {"cluster.ini", "cluster_token.txt"}
                   or re.fullmatch(r"(?:Master|Caves)/[A-Za-z0-9_.-]+\.(?:ini|lua|txt)", relative_name))
        if not allowed:
            continue
        if relative_name in files:
            reject("RELEASE_RUNTIME_BUNDLE_INVALID")
        try:
            files[relative_name] = archive.read(entry)
        except (OSError, RuntimeError, zipfile.BadZipFile):
            reject("RELEASE_RUNTIME_BUNDLE_INVALID")
    if len(roots) != 1:
        reject("RELEASE_RUNTIME_BUNDLE_INVALID")
    required = {"cluster.ini", "cluster_token.txt", "Master/server.ini", "Caves/server.ini"}
    if not required.issubset(files):
        reject("RELEASE_RUNTIME_CONFIG_INCOMPLETE")
    token = files["cluster_token.txt"].strip()
    if not token or len(token) > 512 or any(value < 0x20 for value in token):
        reject("RELEASE_RUNTIME_CONFIG_INCOMPLETE")

    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for relative_name, content in files.items():
        path = destination / relative_name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(content)
        os.chmod(path, 0o600)
    os.chown(destination, 1000, 1000)
    for directory, _, _ in os.walk(destination):
        os.chown(directory, 1000, 1000)
        os.chmod(directory, 0o700)
    for path in destination.rglob("*"):
        if path.is_file():
            os.chown(path, 1000, 1000)
            os.chmod(path, 0o600)

    cluster_path = destination / "cluster.ini"
    cluster = configparser.ConfigParser(interpolation=None)
    try:
        cluster.read(cluster_path, encoding="utf-8")
    except (OSError, configparser.Error, UnicodeError):
        reject("RELEASE_RUNTIME_CONFIG_INVALID")
    if not cluster.has_section("NETWORK"):
        reject("RELEASE_RUNTIME_CONFIG_INVALID")
    cluster.set("NETWORK", "lan_only_cluster", "true")
    if not cluster.has_section("SHARD"):
        cluster.add_section("SHARD")
    cluster.set("SHARD", "bind_ip", "0.0.0.0")
    cluster.set("SHARD", "master_ip", "master")
    cluster.set("SHARD", "shard_enabled", "true")
    if not cluster.has_option("SHARD", "master_port"):
        cluster.set("SHARD", "master_port", "10888")
    if not cluster.has_option("SHARD", "cluster_key"):
        cluster.set("SHARD", "cluster_key", uuid.uuid4().hex)
    with cluster_path.open("w", encoding="utf-8") as output:
        cluster.write(output)
    os.chown(cluster_path, 1000, 1000)
    os.chmod(cluster_path, 0o600)

    ports = {}
    for role in ROLES:
        settings = configparser.ConfigParser(interpolation=None)
        try:
            settings.read(destination / role.title() / "server.ini", encoding="utf-8")
            values = {
                "game": settings.getint("NETWORK", "server_port"),
                "query": settings.getint("STEAM", "master_server_port"),
            }
            if role == "master":
                values["shard"] = cluster.getint("SHARD", "master_port")
        except (OSError, configparser.Error, UnicodeError, ValueError):
            reject("RELEASE_RUNTIME_CONFIG_INVALID")
        if any(value < 1 or value > 65535 for value in values.values()):
            reject("RELEASE_RUNTIME_CONFIG_INVALID")
        ports[role] = values
    return ports


def udp_ports(container):
    result = docker(["docker", "exec", container, "/bin/sh", "-c",
                     "cat /proc/net/udp && { if [ -r /proc/net/udp6 ]; then cat /proc/net/udp6; fi; }"])
    if result.returncode != 0:
        reject("RELEASE_RUNTIME_PROCESS_NOT_READY")
    ports = set()
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 1 and ":" in fields[1]:
            try:
                ports.add(int(fields[1].rsplit(":", 1)[1], 16))
            except ValueError:
                reject("RELEASE_RUNTIME_DOCKER_RESPONSE_INVALID")
    return ports


def process_running(container):
    state = docker(["docker", "inspect", "--format", "{{.State.Running}}", container])
    if state.returncode != 0 or state.stdout.strip() != "true":
        return False
    probe = ("for p in /proc/[0-9]*/exe; do x=$(readlink \"$p\" 2>/dev/null) || continue; "
             "if [ \"$x\" = " + SERVER_BINARY + " ]; then printf DST_RUNNING; exit 0; fi; "
             "done; printf DST_NOT_FOUND")
    processes = docker(["docker", "exec", container, "/bin/sh", "-c", probe])
    return processes.returncode == 0 and processes.stdout.strip() == "DST_RUNNING"


def atomic_evidence(path, value):
    temporary = path.parent / (".runtime." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
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


def verify(request):
    global CURRENT_STAGE
    CURRENT_STAGE = "validateRequest"
    host_id = request.get("hostId")
    release_id = request.get("releaseId")
    expected_digest = request.get("catalogDigest")
    verification_id = request.get("operationId")
    if (not valid_uuid(host_id) or not valid_uuid(release_id) or not valid_uuid(verification_id)
            or not isinstance(expected_digest, str)):
        reject("CREATE_RELEASE_INPUT_INVALID")

    def interrupted(_signum, _frame):
        raise InterruptedError("runtime verification interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    CURRENT_STAGE = "readHostState"
    host = read_json(ROOT / "host.json", 65536)
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    CURRENT_STAGE = "readReleaseCatalog"
    catalog = read_json(CATALOG, 1024 * 1024)
    observed = catalog_digest(catalog)
    if observed != expected_digest:
        reject("RELEASE_CATALOG_CHANGED")
    releases = catalog.get("releases")
    if catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id or not isinstance(releases, list):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    release = next((item for item in releases if isinstance(item, dict) and item.get("releaseId") == release_id), None)
    if release is None or release.get("verified") is not True or release.get("runtimeVerified") is True:
        reject("CREATE_RELEASE_NOT_VERIFIED")
    CURRENT_STAGE = "readReleaseMetadata"
    metadata = read_json(STAGING / release_id / "release-metadata.json", 65536)
    metadata_entry = dict(metadata)
    metadata_entry.pop("schemaVersion", None)
    if metadata_entry != release:
        reject("CREATE_RELEASE_METADATA_INVALID")

    lock_fd = os.open(RUNTIME_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_info = os.fstat(lock_fd)
    if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1
            or lock_info.st_uid not in (0, os.geteuid()) or lock_info.st_mode & 0o077):
        os.close(lock_fd)
        reject("RELEASE_RUNTIME_PATH_UNSAFE")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        reject("RELEASE_RUNTIME_BUSY")

    CURRENT_STAGE = "prepareConfiguration"
    bundle = request.get("settingsBundleBase64")
    temporary_root = TEMPORARY_ROOT
    execution_id = uuid.uuid4().hex
    workspace = temporary_root / execution_id
    container_names = {role: "hd-rv-" + execution_id[:8] + "-" + role for role in ROLES}
    network = "hd-release-verify-" + execution_id[:16]
    evidence = None
    try:
        temporary_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_info = temporary_root.lstat()
        if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != 0 or root_info.st_mode & 0o077):
            reject("RELEASE_RUNTIME_PATH_UNSAFE")
        workspace.mkdir(mode=0o700)
        config_root = workspace / "config" / "Cluster_1"
        ports = prepare_bundle(bundle, config_root)
        for role in ROLES:
            data_path = workspace / ("data-" + role)
            data_path.mkdir(mode=0o700)
            os.chown(data_path, 1000, 1000)
            config_mountpoint = data_path / "config"
            config_mountpoint.mkdir(mode=0o700)
            os.chown(config_mountpoint, 1000, 1000)
            ugc_path = workspace / ("ugc-" + role)
            ugc_path.mkdir(mode=0o700)
            os.chown(ugc_path, 1000, 1000)

        CURRENT_STAGE = "verifyImage"
        image_id = release["imageDigest"]
        image_check = docker(["docker", "image", "inspect", release["imageDigest"], "--format", "{{.Id}}"])
        if image_check.returncode != 0 or image_check.stdout.strip() != image_id:
            reject("CREATE_RELEASE_IMAGE_DIGEST_MISMATCH")
        CURRENT_STAGE = "createNetwork"
        created = docker(["docker", "network", "create", "--driver", "bridge",
                          "--label", "com.hungerdesolate.releaseVerification=" + verification_id, network])
        if created.returncode != 0:
            reject("RELEASE_RUNTIME_NETWORK_FAILED")
        for role in ROLES:
            CURRENT_STAGE = "startMaster" if role == "master" else "startCaves"
            name = container_names[role]
            command = ["docker", "run", "--detach", "--init", "--workdir", "/srv/dst/bin64", "--network", network,
                       "--network-alias", role,
                       "--label", "com.hungerdesolate.releaseVerification=" + verification_id,
                       "--mount", "type=bind,src=" + str(workspace / ("data-" + role)) + ",dst=/srv/data",
                       # The config bind must follow the parent data bind; otherwise the parent
                       # mount hides this nested target and DST cannot read Cluster_1/server.ini.
                       "--mount", "type=bind,src=" + str(workspace / "config") + ",dst=/srv/data/config,readonly",
                       "--mount", "type=bind,src=" + str(workspace / ("ugc-" + role)) + ",dst=/srv/ugc",
                       "--name", name, "--entrypoint", SERVER_BINARY, image_id]
            command.extend(release["commands"][role][1:])
            started = docker(command, timeout=60)
            if started.returncode != 0:
                reject("RELEASE_RUNTIME_CONTAINER_START_FAILED")
        CURRENT_STAGE = "waitForListeners"
        deadline = time.monotonic() + 120
        ready = {role: set() for role in ROLES}
        process_ready = {role: False for role in ROLES}
        while time.monotonic() < deadline:
            all_ready = True
            for role in ROLES:
                name = container_names[role]
                if not process_running(name):
                    all_ready = False
                    continue
                process_ready[role] = True
                ready[role] = udp_ports(name)
                if not set(ports[role].values()).issubset(ready[role]):
                    all_ready = False
            if all_ready:
                ids = {}
                for role, name in container_names.items():
                    result = docker(["docker", "inspect", "--format", "{{.Id}}", name])
                    if result.returncode != 0:
                        reject("RELEASE_RUNTIME_DOCKER_RESPONSE_INVALID")
                    ids[role] = result.stdout.strip()
                evidence = {"schemaVersion": 1, "releaseId": release_id,
                            "imageDigest": image_id, "runtimeVerified": True,
                            "verification": "passed", "verifiedAtUnixSeconds": int(time.time()),
                            "containerIds": ids, "listeners": {role: sorted(ports[role].values()) for role in ROLES},
                            "lanOnly": True}
                break
            time.sleep(2)
        if evidence is None:
            missing = {
                role: sorted(set(ports[role].values()) - ready[role])
                for role in ROLES
            }
            observed = {role: sorted(ready[role])[:64] for role in ROLES}
            reject("RELEASE_RUNTIME_LISTENERS_NOT_READY", {
                "masterMissingPorts": missing["master"],
                "cavesMissingPorts": missing["caves"],
                "masterObservedPorts": observed["master"],
                "cavesObservedPorts": observed["caves"],
                "masterProcessRunning": process_ready["master"],
                "cavesProcessRunning": process_ready["caves"],
            })
    finally:
        CURRENT_STAGE = "cleanup"
        cleanup_failed = False
        label_filter = "label=com.hungerdesolate.releaseVerification=" + verification_id
        containers = docker(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                             "--filter", label_filter])
        if containers.returncode != 0:
            cleanup_failed = True
        for container_id in containers.stdout.splitlines():
            if not re.fullmatch(r"[0-9a-f]{64}", container_id):
                cleanup_failed = True
                continue
            if docker(["docker", "container", "rm", "--force", container_id]).returncode != 0:
                cleanup_failed = True
        networks = docker(["docker", "network", "ls", "--quiet", "--no-trunc",
                           "--filter", label_filter])
        if networks.returncode != 0:
            cleanup_failed = True
        for network_id in networks.stdout.splitlines():
            if not re.fullmatch(r"[0-9a-f]{64}", network_id):
                cleanup_failed = True
                continue
            if docker(["docker", "network", "rm", network_id]).returncode != 0:
                cleanup_failed = True
        try:
            if workspace is not None and workspace.exists():
                shutil.rmtree(workspace)
        except OSError:
            cleanup_failed = True
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    if cleanup_failed:
        reject("RELEASE_RUNTIME_CLEANUP_FAILED")
    if evidence is None:
        reject("RELEASE_RUNTIME_CHECK_FAILED")
    CURRENT_STAGE = "publishEvidence"
    artifact = STAGING / release_id
    atomic_evidence(artifact / "runtime.json", evidence)
    return {"releaseId": release_id, "runtimeVerified": True,
            "imageDigest": release["imageDigest"], "listeners": evidence["listeners"]}


def main(request):
    global CURRENT_STAGE
    CURRENT_STAGE = "initialize"
    try:
        data = verify(request)
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        details = dict(error.details)
        details.setdefault("failureStage", CURRENT_STAGE)
        print(json.dumps({"ok": False, "code": str(error), "details": details}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except Exception as error:
        known_types = {"OSError", "PermissionError", "FileNotFoundError", "NotADirectoryError",
                       "ValueError", "TypeError", "KeyError", "AttributeError", "RuntimeError",
                       "UnicodeError", "BadZipFile", "Error"}
        failure_type = type(error).__name__
        details = {"failureType": failure_type if failure_type in known_types else "Exception",
                   "failureStage": CURRENT_STAGE}
        traceback_node = error.__traceback__
        last_script_frame = None
        while traceback_node is not None:
            frame = traceback_node.tb_frame
            if frame.f_code.co_filename == __file__:
                last_script_frame = (frame.f_code.co_name, traceback_node.tb_lineno)
            traceback_node = traceback_node.tb_next
        if last_script_frame is not None:
            function_name, line_number = last_script_frame
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,47}", function_name) and 1 <= line_number <= 1000:
                details["failureFunction"] = function_name
                details["failureLine"] = line_number
        print(json.dumps({"ok": False, "code": "RELEASE_RUNTIME_VERIFY_FAILED", "details": details},
                         separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__" and len(sys.argv) > 1:
    main(json.loads(os.read(0, MAX_BUNDLE_BYTES * 2).decode("utf-8")))
