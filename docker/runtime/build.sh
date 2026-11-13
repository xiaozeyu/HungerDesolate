#!/usr/bin/env bash
# 构建 DST runtime 镜像并生成 release 元数据
set -euo pipefail

# 配置
IMAGE_NAME="${IMAGE_NAME:-hungerdesolate/dst-runtime}"
RELEASE_ID="${RELEASE_ID:-$(uuidgen | tr '[:upper:]' '[:lower:]')}"
BUILD_TAG="${BUILD_TAG:-release-${RELEASE_ID}}"
PLATFORM="${PLATFORM:-linux/amd64}"
STEAMCMD_BOOTSTRAP_DEADLINE="${STEAMCMD_BOOTSTRAP_DEADLINE:-3600s}"
STEAMCMD_DOWNLOAD_DEADLINE="${STEAMCMD_DOWNLOAD_DEADLINE:-1800s}"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# Docker Engine 29.4.2 的默认 seccomp profile 会阻断 SteamCMD 使用的 32 位 socketcall。
DOCKER_SERVER_VERSION=$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)
case "$DOCKER_SERVER_VERSION" in
    29.4.2|29.4.2-*|29.4.2+*)
        echo "DOCKER_ENGINE_INCOMPATIBLE: Docker Engine ${DOCKER_SERVER_VERSION} breaks 32-bit SteamCMD; upgrade to 29.4.3 or newer" >&2
        exit 1
        ;;
    '')
        echo 'DOCKER_SERVER_VERSION_UNAVAILABLE: cannot query Docker Engine server version' >&2
        exit 1
        ;;
esac
printf '==> Docker Engine server=%s\n' "$DOCKER_SERVER_VERSION"

echo "==> 构建 DST runtime 镜像"
echo "    镜像名称: ${IMAGE_NAME}:${BUILD_TAG}"
echo "    平台: ${PLATFORM}"

echo '==> 准备 Debian 基础镜像'
bash "$SCRIPT_DIR/prepare-base-image.sh"

echo '==> 开始构建 Docker 镜像'
DOCKER_BUILDKIT=1 docker build \
    --platform "${PLATFORM}" \
    --build-arg "STEAMCMD_BOOTSTRAP_DEADLINE=${STEAMCMD_BOOTSTRAP_DEADLINE}" \
    --build-arg "STEAMCMD_DOWNLOAD_DEADLINE=${STEAMCMD_DOWNLOAD_DEADLINE}" \
    --tag "${IMAGE_NAME}:${BUILD_TAG}" \
    --progress=plain \
    .

# 获取镜像 digest
IMAGE_DIGEST=$(docker image inspect "${IMAGE_NAME}:${BUILD_TAG}" --format='{{.Id}}')
if ! [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo 'BUILD_IMAGE_ID_INVALID: Docker returned an invalid local image ID' >&2
    exit 1
fi

echo "==> 镜像构建完成"
echo "    Digest: ${IMAGE_DIGEST}"

# 提取 DST build 版本
echo "==> 提取 DST build 版本"
DST_BUILD=$(docker run --rm --network none --entrypoint /bin/sh "$IMAGE_DIGEST" \
    -ec 'cat /srv/dst/version.txt')
if ! [[ "$DST_BUILD" =~ ^[0-9]+$ ]]; then
    echo 'DST_BUILD_INVALID: version.txt must contain a numeric DST build' >&2
    exit 1
fi

STEAM_BUILD_ID=$(docker run --rm --network none --entrypoint /bin/sh "$IMAGE_DIGEST" \
    -ec 'cat /srv/dst/steamapps/appmanifest_343050.acf' \
    | sed -n 's/.*"buildid"[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' | head -n 1)
if ! [[ "$STEAM_BUILD_ID" =~ ^[0-9]+$ ]]; then
    echo 'STEAM_BUILD_ID_MISSING: appmanifest_343050.acf has no numeric Steam buildid' >&2
    exit 1
fi

docker run --rm --network none --entrypoint /bin/sh "$IMAGE_DIGEST" \
    -ec 'test -x /usr/local/bin/famine-workshop-fetch && grep -Fxq steamcmd-workshop-v1 /srv/capabilities/workshop-fetch'

echo "    DST Build: ${DST_BUILD}"
echo "    Steam build: ${STEAM_BUILD_ID}"

# 生成 release 元数据
SCRIPT_VERSION="V2-$(date +%Y%m%d)"

cat > release-metadata.json <<EOF
{
  "schemaVersion": 1,
  "releaseId": "${RELEASE_ID}",
  "platform": "${PLATFORM}",
  "image": "${IMAGE_NAME}",
  "imageDigest": "${IMAGE_DIGEST}",
  "dstBuild": "${DST_BUILD}",
  "steamBuildId": "${STEAM_BUILD_ID}",
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

echo "==> Release 元数据已生成: release-metadata.json"
echo "    Release ID: ${RELEASE_ID}"

# 保存镜像为 tar 文件（用于离线传输）
ARTIFACT_FILE="dst-runtime-${DST_BUILD}-${PLATFORM/\//-}.tar"
echo "==> 导出镜像到: ${ARTIFACT_FILE}"
docker save "$IMAGE_DIGEST" -o "${ARTIFACT_FILE}"

echo "==> 计算 artifact checksum"
ARTIFACT_SHA256=$(sha256sum "${ARTIFACT_FILE}" | cut -d' ' -f1)
echo "    SHA256: ${ARTIFACT_SHA256}"

# 更新元数据中的 artifact digest
jq ".artifactDigest = \"sha256:${ARTIFACT_SHA256}\"" release-metadata.json > release-metadata.json.tmp
mv release-metadata.json.tmp release-metadata.json

echo ""
echo "=========================================="
echo "构建完成！"
echo "=========================================="
echo "镜像: ${IMAGE_NAME}:${BUILD_TAG}"
echo "Digest: ${IMAGE_DIGEST}"
echo "DST Build: ${DST_BUILD}"
echo "Release ID: ${RELEASE_ID}"
echo "Artifact: ${ARTIFACT_FILE}"
echo ""
echo "下一步："
echo "1. 验证镜像: docker run --rm ${IMAGE_NAME}:${BUILD_TAG} /srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64 -version"
echo "2. 上传到主机: 将 ${ARTIFACT_FILE} 和 release-metadata.json 传输到 /var/lib/famine-hub/staging/"
echo "3. 导入 release: 使用 import_instance_release 命令"
echo "=========================================="
