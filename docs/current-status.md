# 荒野中枢 - 当前状态

- 版本：1.0.5（Bate）
- 平台：Windows/macOS 桌面端；远端 Linux amd64
- 部署：Docker Compose

## 已接入

### 主机

- SSH 指纹扫描、确认和系统凭据库。
- 密码/私钥认证、只读登录验证、hostId 发现。
- 主机环境检查、管理目录初始化和 Docker/Compose 安装。
- 主机连接编辑、删除和失效凭据清理。

### Release

- DST runtime 镜像构建、登记和基础验证。
- Release 导入、删除和引用检查。
- 构建缓存预览与清理；只删除未被 catalog 引用的 dangling 镜像。
- 构建前磁盘空间预检；空间不足不开始下载。
- 构建成功或失败后自动清理构建上下文、BuildKit 缓存；构建日志保留 7 天。
- 构建使用共享 `dst-build-base` / `dst-runtime-base` 镜像；DST 下载到 `dst/<releaseId>`，Compose 只读挂载，不再为每个 Release 构建镜像。
- 新 Release 先复制最近 DST 目录作为增量基线，再执行 `app_update`，不额外保留下载缓存。
- 旧镜像形式 Release 继续兼容；删除 P2 Release 会清理对应 DST 目录，共享运行时镜像保留。
- Release 列表显示镜像大小和使用中/可回收提示；“清理磁盘”合并清理构建缓存与可回收 Release，构建中禁止删除。
- 登记后清理遗留 `artifact.tar`。
- 构建和验证任务进入全局任务记录。

### 专服

- 创建、启动、停止、重启、删除。
- 专服列表、概览、运行摘要、分片日志和诊断。
- 专服列表进入时读取一次、手动刷新，不再后台轮询；运行采集合并为每个分片一次容器探测。
- 删除前保护检查；删除会永久移除实例目录、备份和旧归档目录。

### 备份

- 归档只保存世界存档（`data/Master`、`data/Caves`），不包含服务器配置、Release、Compose 或凭据。
- 恢复使用当前活动 Release，只替换世界存档；兼容旧归档。
- 备份 `dstBuild` 高于当前 build 时阻断恢复；MOD 差异只返回 warning。
- MOD 应用的完整回滚快照保存在任务目录，不进入备份列表。

### DST 更新

- Release 管理页按主机检查最新 DST build，缓存检查结果并列出匹配的已验证 Release。
- 检查状态持久化为 running/ok/failed 并记录阶段；切换页面后重新进入会恢复“检查中”并轮询，重复点击不会再次运行 SteamCMD。
- 检查优先使用已 bootstrap 的 `dst-build-base` 工具镜像，SteamCMD 最长 600 秒；超时/失败会清理检查容器，残留容器在下次检查前回收。
- 专服概览页提供“切换 DST 版本”：预览目标 Release、停服分片、世界存档保护点和回滚能力。
- 切换任务使用一次性 systemd worker，持有 heavy/实例锁；创建保护点、切换 active-release/manifest/compose、重建容器并验证。
- 失败时按保护点和 transaction 恢复旧 Release + 旧存档；其他实例摘要变化会判失败。
- 当前 worker 未写入 heavyQueue，取消只在阶段检查点生效；跨实例排队依赖 heavy 锁。

### 世界配置

- Master/Caves 分片切换。
- 世界规则和世界生成参数。
- Token、密码保存与按需显示。
- 保存、应用和运行分片验证。
- 配置解析失败时显示诊断。

### MOD

- 主机 MOD 库支持 Workshop 链接/ID 和 Steam 合集导入。
- 检查更新通过 Steam `contentHandle` 与固定产物 `sourceHandle` 比较。
- 批量检查、批量更新和同步到专服。
- 专服 MOD 从主机库多选加入、启用/禁用、配置编辑和应用。
- 解析失败时回退主机库名称/版本；数字选项描述可正常解析。
- 主机库图片在版本变化后更新。
- 主机 MOD 更新成功后自动清理旧版本：每个 Workshop 保留最新版本，实例/活动 MOD Release/备份引用不删除。

### 数据与任务

- 本地 SQLite 连接元数据和追加式迁移。
- 任务与操作统一进入任务记录。
- 任务跨页面继续执行；远端任务由主机 controller 守护。
- 主题、应用设置和从设置返回原页面。

## 边界

- 不实现跳板机、SSH agent、keyboard-interactive 或任意远端命令输入。
- 浏览器模式只用于界面预览，不连接真实后端。
- macOS 安装包签名和 Windows 实机发布尚未配置。
- Docker、DST 和 Steam 网络行为仍受目标主机与本机网络环境影响。

## 规划中

以下能力尚未实现，仅有设计文档：

- 饥荒服务器更新：[DST 更新设计](../spec/dst-update-plan.md)。
- 定时准热备份与保留策略：[定时备份设计](../spec/scheduled-backup-plan.md)。
- 玩家信息列表与踢出：[玩家列表设计](../spec/player-list-plan.md)。
- 总体优先级与难度：[运维增强规划](../spec/ops-enhancements-plan.md)。

## 相关文档

- [架构概览](architecture-overview.md)
- [专服诊断](diagnostics.md)
- [备份与恢复文件边界](backup-restore-boundaries.md)
- [主机 MOD 库](host-mod-library.md)
- [DST Release 准备](dst-release-setup.md)
- [接口契约](../spec/api-contract.md)
- [错误文案审计](error-copy-audit.md)
