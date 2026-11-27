"""Host-level DST build check and cached update status."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
import uuid

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ROOT = Path("/var/lib/famine-hub")
UPDATES = ROOT / "updates"
CACHE = UPDATES / "dst-latest.json"
LOCK = UPDATES / "check.lock"
MAX_OUTPUT = 256 * 1024
MAX_DETAIL = 2048
STEAMCMD = "/opt/famine/steamcmd/steamcmd.sh"
BUILD_BASE = "hungerdesolate/dst-build-base:"
CHECK_CONTAINER_PREFIX = "famine-dst-check-"
STEAM_TOTAL_TIMEOUT = 3600
STEAM_STALL_TIMEOUT = 180
TOOL_VOLUME_PREFIX = "famine-dst-check-tool-"
ERROR_MESSAGES = {
    "DST_UPDATE_CHECK_FAILED": "SteamCMD 未能获取 DST 最新版本",
    "DST_UPDATE_CHECK_TIMEOUT": "SteamCMD 查询超时，请检查主机网络后重试",
    "DST_UPDATE_CHECK_INVALID": "SteamCMD 返回的 DST 版本信息无效",
    "DST_UPDATE_CHECK_UNAVAILABLE": "没有可用于检查的已验证 Release 镜像",
    "DST_UPDATE_CHECK_DOCKER_INCOMPATIBLE": "Docker Engine 29.4.2 会阻断 SteamCMD，请升级到 29.4.3 或更新版本",
    "DST_UPDATE_CHECK_INTERRUPTED": "上次检查中断，请重试",
    "DST_UPDATE_CACHE_INVALID": "更新检查缓存无效",
}


class Rejected(Exception):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def reject(code, detail=None):
    raise Rejected(code, detail)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (TypeError, ValueError, AttributeError):
        return False


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path, limit=1024 * 1024, missing=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        reject("DST_UPDATE_CACHE_INVALID")
    data = path.read_bytes()
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        reject("DST_UPDATE_CACHE_INVALID")


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
            output.flush()
            os.fsync(fd)
        os.replace(temporary, path)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def docker_env():
    environment = dict(os.environ)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(key, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    return environment


def host_id(request):
    if (request.get("schemaVersion") != 1 or not valid_uuid(request.get("requestId"))
            or not valid_uuid(request.get("hostId")) or not valid_uuid(request.get("serverId"))):
        reject("DST_UPDATE_INPUT_INVALID")
    host = read_json(ROOT / "host.json", 65536)
    if host.get("schemaVersion") != 1 or host.get("hostId") != request["hostId"]:
        reject("HOST_IDENTITY_MISMATCH")
    return request["hostId"]


def catalog_releases():
    try:
        catalog = read_json(ROOT / "releases" / "catalog.json", 1024 * 1024)
    except FileNotFoundError:
        return []
    if catalog.get("schemaVersion") != 1 or not isinstance(catalog.get("releases"), list):
        reject("DST_UPDATE_CACHE_INVALID")
    releases = []
    for item in catalog["releases"]:
        steam_build_id = item.get("steamBuildId") if isinstance(item, dict) else None
        if (not isinstance(item, dict) or not valid_uuid(item.get("releaseId"))
                or not isinstance(item.get("dstBuild"), str) or not item["dstBuild"].isdigit()
                or not isinstance(item.get("imageDigest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["imageDigest"])
                or type(item.get("verified")) is not bool
                or type(item.get("runtimeVerified")) is not bool
                or (steam_build_id is not None
                    and (not isinstance(steam_build_id, str)
                         or not re.fullmatch(r"[0-9]{1,20}", steam_build_id)))):
            continue
        releases.append(item)
    return releases


def release_summary(release):
    return {"releaseId": release["releaseId"], "build": release["dstBuild"],
            "imageDigest": release["imageDigest"], "runtimeVerified": release["runtimeVerified"],
            "createdAt": release.get("createdAt") if isinstance(release.get("createdAt"), str) else None}


def image_available(release):
    try:
        result = subprocess.run(["docker", "image", "inspect", release["imageDigest"], "--format", "{{.Id}}"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=10, check=False, env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == release["imageDigest"]


def docker_image_exists(image):
    try:
        result = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, text=True, timeout=10, check=False,
                                env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def check_image(releases):
    candidates = [item for item in releases if item["verified"]]
    candidates.sort(key=lambda item: (int(item["dstBuild"]), item.get("createdAt", "")), reverse=True)
    for release in candidates:
        if image_available(release):
            return release
    reject("DST_UPDATE_CHECK_UNAVAILABLE")


def check_tool(release):
    script_version = release.get("scriptVersion")
    if isinstance(script_version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", script_version):
        image = BUILD_BASE + script_version
        if docker_image_exists(image):
            return {"image": image, "steamcmd": "/home/steam/steamcmd.sh", "home": "/home/steam"}
    return {"image": release["imageDigest"], "steamcmd": STEAMCMD, "home": "/home/famine"}


def vdf_block(output, key):
    match = re.search(rf'"{key}"\s*\{{', output)
    if not match:
        reject("DST_UPDATE_CHECK_INVALID")
    body = output[match.end():]
    depth = 0
    for token in re.finditer(r'"(?:[^"\\]|\\.)*"|[{}]', body):
        if token[0] == "{":
            depth += 1
        elif token[0] == "}":
            if depth == 0:
                return body[:token.start()]
            depth -= 1
    reject("DST_UPDATE_CHECK_INVALID")


def parse_build(output):
    # Depot manifests also have a "public" block, but only branches carry build IDs.
    branches = vdf_block(ANSI.sub("", output), "branches")
    section = vdf_block(branches, "public")
    buildids = re.findall(r'"buildid"\s+"([0-9]+)"', section)
    unique = set(buildids)
    if len(unique) != 1:
        reject("DST_UPDATE_CHECK_INVALID")
    return unique.pop()


def docker_command(arguments, timeout=30):
    try:
        return subprocess.run(["docker", *arguments], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              timeout=timeout, check=False, env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def cleanup_container(name):
    docker_command(["rm", "-f", name])


def cleanup_stale_check_containers():
    listed = docker_command(["ps", "-a", "--filter", "name=" + CHECK_CONTAINER_PREFIX, "--quiet"])
    if listed is None or listed.returncode != 0:
        return
    for container_id in listed.stdout.split():
        if re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            docker_command(["rm", "-f", container_id])


def docker_server_version():
    try:
        result = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=10, check=False,
                                env=docker_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def detail_text(data):
    text = ANSI.sub("", data.decode("utf-8", errors="replace")).replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[-MAX_DETAIL:]


def tool_volume(tool):
    digest = hashlib.sha256(tool["image"].encode()).hexdigest()[:16]
    return TOOL_VOLUME_PREFIX + digest


def tool_mount(tool):
    if tool["steamcmd"] == "/home/steam/steamcmd.sh":
        return "/home/steam", "steamcmd.sh"
    return "/opt/famine", "steamcmd/steamcmd.sh"


def ensure_tool_volume(tool):
    volume = tool_volume(tool)
    mount_path, marker = tool_mount(tool)
    created = docker_command(["volume", "create", volume], timeout=30)
    if created is None or created.returncode != 0:
        return None
    seeded = docker_command(
        ["run", "--rm", "--user", "0:0", "-v", volume + ":/persist",
         "--entrypoint", "/bin/sh", tool["image"], "-ec",
         "if [ ! -x /persist/" + marker + " ]; then cp -a " + mount_path + "/. /persist/; fi; "
         "chown -R 1000:1000 /persist"],
        timeout=60)
    if seeded is None or seeded.returncode != 0:
        return None
    return volume


def cleanup_tool_state(tool):
    volume = tool_volume(tool)
    inspected = docker_command(["volume", "inspect", volume], timeout=10)
    if inspected is None or inspected.returncode != 0:
        return
    mount_path, _ = tool_mount(tool)
    docker_command(
        ["run", "--rm", "--user", "0:0", "-v", volume + ":" + mount_path,
         "--entrypoint", "/bin/sh", tool["image"], "-ec",
         "rm -rf " + mount_path + "/Steam/config " + mount_path + "/Steam/ssfn* "
         + mount_path + "/.steam/registry.vdf " + mount_path + "/.steam/loginusers.vdf"],
        timeout=30)


def run_steamcmd(tool, progress=None):
    version = docker_server_version()
    if version and (version == "29.4.2" or version.startswith(("29.4.2-", "29.4.2+"))):
        reject("DST_UPDATE_CHECK_DOCKER_INCOMPATIBLE")
    name = CHECK_CONTAINER_PREFIX + uuid.uuid4().hex
    mount_path, _ = tool_mount(tool)
    volume = ensure_tool_volume(tool)
    # Persist the SteamCMD tool and its home cache together so the client
    # self-update is not repeated; login config files are removed afterwards.
    home = mount_path if volume else tool["home"]
    command = ["docker", "run", "--rm", "--name", name, "--network", "bridge",
               "--user", "1000:1000", "--env", "HOME=" + home]
    if volume:
        command += ["-v", volume + ":" + mount_path]
    command += [tool["image"], tool["steamcmd"],
                "+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
                "+login", "anonymous", "+app_info_update", "1",
                "+app_info_print", "343050", "+app_info_print", "343050",
                "+logoff", "+quit"]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=docker_env())
    except FileNotFoundError:
        reject("DST_UPDATE_CHECK_FAILED", "无法启动 Docker 客户端")
    output = bytearray()
    last_progress = 0.0
    started = time.monotonic()
    last_activity = started
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            events = selector.select(timeout=1)
            if events:
                try:
                    chunk = os.read(process.stdout.fileno(), 8192)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    last_activity = time.monotonic()
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT:
                        del output[:len(output) - MAX_OUTPUT]
                    if progress and time.monotonic() - last_progress >= 3:
                        last_progress = time.monotonic()
                        progress(detail_text(bytes(output)))
                elif process.poll() is not None:
                    break
            elif process.poll() is not None:
                break
            if time.monotonic() - last_activity > STEAM_STALL_TIMEOUT:
                process.kill()
                process.wait()
                cleanup_container(name)
                reject("DST_UPDATE_CHECK_TIMEOUT",
                       detail_text(bytes(output)) or "SteamCMD 长时间没有输出")
            if time.monotonic() - started > STEAM_TOTAL_TIMEOUT:
                process.kill()
                process.wait()
                cleanup_container(name)
                reject("DST_UPDATE_CHECK_TIMEOUT", detail_text(bytes(output)) or "SteamCMD 查询超时")
        while True:
            try:
                chunk = os.read(process.stdout.fileno(), 8192)
            except (BlockingIOError, OSError):
                break
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_OUTPUT:
                del output[:len(output) - MAX_OUTPUT]
    finally:
        selector.close()
        process.stdout.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        cleanup_container(name)
        reject("DST_UPDATE_CHECK_TIMEOUT", detail_text(bytes(output)) or "SteamCMD 未正常退出")
    detail = detail_text(bytes(output))
    if process.returncode != 0:
        cleanup_container(name)
        reject("DST_UPDATE_CHECK_FAILED", detail or "SteamCMD 退出码 " + str(process.returncode))
    try:
        return parse_build(bytes(output).decode("utf-8", errors="replace"))
    except Rejected as failure:
        if not failure.detail:
            failure.detail = detail
        raise


def read_cache(host):
    try:
        cache = read_json(CACHE, 65536)
    except FileNotFoundError:
        return None
    except Rejected:
        return None
    if cache.get("schemaVersion") != 1 or cache.get("hostId") != host:
        return None
    return cache


def lock_held():
    try:
        fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    return False


def error_message(code):
    return ERROR_MESSAGES.get(code, "无法检查 DST 最新版本")


def status_data(host, releases):
    cache = read_cache(host) or {}
    latest = None
    build = cache.get("latestBuild")
    success_at = cache.get("lastSuccessAt") or cache.get("checkedAt")
    if isinstance(build, str) and build.isdigit() and isinstance(success_at, str):
        latest = {"build": build, "checkedAt": success_at, "source": "steamCmd"}
    checking = False
    stage = None
    stage_detail = None
    started_at = None
    last_error = None
    if cache.get("result") == "running":
        started_at = cache.get("startedAt") if isinstance(cache.get("startedAt"), str) else cache.get("checkedAt")
        if lock_held():
            checking = True
            stage = cache.get("stage") if isinstance(cache.get("stage"), str) else None
            stage_detail = cache.get("stageDetail") if isinstance(cache.get("stageDetail"), str) else None
        else:
            last_error = {"code": "DST_UPDATE_CHECK_INTERRUPTED",
                          "message": error_message("DST_UPDATE_CHECK_INTERRUPTED"),
                          "detail": None,
                          "at": started_at or now()}
    elif cache.get("result") == "failed":
        code = cache.get("errorCode") or "DST_UPDATE_CHECK_FAILED"
        last_error = {"code": code, "message": error_message(code),
                      "detail": cache.get("errorDetail") if isinstance(cache.get("errorDetail"), str) else None,
                      "at": cache.get("checkedAt") or now()}
    candidates = [release_summary(item) for item in releases
                  if item["verified"] and latest is not None
                  and item.get("steamBuildId") == latest["build"]]
    return {"schemaVersion": 1, "requestId": None, "target": None,
            "latest": latest, "candidateReleases": candidates, "lastCheckError": last_error,
            "checking": checking, "stage": stage, "stageDetail": stage_detail, "startedAt": started_at}


def check(request):
    host = host_id(request)
    releases = catalog_releases()
    UPDATES.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(UPDATES, 0o700)
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    locked = False
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            return status_data(host, releases)
        previous = read_cache(host) or {}
        started = now()
        state = {"schemaVersion": 1, "hostId": host, "checkedAt": started, "startedAt": started,
                 "lastSuccessAt": previous.get("lastSuccessAt") or previous.get("checkedAt"),
                 "source": "steamCmd", "sourceImageDigest": previous.get("sourceImageDigest"),
                 "latestBuild": previous.get("latestBuild"), "result": "running",
                 "stage": "image", "stageDetail": "选择检查镜像", "errorCode": None}
        atomic_json(CACHE, state)
        cleanup_stale_check_containers()

        def progress(detail):
            line = detail.splitlines()[-1][:200] if detail else ""
            if line and line != state.get("stageDetail"):
                state["stageDetail"] = line
                try:
                    atomic_json(CACHE, state)
                except OSError:
                    pass

        tool = None
        try:
            release = check_image(releases)
            tool = check_tool(release)
            state.update({"stage": "steamcmd", "stageDetail": "启动 SteamCMD",
                          "sourceImageDigest": release["imageDigest"], "checkImage": tool["image"]})
            atomic_json(CACHE, state)
            build = run_steamcmd(tool, progress)
            checked = now()
            atomic_json(CACHE, {"schemaVersion": 1, "hostId": host, "checkedAt": checked,
                                "startedAt": started, "lastSuccessAt": checked, "source": "steamCmd",
                                "sourceImageDigest": release["imageDigest"], "checkImage": tool["image"],
                                "latestBuild": build, "result": "ok", "stage": "finalize",
                                "stageDetail": None, "errorCode": None})
        except Rejected as failure:
            atomic_json(CACHE, {"schemaVersion": 1, "hostId": host, "checkedAt": now(),
                                "startedAt": started, "lastSuccessAt": state.get("lastSuccessAt"),
                                "source": "steamCmd", "sourceImageDigest": state.get("sourceImageDigest"),
                                "checkImage": state.get("checkImage"),
                                "latestBuild": state.get("latestBuild"), "result": "failed",
                                "stage": "finalize", "stageDetail": None,
                                "errorCode": failure.code, "errorDetail": failure.detail})
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as cause:
            atomic_json(CACHE, {"schemaVersion": 1, "hostId": host, "checkedAt": now(),
                                "startedAt": started, "lastSuccessAt": state.get("lastSuccessAt"),
                                "source": "steamCmd", "sourceImageDigest": state.get("sourceImageDigest"),
                                "checkImage": state.get("checkImage"),
                                "latestBuild": state.get("latestBuild"), "result": "failed",
                                "stage": "finalize", "stageDetail": None,
                                "errorCode": "DST_UPDATE_CHECK_FAILED",
                                "errorDetail": str(cause)[:MAX_DETAIL]})
            raise
        finally:
            if tool is not None:
                cleanup_tool_state(tool)
    finally:
        if locked:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return status_data(host, releases)


def main(request):
    host = host_id(request)
    releases = catalog_releases()
    if request.get("action") == "status":
        data = status_data(host, releases)
    elif request.get("action") == "check":
        data = check(request)
    else:
        reject("DST_UPDATE_INPUT_INVALID")
    data["requestId"] = request["requestId"]
    data["target"] = {"serverId": request["serverId"], "hostId": host}
    print(json.dumps({"ok": True, "data": data}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main(json.loads(sys.stdin.buffer.read(65537)))
    except Rejected as failure:
        print(json.dumps({"ok": False, "code": failure.code}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print(json.dumps({"ok": False, "code": "DST_UPDATE_CHECK_FAILED"}, separators=(",", ":")), flush=True)
        raise SystemExit(1)
