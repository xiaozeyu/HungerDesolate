# DST Release 准备指南

本文档说明如何为荒野中枢准备第一个 DST 服务端 release。

## 概念

- **Release**: 一个经过验证的 DST 服务端版本，包含 Docker 镜像和运行配置
- **Runtime 镜像**: 包含 DST Dedicated Server 的 Docker 镜像
- **Release Catalog**: 主机级别的 release 注册表

## 通过应用构建

在 Release 管理页点击“构建镜像”，由目标主机准备 DST Release；构建完成后自动登记基础验证通过的 Release。

当前构建路径复用主机级基础镜像：

- `hungerdesolate/dst-build-base:<scriptVersion>`：Debian、SteamCMD 依赖和 bootstrap；仅缺失时构建。
- `hungerdesolate/dst-runtime-base:<scriptVersion>`：运行库、UID 1000 用户、SteamCMD 和 workshop 脚本；仅缺失时构建。
- DST App 343050 下载到宿主机 `dst/.staging-<releaseId>`；存在其他 Release 目录时先复制最近目录作为增量基线，再执行 `app_update`，校验 `version.txt`、二进制和 `scripts.zip`，计算内容摘要后移动到 `dst/<releaseId>`；不再为每个 Release 构建镜像。
- Compose 将 `dst/<releaseId>` 只读挂载到容器的 `/srv/dst`，Release 元数据的 `imageDigest` 指向共享运行时镜像。
- 构建结束删除暂存目录、构建上下文并清理 BuildKit 缓存；基础镜像和已登记 DST 目录保留。

## Release 验证

通过软件内构建成功后，构建脚本会核对共享运行时镜像 digest、DST 目录内容摘要、`version.txt`，并挂载该目录用 `ldd` 检查 DST 二进制可执行权限和动态库依赖，再将基础验证通过的 Release 登记到主机 catalog。这些检查不代表 DST 服务进程已启动、端口已监听或玩家可以连接。

Release 登记会自动执行基础验证，通过后即可用于创建实例：

1. **基础验证** (`verified: true`)
   - 镜像 digest 与本地主机 Docker image ID 匹配
   - `version.txt` 与 Release 元数据一致
   - DST 二进制可执行，动态库依赖齐全，SteamCMD Workshop 获取能力存在

基础验证不代表 DST 服务进程已启动、端口已监听或玩家可以连接；实例启动时会单独采集进程、端口和运行状态。当前版本不提供 Release 隔离运行验证，也不接收配置 ZIP。catalog 中的 `runtimeVerified` 仅作为历史兼容字段保留，当前恒为 `false`，不参与实例创建或世界配置。

## 故障排查

### 确认正在运行的安装脚本与完整任务日志

软件内构建使用 `src-tauri/src/image_build.rs` 通过 `include_str!` 编译嵌入的构建脚本、共享基础/发布 Dockerfile 与基础镜像准备脚本，每次接纳新构建时通过 SSH 下发到 `/opt/famine-hub/scripts/image-build/<releaseId>/actions`。新目录以无覆盖的 `mkdir` 取得，重复 Release ID 在上传前失败，不改写已有任务脚本。因此仅修改仓库文件不会更新已经运行的桌面后端。开发环境修改后需要重新编译并启动 Rust/Tauri 后端（`npm run desktop:dev`）；仅重启 Vite 前端不够。已开始或结束的任务日志不会因源码更新而变化。旧版后端仍可能使用固定的 `/opt/famine-hub/scripts/actions` 路径，必须重新启动新版后端才能应用脚本隔离。

新版 bootstrap 阶段标题默认包含 `deadline=3600s`，失败诊断包含 `SteamCMD failure evidence`。如果某份任务日志没有这些字段，应核对该任务开始时实际复制到 `/var/lib/famine-hub/image-build/dst-runtime-build/install-dst.sh` 的脚本，而不能据当前仓库内容推定历史任务使用了修复。

先使用 `tail -n 120 /var/lib/famine-hub/build-<releaseId>.log` 读取指定任务的原始日志末尾，再定位断言。`ILocalize::AddFile() failed to load file "public/steambootstrapper_english.txt"` 后如果仍有下载输出，就不能把这条提示当成该任务最终失败原因。只做 `grep -B/-A` 可能仅匹配这条启动提示并遗漏任务末尾；缺少终态标记时也不能认定所选文件已包含失败结果。

### 问题：构建镜像时 SteamCMD 下载失败

日志中的 SteamCMD 自更新、Steam 匿名登录和 DST 内容下载是不同阶段。仅凭末尾 `CWorkThreadPool` 清理信息或退出码 8，不能断定具体网络、权限或空间问题。

当前本地构建和远端下载容器共用 `docker/runtime/install-dst.sh`：本地镜像构建先 bootstrap 再下载，远端由 `dst-build-base` 完成 bootstrap，宿主机挂载目录执行 `install-dst.sh download`。对退出码 8、42 和超时 124 最多尝试 3 次；其他错误立即失败。失败输出 `STEAMCMD_BOOTSTRAP_FAILED` 或 `STEAMCMD_DOWNLOAD_FAILED`、原始退出码、隔离构建目录下 SteamCMD 内部日志末尾及磁盘/ inode 信息。重试耗尽不登记 Release。

软件内构建状态使用宿主脚本的最终结果标记；SteamCMD 重试期间的 `error:` 日志不会让页面提前停止观察。只有镜像构建和 Release 登记均完成后才标记成功。

下载步骤必须同时满足退出码 0、App 343050 完整安装标记、可执行的 DST binary、有效版本文件及非空 `data/databundles/scripts.zip`；缺少任一项返回 `DST_ARTIFACT_INVALID`，不会生成成功镜像。真实 Steam/CDN 联网下载仍须在构建主机实测。

**解决**:
```bash
# 检查网络连接
curl -I https://steamcdn-a.akamaihd.net

# 重试构建
./build.sh
```

### 问题：检查 DST 更新长时间无结果或失败

检查由 Release 管理页触发。主机优先使用已验证 Release 的 `scriptVersion` 对应的 `hungerdesolate/dst-build-base:<scriptVersion>` 工具镜像，该镜像已完成 SteamCMD bootstrap；只有 build-base 不存在时才回退到 Release 运行时镜像。SteamCMD 查询 App 343050 的 `public` build，最长等待 600 秒，并把 `running/ok/failed`、当前阶段和错误码写入 `/var/lib/famine-hub/updates/dst-latest.json`。

- 页面显示“正在检查…”时状态为 `running`，切换页面后重新进入会继续轮询；检查中重复点击不会再次运行 SteamCMD。
- 显示“上次检查中断，请重试”表示检查进程已结束但没有写最终状态，直接重新检查即可。
- 显示 SteamCMD 失败或超时时，先确认 Docker Engine 不是 29.4.2，再用 build-base 手动检查：

```bash
docker version --format '{{.Server.Version}}'
```

```bash
docker run --rm --name famine-dst-check-manual --network bridge --user 1000:1000 \
  --env HOME=/home/steam hungerdesolate/dst-build-base:<scriptVersion> \
  /home/steam/steamcmd.sh \
  +login anonymous +app_info_update 1 +app_info_print 343050 +quit
```

没有 build-base 时回退 Release 镜像（HOME 和路径不同）：

```bash
docker run --rm --name famine-dst-check-manual --network bridge --user 1000:1000 \
  --env HOME=/home/famine <imageDigest> \
  /opt/famine/steamcmd/steamcmd.sh \
  +login anonymous +app_info_update 1 +app_info_print 343050 +quit
```

确认镜像存在：

```bash
docker image inspect hungerdesolate/dst-build-base:<scriptVersion>
```

检查残留的检查容器：

```bash
docker ps -a --filter name=famine-dst-check-
```

查看上次检查状态：

```bash
sudo cat /var/lib/famine-hub/updates/dst-latest.json
```

### 问题：`BASE_IMAGE_UNAVAILABLE` 或拉取 `debian:12-slim` 失败

构建不再下载 `moby/buildkit`，直接使用 Docker daemon 自带的 BuildKit。构建前由 `prepare-base-image.sh` 确认本机已有 linux/amd64 的 `debian:12-slim`；没有时依次尝试 DaoCloud、1ms.run、Tencent Cloud 内网镜像和 Docker Hub，拉取成功后打成本地 `debian:12-slim` 标签。

如果日志显示 `BASE_IMAGE_UNAVAILABLE`，说明所有入口都不可达。先在服务器上手动确认：

```bash
docker pull --platform linux/amd64 docker.m.daocloud.io/library/debian:12-slim
```

能成功时重新构建即可。构建脚本不修改 `daemon.json`、不重启 Docker、不关闭 TLS 校验，也不输出代理凭据或 Docker 原始认证错误正文。

### 问题：SteamCMD 报 `Failed to apply update` 或 `Failed to load steamconsole.so`

**原因**: SteamCMD 解压后需要由运行它的 `steam` 用户更新自身文件。若安装目录仍归 root，SteamCMD 自更新会失败。

**解决**: 使用包含 SteamCMD 目录 `chown` 修复的 Dockerfile 重新构建；若仍失败，先确认构建使用了项目内最新的 `docker/runtime/Dockerfile*`，并检查完整日志中是否存在权限或网络错误。

### 问题：SteamCMD 报 `libcurl.so: cannot open shared object file`

**原因**: Debian 12 的基础镜像只有 32 位 `libcurl` 运行库时，SteamCMD 仍可能直接加载未版本化的 `libcurl.so`；缺少开发包提供的链接文件会导致 bootstrap 在下载 DST 前退出。

**解决**: 当前 `Dockerfile.build-base` 和 `Dockerfile.runtime-base` 都已加入 `libcurl4-gnutls-dev:i386`。把更新后的 `docker/runtime/Dockerfile*` 部署到服务器后重新创建基础镜像和 Release；不要只重试旧的基础镜像或构建缓存。

### 问题：SteamCMD bootstrap 退出码 124

**原因**: 退出码 124 表示 `timeout` 终止了 SteamCMD。bootstrap 需要先完成 SteamCMD 自更新；服务器到 Steam CDN 速度较慢时，10 分钟可能不够。

**解决**: 当前默认 bootstrap 超时为 3600 秒，DST 下载阶段为 1800 秒。远端构建把 bootstrap 超时传给 `Dockerfile.build-base`，宿主机下载容器使用 `STEAMCMD_DOWNLOAD_DEADLINE` 作为 `timeout`；本地路线仍通过 Dockerfile 的 `ARG` 传入两个超时。必须使用正整数与 `s`/`m`/`h` 单位，`0s`、无限等待与非法格式会拒绝执行。退出码 124 只能确认触及等待上限，不能据此认定网络慢是唯一原因。

对已经下发脚本的任务，手动运行远端构建脚本时可明确设置预算（环境变量须设置在服务器执行命令中，路径中的 Release ID 与参数保持一致）：

```bash
STEAMCMD_BOOTSTRAP_DEADLINE=4800s STEAMCMD_DOWNLOAD_DEADLINE=7200s \
  bash '/opt/famine-hub/scripts/image-build/<releaseId>/actions/build-dst-image.sh' '<同一规范 UUID v4 Release ID>'
```

在软件中点击构建时仍使用新版脚本默认预算，桌面客户端不会自动继承用户另一个 SSH 终端中的环境变量。超时重试保留同一构建步骤的工作目录，但不保证 SteamCMD 对每个文件断点续传；每个 Release 使用独立构建目录，尚无跨任务 SteamCMD 下载缓存。更长预算不等于已修复服务器到 Steam CDN 的网络问题。

### 构建命令复核（2026-10-30）

- `DOCKER_BUILDKIT=1 docker build --platform linux/amd64 --progress=plain` 使用宿主 Docker daemon 自带的 BuildKit，不再下载或启动独立 BuildKit 容器。
- 远端构建使用 `Dockerfile.build-base` 和 `Dockerfile.runtime-base`；本地构建路线保留 `docker/runtime/Dockerfile`。
- 构建前由 `prepare-base-image.sh` 准备 linux/amd64 的 `debian:12-slim`，用本地标签固定基础镜像；镜像源不可达时返回 `BASE_IMAGE_UNAVAILABLE`，不进入 Dockerfile。
- 远端构建固定管理面的本机 Docker socket，避免构建使用另一 daemon，而登记验证却查本机。
- 远端构建在清理共享上下文前获取独占文件锁；已有构建运行时返回 `IMAGE_BUILD_ALREADY_RUNNING`，不删除活动任务文件。
- 镜像验证/元数据/本地导出使用经过格式校验的本地 image ID；不要求尚未推送的镜像具有 `RepoDigests`。版本读取失败或非数字版本直接失败，不生成 `unknown` 成功结果。
- 远端后台命令重定向标准输入到 `/dev/null`；构建/日志查询的 Release ID 在建立 SSH 会话前强制校验规范 UUID v4。构建脚本从自身位置定位同一 Release 的配套脚本，后续任务不覆盖旧任务源文件。

回归入口为 `python3 scripts/test-image-build-commands.py`、`python3 scripts/test-steamcmd-install.py`。命令回归使用隔离 CLI fixture，不运行真实 Docker、sudo、APT 或联网下载，不能替代 Linux amd64 完整构建验收。

### 问题：SteamCMD 生成 `assert_*.dmp` 并以退出码 254 退出

**排查**: 退出码 254 和 minidump 上传信息不能单独确定根因。优先核对 Docker Engine：29.4.2 的默认 seccomp 配置会阻断 32 位程序使用的 `socketcall`，Docker 官方已记录 SteamCMD 会因此失败；29.4.3 修复了这个回归。

**解决**: 先检查服务器版本：

```bash
docker version --format '{{.Server.Version}}'
```

如果是 `29.4.2`，升级 Docker Engine 至 `29.4.3` 或更新版本并重启 Docker。当前构建脚本会在构建开始前拒绝该版本，并返回 `DOCKER_ENGINE_INCOMPATIBLE`，避免等待 SteamCMD 超时。不要使用 `seccomp=unconfined` 绕过此问题；Docker 官方建议使用已修复版本。

如果不是受影响版本，读取 `/var/lib/famine-hub/build-<releaseId>.log` 中 minidump 首次出现前的断言、Fatal Error 与 bootstrap 日志再判断；不要仅增加超时或把退出码 254 视为成功。安装脚本现在额外输出本轮阶段日志末尾 80 行，桌面构建日志保留末尾 120 行。

服务器报告为 `29.9.2` 时，不能再直接套用 `29.4.2` 的版本回归结论。下载到某个百分比也不等于网络是唯一原因；必须读取退出前的断言。安装脚本失败摘要现在额外摘录原始断言、Fatal Error 和更新失败信息（每个日志最多 4 行、每行最多 512 字符），放在诊断末尾，避免被下载进度或 minidump 上传信息挤出桌面日志范围。该摘要只辅助诊断，不改变原始退出码或成功条件。

官方依据（2026-10-25 核对）：[Docker Engine 29.4.2 已知问题](https://docs.docker.com/engine/release-notes/29/#2942)、[29.4.3 修复记录](https://docs.docker.com/engine/release-notes/29/#2943)。

### 问题：传输文件失败

**原因**: SSH 权限或磁盘空间不足

**解决**:
```bash
# 检查远端空间
ssh ${SSH_HOST} "df -h /var/lib/famine-hub"

# 检查目录权限
ssh ${SSH_HOST} "ls -la /var/lib/famine-hub/staging"
```

### 问题：导入时提示 CATALOG_INVALID

**原因**: catalog 文件损坏

**解决**:
```bash
# 在远端主机上重新初始化 catalog（需要 sudo）
ssh ${SSH_HOST} "sudo python3 -c '
import json
from pathlib import Path
ROOT = Path(\"/var/lib/famine-hub\")
host = json.loads((ROOT / \"host.json\").read_bytes())
catalog = {
    \"schemaVersion\": 1,
    \"hostId\": host[\"hostId\"],
    \"releases\": []
}
(ROOT / \"releases\").mkdir(exist_ok=True)
(ROOT / \"releases\" / \"catalog.json\").write_text(
    json.dumps(catalog, indent=2)
)
print(\"Catalog 已重新初始化\")
'"
```

## 下一步

Release 导入成功后，您可以：

1. 在实例创建页面选择该 release
2. 配置世界参数
3. 分配端口
4. 创建并启动专服

创建流程见 [项目 README](../README.md)。
