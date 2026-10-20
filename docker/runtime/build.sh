#!/usr/bin/env bash
# 构建 DST runtime 镜像并生成 release 元数据
set -euo pipefail

# 配置
IMAGE_NAME="${IMAGE_NAME:-hungerdesolate/dst-runtime}"
BUILD_TAG="${BUILD_TAG:-latest}"
PLATFORM="${PLATFORM:-linux/amd64}"

echo "==> 构建 DST runtime 镜像"
echo "    镜像名称: ${IMAGE_NAME}:${BUILD_TAG}"
echo "    平台: ${PLATFORM}"

# 构建镜像
docker build \
    --platform "${PLATFORM}" \
    --tag "${IMAGE_NAME}:${BUILD_TAG}" \
    --progress=plain \
    .

# 获取镜像 digest
IMAGE_DIGEST=$(docker inspect "${IMAGE_NAME}:${BUILD_TAG}" \
    --format='{{index .RepoDigests 0}}' | cut -d'@' -f2)

if [ -z "${IMAGE_DIGEST}" ]; then
    echo "警告: 无法获取镜像 digest，使用镜像 ID"
    IMAGE_ID=$(docker inspect "${IMAGE_NAME}:${BUILD_TAG}" --format='{{.Id}}')
    IMAGE_DIGEST="sha256:${IMAGE_ID#sha256:}"
fi

echo "==> 镜像构建完成"
echo "    Digest: ${IMAGE_DIGEST}"

# 提取 DST build 版本
echo "==> 提取 DST build 版本"
DST_BUILD=$(docker run --rm "${IMAGE_NAME}:${BUILD_TAG}" \
    cat /srv/dst/version.txt 2>/dev/null || echo "unknown")

echo "    DST Build: ${DST_BUILD}"

# 生成 release 元数据
RELEASE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
SCRIPT_VERSION="V2-$(date +%Y%m%d)"

cat > release-metadata.json <<EOF
{
  "schemaVersion": 1,
  "releaseId": "${RELEASE_ID}",
  "platform": "${PLATFORM}",
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

echo "==> Release 元数据已生成: release-metadata.json"
echo "    Release ID: ${RELEASE_ID}"

# 保存镜像为 tar 文件（用于离线传输）
ARTIFACT_FILE="dst-runtime-${DST_BUILD}-${PLATFORM/\//-}.tar"
echo "==> 导出镜像到: ${ARTIFACT_FILE}"
docker save "${IMAGE_NAME}:${BUILD_TAG}" -o "${ARTIFACT_FILE}"

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
