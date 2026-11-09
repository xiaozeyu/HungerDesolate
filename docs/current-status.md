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
- 构建缓存预览与全部清理。
- 构建和验证任务进入全局任务记录。

### 专服

- 创建、启动、停止、重启、删除。
- 专服列表、概览、运行摘要、分片日志和诊断。
- 删除前保护检查；删除会永久移除实例目录、备份和旧归档目录。

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
