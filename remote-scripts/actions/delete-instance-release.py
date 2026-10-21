"""Delete one unreferenced Release registration and its exact cached image."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

ROOT = Path("/var/lib/famine-hub")
CATALOG = ROOT / "releases" / "catalog.json"
REGISTRY = ROOT / "registry.json"
RELEASES = ROOT / "releases"
STAGING = ROOT / "staging"
REGISTRY_LOCK = ROOT / "locks" / "registry.lock"
CATALOG_LOCK = RELEASES / "catalog.lock"
MAX_BYTES = 1024 * 1024


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


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def safe_json(path, limit=MAX_BYTES):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("RELEASE_DELETE_STATE_INVALID")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            reject("RELEASE_DELETE_STATE_INVALID")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        reject("RELEASE_DELETE_STATE_INVALID")
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("RELEASE_DELETE_STATE_INVALID")


def write_catalog(catalog):
    temporary = RELEASES / (".catalog." + str(uuid.uuid4()) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode())
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


def lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        os.close(fd)
        reject("RELEASE_DELETE_STATE_INVALID")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def docker(command, timeout=15):
    env = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        env.pop(name, None)
    env.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout, check=False, env=env)
    except FileNotFoundError:
        reject("RELEASE_DELETE_DOCKER_UNAVAILABLE")
    except subprocess.TimeoutExpired:
        reject("RELEASE_DELETE_DOCKER_TIMEOUT")
    if len(result.stdout) > 65536:
        reject("RELEASE_DELETE_DOCKER_RESPONSE_INVALID")
    return result


def image_remove_error(stderr):
    message = stderr.lower() if isinstance(stderr, str) else ""
    if "no such image" in message or "image not known" in message:
        return "IMAGE_ALREADY_ABSENT"
    if "used by" in message and "container" in message:
        return "RELEASE_DELETE_IMAGE_IN_USE"
    if "permission denied" in message or "access denied" in message:
        return "RELEASE_DELETE_DOCKER_PERMISSION_DENIED"
    if "cannot connect" in message or "connection refused" in message:
        return "RELEASE_DELETE_DOCKER_UNAVAILABLE"
    return "RELEASE_DELETE_IMAGE_FAILED"


def image_containers(image_digest):
    listed = docker(["docker", "container", "ls", "--all", "--quiet", "--no-trunc"])
    ids = listed.stdout.splitlines()
    if listed.returncode != 0 or len(ids) > 400 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids):
        reject("RELEASE_DELETE_DOCKER_RESPONSE_INVALID")
    if not ids:
        return []
    inspected = docker(["docker", "container", "inspect", "--format", "{{.Image}}", *ids])
    images = inspected.stdout.splitlines()
    if inspected.returncode != 0 or len(images) != len(ids):
        reject("RELEASE_DELETE_DOCKER_RESPONSE_INVALID")
    return [container_id for container_id, image_id in zip(ids, images) if image_id == image_digest]


def release_directory(release_id):
    path = STAGING / release_id
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("RELEASE_DELETE_STATE_INVALID")
    for child in path.rglob("*"):
        info = child.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or info.st_mode & 0o077
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
            reject("RELEASE_DELETE_STATE_INVALID")
    return path


def inspect_release(request):
    host_id, release_id = request.get("hostId"), request.get("releaseId")
    if not valid_uuid(host_id) or not valid_uuid(release_id):
        reject("RELEASE_DELETE_INPUT_INVALID")
    host = safe_json(ROOT / "host.json", 65536)
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        reject("HOST_IDENTITY_MISMATCH")
    catalog = safe_json(CATALOG)
    if (catalog.get("schemaVersion") != 1 or catalog.get("hostId") != host_id
            or not isinstance(catalog.get("releases"), list)):
        reject("CREATE_RELEASE_CATALOG_INVALID")
    entry = next((item for item in catalog["releases"]
                  if isinstance(item, dict) and item.get("releaseId") == release_id), None)
    if entry is None:
        reject("RELEASE_NOT_FOUND")
    registry = safe_json(REGISTRY)
    if registry.get("schemaVersion") != 1 or registry.get("hostId") != host_id or not isinstance(registry.get("instances"), list):
        reject("RELEASE_DELETE_STATE_INVALID")
    instance_refs = {item.get("instanceId") for item in registry["instances"]
                     if isinstance(item, dict) and item.get("lifecycle") != "retired"
                     and item.get("dstReleaseId") == release_id}
    instances_root = ROOT / "instances"
    if instances_root.exists():
        info = instances_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            reject("RELEASE_DELETE_STATE_INVALID")
        for instance_dir in instances_root.iterdir():
            if instance_dir.is_symlink() or not instance_dir.is_dir() or not valid_uuid(instance_dir.name):
                continue
            for filename in ("manifest.json", "active-release.json"):
                pointer = instance_dir / filename
                if pointer.exists() and safe_json(pointer, 65536).get("dstReleaseId") == release_id:
                    instance_refs.add(instance_dir.name)
    if any(not valid_uuid(value) for value in instance_refs):
        reject("RELEASE_DELETE_STATE_INVALID")
    task_refs = []
    tasks = ROOT / "tasks"
    if tasks.exists():
        for task_dir in tasks.iterdir():
            if not task_dir.is_dir() or task_dir.is_symlink() or not valid_uuid(task_dir.name):
                continue
            release_path = task_dir / "release.json"
            if release_path.exists() and safe_json(release_path, 65536).get("releaseId") == release_id:
                status_path = task_dir / "status.json"
                status = safe_json(status_path, 65536).get("status") if status_path.exists() else "unknown"
                if status not in ("succeeded", "failed", "cancelled"):
                    task_refs.append(task_dir.name)
    same_image = [item.get("releaseId") for item in catalog["releases"]
                  if isinstance(item, dict) and item.get("imageDigest") == entry.get("imageDigest")]
    containers = image_containers(entry.get("imageDigest"))
    shared_image = len(same_image) > 1
    return {"hostId": host_id, "releaseId": release_id, "dstBuild": entry.get("dstBuild"),
            "image": entry.get("image"), "imageDigest": entry.get("imageDigest"),
            "catalogDigest": digest(catalog), "instanceReferences": sorted(instance_refs),
            "taskReferences": task_refs, "containerReferences": containers,
            "sharedImage": shared_image,
            "allowed": not (instance_refs or task_refs or (containers and not shared_image))}


def remove(request):
    if os.geteuid() != 0:
        reject("ROOT_REQUIRED")
    inspected = inspect_release(request)
    if request.get("catalogDigest") != inspected["catalogDigest"]:
        reject("RELEASE_CATALOG_CHANGED")
    if request.get("typedName") != inspected["dstBuild"]:
        reject("RELEASE_CONFIRMATION_MISMATCH")
    if not inspected["allowed"]:
        reject("RELEASE_IN_USE")
    registry_fd = lock(ROOT / "locks" / "registry.lock")
    catalog_fd = lock(CATALOG_LOCK)
    try:
        inspected = inspect_release(request)
        if request.get("catalogDigest") != inspected["catalogDigest"]:
            reject("RELEASE_CATALOG_CHANGED")
        if request.get("typedName") != inspected["dstBuild"]:
            reject("RELEASE_CONFIRMATION_MISMATCH")
        if not inspected["allowed"]:
            reject("RELEASE_IN_USE")
        catalog = safe_json(CATALOG)
        old_catalog = json.loads(json.dumps(catalog))
        image_shared = inspected["sharedImage"]
        path = release_directory(inspected["releaseId"])
        catalog["releases"] = [item for item in catalog["releases"] if item.get("releaseId") != inspected["releaseId"]]
        write_catalog(catalog)

        image_removed = False
        if not image_shared:
            removed = docker(["docker", "image", "rm", inspected["imageDigest"]])
            image_removed = removed.returncode == 0
            if not image_removed:
                failure_code = image_remove_error(getattr(removed, "stderr", ""))
                if failure_code == "IMAGE_ALREADY_ABSENT":
                    image_removed = True
                else:
                    write_catalog(old_catalog)
                    reject(failure_code)
        staging_removed = True
        if path is not None:
            try:
                shutil.rmtree(path)
            except OSError:
                staging_removed = False
        return {"releaseId": inspected["releaseId"], "catalogDigest": digest(catalog),
                "imageRemoved": image_removed, "imageShared": image_shared,
                "stagingRemoved": staging_removed}
    finally:
        fcntl.flock(catalog_fd, fcntl.LOCK_UN)
        os.close(catalog_fd)
        fcntl.flock(registry_fd, fcntl.LOCK_UN)
        os.close(registry_fd)


def main(request):
    try:
        action = request.get("action") if isinstance(request, dict) else None
        data = inspect_release(request) if action == "preview" else remove(request) if action == "delete" else reject("RELEASE_DELETE_INPUT_INVALID")
        print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        print(json.dumps({"ok": False, "code": "RELEASE_DELETE_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)


if __name__ == "__main__" and len(sys.argv) > 1:
    main(json.loads(os.read(0, 65536).decode("utf-8")))
