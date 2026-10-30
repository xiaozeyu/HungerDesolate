"""Read-only eligibility check for the Ubuntu 26.04 Docker installer."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path("/var/lib/famine-hub")
BOOTSTRAP = Path("/var/lib/famine-hub.bootstrap")
SOURCE_FILE = Path("/etc/apt/sources.list.d/docker.sources")
LEGACY_SOURCE_FILE = Path("/etc/apt/sources.list.d/docker.list")
KEY_FILE = Path("/etc/apt/keyrings/docker.asc")
RELEASE_FILE = Path("/etc/os-release")
SYSTEMD_DIR = Path("/run/systemd/system")
SOURCES_DIR = Path("/etc/apt/sources.list.d")
APT_SOURCE_LIST = Path("/etc/apt/sources.list")
SOURCE = "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: resolute\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n"
PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]
CONFLICTS = ["docker.io", "docker-compose", "docker-compose-v2", "docker-doc", "docker-buildx", "podman-docker", "containerd", "runc"]


def release(path):
    fields = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return fields
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key not in {"ID", "VERSION_ID", "VERSION_CODENAME", "UBUNTU_CODENAME", "PRETTY_NAME"}:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if len(value) <= 128 and not any(ord(char) < 32 for char in value):
            fields[key] = value
    return fields


def installed_conflicts():
    if not shutil.which("dpkg-query"):
        return ["dpkg-query"]
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Package}\t${db:Status-Status}\n", *CONFLICTS],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=5, check=False,
    )
    if result.returncode not in (0, 1):
        return ["dpkg-query-error"]
    return sorted({line.split("\t", 1)[0] for line in result.stdout.splitlines()
                   if line.endswith("\tinstalled")})


def installed_docker_packages():
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Package}\t${Version}\t${db:Status-Status}\n", *PACKAGES],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=5, check=False,
    )
    if result.returncode not in (0, 1):
        return {}
    installed = {}
    for line in result.stdout.splitlines():
        columns = line.split("\t")
        if len(columns) == 3 and columns[0] in PACKAGES and columns[2] == "installed":
            installed[columns[0]] = columns[1]
    return installed


def apt_state():
    if not Path("/proc").is_dir():
        return False, True
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            command = (process / "cmdline").read_bytes()[:2048].split(b"\0")
        except (OSError, PermissionError):
            continue
        executable = Path(command[0].decode("utf-8", errors="ignore")).name if command and command[0] else ""
        if executable in {"apt", "apt-get", "dpkg", "dpkg-deb", "unattended-upgrade"}:
            return True, True
    try:
        audit = subprocess.run(["dpkg", "--audit"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False, False
    if audit.returncode != 0:
        return False, False
    return False, not audit.stdout.strip()


def safe_regular_file(path, limit):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.geteuid())
            and info.st_nlink == 1 and not info.st_mode & 0o022 and info.st_size <= limit)


def host_identity():
    if ROOT.is_symlink() or not ROOT.is_dir():
        return None, "HOST_NOT_INITIALIZED"
    root_info=ROOT.stat(follow_symlinks=False)
    if root_info.st_uid not in (0,os.geteuid()) or root_info.st_mode & 0o022:
        return None, "MANAGEMENT_ROOT_UNSAFE"
    values=[]
    for path,limit in [(ROOT/"host.json",65536),(ROOT/"registry.json",1024*1024)]:
        try:
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.geteuid()) or info.st_nlink!=1 or info.st_mode & 0o022 or info.st_size>limit:
                return None,"HOST_IDENTITY_INVALID"
            values.append(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None,"HOST_IDENTITY_MISSING"
        except (OSError,ValueError):
            return None,"HOST_IDENTITY_INVALID"
    host,registry=values
    host_id=host.get("hostId")
    try:
        parsed=uuid.UUID(host_id)
    except (ValueError,TypeError,AttributeError):
        return None,"HOST_IDENTITY_INVALID"
    if (host.get("schemaVersion")!=1 or parsed.version!=4 or str(parsed)!=host_id
            or registry.get("schemaVersion")!=1 or registry.get("hostId")!=host_id
            or type(registry.get("registryRevision")) is not int
            or not isinstance(registry.get("instances"),list)):
        return None,"HOST_IDENTITY_INVALID"
    return host_id,None


def repository_http_error(status):
    if type(status) is int and 100 <= status <= 599:
        return "DOCKER_REPOSITORY_HTTP_" + str(status)
    return "DOCKER_REPOSITORY_RESPONSE_INVALID"


def check_repository():
    """Return a bounded error code, never remote bodies, headers or exception text."""
    try:
        request = urllib.request.Request(
            "https://download.docker.com/linux/ubuntu/dists/resolute/InRelease",
            headers={"Range": "bytes=0-1023"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status not in (200, 206):
                return repository_http_error(response.status)
            first = response.read(1024)
            if not first.startswith(b"-----BEGIN PGP SIGNED MESSAGE-----"):
                return "DOCKER_REPOSITORY_RESPONSE_INVALID"
        return None
    except urllib.error.HTTPError as error:
        code = repository_http_error(error.code)
        error.close()
        return code
    except (OSError, urllib.error.URLError) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        if isinstance(reason, TimeoutError):
            return "DOCKER_REPOSITORY_TIMEOUT"
        if isinstance(reason, socket.gaierror):
            return "DOCKER_REPOSITORY_DNS_FAILED"
        if isinstance(reason, ssl.SSLCertVerificationError):
            return "DOCKER_REPOSITORY_TLS_CERT_INVALID"
        if isinstance(reason, ssl.SSLError):
            return "DOCKER_REPOSITORY_TLS_FAILED"
        return "DOCKER_REPOSITORY_CONNECTION_FAILED"
    except http.client.HTTPException:
        return "DOCKER_REPOSITORY_RESPONSE_INVALID"


def inspect(*, check_repository_access=True):
    # Preview probes the network; admission/worker recheck local state only.
    # A skipped probe is unknown, never a fabricated successful connection.
    release_info = release(RELEASE_FILE)
    distribution_id = release_info.get("ID", "").lower()
    version = release_info.get("VERSION_ID", "")
    codename = release_info.get("UBUNTU_CODENAME", release_info.get("VERSION_CODENAME", "")).lower()
    architecture = platform.machine().lower()
    uid = os.geteuid()
    blockers = []
    if distribution_id != "ubuntu" or version != "26.04" or codename != "resolute":
        blockers.append("UNSUPPORTED_DISTRIBUTION")
    if architecture not in {"x86_64", "amd64"}:
        blockers.append("UNSUPPORTED_ARCHITECTURE")
    if uid != 0:
        blockers.append("ROOT_REQUIRED")
    if not SYSTEMD_DIR.exists() or not shutil.which("systemd-run"):
        blockers.append("SYSTEMD_REQUIRED")
    if not shutil.which("apt-get") or not shutil.which("dpkg-query"):
        blockers.append("APT_REQUIRED")
    conflicts = installed_conflicts()
    if conflicts:
        blockers.append("CONFLICTING_PACKAGES")
    installed_packages = installed_docker_packages()
    apt_running, dpkg_clean = apt_state()
    if apt_running:
        blockers.append("APT_BUSY")
    if not dpkg_clean:
        blockers.append("DPKG_STATE_INCOMPLETE")
    docker_client = shutil.which("docker")
    if docker_client and not installed_packages:
        blockers.append("DOCKER_ALREADY_INSTALLED")
    host_id,identity_error=host_identity()
    if identity_error:
        blockers.append(identity_error)
    if BOOTSTRAP.exists() or BOOTSTRAP.is_symlink():
        if BOOTSTRAP.is_symlink() or not BOOTSTRAP.is_dir():
            blockers.append("BOOTSTRAP_PATH_UNSAFE")
        else:
            info = BOOTSTRAP.stat()
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                blockers.append("BOOTSTRAP_OWNER_OR_MODE_INVALID")
    repository_error = check_repository() if check_repository_access else None
    repository_reachable = repository_error is None if check_repository_access else None
    if repository_error:
        blockers.append(repository_error)
    source_state = "missing"
    if SOURCE_FILE.exists() or SOURCE_FILE.is_symlink():
        if SOURCE_FILE.is_symlink() or not safe_regular_file(SOURCE_FILE, 65536):
            blockers.append("DOCKER_SOURCE_UNSAFE")
        elif SOURCE_FILE.read_text(encoding="utf-8", errors="replace") == SOURCE:
            source_state = "matching"
        else:
            blockers.append("DOCKER_SOURCE_CONFLICT")
    if LEGACY_SOURCE_FILE.exists() or LEGACY_SOURCE_FILE.is_symlink():
        blockers.append("LEGACY_DOCKER_SOURCE_PRESENT")
    if KEY_FILE.exists() or KEY_FILE.is_symlink():
        if KEY_FILE.is_symlink() or not safe_regular_file(KEY_FILE, 1024 * 1024):
            blockers.append("DOCKER_KEY_UNSAFE")
        elif source_state == "missing":
            blockers.append("DOCKER_KEY_CONFLICT")
    sources_dir = SOURCES_DIR
    if sources_dir.is_symlink() or not sources_dir.is_dir():
        blockers.append("APT_SOURCE_DIRECTORY_UNSAFE")
    try:
        source_entries = list(sources_dir.iterdir())
    except OSError:
        source_entries = []
        blockers.append("APT_SOURCE_DIRECTORY_UNREADABLE")
    if len(source_entries) > 256:
        blockers.append("APT_SOURCE_DIRECTORY_TOO_LARGE")
    for entry in source_entries[:256]:
        if entry in {SOURCE_FILE, LEGACY_SOURCE_FILE} or entry.suffix not in {".list", ".sources"}:
            continue
        try:
            if entry.is_file() and entry.stat().st_size <= 1024 * 1024:
                if "download.docker.com/linux/ubuntu" in entry.read_text(encoding="utf-8", errors="replace"):
                    blockers.append("DOCKER_SOURCE_CONFLICT")
        except OSError:
            blockers.append("APT_SOURCE_DIRECTORY_UNREADABLE")
    try:
        if APT_SOURCE_LIST.is_symlink():
            blockers.append("APT_SOURCE_DIRECTORY_UNSAFE")
        elif APT_SOURCE_LIST.exists() and "download.docker.com/linux/ubuntu" in APT_SOURCE_LIST.read_text(encoding="utf-8", errors="replace"):
            blockers.append("DOCKER_SOURCE_CONFLICT")
    except OSError:
        blockers.append("APT_SOURCE_DIRECTORY_UNREADABLE")
    free_bytes = shutil.disk_usage("/var").free
    if free_bytes < 4 * 1024**3:
        blockers.append("INSUFFICIENT_DISK_SPACE")
    snapshot = {
        "distributionId": distribution_id,
        "distributionVersion": version,
        "distributionCodename": codename,
        "hostId": host_id,
        "architecture": architecture,
        "uid": uid,
        "conflictingPackages": conflicts,
        "sourceState": source_state,
        "dockerClientPresent": bool(docker_client),
        "installedDockerPackages": installed_packages,
        "aptRunning": apt_running,
        "dpkgClean": dpkg_clean,
        "managementRootDevice": ROOT.stat().st_dev if ROOT.exists() and ROOT.is_dir() else None,
        "managementRootInode": ROOT.stat().st_ino if ROOT.exists() and ROOT.is_dir() else None,
        "hasRequiredFreeSpace": free_bytes >= 4 * 1024**3,
    }
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schemaVersion": 1,
        "status": "ready" if not blockers else "blocked",
        "blockers": sorted(set(blockers)),
        "snapshotDigest": digest,
        "expiresAtUnixSeconds": int(time.time()) + 600,
        "distribution": {
            "id": distribution_id or None,
            "version": version or None,
            "codename": codename or None,
            "architecture": architecture or None,
        },
        "hostId": host_id,
        "uid": uid,
        "rootAvailableBytes": free_bytes,
        "sourceState": source_state,
        "repositoryReachable": repository_reachable,
        "installedDockerPackages": installed_packages,
        "aptRunning": apt_running,
        "dpkgClean": dpkg_clean,
        "packages": PACKAGES,
        "repository": "https://download.docker.com/linux/ubuntu",
        "sourceFile": str(SOURCE_FILE),
        "keyFile": str(KEY_FILE),
    }
