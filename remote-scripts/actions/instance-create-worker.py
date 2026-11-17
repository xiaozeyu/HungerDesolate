"""Persistent instance-create worker until game deployment is validated."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from datetime import datetime, timezone

BRIDGE_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
BRIDGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def task_error(task_id, target, code="DEPLOYMENT_VALIDATION_REQUIRED", category="stagingVerify",
               message="游戏镜像和 Compose 部署兼容性尚未验证", details=None):
    return {"schemaVersion": 1, "code": code, "category": category,
            "operation": "instanceCreate", "requestId": None,
            "target": target, "message": message,
            "retryable": False, "remoteChanged": True, "taskId": task_id,
            "recoverySteps": ["完成阶段 0 验证后重新预览并继续创建", "或使用创建清理预览释放现场"],
            "details": details or {}}


def success_result(host_id, instance_id, deployment_id):
    return {"preparedHost": None, "verifiedRevision": None, "verification": "passed",
            "remoteChanged": True, "backupId": None, "deploymentId": deployment_id,
            "invalidate": [{"hostId": host_id, "instanceId": instance_id,
                            "domains": ["instances", "overview", "runtime"]}],
            "recoveryActions": []}


def captured_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resource_plan(manifest, release):
    project = manifest["composeProject"]
    mounts = ["config", "data/Master", "mods/releases/" + release["releaseId"] + "/Master",
              "bridge/Master"]
    if manifest.get("cavesEnabled"):
        mounts.extend(["data/Caves", "mods/releases/" + release["releaseId"] + "/Caves",
                       "bridge/Caves"])
    return {"schemaVersion": 1, "instanceId": manifest["instanceId"],
            "composeProject": project, "network": project + "_default",
            "bindMounts": mounts, "publishedPorts": manifest["ports"],
            "composeConfigVerified": True}


def compose_config(controller, compose_path, project):
    environment = dict(controller["os"].environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = controller["subprocess"].run(
            ["docker", "compose", "-p", project, "-f", str(compose_path), "config", "--quiet"],
            stdin=controller["subprocess"].DEVNULL, stdout=controller["subprocess"].PIPE,
            stderr=controller["subprocess"].PIPE, timeout=30, check=False, env=environment,
            text=True, errors="replace")
    except FileNotFoundError:
        return "CREATE_COMPOSE_CONFIG_UNAVAILABLE", None
    except controller["subprocess"].TimeoutExpired:
        return "CREATE_COMPOSE_CONFIG_TIMEOUT", None
    if result.returncode == 0:
        return None, None
    diagnostic = "\n".join(part.strip() for part in (result.stderr, result.stdout) if part and part.strip())
    diagnostic = "".join(char for char in diagnostic if char in "\n\t" or ord(char) >= 32)[:2000]
    return "CREATE_COMPOSE_CONFIG_FAILED", diagnostic or None


def reject(code):
    raise RuntimeError(code)


def safe_directory(path, controller, create=False, allow_runtime=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        path.mkdir(mode=0o700)
        info = path.lstat()
    owners = (0, os.geteuid(), 1000) if allow_runtime else (0, os.geteuid())
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
            or info.st_mode & 0o077):
        reject("CREATE_INSTANCE_PATH_UNSAFE")
    return info


def manifest(admission, request_record, player_bridge):
    settings = request_record.get("input")
    if not isinstance(settings, dict) or not isinstance(settings.get("cavesEnabled"), bool):
        reject("CREATE_REQUEST_INVALID")
    return {"schemaVersion": 1, "hostId": admission["hostId"],
            "instanceId": admission["instanceId"],
            "displayName": str(settings["displayName"]).strip(), "configRevision": 0,
            "composeProject": "fh_" + admission["instanceId"].replace("-", ""),
            "cavesEnabled": settings["cavesEnabled"], "autostart": settings["autostart"],
            "ports": admission["ports"],
            "desiredDigests": {"world": None, "mods": None},
            "deploymentId": None, "dstReleaseId": None, "modsReleaseId": None,
            "playerBridge": player_bridge,
            "worldAppliedRevision": None, "modsAppliedRevision": None}


def bridge_build(request_record):
    build = request_record.get("bridge")
    if (not isinstance(build, dict) or set(build) != {"version", "digest", "files"}
            or not isinstance(build.get("version"), str)
            or BRIDGE_VERSION_RE.fullmatch(build["version"]) is None
            or not isinstance(build.get("digest"), str)
            or BRIDGE_DIGEST_RE.fullmatch(build["digest"]) is None
            or not isinstance(build.get("files"), dict)
            or set(build["files"]) != {"modinfo.lua", "modmain.lua"}):
        reject("CREATE_BRIDGE_PAYLOAD_INVALID")
    decoded = {}
    for name in ("modinfo.lua", "modmain.lua"):
        value = build["files"][name]
        if not isinstance(value, str):
            reject("CREATE_BRIDGE_PAYLOAD_INVALID")
        try:
            data = base64.b64decode(value, validate=True)
            data.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            reject("CREATE_BRIDGE_PAYLOAD_INVALID")
        if not data or len(data) > 256 * 1024:
            reject("CREATE_BRIDGE_PAYLOAD_INVALID")
        decoded[name] = data
    hasher = hashlib.sha256()
    for name in ("modinfo.lua", "modmain.lua"):
        hasher.update(name.encode("ascii") + b"\0" + decoded[name])
    if "sha256:" + hasher.hexdigest() != build["digest"]:
        reject("CREATE_BRIDGE_DIGEST_MISMATCH")
    return build["version"], build["digest"], decoded


def prepare_bridge(task_dir, instance, caves, request_record, controller, existing_manifest):
    version, digest, files = bridge_build(request_record)
    overrides_ns = {"__name__": "mods_overrides"}
    exec(compile((task_dir / "overrides-source.py").read_text(),
                "<mods-overrides>", "exec"), overrides_ns)
    bridge_root = instance / "bridge"
    safe_directory(bridge_root, controller, create=True, allow_runtime=True)
    os.chown(bridge_root, 1000, 1000)
    os.chmod(bridge_root, 0o700)
    for shard in ("Master", "Caves") if caves else ("Master",):
        bridge = bridge_root / shard
        safe_directory(bridge, controller, create=True, allow_runtime=True)
        os.chown(bridge, 1000, 1000)
        os.chmod(bridge, 0o700)
        for name in ("modinfo.lua", "modmain.lua"):
            path = bridge / name
            controller["atomic_bytes"](path, files[name], 0o644, allow_runtime=True)
            os.chown(path, 0, 0)
            os.chmod(path, 0o644)
        config = instance / "config" / "Cluster_1" / shard
        for current in (instance / "config", instance / "config" / "Cluster_1", config):
            safe_directory(current, controller, create=True, allow_runtime=True)
            os.chown(current, 1000, 1000)
            os.chmod(current, 0o700)
        overrides = config / "modoverrides.lua"
        controller["atomic_bytes"](overrides, overrides_ns["modoverrides"]([], True), 0o600,
                                   allow_runtime=True)
        os.chown(overrides, 1000, 1000)
        os.chmod(overrides, 0o600)
    installed_at = None
    if isinstance(existing_manifest, dict):
        previous = existing_manifest.get("playerBridge")
        if (isinstance(previous, dict) and previous.get("version") == version
                and previous.get("digest") == digest
                and isinstance(previous.get("installedAt"), str)
                and previous["installedAt"]):
            installed_at = previous["installedAt"]
    return {"enabled": True, "version": version, "digest": digest,
            "installedAt": installed_at or captured_at()}


def prepare_instance(task_dir, admission, request_record, controller):
    root = controller["ROOT"]
    instance = root / "instances" / admission["instanceId"]
    safe_directory(root / "instances", controller, create=True)
    safe_directory(instance, controller, create=True)
    settings = request_record["input"]
    caves = settings["cavesEnabled"]
    directories = [
        "config", "desired", "desired-secrets",
        "mods", "mods/releases", "data", "data/Master", "data/Master/config", "logs", "staging",
        "staging/" + task_dir.name, "tasks", "tasks/" + task_dir.name, "locks", "bridge",
    ]
    if caves:
        directories.extend(["data/Caves", "data/Caves/config", "mods/releases/Master",
                            "mods/releases/Caves", "bridge/Caves"])
    else:
        directories.append("mods/releases/Master")
    directories.append("bridge/Master")
    for relative in directories:
        safe_directory(instance / relative, controller, create=True,
                       allow_runtime=relative.startswith("data/") or relative.startswith("bridge"))
    manifest_path = instance / "manifest.json"
    existing = controller["read_json"](manifest_path, 65536) if manifest_path.exists() else None
    player_bridge = prepare_bridge(task_dir, instance, caves, request_record, controller, existing)
    expected = manifest(admission, request_record, player_bridge)
    if existing is not None:
        if existing != expected:
            reject("CREATE_MANIFEST_CONFLICT")
    else:
        controller["write_json"](manifest_path, expected)
    controller["write_json"](instance / "tasks" / task_dir.name / "task-ref.json",
                              {"schemaVersion": 1, "taskId": task_dir.name,
                               "instanceId": admission["instanceId"]})
    return instance


def main(task_path):
    task_dir = Path(task_path)
    task_id = task_dir.name
    if len(task_id) != 36 or task_dir.parent.name != "tasks":
        return 2
    info = task_dir.lstat()
    if (os.geteuid() != 0 or not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o077):
        return 2
    controller = __import__("runpy").run_path(str(task_dir / "controller-runtime.py"))
    release_module = __import__("runpy").run_path(str(task_dir / "instance-release.py"))
    docker_module = __import__("runpy").run_path(str(task_dir / "instance-docker.py"))
    update_status = controller["update_status"]
    load_status = controller["load_status"]
    read_json = controller["read_json"]
    admission = read_json(task_dir / "admission.json", 65536)
    request_record = read_json(task_dir / "request.json", 65536)
    state = load_status(task_id)
    if state["status"] in ("succeeded", "failed", "cancelled"):
        return 0
    update_status(task_dir, "running", "prepareDirectories", last_completed="reserve", cancel_allowed=False)
    try:
        prepare_instance(task_dir, admission, request_record, controller)
    except Exception as error:
        code = str(error) if type(error).__name__ in ("RuntimeError", "TaskRejected") \
            else "CREATE_PREPARE_FAILED"
        update_status(task_dir, "failed", "prepareDirectories", last_completed="reserve",
                      error={"schemaVersion": 1, "code": code, "category": "prepareDirectories",
                             "operation": "instanceCreate", "requestId": None,
                             "target": {"hostId": admission["hostId"], "instanceId": admission["instanceId"]},
                             "message": "实例目录或 manifest 无法安全发布", "retryable": False,
                             "remoteChanged": True, "taskId": task_id,
                             "recoverySteps": ["检查实例现场后选择继续或清理"], "details": {}},
                      result=controller["recovery_result"](admission["hostId"], admission["instanceId"]))
        return 1
    target = {"hostId": admission["hostId"], "instanceId": admission["instanceId"]}
    release_path = task_dir / "release.json"
    if not release_path.exists():
        update_status(task_dir, "failed", "download", last_completed="prepareDirectories",
                      error=task_error(task_id, target, "CREATE_RELEASE_REQUIRED", "download",
                                      "没有已验证的 DST release 可供创建"),
                      result=controller["recovery_result"](admission["hostId"], admission["instanceId"]))
        return 1
    release_error = None
    try:
        release = read_json(release_path, 65536)
        release = release_module["validate_release"](release)
        instance = controller["ROOT"] / "instances" / admission["instanceId"]
        manifest_value = read_json(instance / "manifest.json", 65536)
        release_module["prepare_runtime_paths"](str(instance), release["releaseId"], manifest_value["cavesEnabled"])
        compose = release_module["render_compose"](manifest_value, release, str(instance))
        staging = instance / "staging" / task_id
        controller["atomic_bytes"](staging / "compose.yaml", compose.encode())
        controller["write_json"](staging / "release.json", release)
        config_error, config_diagnostic = compose_config(
            controller, staging / "compose.yaml", manifest_value["composeProject"])
        if config_error:
            release_error = config_error
        else:
            controller["write_json"](staging / "resource-plan.json", resource_plan(manifest_value, release))
    except release_module["ReleaseRejected"] as error:
        release_error = str(error)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        release_error = "CREATE_COMPOSE_PUBLISH_FAILED"
    if release_error is not None:
        details = {"composeDiagnostic": config_diagnostic} if release_error == "CREATE_COMPOSE_CONFIG_FAILED" and config_diagnostic else None
        update_status(task_dir, "failed", "download", last_completed="prepareDirectories",
                      error=task_error(task_id, target, release_error, "download",
                                       "Compose 配置校验失败" if release_error.startswith("CREATE_COMPOSE_CONFIG_") else "release 元数据或 Compose 计划无效",
                                       details),
                      result=controller["recovery_result"](admission["hostId"], admission["instanceId"]))
        return 1
    update_status(task_dir, "running", "publish", last_completed="stagingVerify", cancel_allowed=False)
    try:
        network = docker_module["ensure_network"](manifest_value["composeProject"], admission["hostId"], admission["instanceId"])
        containers = docker_module["create_containers"](str(staging / "compose.yaml"), manifest_value["composeProject"], str(controller["ROOT"]))
    except docker_module["DockerRejected"] as error:
        details = {"dockerDiagnostic": error.diagnostic} if getattr(error, "diagnostic", None) else None
        update_status(task_dir, "failed", "publish", last_completed="stagingVerify",
                      error=task_error(task_id, target, str(error), "publish", "Docker 资源创建失败，现场已保留", details),
                      result=controller["recovery_result"](admission["hostId"], admission["instanceId"]))
        return 1
    try:
        verification = docker_module["verify_containers"](manifest_value["composeProject"], admission["hostId"], admission["instanceId"], manifest_value, str(controller["ROOT"]), release["releaseId"])
    except docker_module["DockerRejected"] as error:
        update_status(task_dir, "failed", "verify", last_completed="publish",
                      error=task_error(task_id, target, str(error), "verify", "容器挂载、标签或端口验证失败"),
                      result=controller["recovery_result"](admission["hostId"], admission["instanceId"]))
        return 1
    deployment_id = str(uuid.uuid4())
    instance = controller["ROOT"] / "instances" / admission["instanceId"]
    active_release = {"schemaVersion": 1, "deploymentId": deployment_id,
                      "dstReleaseId": release["releaseId"], "modsReleaseId": None,
                      "composeProject": manifest_value["composeProject"],
                      "verifiedAt": captured_at()}
    runtime = {"schemaVersion": 1, "snapshotId": str(uuid.uuid4()), "capturedAt": captured_at(),
               "source": "remote", "status": "stopped", "verificationLevel": "localVerified",
               "deploymentId": deployment_id, "network": network, "containers": verification}
    manifest_value.update({"deploymentId": deployment_id, "dstReleaseId": release["releaseId"],
                           "modsReleaseId": None})
    controller["atomic_bytes"](instance / "compose.yaml", (staging / "compose.yaml").read_bytes())
    controller["write_json"](instance / "active-release.json", active_release)
    controller["write_json"](instance / "runtime.json", runtime)
    controller["write_json"](instance / "manifest.json", manifest_value)
    update_status(task_dir, "succeeded", "finalize", last_completed="verify",
                  result=success_result(admission["hostId"], admission["instanceId"], deployment_id),
                  cancel_allowed=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
