#!/usr/bin/env bash
# 在远端主机上构建 DST runtime 镜像
# 此脚本通过 SSH 执行，不需要本地 Docker
set -euo pipefail
umask 077
trap 'status=$?; printf "错误：命令执行失败（退出码 %s）：%s\n" "$status" "$BASH_COMMAND" >&2; exit "$status"' ERR

SCRIPT_DIR="/opt/famine-hub/scripts"
BUILD_DIR="/var/lib/famine-hub/image-build"
STAGING_DIR="/var/lib/famine-hub/staging"
RELEASE_ID="${1:-}"

if [ -z "$RELEASE_ID" ]; then
    echo "错误：缺少 RELEASE_ID 参数" >&2
    exit 1
fi

# 验证 RELEASE_ID 是 UUID v4
if ! echo "$RELEASE_ID" | grep -qE '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'; then
    echo "错误：无效的 RELEASE_ID" >&2
    exit 1
fi

echo "==> 配置 Docker 镜像加速（优化国内网络）"
DAEMON_JSON="/etc/docker/daemon.json"
if [ ! -f "$DAEMON_JSON" ] || ! grep -q "registry-mirrors" "$DAEMON_JSON"; then
    echo "配置镜像加速器..."
    sudo mkdir -p /etc/docker
    sudo tee "$DAEMON_JSON" > /dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF
    echo "重启 Docker 服务..."
    sudo systemctl restart docker || true
    sleep 5
    echo "Docker 镜像加速配置完成"
fi

echo "==> 准备构建目录"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# 清理旧的构建文件
rm -rf dst-runtime-build
mkdir dst-runtime-build
cd dst-runtime-build

echo "==> 创建 Dockerfile"
cat > Dockerfile <<'DOCKERFILE_END'
# DST Dedicated Server Runtime Image
FROM debian:12-slim AS builder

# 使用国内镜像源加速
RUN sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装 SteamCMD 依赖
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        lib32gcc-s1 \
        lib32stdc++6 \
        curl \
        && rm -rf /var/lib/apt/lists/*

# 创建 Steam 用户
RUN useradd -m -s /bin/bash steam

# 下载并安装 SteamCMD
WORKDIR /home/steam
RUN curl -sqL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" | tar zxvf - && \
    chmod +x steamcmd.sh linux32/steamcmd
RUN chown -R steam:steam /home/steam

# 下载 DST Dedicated Server (App ID: 343050)
USER steam
RUN ./steamcmd.sh \
    +force_install_dir /home/steam/dst \
    +login anonymous \
    +app_update 343050 validate \
    +quit

# Runtime stage
FROM debian:12-slim

# 使用国内镜像源加速
RUN sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装运行时依赖
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        lib32gcc-s1 \
        lib32stdc++6 \
        libcurl3-gnutls \
        libcurl4:i386 \
        && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN groupadd -g 1000 famine && \
    useradd -u 1000 -g 1000 -m -s /bin/bash famine

# 复制 DST 服务端文件
COPY --from=builder --chown=famine:famine /home/steam/dst /srv/dst

# 设置工作目录
WORKDIR /srv

# 创建必要的目录
RUN mkdir -p /srv/config /srv/data /srv/ugc && \
    chown -R famine:famine /srv

USER famine

# 默认命令
CMD ["/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64"]
DOCKERFILE_END

echo "==> 开始构建 Docker 镜像（这可能需要 10-20 分钟）"
IMAGE_NAME="hungerdesolate/dst-runtime"
BUILD_TAG="latest"

docker build \
    --platform linux/amd64 \
    --tag "${IMAGE_NAME}:${BUILD_TAG}" \
    --progress=plain \
    .

echo "==> 获取镜像 digest"
IMAGE_ID=$(docker inspect "${IMAGE_NAME}:${BUILD_TAG}" --format='{{.Id}}')
IMAGE_DIGEST="sha256:${IMAGE_ID#sha256:}"

echo "==> 提取 DST build 版本"
DST_BUILD=$(docker run --rm "${IMAGE_NAME}:${BUILD_TAG}" \
    cat /srv/dst/version.txt 2>/dev/null || echo "unknown")

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
