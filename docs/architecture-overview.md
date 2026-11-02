# 荒野中枢架构概览

## 核心设计理念

**多实例隔离 + Docker Compose + 完整身份追踪**

不同于旧项目的固定单实例部署，新项目从设计上支持同一主机运行多个独立专服，每个专服有自己的配置、MOD、存档、端口和容器。

## 三层对象模型

```
Linux 主机 (serverId + hostId)
  ├─ 专服 A (instanceId)
  │   ├─ Master 分片 (容器)
  │   └─ Caves 分片 (容器)
  ├─ 专服 B (instanceId)
  │   ├─ Master 分片
  │   └─ Caves 分片
  └─ 专服 C (instanceId)
      └─ Master 分片 (无洞穴)
```

### 主机 (Host)
- **本地标识**: `serverId` (UUID) - 本机保存的连接记录
- **远端标识**: `hostId` (UUID) - 远端持久化在 `/var/lib/famine-hub/host.json`
- **职责**: 计算资源、SSH 连接、端口空间、实例注册表

### 专服 (Instance)
- **标识**: `instanceId` (UUID) - 不可变，独立于名称
- **职责**: 世界配置、MOD、存档、备份策略、运行状态
- **隔离**: 每个实例独立的 Compose 项目、网络、目录

### 分片 (Shard)
- **标识**: `instanceId + shardKind` (master/caves)
- **职责**: 分片配置、进程、日志、游戏端口
- **关联**: 同一实例的分片通过项目内部网络通信

## Docker Compose 部署

每个专服 = 一个 Compose 项目

```yaml
# fh_<instanceId 去连字符>
services:
  master:
    image: hungerdesolate/dst-runtime@sha256:...
    volumes:
      - /var/lib/famine-hub/instances/<instanceId>/config:/srv/data/config:rw
      - /var/lib/famine-hub/instances/<instanceId>/data/Master:/srv/data:rw
      - /var/lib/famine-hub/instances/<instanceId>/mods/releases/<releaseId>/Master:/srv/ugc:rw
    ports:
      - "11000:11000/udp"  # game
      - "8766:8766/udp"    # auth
      - "27016:27016/udp"  # master_server
    networks:
      - instance
    restart: unless-stopped

  caves:
    image: hungerdesolate/dst-runtime@sha256:...
    depends_on:
      - master
    # ... 类似配置，不同端口和数据目录

networks:
  instance:
    driver: bridge
```

**关键特性：**
- 对外端口 = 容器端口（hostPort:containerPort 相同）
- DST 二进制包含于 digest 固定的 runtime image，Master/Caves 使用同一 Release
- 配置、存档、MOD 可写（分片独立）
- 固定镜像 digest（不使用浮动 tag）

## 端口分配策略

### 对外端口（宿主机预留）
- Master 游戏端口：11000
- Master Steam 认证：8766
- Master Steam 查询：27016
- Caves 游戏端口：11001
- Caves Steam 认证：8767
- Caves Steam 查询：27017

### 内部端口（项目网络内）
- Master 分片通信：10888（不发布到宿主机）

**冲突检测：**
1. 检查注册表预留（停止的实例仍占预留）
2. 检查实际占用（netstat/ss/lsof + Docker publish）
3. 预留成功后才创建实例

## Release 模型

Release = 一个经过验证的 DST 版本组合

```json
{
  "releaseId": "uuid",
  "platform": "linux/amd64",
  "image": "hungerdesolate/dst-runtime",
  "imageDigest": "sha256:...",
  "dstBuild": "639491",
  "artifactDigest": "sha256:...",
  "scriptVersion": "V2-20261019",
  "verified": true,
  "runtimeVerified": true,
  "commands": {
    "master": ["/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64", ...],
    "caves": ["/srv/dst/bin64/dontstarve_dedicated_server_nullrenderer_x64", ...]
  }
}
```

**验证层级：**
1. **基础验证** (`verified: true`)
   - 镜像 digest 匹配
   - 元数据完整性
   - 命令格式正确

2. **运行时验证** (`runtimeVerified: true`)
   - 容器可以启动
   - DST 进程运行正常
   - 端口正确监听

**当前限制：**
- 需要手动构建镜像（`docker/runtime/build.sh`）
- 需要手动传输到远端
- 需要手动 `docker load` 导入

**未来计划：**
- 官方预构建镜像仓库
- 自动拉取和验证
- 版本更新通知

## 数据流

### 创建实例
```
用户填写名称 + 选择 Release
  ↓
创建专服 (create_instance)
  ├─ 检查 (同一命令内，只读)
  │   ├─ HostTarget / 连接版本 / 注册表版本
  │   ├─ 端口占用与 release 证据
  │   ├─ Docker / Compose / 管理目录
  │   └─ 阻断项 → CREATE_BLOCKED，不进入接纳
  ↓ 检查通过
  ├─ 注册表锁
  ├─ 预留端口（与检查计划一致）
  ├─ 生成 instanceId
  ├─ 创建目录结构
  ├─ 写入 manifest
  ├─ 渲染 Compose
  └─ 创建容器（不启动）
  ↓
任务轮询与验证结果
  ├─ 容器标签正确
  ├─ 挂载点正确
  └─ 网络配置正确
```

### 查询实例
```
list_instances
  ↓
读取 registry.json
  ├─ 验证 hostId
  ├─ 获取实例列表
  └─ 读取每个 manifest
  ↓
聚合响应
  ├─ 基础信息（名称、模式、洞穴）
  ├─ 运行状态（从 runtime.json）
  └─ 任务状态（从 tasks/）
```

### 前端状态隔离
```
用户切换：主机 X → 主机 Y
  ↓
contextEpoch++
  ↓
X 的迟到响应到达
  ↓
校验 contextEpoch ❌
  ↓
丢弃（不写入 Y 的数据）
```

## 目录结构

```
/var/lib/famine-hub/                   # 管理根
├─ host.json                           # hostId + schema
├─ registry.json                       # 实例注册表 + 端口预留
├─ releases/
│  └─ catalog.json                     # Release 目录
├─ staging/
│  └─ <releaseId>/
│     ├─ release-metadata.json
│     └─ artifact.tar                  # Docker 镜像 tar
├─ instances/
│  └─ <instanceId>/
│     ├─ manifest.json                 # 实例元数据
│     ├─ runtime.json                  # 运行时证据
│     ├─ compose.yaml                  # 生成的 Compose 配置
│     ├─ config/
│     │  ├─ cluster.ini
│     │  ├─ Master/server.ini
│     │  └─ Caves/server.ini
│     ├─ data/
│     │  ├─ Master/                    # Master 存档
│     │  └─ Caves/                     # Caves 存档
│     ├─ mods/releases/<releaseId>/
│     │  ├─ Master/                    # Master MOD
│     │  └─ Caves/                     # Caves MOD
│     ├─ backups/
│     ├─ staging/
│     ├─ tasks/
│     └─ locks/
└─ retired/                            # 删除后的归档
```

## 锁与并发

### 锁类型
1. **registry.lock** - 注册表/端口/队列短临界区
2. **heavy.lock** - 同主机重型任务限流
3. **maintenance.lock** - 单实例维护锁
4. **task 状态写锁** - 任务序号防冲突

### 获取顺序
```
registry → heavy → maintenance → task
```

**禁止反向等待** - 例如在 maintenance 内等待 registry

## 任务模型

### 任务状态机
```
queued → running → succeeded
               ↓
            failed
               ↓
         interrupted (崩溃/断线)
```

### 持久化阶段
```
preflight → download → stop → backup →
publish → start → verify → finalize
```

每个阶段：
1. 写 `started`
2. 执行动作
3. 写证据/摘要
4. 写 `completed`

**断线恢复：**
- 检查 journal
- 对比实际状态
- 继续未完成阶段或标记 interrupted

## 前端架构

### 请求隔离
```typescript
// 每个详情视图
const context = {
  serverId: string,
  hostId: string,
  instanceId: string,
  contextEpoch: number  // 每次进入/离开递增
}

// 每个 query
const request = {
  ...context,
  requestSeq: number    // 每次请求递增
}

// 响应校验
if (response.contextEpoch !== current.contextEpoch) {
  discard()
}
if (response.requestSeq !== latest.requestSeq) {
  discard()
}
```

### 状态分类
1. **编辑草稿** - 本地输入，后台刷新不覆盖
2. **期望配置** - 已保存，带 revision
3. **实际部署** - 远端 manifest + 文件摘要
4. **运行事实** - 容器/进程/日志，带采集时间

## 安全边界

### 凭据管理
- 密码/私钥 → 系统 keyring
- Klei Token → 受保护远端文件
- cluster_key → 每实例随机生成

**不进入：**
- SQLite
- 草稿
- 日志
- 任务状态
- Compose 内联环境

### 路径安全
- 受控根 + 校验 UUID + 固定子路径
- 拒绝 `..`、符号链接、控制字符
- SFTP 上传到专属 staging
- 归档解压容量上限

### 容器权限
- 非 root (UID 1000:1000)
- 不 privileged
- 不挂 Docker socket
- 限制可写目录

## 当前重点

当前版本已完成主机连接、环境与 Docker 安装、Release 构建/导入/验证、专服生命周期、世界配置、主机/专服 MOD、备份恢复、诊断、构建缓存清理和全局任务记录。

后续工作只保留真实功能缺口，不再维护阶段工作包。具体能力与边界见：

- [当前状态](current-status.md)
- [备份与恢复文件边界](backup-restore-boundaries.md)

## 相关文档

- [后端实施文档](../spec/backend-implementation.md) - Rust/远端脚本设计
- [前端实施文档](../spec/frontend-implementation.md) - Vue/状态管理设计
- [DST Release 准备指南](./dst-release-setup.md) - 如何构建镜像
- [当前状态](./current-status.md) - 已完成功能与限制
