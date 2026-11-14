"""Gated Docker resource creation for a verified instance release."""
import json
import os
import re
import subprocess


class DockerRejected(Exception):
    def __init__(self, code, diagnostic=None):
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic


def reject(code):
    raise DockerRejected(code)


def diagnostic_text(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    cleaned = "".join(char for char in value if char in "\n\t" or ord(char) >= 32).strip()
    return cleaned[:2000] or None


def safe_label(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9._:/-]{1,128}", value) is not None


def run(command, timeout=30):
    environment = dict(os.environ)
    for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION", "DOCKER_CONFIG"):
        environment.pop(name, None)
    environment.update({"LC_ALL": "C", "DOCKER_HOST": "unix:///var/run/docker.sock"})
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace", timeout=timeout,
                                check=False, env=environment)
    except FileNotFoundError:
        reject("CREATE_DOCKER_UNAVAILABLE")
    except subprocess.TimeoutExpired:
        reject("CREATE_DOCKER_TIMEOUT")
    if len(result.stdout) > 65536:
        reject("CREATE_DOCKER_RESPONSE_INVALID")
    result.stderr = diagnostic_text(result.stderr)
    return result


def ensure_network(project, host_id, instance_id):
    if not safe_label(project) or not safe_label(host_id) or not safe_label(instance_id):
        reject("CREATE_DOCKER_LABEL_INVALID")
    name = project + "_instance"
    expected = {"com.hungerdesolate.schemaVersion": "1", "com.hungerdesolate.hostId": host_id,
                "com.hungerdesolate.instanceId": instance_id, "com.hungerdesolate.role": "network"}
    inspected = run(["docker", "network", "inspect", "--format", "{{json .Labels}}", name])
    if inspected.returncode == 0:
        try:
            labels = json.loads(inspected.stdout.strip() or "{}")
        except ValueError:
            reject("CREATE_DOCKER_RESPONSE_INVALID")
        if any(labels.get(key) != value for key, value in expected.items()):
            reject("CREATE_NETWORK_CONFLICT")
        return name
    command = ["docker", "network", "create", "--driver", "bridge"]
    for key, value in expected.items():
        command.extend(["--label", key + "=" + value])
    command.append(name)
    created = run(command)
    if created.returncode != 0:
        raise DockerRejected("CREATE_NETWORK_FAILED", created.stderr)
    return name


def create_containers(compose_path, project, root="/var/lib/famine-hub"):
    if not isinstance(compose_path, str) or not compose_path.startswith(root + "/"):
        reject("CREATE_PATH_INVALID")
    if not safe_label(project):
        reject("CREATE_DOCKER_LABEL_INVALID")
    result = run(["docker", "compose", "-p", project, "-f", compose_path, "create"], timeout=120)
    if result.returncode != 0:
        raise DockerRejected("CREATE_CONTAINERS_FAILED", result.stderr)
    return {"project": project, "created": True, "started": False}


def verify_containers(project, host_id, instance_id, manifest, root="/var/lib/famine-hub", dst_release_id=None):
    if not safe_label(project) or not safe_label(host_id) or not safe_label(instance_id):
        reject("CREATE_DOCKER_LABEL_INVALID")
    listed = run(["docker", "container", "ls", "--all", "--quiet", "--no-trunc",
                  "--filter", "label=com.hungerdesolate.instanceId=" + instance_id])
    if listed.returncode != 0:
        reject("CREATE_DOCKER_RESPONSE_INVALID")
    ids = listed.stdout.splitlines()
    expected_roles = {"master"} | ({"caves"} if manifest.get("cavesEnabled") else set())
    if len(ids) != len(expected_roles) or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        reject("CREATE_CONTAINER_SET_INVALID")
    inspected = run(["docker", "container", "inspect", "--format",
                     "{{json .Config.Labels}}\t{{json .Mounts}}\t{{json .HostConfig.PortBindings}}", *ids])
    if inspected.returncode != 0:
        reject("CREATE_DOCKER_RESPONSE_INVALID")
    rows = inspected.stdout.splitlines()
    if len(rows) != len(ids):
        reject("CREATE_DOCKER_RESPONSE_INVALID")
    roles = set()
    expected_root = root + "/instances/" + instance_id + "/"
    shared_dst = root + "/dst/" + dst_release_id if isinstance(dst_release_id, str) and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", dst_release_id) else None
    def mount_allowed(item):
        if not isinstance(item, dict) or not isinstance(item.get("Source"), str):
            return False
        if item["Source"].startswith(expected_root):
            return True
        return (shared_dst is not None and item.get("Type") == "bind" and item.get("RW") is False
                and item["Source"].rstrip("/") == shared_dst)
    for row in rows:
        parts = row.split("\t")
        if len(parts) != 3:
            reject("CREATE_DOCKER_RESPONSE_INVALID")
        try:
            labels, mounts, bindings = (json.loads(part) for part in parts)
        except ValueError:
            reject("CREATE_DOCKER_RESPONSE_INVALID")
        expected = {"com.hungerdesolate.schemaVersion": "1", "com.hungerdesolate.hostId": host_id,
                    "com.hungerdesolate.instanceId": instance_id}
        if any(labels.get(key) != value for key, value in expected.items()):
            reject("CREATE_CONTAINER_LABEL_INVALID")
        role = labels.get("com.hungerdesolate.role")
        if role not in expected_roles or role in roles:
            reject("CREATE_CONTAINER_LABEL_INVALID")
        roles.add(role)
        if not isinstance(mounts, list) or any(not mount_allowed(item) for item in mounts):
            reject("CREATE_CONTAINER_MOUNT_INVALID")
        if not isinstance(bindings, dict):
            reject("CREATE_CONTAINER_PORT_INVALID")
    if roles != expected_roles:
        reject("CREATE_CONTAINER_LABEL_INVALID")
    return {"containerIds": ids, "roles": sorted(roles), "mountsVerified": True, "portsVerified": True}
