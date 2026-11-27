#!/usr/bin/env bash
# 在远端主机上构建 DST runtime 镜像
# 此脚本通过 SSH 执行，不需要本地 Docker
set -eEuo pipefail
umask 077
trap 'status=$?; printf "\nFAMINE_BUILD_FAILED:%s\n错误：命令执行失败（退出码 %s）：%s\n" "$status" "$status" "$BASH_COMMAND" >&2; exit "$status"' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_DIR="/var/lib/famine-hub/image-build"
STAGING_DIR="/var/lib/famine-hub/staging"
RELEASE_ID="${1:-}"
STEAMCMD_BOOTSTRAP_DEADLINE="${STEAMCMD_BOOTSTRAP_DEADLINE:-3600s}"
STEAMCMD_DOWNLOAD_DEADLINE="${STEAMCMD_DOWNLOAD_DEADLINE:-3600s}"
SCRIPT_VERSION="V3-$(cat "${SCRIPT_DIR}/actions/Dockerfile.build-base" \
    "${SCRIPT_DIR}/actions/Dockerfile.runtime-base" \
    "${SCRIPT_DIR}/actions/install-dst.sh" \
    "${SCRIPT_DIR}/actions/fetch-workshop.sh" | sha256sum | cut -c1-12)"
BUILD_BASE_IMAGE="hungerdesolate/dst-build-base:${SCRIPT_VERSION}"
RUNTIME_IMAGE_NAME="hungerdesolate/dst-runtime-base"
RUNTIME_BASE_IMAGE="${RUNTIME_IMAGE_NAME}:${SCRIPT_VERSION}"
DST_ROOT="/var/lib/famine-hub/dst"
DST_DOWNLOAD="$DST_ROOT/.staging-${RELEASE_ID}"
DST_PUBLISHED=0
DST_REGISTERED=0
BUILD_CLEANED=0

cleanup_build_artifacts() {
    local root_dir
    root_dir=$(dirname "$BUILD_DIR")
    cd "$BUILD_DIR" 2>/dev/null || true
    rm -rf "$BUILD_DIR/dst-runtime-build" "$BUILD_DIR/steamapps" "$DST_DOWNLOAD" "$DST_ROOT"/.staging-*
    docker builder prune --all -f >/dev/null 2>&1 || echo 'IMAGE_BUILD_CACHE_CLEANUP_FAILED' >&2
    find "$root_dir" -maxdepth 1 -type f -name 'build-*.log' -mtime +7 -delete 2>/dev/null || true
}

available_bytes() {
    df -Pk "$BUILD_DIR" | awk 'NR == 2 { printf "%.0f\n", $4 * 1024 }'
}

release_payload_bytes() {
    local size
    if [ -d "$DST_ROOT" ]; then
        size=$(find "$DST_ROOT" -mindepth 1 -maxdepth 1 -type d ! -name '.staging-*' -print0 \
            | xargs -0 -r du -sb 2>/dev/null | sort -nr | head -n 1 | cut -f1)
        if [ -n "$size" ]; then
            echo "$size"
            return
        fi
    fi
    echo $((6 * 1024 * 1024 * 1024))
}

preflight_space() {
    local required available
    required=$(( $(release_payload_bytes) * 2 + 2 * 1024 * 1024 * 1024 ))
    available=$(available_bytes)
    if [ "$available" -lt "$required" ]; then
        printf 'IMAGE_BUILD_SPACE_INSUFFICIENT: required=%s available=%s\n' "$required" "$available" >&2
        echo 'FAMINE_BUILD_FAILED:1' >&2
        exit 1
    fi
    printf '==> 磁盘空间检查通过 available=%s required=%s\n' "$available" "$required"
}

ensure_base_image() {
    local tag=$1 file=$2
    shift 2
    if docker image inspect "$tag" --format '{{.Id}}' >/dev/null 2>&1; then
        printf '==> 基础镜像已存在 %s\n' "$tag"
        return
    fi
    DOCKER_BUILDKIT=1 docker build --platform linux/amd64 --tag "$tag" "$@" -f "$file" \
        --progress=plain "$SCRIPT_DIR/actions"
}

if [ -z "$RELEASE_ID" ]; then
    echo "错误：缺少 RELEASE_ID 参数" >&2
    exit 1
fi

# 验证 RELEASE_ID 是 UUID v4
if ! echo "$RELEASE_ID" | grep -qE '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'; then
    echo "错误：无效的 RELEASE_ID" >&2
    exit 1
fi

echo 'FAMINE_BUILD_STARTED'

# Build and registration must use the same managed local daemon.
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH DOCKER_API_VERSION DOCKER_CONFIG
export DOCKER_HOST=unix:///var/run/docker.sock

# Docker Engine 29.4.2 的默认 seccomp profile 会阻断 SteamCMD 使用的 32 位 socketcall。
# 29.4.3 已修复该回归；在构建前明确阻断易失败环境，避免 bootstrap 运行数十分钟后才崩溃。
DOCKER_SERVER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
case "$DOCKER_SERVER_VERSION" in
    29.4.2|29.4.2-*|29.4.2+*)
        echo "DOCKER_ENGINE_INCOMPATIBLE: Docker Engine ${DOCKER_SERVER_VERSION} breaks 32-bit SteamCMD; upgrade to 29.4.3 or newer" >&2
        echo 'FAMINE_BUILD_FAILED:1' >&2
        exit 1
        ;;
    '')
        echo 'DOCKER_SERVER_VERSION_UNAVAILABLE: cannot query Docker Engine server version' >&2
        echo 'FAMINE_BUILD_FAILED:1' >&2
        exit 1
        ;;
esac
printf '==> Docker Engine server=%s\n' "$DOCKER_SERVER_VERSION"

# Do not let a second build remove the first build's shared context.
mkdir -p "$BUILD_DIR"
exec 9>"$BUILD_DIR/build.lock"
if ! flock -n 9; then
    echo 'IMAGE_BUILD_ALREADY_RUNNING: another image build is active on this host' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi
# Docker children inherit fd 9; release the lock explicitly on exit so a
# successful build cannot leave the cache-cleanup lock permanently busy.
cleanup_on_exit() {
    local status=$?
    if [ "$BUILD_CLEANED" -eq 0 ]; then
        if [ "$DST_PUBLISHED" -eq 1 ] && [ "$DST_REGISTERED" -eq 0 ]; then
            rm -rf "$DST_ROOT/$RELEASE_ID"
        fi
        cleanup_build_artifacts
    fi
    flock -u 9 2>/dev/null || true
    exit "$status"
}
trap cleanup_on_exit EXIT

echo '==> 检查构建磁盘空间'
preflight_space

echo "==> 准备构建目录"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

printf '==> 构建参数（bootstrap 超时=%s，DST 下载超时=%s）\n' \
    "$STEAMCMD_BOOTSTRAP_DEADLINE" "$STEAMCMD_DOWNLOAD_DEADLINE"
echo '==> 准备 Debian 基础镜像'
bash "${SCRIPT_DIR}/actions/prepare-base-image.sh" 9>&-

echo '==> 准备稳定基础镜像'
ensure_base_image "$BUILD_BASE_IMAGE" "${SCRIPT_DIR}/actions/Dockerfile.build-base" \
    --build-arg "STEAMCMD_BOOTSTRAP_DEADLINE=${STEAMCMD_BOOTSTRAP_DEADLINE}"
ensure_base_image "$RUNTIME_BASE_IMAGE" "${SCRIPT_DIR}/actions/Dockerfile.runtime-base" \
    --build-arg "BUILD_BASE_IMAGE=${BUILD_BASE_IMAGE}"

echo '==> 准备 DST 目录'
mkdir -p "$DST_ROOT"
chmod 700 "$DST_ROOT"
if [ -e "$DST_ROOT/$RELEASE_ID" ]; then
    echo 'DST_RELEASE_DIR_EXISTS: release directory already exists' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi

reset_download_dirs() {
    rm -rf -- "$DST_DOWNLOAD" "$BUILD_DIR/steamapps"
    mkdir -p -- "$DST_DOWNLOAD" "$BUILD_DIR/steamapps"
    chown 1000:1000 "$DST_DOWNLOAD" "$BUILD_DIR/steamapps"
}

download_dst() {
    docker run 9>&- --rm --network bridge --user 1000:1000 --env HOME=/home/steam \
        -v "$DST_DOWNLOAD:/dst" -v "$BUILD_DIR/steamapps:/home/steam/steamapps" --env DST_DIR=/dst \
        --env "STEAMCMD_DOWNLOAD_DEADLINE=${STEAMCMD_DOWNLOAD_DEADLINE}" \
        "$BUILD_BASE_IMAGE" /bin/bash /home/steam/install-dst.sh download
}

echo '==> 下载 DST 服务端到宿主机'
reset_download_dirs
DST_BASE=""
if [ -d "$DST_ROOT" ]; then
    DST_BASE=$(ls -1dt "$DST_ROOT"/*/ 2>/dev/null | head -n 1) || DST_BASE=""
    DST_BASE=${DST_BASE%/}
fi
if [ -n "$DST_BASE" ]; then
    echo "==> 复用最近 DST 目录作为增量基线: ${DST_BASE}"
    cp -a "$DST_BASE/." "$DST_DOWNLOAD/"
    chown -R 1000:1000 "$DST_DOWNLOAD"
    download_dst || {
        printf '==> 增量下载失败（退出码 %s），清空临时目录后全量重试\n' "$?" >&2
        reset_download_dirs
        download_dst
    }
else
    download_dst
fi

if [ ! -x "$DST_DOWNLOAD/bin64/dontstarve_dedicated_server_nullrenderer_x64" ] \
    || [ ! -s "$DST_DOWNLOAD/version.txt" ] \
    || [ ! -s "$DST_DOWNLOAD/data/databundles/scripts.zip" ]; then
    echo 'DST_ARTIFACT_INVALID: downloaded DST content is incomplete' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi
mkdir -p "$DST_DOWNLOAD/mods"
: > "$DST_DOWNLOAD/mods/dedicated_server_mods_setup.lua"
chown 1000:1000 "$DST_DOWNLOAD/mods" "$DST_DOWNLOAD/mods/dedicated_server_mods_setup.lua"
DST_BUILD=$(tr -d '[:space:]' < "$DST_DOWNLOAD/version.txt")
if ! [[ "$DST_BUILD" =~ ^[0-9]+$ ]]; then
    echo 'DST_BUILD_INVALID: version.txt must contain a numeric DST build' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi
STEAM_BUILD_ID=""
APP_MANIFEST=$(find "$DST_DOWNLOAD/steamapps" "$BUILD_DIR/steamapps" \
    -name appmanifest_343050.acf -print -quit 2>/dev/null || true)
if [[ -n "$APP_MANIFEST" && -f "$APP_MANIFEST" ]]; then
    STEAM_BUILD_ID=$(sed -n 's/.*"buildid"[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' "$APP_MANIFEST" | head -n 1)
fi
if ! [[ "$STEAM_BUILD_ID" =~ ^[0-9]+$ ]]; then
    echo 'STEAM_BUILD_ID_MISSING: appmanifest_343050.acf has no numeric Steam buildid' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi
DST_CONTENT_DIGEST=$(cd "$DST_DOWNLOAD" && find . -type f -print0 | LC_ALL=C sort -z \
    | xargs -0 -r sha256sum | sha256sum | cut -d' ' -f1)

echo '==> 校验共享运行时镜像'
IMAGE_DIGEST=$(docker image inspect "$RUNTIME_BASE_IMAGE" --format='{{.Id}}')
if ! [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo 'BUILD_IMAGE_ID_INVALID: Docker returned an invalid local image ID' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi
docker run 9>&- --rm --network none --entrypoint /bin/sh "$IMAGE_DIGEST" \
    -ec 'test -x /usr/local/bin/famine-workshop-fetch && test -s /srv/capabilities/workshop-fetch && grep -Fxq steamcmd-workshop-v1 /srv/capabilities/workshop-fetch'

mv "$DST_DOWNLOAD" "$DST_ROOT/$RELEASE_ID"
DST_PUBLISHED=1
echo "DST Build: ${DST_BUILD}"
echo "Steam build: ${STEAM_BUILD_ID}"
echo "DST content digest: sha256:${DST_CONTENT_DIGEST}"

echo "==> 生成 release 元数据"

mkdir -p "${STAGING_DIR}/${RELEASE_ID}"
cd "${STAGING_DIR}/${RELEASE_ID}"

cat > release-metadata.json <<EOF
{
  "schemaVersion": 1,
  "releaseId": "${RELEASE_ID}",
  "platform": "linux/amd64",
  "image": "${RUNTIME_IMAGE_NAME}",
  "imageDigest": "${IMAGE_DIGEST}",
  "dstBuild": "${DST_BUILD}",
  "steamBuildId": "${STEAM_BUILD_ID}",
  "artifactDigest": "sha256:${DST_CONTENT_DIGEST}",
  "scriptVersion": "${SCRIPT_VERSION}",
  "verified": false,
  "runtimeVerified": false,
  "commands": {
    "master": [
      "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64",
      "-console",
      "-persistent_storage_root", "/srv/data",
      "-conf_dir", "config",
      "-cluster", "Cluster_1",
      "-shard", "Master"
    ],
    "caves": [
      "/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64",
      "-console",
      "-persistent_storage_root", "/srv/data",
      "-conf_dir", "config",
      "-cluster", "Cluster_1",
      "-shard", "Caves"
    ]
  }
}
EOF

echo "==> 登记 Release 到主机 catalog（等待运行时验证）"
sudo chown root:root "${STAGING_DIR}/${RELEASE_ID}" "${STAGING_DIR}/${RELEASE_ID}/release-metadata.json"
sudo chmod 700 "${STAGING_DIR}/${RELEASE_ID}"
sudo chmod 600 "${STAGING_DIR}/${RELEASE_ID}/release-metadata.json"
sudo python3 -I "${SCRIPT_DIR}/actions/register-built-release.py" "${RELEASE_ID}" 9>&-
DST_REGISTERED=1

echo "==> DST Release 准备完成"
echo "Release ID: ${RELEASE_ID}"
echo "Runtime image: ${RUNTIME_IMAGE_NAME}@${IMAGE_DIGEST}"
echo "DST directory: ${DST_ROOT}/${RELEASE_ID}"
echo "DST Build: ${DST_BUILD}"
echo "Metadata: ${STAGING_DIR}/${RELEASE_ID}/release-metadata.json"
echo ""
echo "DST 目录已保存，可直接用于切换或创建实例"
echo "==> 清理构建缓存"
cleanup_build_artifacts
BUILD_CLEANED=1
flock -u 9 2>/dev/null || true
echo 'FAMINE_BUILD_COMPLETED'
