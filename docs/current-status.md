# 荒野中枢 - 当前实施状态

## 已完成功能

### ✅ 基础设施
- [x] 主机管理（SSH 连接、指纹确认、凭据管理）
- [x] 主机初始化（创建管理目录、生成 hostId）
- [x] Docker 自动安装（Ubuntu 26.04 root 账户）
- [x] 端口分配与注册表管理

### ✅ Release 管理
- [x] Release catalog 查询（列出已导入的 release）
- [x] Release 导入预览（检查 staging 中的 release）
- [x] Release 导入执行（将 release 注册到 catalog）
- [x] Runtime 镜像构建与两阶段 Release 验证入口
- [x] Dockerfile（基于 SteamCMD 下载 DST）

### ✅ 实例管理
- [x] 实例创建（生成 manifest、预留端口、创建目录结构）
- [x] Compose 配置生成（基于 release 和 manifest）
- [x] 容器创建验证（Docker 容器启动检查）
- [x] 无容器实例移出列表（活动任务/容器/网络会阻止；实例目录移入 retired 保留）
- [x] 无引用 DST Release 删除（检查实例/任务/容器引用，共用镜像不删除）

### ⚠️  阶段 0 验证
- [ ] 真实 Ubuntu 26.04 主机部署
- [ ] 三个实例同时运行验证
- [ ] 公网/LAN/Steam 网络验证
- [ ] MOD/UGC 一致性验证
- [ ] 断线/重启对账验证

## 当前工作流程

### 1. 准备主机

```bash
# 在应用中添加 SSH 连接
# → 主机管理页面 → 添加连接

# 确认指纹 → 连接

# 初始化主机（创建 /var/lib/famine-hub）
# → 主机环境页面 → 初始化主机

# （可选）自动安装 Docker
# → 主机环境页面 → 安装 Docker
```

### 2. 构建和导入 DST Release

**本地构建镜像：**

```bash
cd docker/runtime
./build.sh
```

**传输到远端：**

```bash
# 获取 release ID
RELEASE_ID=$(jq -r '.releaseId' release-metadata.json)

# 创建 staging 目录
ssh user@host "sudo mkdir -p /var/lib/famine-hub/staging/${RELEASE_ID} && \
               sudo chown $(whoami) /var/lib/famine-hub/staging/${RELEASE_ID}"

# 传输文件
scp release-metadata.json user@host:/var/lib/famine-hub/staging/${RELEASE_ID}/
scp dst-runtime-*.tar user@host:/var/lib/famine-hub/staging/${RELEASE_ID}/artifact.tar
```

**在远端加载镜像：**

```bash
ssh user@host "cd /var/lib/famine-hub/staging/${RELEASE_ID} && \
               sudo docker load -i artifact.tar"
```

**在应用中导入：**

```
# → 实例列表页面 → 新建专服
# → 创建对话框会检测到 staging 中的 release
# → 选择该 release 完成导入
```

### 3. 创建专服实例

```
# → 实例列表页面 → 新建专服
# → 选择基础验证通过的 release 创建容器；Klei ZIP 可用于额外运行验证
# → 配置端口（或使用自动分配）
# → 确认创建
```

## 当前限制

### 🚧 手动步骤

1. **Release 需要手动构建和传输**
   - 必须在本地运行 `build.sh`
   - 必须手动 scp 文件到远端
   - 必须手动 `docker load` 导入镜像

2. **世界配置应用首版已接入**
   - 基础配置、凭据保存、世界规则编辑和无存档配置发布已接入
   - 有现存存档时保护备份能力尚未接入，因此应用会阻断，不覆盖现存数据
   - DST 隔离运行验证仍显示为额外状态，不是第一版应用门槛；发布配置不代表进程/公网连接已验证

3. **MOD 管理未实现**
   - 无法添加/删除 MOD
   - 无法配置 MOD 参数

4. **备份/恢复未实现**
   - 无法创建备份
   - 无法从备份恢复

5. **更新未实现**
   - 无法更新 DST 版本
   - 无法切换 release

### ⏳ 需要验证的功能

- Release runtime 验证需要真实 Linux Docker 主机与有效 Klei 配置包实测
- 游戏端口监听
- 分片通信（Master ↔ Caves）
- 公网大厅登记
- Steam 查询响应
- 玩家连接

## 下一步开发优先级

根据 `spec/implementation-roadmap.md`，接下来的工作包：

### S3-01: 世界配置 (P0)
- [ ] 世界配置 UI（游戏模式、季节、难度等）
- [ ] Token 管理（keyring 存储）
- [ ] 配置应用流程（desired → deployed）
- [ ] Revision 冲突检测

### S3-02: MOD 管理 (P0)
- [ ] MOD 搜索和添加
- [ ] MOD 配置 UI
- [ ] MOD 下载和应用
- [ ] UGC 一致性验证

### 阶段 0: 真实部署验证
- [ ] 在实际 Ubuntu 26.04 主机上完整部署
- [ ] N01-N14 全部验证场景
- [ ] 输出验证报告

## 如何参与

### 运行本地测试

```bash
# 前端检查
npm run check

# Rust 检查
cd src-tauri
cargo fmt --check
cargo check
cargo test
cargo clippy -- -D warnings
```

### 查看文档

- [DST Release 准备指南](./dst-release-setup.md)
- [后端实施文档](../spec/backend-implementation.md)
- [前端实施文档](../spec/frontend-implementation.md)
- [实施路线图](../spec/implementation-roadmap.md)

## 常见问题

### Q: 为什么需要手动构建镜像？

A: 当前处于阶段 0 验证期间，尚未建立自动构建和分发流程。未来版本将支持：
- 官方预构建镜像
- 从容器仓库自动拉取
- 版本更新通知

### Q: 可以使用社区 DST Docker 镜像吗？

A: 不推荐。社区镜像的目录结构、启动参数、权限模型可能与本项目设计不兼容。本项目的镜像：
- 固定非 root UID/GID (1000:1000)
- 明确的挂载点约定 (/srv/dst, /srv/data/config, /srv/data, /srv/ugc)
- 不包含自动更新逻辑

### Q: 创建实例后如何启动？

A: 当前版本的启动功能尚未实现。预期流程：
1. 创建实例（已完成）
2. 配置世界参数（S3-01，开发中）
3. 启动容器（阶段 0 验证后实现）

### Q: 如何查看实例状态？

A: 进入实例详情页面（概览、世界、MOD 等）。运行状态查询和容器监控在阶段 0 验证后完善。

## 更新日志

### 2026-10-19
- ✅ 创建 DST runtime Dockerfile
- ✅ 创建镜像构建脚本 (build.sh)
- ✅ 创建 release 准备和导入脚本
- ✅ 编写 DST release 准备指南
- ✅ 完成当前状态总结文档

---

**注意**: 本项目仍处于早期开发阶段。当前功能足以创建实例的基础结构，但完整的游戏服务器运行需要完成阶段 0 验证和 S3 工作包。
