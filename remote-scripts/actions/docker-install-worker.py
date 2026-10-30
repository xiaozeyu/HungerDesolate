"""Root-owned systemd worker for the confirmed Ubuntu 26.04 Docker install."""
import errno
import fcntl
import http.client
import json
import os
from pathlib import Path
import runpy
import stat
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BOOTSTRAP = Path("/var/lib/famine-hub.bootstrap")
TASKS = BOOTSTRAP / "docker-install"
SOURCE_FILE = Path("/etc/apt/sources.list.d/docker.sources")
KEY_FILE = Path("/etc/apt/keyrings/docker.asc")
SOURCE = "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: resolute\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n"
PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]
MAX_OUTPUT = 2 * 1024 * 1024


def write_status(task_dir, state, phase, message_code=None, versions=None):
    path = task_dir / "status.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        sequence = current.get("sequence", 0) + 1
        started = current.get("startedAtUnixSeconds", int(time.time()))
        host_id = current.get("hostId")
    except (OSError, ValueError, AttributeError):
        sequence = 1
        started = int(time.time())
        host_id = None
    body = {"schemaVersion": 1, "taskId": task_dir.name, "sequence": sequence,
            "status": state, "phase": phase, "messageCode": message_code,
            "startedAtUnixSeconds": started, "updatedAtUnixSeconds": int(time.time()),
            "installedVersions": versions or {}, "hostId": host_id}
    temporary = task_dir / (".status-" + str(uuid.uuid4()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(body, separators=(",", ":")).encode())
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
        directory = os.open(task_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run(command, log, timeout):
    with log.open("ab") as output:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, check=False, timeout=timeout,
                                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"})
    if result.returncode != 0:
        raise RuntimeError("INSTALL_COMMAND_FAILED")


def simulate_install(log):
    result = subprocess.run(
        ["apt-get", "--simulate", "install", "--yes", "--no-install-recommends", *PACKAGES],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=120,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"},
    )
    with log.open("ab") as output:
        output.write(result.stdout[-MAX_OUTPUT:])
    if result.returncode != 0:
        raise RuntimeError("INSTALL_SIMULATION_FAILED")
    if any(line.startswith("Remv ") for line in result.stdout.decode("utf-8", errors="replace").splitlines()):
        raise RuntimeError("INSTALL_WOULD_REMOVE_PACKAGES")


def create_file(path, content, mode):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent_info = path.parent.stat(follow_symlinks=False)
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in (0, os.geteuid()) or parent_info.st_mode & 0o022:
        raise RuntimeError("INSTALL_PATH_UNSAFE")
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(content)
            output.flush()
            os.fsync(fd)
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def managed_file(path, limit):
    try:
        info=path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > limit:
        raise RuntimeError("INSTALL_PATH_UNSAFE")
    return True


def valid_signing_key(key):
    return (0 < len(key) <= 1024 * 1024
            and key.startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----")
            and key.rstrip().endswith(b"-----END PGP PUBLIC KEY BLOCK-----"))


def download_signing_key():
    # Retry only transient network failures; certificate and format checks always fail closed.
    for attempt in range(3):
        retryable = False
        try:
            request = urllib.request.Request("https://download.docker.com/linux/ubuntu/gpg")
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError("INSTALL_KEY_RESPONSE_INVALID")
                key = response.read(1024 * 1024 + 1)
            if not valid_signing_key(key):
                raise RuntimeError("INSTALL_SIGNING_KEY_INVALID")
            return key
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if type(status) is not int or not 100 <= status <= 599:
                raise RuntimeError("INSTALL_KEY_RESPONSE_INVALID") from None
            code = "INSTALL_KEY_HTTP_" + str(status)
            retryable = status in (408, 429) or 500 <= status <= 599
        except (OSError, urllib.error.URLError) as error:
            reason = error.reason if isinstance(error, urllib.error.URLError) else error
            if isinstance(reason, TimeoutError):
                code, retryable = "INSTALL_KEY_TIMEOUT", True
            elif isinstance(reason, socket.gaierror):
                code, retryable = "INSTALL_KEY_DNS_FAILED", True
            elif isinstance(reason, ssl.SSLCertVerificationError):
                code = "INSTALL_KEY_TLS_CERT_INVALID"
            elif isinstance(reason, ssl.SSLError):
                code = "INSTALL_KEY_TLS_FAILED"
            else:
                code, retryable = "INSTALL_KEY_CONNECTION_FAILED", True
        except http.client.HTTPException:
            code, retryable = "INSTALL_KEY_RESPONSE_INVALID", True
        if not retryable or attempt == 2:
            raise RuntimeError(code) from None
        time.sleep(attempt + 1)


def ensure_repository():
    source = SOURCE_FILE
    if source.is_symlink():
        raise RuntimeError("INSTALL_SOURCE_UNSAFE")
    source_present = managed_file(source, 65536)
    if source_present and source.read_text(encoding="utf-8") != SOURCE:
        raise RuntimeError("INSTALL_SOURCE_CONFLICT")
    if KEY_FILE.is_symlink():
        raise RuntimeError("INSTALL_KEY_UNSAFE")
    key_present = managed_file(KEY_FILE, 1024 * 1024)
    if key_present:
        if not valid_signing_key(KEY_FILE.read_bytes()):
            raise RuntimeError("INSTALL_SIGNING_KEY_INVALID")
    else:
        key = download_signing_key()
    # Publish only after the download succeeds. A matching source with no key remains retryable.
    if not source_present:
        create_file(source, SOURCE.encode(), 0o644)
    if not key_present:
        create_file(KEY_FILE, key, 0o644)


def filesystem_error(error):
    if error.errno in (errno.EACCES, errno.EPERM):
        return "INSTALL_PERMISSION_DENIED"
    if error.errno in (errno.ENOSPC, errno.EDQUOT):
        return "INSTALL_DISK_FULL"
    if error.errno == errno.EROFS:
        return "INSTALL_FILESYSTEM_READ_ONLY"
    if error.errno == errno.ENOENT:
        return "INSTALL_FILE_MISSING"
    if error.errno == errno.EEXIST:
        return "INSTALL_FILE_CONFLICT"
    return "INSTALL_FILESYSTEM_ERROR"


def package_versions():
    result = subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", *PACKAGES],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=10, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError("INSTALL_VERIFY_FAILED")
    versions = {}
    for line in result.stdout.splitlines():
        name, separator, version = line.partition("\t")
        if separator and name in PACKAGES and version:
            versions[name] = version
    return versions


def compose_major(version):
    candidate=version.strip().lstrip("v").split(".",1)[0]
    return int(candidate) if candidate.isdigit() else None


def verify_runtime(log):
    engine=subprocess.run(["docker","version","--format","{{.Server.Os}} {{.Server.Arch}} {{.Server.Version}}"],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30,check=False)
    with log.open("ab") as output:
        output.write(engine.stdout.encode("utf-8",errors="replace")[-65536:])
    if engine.returncode!=0:
        raise RuntimeError("INSTALL_VERIFY_FAILED")
    fields=engine.stdout.strip().split()
    if len(fields)<3 or fields[0]!="linux" or fields[1] not in {"amd64","x86_64"}:
        raise RuntimeError("INSTALL_VERIFY_FAILED")
    compose=subprocess.run(["docker","compose","version","--short"],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30,check=False)
    with log.open("ab") as output:
        output.write(compose.stdout.encode("utf-8",errors="replace")[-65536:])
    if compose.returncode!=0 or (compose_major(compose.stdout) or 0)<2:
        raise RuntimeError("INSTALL_VERIFY_FAILED")
    run(["systemctl","is-active","--quiet","docker.service"],log,30)
    return {"dockerEngine":fields[2],"dockerCompose":compose.stdout.strip()}


def main(task_path):
    os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    os.environ["LC_ALL"] = "C"
    task_dir = Path(task_path)
    if task_dir.parent != TASKS or task_dir.name != task_dir.name.lower() or len(task_dir.name) != 36:
        return 2
    info = task_dir.lstat()
    if os.geteuid() != 0 or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        return 2
    log = task_dir / "apt.log"
    phase = "rechecking"
    try:
        write_status(task_dir, "running", phase)
        plan = json.loads((task_dir / "plan.json").read_text(encoding="utf-8"))
        if plan.get("taskId") != task_dir.name:
            raise RuntimeError("INSTALL_PLAN_STALE")
        # Actual key/APT operations enforce connectivity and verification in their own phases.
        preflight = runpy.run_path(str(task_dir / "preflight.py"))["inspect"](check_repository_access=False)
        if (preflight["status"] != "ready" or preflight["snapshotDigest"] != plan["snapshotDigest"]
                or preflight.get("hostId") != plan["hostId"]):
            raise RuntimeError("INSTALL_PLAN_STALE")
        lock = os.open(BOOTSTRAP / "docker-install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            lock_info = os.fstat(lock)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != 0 or lock_info.st_nlink != 1 or lock_info.st_mode & 0o077:
                raise RuntimeError("INSTALL_LOCK_UNSAFE")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            phase = "configuringRepository"
            write_status(task_dir, "running", phase)
            ensure_repository()
            phase = "updatingPackageIndex"
            write_status(task_dir, "running", phase)
            run(["apt-get", "-qq", "update"], log, 900)
            phase = "checkingPackageChanges"
            write_status(task_dir, "running", phase)
            simulate_install(log)
            phase = "installingPackages"
            write_status(task_dir, "running", phase)
            run(["apt-get", "-qq", "install", "--yes", "--no-install-recommends", *PACKAGES], log, 1800)
            phase = "startingDocker"
            write_status(task_dir, "running", phase)
            run(["systemctl", "enable", "--now", "docker.service"], log, 60)
            phase = "verifyingRuntime"
            write_status(task_dir, "running", phase)
            runtime_versions=verify_runtime(log)
            versions = package_versions()
            if set(versions) != set(PACKAGES):
                raise RuntimeError("INSTALL_VERIFY_FAILED")
            versions.update(runtime_versions)
            phase = "verified"
            write_status(task_dir, "succeeded", phase, versions=versions)
        finally:
            os.close(lock)
        return 0
    except subprocess.TimeoutExpired:
        write_status(task_dir, "failed", phase, "INSTALL_COMMAND_TIMEOUT")
    except BlockingIOError:
        write_status(task_dir, "failed", phase, "INSTALL_ALREADY_RUNNING")
    except RuntimeError as error:
        code = str(error)
        allowed = {"INSTALL_PLAN_STALE", "INSTALL_PATH_UNSAFE", "INSTALL_SOURCE_UNSAFE",
                   "INSTALL_SOURCE_CONFLICT", "INSTALL_SIGNING_KEY_INVALID", "INSTALL_KEY_UNSAFE",
                   "INSTALL_LOCK_UNSAFE", "INSTALL_COMMAND_FAILED", "INSTALL_VERIFY_FAILED",
                   "INSTALL_SIMULATION_FAILED", "INSTALL_WOULD_REMOVE_PACKAGES",
                   "INSTALL_KEY_TIMEOUT", "INSTALL_KEY_DNS_FAILED", "INSTALL_KEY_TLS_CERT_INVALID",
                   "INSTALL_KEY_TLS_FAILED", "INSTALL_KEY_CONNECTION_FAILED", "INSTALL_KEY_RESPONSE_INVALID"}
        http_status = code.removeprefix("INSTALL_KEY_HTTP_")
        is_http_error = (code.startswith("INSTALL_KEY_HTTP_") and len(http_status) == 3
                         and http_status.isascii() and http_status.isdigit() and 100 <= int(http_status) <= 599)
        write_status(task_dir, "failed", phase, code if code in allowed or is_http_error else "INSTALL_FAILED")
    except FileNotFoundError as error:
        # subprocess records the missing executable in filename; never persist that raw value.
        executable = error.filename
        code = "INSTALL_COMMAND_MISSING" if executable in {"apt-get", "dpkg-query", "docker", "systemctl"} else "INSTALL_FILE_MISSING"
        write_status(task_dir, "failed", phase, code)
    except OSError as error:
        write_status(task_dir, "failed", phase, filesystem_error(error))
    except (ValueError, KeyError, TypeError, AttributeError, SyntaxError):
        write_status(task_dir, "failed", phase, "INSTALL_TASK_DATA_INVALID")
    return 1


if __name__ == "__main__" and len(sys.argv) == 2:
    raise SystemExit(main(sys.argv[1]))
