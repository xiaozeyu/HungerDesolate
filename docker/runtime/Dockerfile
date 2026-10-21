# DST Dedicated Server Runtime Image
# 基于 Debian 12 构建，包含完整的 DST 服务端

FROM debian:12-slim AS builder

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
RUN curl --fail --show-error --location --retry 3 --connect-timeout 30 --max-time 180 \
        "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" -o /tmp/steamcmd.tar.gz && \
    tar -xzf /tmp/steamcmd.tar.gz && rm /tmp/steamcmd.tar.gz && \
    chmod +x steamcmd.sh linux32/steamcmd
RUN chown -R steam:steam /home/steam
COPY --chown=steam:steam install-dst.sh /home/steam/install-dst.sh

# 下载 DST Dedicated Server (App ID: 343050)
USER steam
RUN bash ./install-dst.sh bootstrap
RUN bash ./install-dst.sh download

# Runtime stage
FROM debian:12-slim

# 安装运行时依赖
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
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
WORKDIR /srv/dst/bin64

# 创建必要的目录
RUN mkdir -p /srv/config /srv/data /srv/ugc && \
    chown -R famine:famine /srv

USER famine

# 默认命令（将被 Compose 覆盖）
CMD ["/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64"]
