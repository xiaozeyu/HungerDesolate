# 阶段 0 Docker 验证准备清单

- 创建日期：2026-10-19
- 状态：准备中
- 目标：从干净 Ubuntu 26.04 验证三专服联网、更新、恢复

## 1. 环境准备

### 测试主机要求

| 项目 | 要求 | 状态 |
|-----|------|------|
| 操作系统 | Ubuntu 26.04 LTS / Linux amd64 | ⏳ 待准备 |
| 初始状态 | 无 Docker 包/daemon | ⏳ 待准备 |
| SSH 访问 | root 或 sudo 权限 | ⏳ 待准备 |
| 网络 | 可从不同外部网络访问 | ⏳ 待准备 |
| 资源 | 至少 8GB RAM、50GB 磁盘 | ⏳ 待准备 |
| 端口 | 可分配公网 UDP 端口 | ⏳ 待准备 |

### 环境记录模板

准备好后填写：

```yaml
host:
  distribution: Ubuntu
  version: 26.04
  codename: __填写__
  kernel: __填写__ (uname -r)
  architecture: amd64

docker:
  apt_source: https://download.docker.com/linux/ubuntu
  gpg_key_url: https://download.docker.com/linux/ubuntu/gpg
  installed_packages:
    - docker-ce: __版本__
    - docker-ce-cli: __版本__
    - containerd.io: __版本__
    - docker-buildx-plugin: __版本__
    - docker-compose-plugin: __版本__
  
  daemon_version: __docker version 输出__
  compose_version: __docker compose version 输出__
  data_root: /var/lib/docker
  socket: /var/run/docker.sock

resources:
  memory_total_gb: __填写__
  disk_path: __管理目录所在盘__
  disk_total_gb: __填写__
  disk_available_gb: __创建前可用__
```

## 2. DST Release 准备

### 获取 DST 服务端

根据 `spec/backend-implementation.md` §11，需要：

1. **SteamCMD 方式**（推荐）
   ```bash
   # 安装 SteamCMD
   # Ubuntu/Debian
   sudo add-apt-repository multiverse
   sudo dpkg --add-architecture i386
   sudo apt update
   sudo apt install steamcmd
   
   # 下载 DST 服务端 (App ID: 343050)
   steamcmd +login anonymous \
     +force_install_dir /opt/dst-server \
     +app_update 343050 validate \
     +quit
   ```

2. **记录产物信息**
   ```yaml
   dst:
     acquisition_method: steamcmd
     app_id: 343050
     install_path: /opt/dst-server
     build_number: __查看 steamapps/appmanifest_343050.acf__
     download_date: __填写__
     artifact_size_mb: __du -sh 输出__
   ```

### 构建 Docker 镜像

创建 `docker/dst-server/Dockerfile`：

```dockerfile
FROM ubuntu:26.04

# 安装运行时依赖
RUN apt-get update && apt-get install -y \
    lib32gcc-s1 \
    lib32stdc++6 \
    libcurl4-gnutls-dev:i386 \
    && rm -rf /var/lib/apt/lists/*

# 复制 DST 服务端文件
COPY dst-server /opt/dst-server

# 工作目录
WORKDIR /opt/dst-server/bin64

# 默认命令（不直接运行，由 Compose 覆盖）
CMD ["./dontstarve_dedicated_server_nullrenderer_x64"]
```

**构建命令**：
```bash
docker build -t dst-server:v1 docker/dst-server/
docker images --digests | grep dst-server
# 记录 IMAGE DIGEST
```

### 创建 Release Catalog

在远端主机执行：

```bash
# 创建目录
mkdir -p /var/lib/famine-hub/releases/staging/release-$(date +%Y%m%d)-001

# 准备产物
cd /var/lib/famine-hub/releases/staging/release-$(date +%Y%m%d)-001
tar -czf dst_server.tar.gz /opt/dst-server
ARTIFACT_DIGEST=$(sha256sum dst_server.tar.gz | awk '{print $1}')

# 获取镜像 digest
IMAGE_DIGEST=$(docker images --digests dst-server:v1 --format "{{.Digest}}")
DST_BUILD=$(cat /opt/dst-server/version.txt) # 或从 appmanifest 读取

# 创建 release.json
cat > release.json <<EOF
{
  "schemaVersion": 1,
  "releaseId": "release-$(date +%Y%m%d)-001",
  "platform": "linux/amd64",
  "image": "dst-server:v1",
  "imageDigest": "$IMAGE_DIGEST",
  "dstBuild": "$DST_BUILD",
  "artifactDigest": "sha256:$ARTIFACT_DIGEST",
  "scriptVersion": "1.0.0",
  "verified": true,
  "commands": {
    "master": ["./dontstarve_dedicated_server_nullrenderer_x64", "-console", "-cluster", "Master", "-shard", "Master"],
    "caves": ["./dontstarve_dedicated_server_nullrenderer_x64", "-console", "-cluster", "Caves", "-shard", "Caves"]
  }
}
EOF
```

**注意**：`verified: true` 只是准备阶段标记，`runtimeVerified` 需要容器验证通过后设置。

## 3. 验证实验清单

根据 `spec/implementation-roadmap.md` §3：

| 编号 | 实验 | 准备项 | 状态 |
|------|------|--------|------|
| N01 | 三实例创建启动 | 准备 A/B/C 三个专服配置 | ⏳ |
| N02 | 同号端口 | 验证 UDP 端口分配 | ⏳ |
| N03 | Master/Caves | 验证双向迁移 | ⏳ |
| N04 | 公网大厅 | 外网可发现 | ⏳ |
| N05 | 玩家连接 | 客户端连接测试 | ⏳ |
| N06 | LAN 发现 | 局域网发现 | ⏳ |
| N07 | MOD/UGC | 准备测试 MOD | ⏳ |
| N08 | 发布切换 | 准备两个版本 | ⏳ |
| N09 | 回滚 | 备份恢复测试 | ⏳ |
| N10 | SSH 断线 | 验证任务持续 | ⏳ |
| N11 | 异常恢复 | worker 崩溃测试 | ⏳ |
| N12 | 开机启动 | 重启策略测试 | ⏳ |
| N13 | 权限/秘密 | 安全检查 | ⏳ |
| N14 | 资源预算 | 性能测试 | ⏳ |

## 4. Runtime 验证流程

创建容器后需要执行：

```bash
# 1. 创建 network
docker network create \
  --label "app.hungerdesolate.schema=1" \
  --label "app.hungerdesolate.hostId=<hostId>" \
  --label "app.hungerdesolate.instanceId=<instanceId>" \
  fh_<instanceId>_instance

# 2. 创建容器（不启动）
docker compose -f /var/lib/famine-hub/instances/<instanceId>/compose.yaml \
  create

# 3. 验证容器
MASTER_ID=$(docker ps -a --filter "label=app.hungerdesolate.instanceId=<instanceId>" \
  --filter "label=app.hungerdesolate.shard=master" --format "{{.ID}}")

# 检查 labels
docker inspect $MASTER_ID --format '{{json .Config.Labels}}'

# 检查挂载
docker inspect $MASTER_ID --format '{{json .Mounts}}'

# 检查端口
docker inspect $MASTER_ID --format '{{json .HostConfig.PortBindings}}'

# 4. 记录 runtime.json
cat > /var/lib/famine-hub/releases/staging/release-$(date +%Y%m%d)-001/runtime.json <<EOF
{
  "schemaVersion": 1,
  "releaseId": "release-$(date +%Y%m%d)-001",
  "runtimeVerified": true,
  "verification": "passed",
  "verifiedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "networkId": "$(docker network ls --filter name=fh_ --format '{{.ID}}')",
  "containerIds": {
    "master": "$MASTER_ID",
    "caves": "$(docker ps -a --filter label=app.hungerdesolate.shard=caves --format '{{.ID}}')"
  }
}
EOF
```

## 5. 阻断项处理

### 当前已知阻断

1. **CREATE_RELEASE_REQUIRED**
   - 原因：catalog.json 不存在或无 verified release
   - 解决：完成 §2 DST Release 准备

2. **CREATE_DOCKER_RUNTIME_REQUIRED**
   - 原因：release 未通过 runtime 验证
   - 解决：完成 §4 Runtime 验证

3. **DEPLOYMENT_VALIDATION_REQUIRED**
   - 原因：游戏镜像和 Compose 部署兼容性未验证
   - 解决：完成 N01-N14 实验

### 解除阻断顺序

```
准备 DST 服务端 (§2)
  ↓
构建镜像并创建 catalog.json
  ↓
在应用中点击"验证 release" (前端已实现)
  ↓
importInstanceRelease 检查 staging
  ↓
创建测试容器验证 runtime
  ↓
更新 catalog.json 设置 runtimeVerified: true
  ↓
重新检查 → 显示"1 个可用 release"
  ↓
可以创建容器
```

## 6. 下一步行动

### 立即（阻断解除前）

由于阶段 0 准备需要时间，建议并行开发：

1. **世界配置 UI**（不依赖真实容器）
   - [ ] 参数编辑表单
   - [ ] Token 输入组件
   - [ ] desired/deployed 状态展示
   - [ ] 保存逻辑与 revision CAS

2. **视觉验收**（不依赖后端）
   - [ ] 录制四组截图
   - [ ] 键盘交互测试

### 阶段 0 准备（3-5 天）

1. 准备 Ubuntu 26.04 测试主机
2. 获取 DST 服务端（SteamCMD）
3. 构建 Docker 镜像
4. 创建 release catalog
5. Runtime 验证
6. 执行 N01-N14 实验

### 阶段 0 完成后

1. 完整创建 → 配置 → 启动流程
2. 备份与恢复
3. MOD 管理
4. 更新机制

## 7. 风险与备选

| 风险 | 影响 | 备选方案 |
|-----|------|---------|
| DST 获取困难 | 阻断阶段 0 | 使用测试镜像先验证流程 |
| 网络不通 | N04/N05 失败 | 先验证本地功能 |
| 资源不足 | 三专服无法同时运行 | 减少为两专服 |
| 端口冲突 | N02 失败 | 调整端口范围 |

## 8. 成功标准

阶段 0 通过条件：

- ✅ 三专服可同时创建并运行
- ✅ 完整端口计划可对应
- ✅ Master/Caves 双向迁移成功
- ✅ 外网玩家可连接
- ✅ 备份恢复一致
- ✅ SSH 断线任务持续
- ✅ 精确版本和资源数据记录

完成后输出：`spec/evidence/stage-0-report.md`
