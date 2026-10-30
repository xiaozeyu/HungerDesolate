#!/usr/bin/env bash
# 在远端主机上构建 DST runtime 镜像
# 此脚本通过 SSH 执行，不需要本地 Docker
set -euo pipefail
umask 077
trap 'status=$?; printf "\nFAMINE_BUILD_FAILED:%s\n错误：命令执行失败（退出码 %s）：%s\n" "$status" "$status" "$BASH_COMMAND" >&2; exit "$status"' ERR

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_DIR="/var/lib/famine-hub/image-build"
STAGING_DIR="/var/lib/famine-hub/staging"
RELEASE_ID="${1:-}"
STEAMCMD_BOOTSTRAP_DEADLINE="${STEAMCMD_BOOTSTRAP_DEADLINE:-3600s}"
STEAMCMD_DOWNLOAD_DEADLINE="${STEAMCMD_DOWNLOAD_DEADLINE:-1800s}"

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

echo "==> 准备构建目录"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# 清理旧的构建文件
rm -rf dst-runtime-build
mkdir dst-runtime-build
cd dst-runtime-build
cp "${SCRIPT_DIR}/actions/install-dst.sh" ./install-dst.sh
cp "${SCRIPT_DIR}/actions/fetch-workshop.sh" ./fetch-workshop.sh

cp "${SCRIPT_DIR}/actions/Dockerfile" ./Dockerfile

printf '==> 构建参数（bootstrap 超时=%s，DST 下载超时=%s；每阶段最多 3 次）\n' \
    "$STEAMCMD_BOOTSTRAP_DEADLINE" "$STEAMCMD_DOWNLOAD_DEADLINE"
IMAGE_NAME="hungerdesolate/dst-runtime"
BUILD_TAG="release-${RELEASE_ID}"

echo '==> 准备 Debian 基础镜像'
bash "${SCRIPT_DIR}/actions/prepare-base-image.sh"

echo '==> 开始构建 Docker 镜像'
DOCKER_BUILDKIT=1 docker build \
    --platform linux/amd64 \
    --build-arg "STEAMCMD_BOOTSTRAP_DEADLINE=${STEAMCMD_BOOTSTRAP_DEADLINE}" \
    --build-arg "STEAMCMD_DOWNLOAD_DEADLINE=${STEAMCMD_DOWNLOAD_DEADLINE}" \
    --tag "${IMAGE_NAME}:${BUILD_TAG}" \
    --progress=plain \
    .

echo "==> 获取镜像 digest"
IMAGE_DIGEST=$(docker image inspect "${IMAGE_NAME}:${BUILD_TAG}" --format='{{.Id}}')
if ! [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo 'BUILD_IMAGE_ID_INVALID: Docker returned an invalid local image ID' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi

echo "==> 验证固定 SteamCMD Workshop 获取能力"
docker run --rm --network none --entrypoint /bin/sh "$IMAGE_DIGEST" \
    -ec 'test -x /usr/local/bin/famine-workshop-fetch && test -s /srv/capabilities/workshop-fetch && grep -Fxq steamcmd-workshop-v1 /srv/capabilities/workshop-fetch'

echo "==> 提取 DST build 版本"
DST_BUILD=$(docker run --rm --network none --entrypoint /bin/sh "$IMAGE_DIGEST" \
    -ec 'cat /srv/dst/version.txt')
if ! [[ "$DST_BUILD" =~ ^[0-9]+$ ]]; then
    echo 'DST_BUILD_INVALID: version.txt must contain a numeric DST build' >&2
    echo 'FAMINE_BUILD_FAILED:1' >&2
    exit 1
fi

echo "DST Build: ${DST_BUILD}"

echo "==> 生成 release 元数据"
SCRIPT_VERSION="V2-$(date +%Y%m%d)"

mkdir -p "${STAGING_DIR}/${RELEASE_ID}"
cd "${STAGING_DIR}/${RELEASE_ID}"

cat > release-metadata.json <<EOF
{
  "schemaVersion": 1,
  "releaseId": "${RELEASE_ID}",
  "platform": "linux/amd64",
  "image": "${IMAGE_NAME}",
  "imageDigest": "${IMAGE_DIGEST}",
  "dstBuild": "${DST_BUILD}",
  "artifactDigest": "${IMAGE_DIGEST}",
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
sudo python3 -I "${SCRIPT_DIR}/actions/register-built-release.py" "${RELEASE_ID}"

echo "==> 镜像构建完成"
echo "Release ID: ${RELEASE_ID}"
echo "Image: ${IMAGE_NAME}:${BUILD_TAG}"
echo "Digest: ${IMAGE_DIGEST}"
echo "DST Build: ${DST_BUILD}"
echo "Metadata: ${STAGING_DIR}/${RELEASE_ID}/release-metadata.json"
echo ""
echo "镜像已保存到 Docker，可以直接使用"
echo 'FAMINE_BUILD_COMPLETED'
