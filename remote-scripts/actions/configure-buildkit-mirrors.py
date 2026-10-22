#!/usr/bin/env python3
"""Preserve Docker daemon settings and configure mirrors for an isolated BuildKit builder."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from urllib.parse import urlsplit

DEFAULT_MIRRORS = [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.ccs.tencentyun.com",
]


def load_daemon_config(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}, None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("daemon.json must be a regular file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_size > 1024 * 1024):
            raise ValueError("daemon.json changed or exceeds the size limit")
        with os.fdopen(fd, "rb", closefd=False) as source:
            value = json.loads(source.read(1024 * 1024 + 1).decode("utf-8"))
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise ValueError("daemon.json must contain a JSON object")
    return value, info


def atomic_write(path, data, mode, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        if owner is not None:
            os.fchown(fd, owner.st_uid, owner.st_gid)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def validate_mirrors(value):
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError("registry-mirrors must be a nonempty URL list")
    for mirror in value:
        if not isinstance(mirror, str) or len(mirror) > 2048:
            raise ValueError("registry-mirrors contains an invalid URL")
        parsed = urlsplit(mirror)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("registry-mirrors contains an invalid URL")
    return value


def configure(daemon_json, buildkit_config):
    daemon_path = Path(daemon_json)
    buildkit_path = Path(buildkit_config)
    daemon, info = load_daemon_config(daemon_path)
    mirrors = daemon.get("registry-mirrors")
    daemon_updated = mirrors is None or mirrors == []
    mirrors = validate_mirrors(DEFAULT_MIRRORS if daemon_updated else mirrors)

    if daemon_updated:
        daemon["registry-mirrors"] = mirrors
        atomic_write(daemon_path, (json.dumps(daemon, indent=2) + "\n").encode("utf-8"),
                    stat.S_IMODE(info.st_mode) if info else 0o644, info)

    config = '[registry."docker.io"]\n  mirrors = ' + json.dumps(mirrors, ensure_ascii=False) + "\n"
    atomic_write(buildkit_path, config.encode("utf-8"), 0o644)
    return "daemon-updated" if daemon_updated else "configured"


def main(argv):
    if len(argv) != 3:
        print("usage: configure-buildkit-mirrors.py DAEMON_JSON BUILDKIT_CONFIG", file=sys.stderr)
        return 2
    try:
        print(configure(argv[1], argv[2]))
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print("Docker registry mirror configuration failed: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
