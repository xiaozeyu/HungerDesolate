#!/usr/bin/env bash
# 从可达的镜像源取得 amd64 Debian 基础镜像，并打成本地 debian:12-slim 标签。
set -euo pipefail

BASE_IMAGE="debian:12-slim"

if docker image inspect "$BASE_IMAGE" --format '{{.Os}}/{{.Architecture}}' 2>/dev/null | grep -qx 'linux/amd64'; then
    exit 0
fi

for source in \
    docker.m.daocloud.io \
    docker.1ms.run \
    mirror.ccs.tencentyun.com \
    registry.hub.docker.com; do
    image="${source}/library/${BASE_IMAGE}"
    printf '==> 拉取基础镜像：%s\n' "$image" >&2
    if docker pull --platform linux/amd64 "$image"; then
        docker tag "$image" "$BASE_IMAGE"
        if docker image inspect "$BASE_IMAGE" --format '{{.Os}}/{{.Architecture}}' | grep -qx 'linux/amd64'; then
            exit 0
        fi
    fi
done

echo 'BASE_IMAGE_UNAVAILABLE: cannot obtain debian:12-slim from configured mirrors' >&2
exit 1
