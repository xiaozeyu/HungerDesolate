"""Create and inspect identity-bound, secret-free instance backup archives."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
from datetime import datetime, timezone
import uuid

MAX_FILES = 200_000
MAX_BYTES = 64 * 1024 * 1024 * 1024
MAX_METADATA = 2 * 1024 * 1024
EXCLUDED_NAMES = {"cluster_token.txt", "adminlist.txt", "blocklist.txt", "whitelist.txt",
                  "server_log.txt", "server_chat_log.txt", "client_log.txt"}


class BackupRejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reject(code):
    raise BackupRejected(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest_file(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            value.update(chunk)
    return value.hexdigest()


def checked_open(path, expected_device, expected_inode):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    opened = os.fstat(fd)
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (expected_device, expected_inode)):
        os.close(fd)
        reject("BACKUP_SOURCE_CHANGED")
    return fd, opened


def checked_files(source_root, include_paths=None, overrides=None):
    root = Path(source_root)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink():
        reject("BACKUP_SOURCE_INVALID")
    entries = []
    total = 0
    selected = include_paths or ["."]
    overrides = overrides or {}
    def add_file(path, relative):
        nonlocal total
        name = path.name
        if name in EXCLUDED_NAMES:
            return
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            reject("BACKUP_SOURCE_UNSAFE")
        if not safe_member_name(relative):
            reject("BACKUP_SOURCE_UNSAFE")
        if relative in overrides:
            payload = overrides[relative]
            total += len(payload)
            if len(entries) >= MAX_FILES or total > MAX_BYTES:
                reject("BACKUP_SIZE_LIMIT")
            entries.append({"path": relative, "sizeBytes": len(payload),
                            "sha256": None, "mode": 0o600, "_content": payload})
            return
        total += info.st_size
        if len(entries) >= MAX_FILES or total > MAX_BYTES:
            reject("BACKUP_SIZE_LIMIT")
        entries.append({"path": relative, "sizeBytes": info.st_size,
                        "sha256": None, "mode": stat.S_IMODE(info.st_mode) & 0o755,
                        "_device": info.st_dev, "_inode": info.st_ino})

    for relative_root in selected:
        if relative_root == ".":
            scan_root = root
            archive_prefix = ""
        elif safe_member_name(relative_root):
            scan_root = root
            parts = PurePosixPath(relative_root).parts
            for part in parts[:-1]:
                scan_root = scan_root / part
                info = scan_root.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    reject("BACKUP_SOURCE_UNSAFE")
            selected_path = scan_root / parts[-1]
            selected_info = selected_path.lstat()
            if stat.S_ISREG(selected_info.st_mode):
                add_file(selected_path, relative_root)
                continue
            if not stat.S_ISDIR(selected_info.st_mode):
                reject("BACKUP_SOURCE_UNSAFE")
            scan_root = selected_path
            archive_prefix = relative_root + "/"
        else:
            reject("BACKUP_SOURCE_UNSAFE")
        for current, directories, files in os.walk(scan_root, followlinks=False):
            current_path = Path(current)
            for name in list(directories):
                path = current_path / name
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    reject("BACKUP_SOURCE_UNSAFE")
            for name in files:
                path = current_path / name
                relative = archive_prefix + path.relative_to(scan_root).as_posix()
                add_file(path, relative)
    entries.sort(key=lambda item: item["path"])
    return entries, total


def safe_member_name(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and bool(path.parts) and path.as_posix() == name
            and all(part not in ("", ".", "..") for part in path.parts))


def create_archive(source_root, output_path, identity, include_paths=None, overrides=None):
    if (not isinstance(identity, dict) or identity.get("schemaVersion") != 1
            or not isinstance(identity.get("hostId"), str)
            or not isinstance(identity.get("instanceId"), str)
            or not isinstance(identity.get("backupId"), str)
            or identity.get("captureMode") not in ("cold", "quasiHot")
            or identity.get("runtimeStatus") not in ("running", "stopped", "degraded", "unknown")
            or type(identity.get("runtimeVerified")) is not bool):
        reject("BACKUP_IDENTITY_INVALID")
    if (identity["captureMode"] == "cold"
            and (identity["runtimeStatus"] != "stopped" or not identity["runtimeVerified"])):
        reject("BACKUP_COLD_REQUIRES_VERIFIED_STOP")
    started_at = datetime.now(timezone.utc).isoformat()
    entries, total = checked_files(source_root, include_paths, overrides)
    manifest = None
    output = Path(output_path)
    if output.exists() or output.is_symlink():
        reject("BACKUP_OUTPUT_EXISTS")
    temporary = output.with_name("." + output.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tarfile.open(temporary, "x:gz", format=tarfile.PAX_FORMAT) as archive:
            for item in entries:
                payload = item.get("_content")
                if payload is not None:
                    info = tarfile.TarInfo("data/" + item["path"])
                    info.size = len(payload)
                    info.mode = item["mode"]
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(payload))
                    item["sha256"] = hashlib.sha256(payload).hexdigest()
                    continue
                path = Path(source_root) / item["path"]
                fd, opened = checked_open(path, item["_device"], item["_inode"])
                try:
                    delta = opened.st_size - item["sizeBytes"]
                    total += delta
                    if total > MAX_BYTES:
                        reject("BACKUP_SIZE_LIMIT")
                    item["sizeBytes"] = opened.st_size
                    item["mode"] = stat.S_IMODE(opened.st_mode) & 0o755
                    info = tarfile.TarInfo("data/" + item["path"])
                    info.size = opened.st_size
                    info.mode = item["mode"]
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    value = hashlib.sha256()
                    class HashingReader:
                        def __init__(self, stream):
                            self.stream = stream
                            self.remaining = opened.st_size
                        def read(self, length=-1):
                            if self.remaining <= 0:
                                return b""
                            length = self.remaining if length < 0 else min(length, self.remaining)
                            payload = self.stream.read(length)
                            self.remaining -= len(payload)
                            value.update(payload)
                            return payload
                    reader = HashingReader(os.fdopen(fd, "rb", closefd=False))
                    archive.addfile(info, reader)
                    if reader.remaining != 0:
                        reject("BACKUP_SOURCE_CHANGED")
                    item["sha256"] = value.hexdigest()
                finally:
                    os.close(fd)
            public_entries = [{key: value for key, value in item.items() if not key.startswith("_")}
                              for item in entries]
            manifest = {"schemaVersion": 1, **identity, "captureStartedAt": started_at,
                        "captureCompletedAt": datetime.now(timezone.utc).isoformat(),
                        "captureSemantics": ("stoppedBeforeRead" if identity["captureMode"] == "cold"
                                             else "onDiskBestEffort"),
                        "fileCount": len(entries), "sizeBytes": total, "files": public_entries}
            raw = canonical(manifest)
            info = tarfile.TarInfo("backup-manifest.json")
            info.size = len(raw)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(raw))
        with temporary.open("rb") as bundle:
            os.fsync(bundle.fileno())
        checksum = digest_file(temporary)
        inspect_archive(temporary, identity["hostId"], identity["instanceId"], checksum)
        os.link(temporary, output, follow_symlinks=False)
        temporary.unlink()
        return {"manifest": manifest, "checksum": checksum,
                "archiveSizeBytes": output.stat().st_size}
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def inspect_archive(archive_path, expected_host_id, expected_instance_id, expected_checksum=None):
    path = Path(archive_path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
        reject("BACKUP_ARCHIVE_INVALID")
    checksum = digest_file(path)
    if expected_checksum is not None and checksum != expected_checksum:
        reject("BACKUP_CHECKSUM_MISMATCH")
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_FILES + 1:
                reject("BACKUP_SIZE_LIMIT")
            by_name = {}
            for member in members:
                if member.name in by_name or not (member.isfile() or member.isdir()):
                    reject("BACKUP_ARCHIVE_UNSAFE")
                if member.name != "backup-manifest.json" and not (
                        member.name.startswith("data/") and safe_member_name(member.name[5:])):
                    reject("BACKUP_ARCHIVE_UNSAFE")
                by_name[member.name] = member
            manifest_member = by_name.get("backup-manifest.json")
            if manifest_member is None or manifest_member.size > MAX_METADATA:
                reject("BACKUP_ARCHIVE_INVALID")
            stream = archive.extractfile(manifest_member)
            manifest_bytes = stream.read(MAX_METADATA + 1) if stream else b""
            if len(manifest_bytes) > MAX_METADATA:
                reject("BACKUP_ARCHIVE_INVALID")
            manifest = json.loads(manifest_bytes)
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if (manifest.get("schemaVersion") != 1 or manifest.get("hostId") != expected_host_id
                    or manifest.get("instanceId") != expected_instance_id
                    or not isinstance(files, list) or len(files) != len(members) - 1):
                reject("BACKUP_TARGET_MISMATCH")
            total = 0
            expected_names = set()
            for item in files:
                if (not isinstance(item, dict) or not safe_member_name(item.get("path"))
                        or type(item.get("sizeBytes")) is not int or item["sizeBytes"] < 0
                        or not isinstance(item.get("sha256"), str)
                        or len(item["sha256"]) != 64
                        or any(character not in "0123456789abcdef" for character in item["sha256"])
                        or type(item.get("mode")) is not int or item["mode"] & ~0o777):
                    reject("BACKUP_ARCHIVE_INVALID")
                member_name = "data/" + item["path"]
                if member_name in expected_names:
                    reject("BACKUP_ARCHIVE_INVALID")
                expected_names.add(member_name)
                total += item["sizeBytes"]
                if total > MAX_BYTES:
                    reject("BACKUP_SIZE_LIMIT")
            if expected_names != set(by_name) - {"backup-manifest.json"}:
                reject("BACKUP_ARCHIVE_INVALID")
            if manifest.get("fileCount") != len(files) or manifest.get("sizeBytes") != total:
                reject("BACKUP_ARCHIVE_INVALID")
            for item in files:
                member = by_name["data/" + item["path"]]
                if not member.isfile() or member.size != item["sizeBytes"]:
                    reject("BACKUP_ARCHIVE_INVALID")
                content = archive.extractfile(member)
                value = hashlib.sha256()
                while True:
                    chunk = content.read(1024 * 1024)
                    if not chunk:
                        break
                    value.update(chunk)
                if value.hexdigest() != item.get("sha256"):
                    reject("BACKUP_CHECKSUM_MISMATCH")
            return {"manifest": manifest, "checksum": checksum,
                    "archiveSizeBytes": info.st_size}
    except (OSError, tarfile.TarError, ValueError, TypeError, KeyError, AttributeError):
        reject("BACKUP_ARCHIVE_INVALID")


def extract_to_staging(archive_path, staging_path, expected_host_id, expected_instance_id,
                       expected_checksum):
    result = inspect_archive(archive_path, expected_host_id, expected_instance_id, expected_checksum)
    staging = Path(staging_path)
    if staging.exists() or staging.is_symlink():
        reject("BACKUP_STAGING_NOT_EMPTY")
    staging.mkdir(mode=0o700, parents=False)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for item in result["manifest"]["files"]:
                member = archive.getmember("data/" + item["path"])
                destination = staging.joinpath(*PurePosixPath(item["path"]).parts)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(destination, flags, 0o600)
                with os.fdopen(fd, "wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(destination, item["mode"] & 0o700)
        return result
    except (OSError, tarfile.TarError, KeyError):
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        reject("BACKUP_RESTORE_STAGING_FAILED")
