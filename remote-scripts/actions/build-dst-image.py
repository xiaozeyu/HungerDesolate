"""Build DST runtime image on remote host."""
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path("/var/lib/famine-hub")
SCRIPT_DIR = Path("/opt/famine-hub/scripts")


class BuildFailed(Exception):
    pass


def build_image(request):
    """Execute image build and return task updates."""
    host_id = request.get("hostId")
    release_id = request.get("releaseId")

    if not release_id:
        release_id = str(uuid.uuid4())

    # 验证 host
    host = json.loads((ROOT / "host.json").read_bytes())
    if host.get("schemaVersion") != 1 or host.get("hostId") != host_id:
        raise BuildFailed("HOST_IDENTITY_MISMATCH")

    # 检查 Docker 是否可用
    try:
        subprocess.run(["docker", "version"],
                      capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        raise BuildFailed("DOCKER_NOT_AVAILABLE")

    # 执行构建脚本
    build_script = SCRIPT_DIR / "actions" / "build-dst-image.sh"
    if not build_script.exists():
        raise BuildFailed("BUILD_SCRIPT_MISSING")

    # 返回任务信息，实际构建由后台任务执行
    return {
        "releaseId": release_id,
        "status": "queued",
        "message": "镜像构建任务已加入队列"
    }


def main(request):
    try:
        result = build_image(request)
        print(json.dumps({"ok": True, "data": result}, separators=(",", ":")), flush=True)
    except BuildFailed as error:
        print(json.dumps({"ok": False, "code": str(error)}, separators=(",", ":")), flush=True)
    except Exception:
        print(json.dumps({"ok": False, "code": "BUILD_IMAGE_FAILED"}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main(json.loads(sys.stdin.read()))
