# DST Release 准备指南

本文档说明如何为荒野中枢准备第一个 DST 服务端 release。

## 概念

- **Release**: 一个经过验证的 DST 服务端版本，包含 Docker 镜像和运行配置
- **Runtime 镜像**: 包含 DST Dedicated Server 的 Docker 镜像
- **Release Catalog**: 主机级别的 release 注册表

## 方式一：本地构建（推荐用于开发测试）

### 1. 构建 runtime 镜像

```bash
cd docker/runtime
./build.sh
```

脚本会：
- 通过 SteamCMD 下载 DST Dedicated Server
- 构建 Docker 镜像
- 生成 `release-metadata.json` 和 `dst-runtime-*.tar` 文件

### 2. 传输到远端主机

将构建产物传输到远端主机的 staging 目录：

```bash
# 假设 release ID 为 a1b2c3d4-e5f6-4789-a123-456789abcdef
RELEASE_ID="<从 release-metadata.json 中获取>"
SSH_HOST="user@your-server"
REMOTE_STAGING="/var/lib/famine-hub/staging/${RELEASE_ID}"

# 在远端创建 staging 目录
ssh ${SSH_HOST} "sudo mkdir -p ${REMOTE_STAGING} && sudo chown $(whoami) ${REMOTE_STAGING}"

# 传输文件
scp release-metadata.json ${SSH_HOST}:${REMOTE_STAGING}/
scp dst-runtime-*.tar ${SSH_HOST}:${REMOTE_STAGING}/artifact.tar
```

### 3. 在应用中导入

1. 打开荒野中枢应用
2. 进入主机管理页面
3. 点击"导入 Release"或在创建实例时选择"导入新 Release"
4. 应用会检测到 staging 中的 release 并执行导入

## 方式二：使用预构建镜像（未来支持）

未来版本将支持：
- 从容器仓库拉取预构建镜像
- 自动检测和导入官方 release

## Release 验证

通过软件内构建成功后，构建脚本会核对 Docker image ID 与 Release digest、检查 `version.txt`，并用 `ldd` 检查 DST 二进制可执行权限和动态库依赖，再将基础验证通过的 Release 登记到主机 catalog。这些检查不代表 DST 服务进程及网络端口已通过运行时验证。

Release 登记会自动执行基础验证，通过后即可用于创建实例：

1. **基础验证** (`verified: true`)
   - 镜像 digest 与本地主机 Docker image ID 匹配
   - `version.txt` 与 Release 元数据一致
   - DST 二进制可执行，动态库依赖齐全

2. **隔离运行验证** (`runtimeVerified: true`，可选)
   - 容器可以成功启动
   - DST 进程可以正常运行
   - 网络端口正确监听

创建只要求基础验证通过。运行时验证提供额外的双分片启动证据，不会把未运行验证的 Release 标成运行时验证通过。实例创建只生成并验证容器资源，不代表游戏已经启动或玩家可以连接。

若要进行隔离运行验证，在 DST 游戏主菜单打开“账户”并生成服务器令牌。这个令牌文件通常不会预先存在；生成后将令牌内容保存为 `cluster_token.txt`，再与下面的配置文件一起放入 ZIP。令牌是凭据，不要提交到代码仓库或发给他人。

ZIP 根目录必须直接包含以下文件和目录（不要再套一层 `Cluster_1/` 文件夹）：

```text
cluster.ini
cluster_token.txt
Master/server.ini
Caves/server.ini
```

`cluster.ini`、`Master/server.ini` 和 `Caves/server.ini` 可从本机已生成的 DST 世界配置目录取得；如果还没有这些配置，可在游戏内创建一个本地世界后复制对应文件。当前验证器要求 Master 和 Caves 两个分片配置都存在。完成后在 Release 管理页选择 ZIP 并运行验证。

验证会在主机 `/run` 临时目录中创建 LAN-only 集群，启动两个分片并检查配置端口；不发布宿主端口、不挂载实例存档。配置包只在验证期间传输和使用，容器、网络与临时文件随后清理，catalog 只保存脱敏验证结果。

## 故障排查

### 问题：构建镜像时 SteamCMD 下载失败

日志中的 SteamCMD 自更新、Steam 匿名登录和 DST 内容下载是不同阶段。仅凭末尾 `CWorkThreadPool` 清理信息或退出码 8，不能断定具体网络、权限或空间问题。

当前本地和软件内构建共用 `docker/runtime/install-dst.sh`：先独立执行 SteamCMD bootstrap，再匿名下载并验证 App 343050。对退出码 8、42 和超时 124 最多尝试 3 次；其他错误立即失败。失败输出 `STEAMCMD_BOOTSTRAP_FAILED` 或 `STEAMCMD_DOWNLOAD_FAILED`、原始退出码、隔离构建目录下 SteamCMD 内部日志末尾及磁盘/ inode 信息。重试耗尽不登记 Release。

软件内构建状态使用宿主脚本的最终结果标记；SteamCMD 重试期间的 `error:` 日志不会让页面提前停止观察。只有镜像构建和 Release 登记均完成后才标记成功。

下载步骤必须同时满足退出码 0、App 343050 完整安装标记、可执行的 DST binary、有效版本文件及非空 `data/databundles/scripts.zip`；缺少任一项返回 `DST_ARTIFACT_INVALID`，不会生成成功镜像。真实 Steam/CDN 联网下载仍须在构建主机实测。

**解决**:
```bash
# 检查网络连接
curl -I https://steamcdn-a.akamaihd.net

# 重试构建
./build.sh
```

### 问题：SteamCMD 报 `Failed to apply update` 或 `Failed to load steamconsole.so`

**原因**: SteamCMD 解压后需要由运行它的 `steam` 用户更新自身文件。若安装目录仍归 root，SteamCMD 自更新会失败。

**解决**: 使用包含 SteamCMD 目录 `chown` 修复的 Dockerfile 重新构建；若仍失败，先确认构建使用了项目内最新的 `docker/runtime/Dockerfile`，并检查完整日志中是否存在权限或网络错误。

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

查看 [创建第一个专服](./create-first-instance.md) 了解详细步骤。
