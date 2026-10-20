"""Atomically register a successfully built, not-yet-runtime-verified release."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

ROOT = Path("/var/lib/famine-hub")
STAGING = ROOT / "staging"
RELEASES = ROOT / "releases"
CATALOG = RELEASES / "catalog.json"
LOCK = RELEASES / "catalog.lock"
MAX_RELEASES = 16
MAX_METADATA_BYTES = 65536


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


def safe_json(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("CREATE_RELEASE_ARTIFACT_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("CREATE_RELEASE_ARTIFACT_CHANGED")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("CREATE_RELEASE_ARTIFACT_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("CREATE_RELEASE_ARTIFACT_INVALID")


def safe_dir(path, create=False, private=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or info.st_mode & 0o022):
        reject("CREATE_RELEASE_PATH_UNSAFE")
    if private and info.st_mode & 0o077:
        os.chmod(path, 0o700, follow_symlinks=False)


def release_entry(metadata, release_id):
    if (not isinstance(metadata, dict) or metadata.get("schemaVersion") != 1
            or metadata.get("releaseId") != release_id or metadata.get("platform") != "linux/amd64"
            or not isinstance(metadata.get("image"), str)
            or re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", metadata["image"]) is None
            or not isinstance(metadata.get("imageDigest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", metadata["imageDigest"]) is None
            or not isinstance(metadata.get("artifactDigest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", metadata["artifactDigest"]) is None
            or not isinstance(metadata.get("dstBuild"), str) or not metadata["dstBuild"]
            or not isinstance(metadata.get("scriptVersion"), str) or not metadata["scriptVersion"]
            or not isinstance(metadata.get("commands"), dict)
            or set(metadata["commands"]) != {"master", "caves"}):
        reject("CREATE_RELEASE_METADATA_INVALID")
    for role in ("master", "caves"):
        command = metadata["commands"][role]
        if (not isinstance(command, list) or not command or len(command) > 32
                or any(not isinstance(value, str) or not value or any(ord(ch) < 32 for ch in value)
                       for value in command)):
            reject("CREATE_RELEASE_METADATA_INVALID")
    if metadata["commands"]["master"][0] != "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64" or metadata["commands"]["caves"][0] != "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64":
        reject("CREATE_RELEASE_METADATA_INVALID")
    return {key: metadata[key] for key in (
        "releaseId", "platform", "image", "imageDigest", "dstBuild", "artifactDigest",
        "scriptVersion", "commands"
    )} | {"verified": True, "runtimeVerified": False}


def verify_image(metadata):
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", metadata["imageDigest"], "--format", "{{.Id}}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=30, check=False, env=environment,
        )
        if inspected.returncode != 0 or inspected.stdout.strip() != metadata["imageDigest"]:
            reject("CREATE_RELEASE_IMAGE_DIGEST_MISMATCH")

        image_id = metadata["imageDigest"]
        version_check = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh", image_id,
             "-ec", "cat /srv/dst/version.txt"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=30, check=False, env=environment,
        )
        binary_check = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh", image_id,
             "-ec", "test -x /srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64 && "
                   "ldd /srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=30, check=False, env=environment,
        )
    except FileNotFoundError:
        reject("CREATE_DOCKER_UNAVAILABLE")
    except subprocess.TimeoutExpired:
        reject("CREATE_RELEASE_IMAGE_CHECK_TIMEOUT")
    if version_check.returncode != 0 or version_check.stdout.strip() != metadata["dstBuild"]:
        reject("CREATE_RELEASE_BUILD_MISMATCH")
    if binary_check.returncode != 0 or "not found" in binary_check.stdout:
        reject("CREATE_RELEASE_BINARY_CHECK_FAILED")


def write_catalog(value):
    temporary = RELEASES / (".catalog." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            output.flush()
            os.fsync(fd)
        os.replace(temporary, CATALOG)
        directory = os.open(RELEASES, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
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


def write_metadata(path, value):
    temporary = path.parent / ("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
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


def register(release_id):
    if not valid_uuid(release_id):
        reject("CREATE_RELEASE_INPUT_INVALID")
    host = safe_json(ROOT / "host.json", 65536)
    host_id = host.get("hostId") if isinstance(host, dict) and host.get("schemaVersion") == 1 else None
    if not valid_uuid(host_id):
        reject("HOST_IDENTITY_MISMATCH")
    safe_dir(STAGING)
    safe_dir(STAGING / release_id, private=True)
    metadata = safe_json(STAGING / release_id / "release-metadata.json", MAX_METADATA_BYTES)
    entry = release_entry(metadata, release_id)
    verify_image(metadata)
    metadata["verified"] = True
    metadata["runtimeVerified"] = False
    write_metadata(STAGING / release_id / "release-metadata.json", metadata)

    safe_dir(RELEASES, create=True, private=True)
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if CATALOG.exists():
            catalog = safe_json(CATALOG, 1024 * 1024)
            if (catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id
                    or not isinstance(catalog.get("releases"), list)
                    or len(catalog["releases"]) > MAX_RELEASES):
                reject("CREATE_RELEASE_CATALOG_INVALID")
        else:
            catalog = {"schemaVersion": 1, "hostId": host_id, "releases": []}

        existing = next((item for item in catalog["releases"]
                         if isinstance(item, dict) and item.get("releaseId") == release_id), None)
        if existing is not None:
            comparable = dict(existing)
            comparable["verified"] = entry["verified"]
            comparable["runtimeVerified"] = False
            if comparable != entry:
                reject("CREATE_RELEASE_ID_CONFLICT")
            updated = dict(existing, verified=True)
            changed = updated != existing
            if changed:
                catalog["releases"] = [updated if item.get("releaseId") == release_id else item
                                       for item in catalog["releases"]]
                write_catalog(catalog)
        else:
            if len(catalog["releases"]) >= MAX_RELEASES:
                reject("CREATE_RELEASE_CATALOG_FULL")
            catalog["releases"].append(entry)
            write_catalog(catalog)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(request=None):
    try:
        release_id = request.get("releaseId") if isinstance(request, dict) else None
        if release_id is None and len(sys.argv) == 2:
            release_id = sys.argv[1]
        if release_id is None:
            reject("CREATE_RELEASE_INPUT_INVALID")
        register(release_id)
        print(json.dumps({"ok": True, "data": {"releaseId": release_id, "verified": True}}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({"ok": False, "code": "CREATE_RELEASE_CATALOG_INVALID"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__" and len(sys.argv) > 1:
    main()
