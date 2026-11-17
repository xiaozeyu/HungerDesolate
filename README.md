# 荒野中枢 · Hunger Desolate

Windows/macOS 桌面控制台，通过 SSH/SFTP 管理 Linux 上的独立 DST Docker Compose 专服。

当前版本：**1.3.8**。版本号只在 [package.json](package.json) 中维护。

## 功能

- 主机连接管理：SSH 指纹确认、系统凭据库、登录验证与 hostId 发现。
- 主机环境：系统、Docker/Compose、资源和目录权限检查；Docker/Compose 安装。
- Docker 镜像与 Release：构建、导入、验证、删除和构建缓存清理。
- DST 更新：检查最新 build，按实例切换版本并保留世界存档保护点。
- 专服：创建、启停、删除、概览、诊断和运行日志。
- 世界配置：Master/Caves 分片、规则、生成参数、Token/密码和应用。
- 主机 MOD 库：Workshop 链接/ID 导入、Steam 合集批量导入、检查更新、批量更新和同步到专服。
- 专服 MOD：从主机库批量选择、配置解析、启用/禁用、应用和版本更新；UGC 预置 + 索引，首启直接加载。
- 备份：创建、下载、恢复和保护点。
- 全局任务记录：远端任务、构建任务和本地操作统一在任务抽屉中查看；切换页面后任务继续执行。
- 应用设置：主题和本地数据信息；设置页保留进入前的导航上下文，可返回原页面。

## 开发环境

- Node.js 22.18+、npm 10+
- Rust stable 与对应平台 Tauri 2 构建环境
- macOS 需要 Xcode Command Line Tools；Windows 需要 MSVC 构建工具与 WebView2
- 远端 Linux amd64，Docker Compose 路线

前后端依赖已用 lockfile 固定。本项目不使用 Maven。

## 启动

```bash
npm ci
npm run dev
```

浏览器预览地址：`http://127.0.0.1:1421`。浏览器模式不连接真实后端。

```bash
npm run desktop:dev
```

桌面模式使用应用标识 `app.hungerdesolate.hub`，数据库为 `hunger-desolate.sqlite3`。

## 版本管理

版本唯一来源：

```text
package.json
```

发布流程：

1. 修改 `package.json` 的 `version`；
2. 运行 `npm run desktop:build`，构建前自动执行 `version:sync`；
3. `version:sync` 将版本同步到 `src-tauri/Cargo.toml`；
4. Tauri 通过 `src-tauri/tauri.conf.json` 的 `"version": "../package.json"` 读取应用版本；
5. 界面通过 `get_bootstrap_snapshot` 显示运行时版本。

仅同步 Rust/Cargo 版本：

```bash
npm run version:sync
```

界面显示的 `Bate` 后缀是产品文案，不属于版本号。

## 检查与构建

```bash
npm run check
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo check --manifest-path src-tauri/Cargo.toml --all-targets
cargo test --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
npm run desktop:build
```

仅编译桌面程序、不生成安装包：

```bash
npm run tauri -- build --debug --no-bundle
```

## 目录

| 目录 | 职责 |
| --- | --- |
| `src/pages` | 主机、专服、环境、Release、世界、MOD、备份、诊断与设置 |
| `src/components` | 通用组件、任务面板和对话框 |
| `src/api` | Tauri command 调用入口 |
| `src/composables` | 工作区、任务、操作、设置和弹窗状态 |
| `src/lib` | 请求隔离、任务合并、配置解析、持久化规则及测试 |
| `src-tauri/src` | Rust command、DTO、SQLite、SSH、任务与远端脚本编排 |
| `src-tauri/migrations` | 追加式数据库迁移 |
| `remote-scripts`、`docker` | 远端动作、探测脚本和运行时镜像 |
| `spec` | 接口契约、前后端实现与功能设计 |
| `docs` | 架构、当前状态、诊断、Release 和 MOD 库说明 |

## 关键约束

- 远端实例业务必须携带完整 target；不能依赖“当前选中实例”。
- 查询、写入、任务和草稿绑定主机连接版本与请求身份。
- 密码、Token、SSH 密钥不进入 SQLite、草稿、日志、事件或诊断。
- 远端 Docker 路线固定，不引入第二套部署实现。
- 界面保持极简，不恢复阶段说明性常驻文案。

## 文档

- [文档入口](spec/README.md)
- [当前状态](docs/current-status.md)
- [架构概览](docs/architecture-overview.md)
- [接口契约](spec/api-contract.md)
- [主机 MOD 库](docs/host-mod-library.md)
- [专服诊断](docs/diagnostics.md)
- [备份与恢复文件边界](docs/backup-restore-boundaries.md)
- [DST Release 准备](docs/dst-release-setup.md)
- [运维增强规划](spec/ops-enhancements-plan.md)
- [DST 更新设计](spec/dst-update-plan.md)
- [定时备份设计](spec/scheduled-backup-plan.md)
- [玩家列表设计](spec/player-list-plan.md)
