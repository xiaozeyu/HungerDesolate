# 荒野中枢 · Hunger Desolate

Windows/macOS 桌面控制台，通过 SSH/SFTP 管理 Linux 上的独立 DST Docker Compose 专服。

当前版本 `0.1.0` 已具备项目骨架与本地连接管理。桌面环境可以添加、编辑、删除连接元数据；点击“查看界面预览”可使用隔离的示例数据查看主机 → 专服列表 → 专服详情两层导航。预览不连接远端、不下载游戏、不执行写入。

SSH 连接设置已接入指纹扫描与确认、密码/私钥保存到系统凭据库、只读登录验证和 hostId 发现。“主机环境”已支持真实系统、Docker/Compose、资源和目录权限检查。已支持管理目录初始化与 Ubuntu 26.04 root SSH 的 Docker/Compose 安装。用户已确认安装主路径通过；真实 DST 部署仍待阶段 0 验证。

## 开发环境

- Node.js 22.18+、npm 10+
- Rust stable 与对应平台 Tauri 2 构建环境
- macOS 需要 Xcode Command Line Tools；Windows 需要 MSVC 构建工具与 WebView2
- 远端 Linux amd64/Docker Compose 的最低兼容版本尚待阶段 0 实测

前后端依赖已用 lockfile 固定；Node 和 Rust 是不同构建链。本项目不使用 Maven。

## 启动

```bash
npm ci
npm run dev
```

浏览器访问本地 `http://127.0.0.1:1421`。首次显示空主机列表，点击“查看界面预览”即可检查导航和示例页面。

```bash
npm run desktop:dev
```

桌面开发启动 Tauri 窗口与 Rust 后端。新应用标识为 `app.hungerdesolate.hub`，数据库文件为 `hunger-desolate.sqlite3`，独立于旧项目 `app.famine.ecs`。

## 检查与构建

```bash
npm run check
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo check --manifest-path src-tauri/Cargo.toml --all-targets
cargo test --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
npm run desktop:build
```

本地依赖缓存完整时，Cargo 可以额外带 `--offline`。仅编译桌面程序、不生成安装包：`npm run tauri -- build --debug --no-bundle`。Windows/macOS 安装包需要分别在相应环境构建，签名与正式发布尚未配置。

## 目录

| 目录 | 职责 |
| --- | --- |
| `src/pages` | 主机、专服列表、环境、连接、概览、世界、MOD、备份、更新、诊断与设置 |
| `src/components` | 页面标题、状态、Reka Select/Tooltip 与预览提示 |
| `src/api` | Tauri 调用入口；远端未实现功能显式返回不可用 |
| `src/context`、`src/composables` | 活动实例代次、列表选择授权、请求隔离与任务事件 |
| `src/lib` | 生产调用的身份/请求/事件合并规则及回归测试 |
| `src/fixtures` | 仅明确开启预览时使用的演示数据 |
| `src-tauri/src` | Rust command、DTO、本地 SQLite 初始化、连接 CRUD 与输入校验 |
| `src-tauri/migrations` | 追加式数据库迁移 |
| `remote-scripts`、`docker` | 只读环境探测脚本；部署编排和生产镜像待实现 |
| `spec` | 产品实施文档与验收工作包 |

## 已实现与边界

已实现独立应用标识、项目锁文件、两层路由、列表选择约束、上下文代次/序号校验、固定任务目标的事件合并、本地 SQLite 版本迁移、真实连接元数据 CRUD、记录版本冲突与幂等处理、版本一致性检查。

添加主机时可直接填写 SSH 密码，或输入/选择本机私钥文件路径及可选口令；连接设置中也支持粘贴完整私钥。元数据和凭据分别提交，秘密只存入系统凭据库，不回显、不写 SQLite/草稿/日志。Docker 安装已实现；世界/MOD、备份更新及专服创建执行 command 仍未实现。浏览器只预览，文件选择与真实保存需桌面环境。默认字体使用系统回退，Inter/JetBrains Mono 本地字体与正式发行图标仍待固化。

## 验证真实主机

1. 使用 `npm run desktop:dev`，退出界面预览后添加主机。选择密码时输入 SSH 密码；选择密钥时填写/选择私钥文件路径（如 `~/.ssh/id_ed25519`）及可选口令，然后保存。
2. 进入“连接设置”，点击“扫描指纹”，通过主机控制台或可信渠道核对 SHA-256 指纹，再确认。
3. 添加时已保存的凭据在首次确认指纹后继续可用；需要修改时点击“替换凭据”。私钥文件内容导入独立系统凭据库 `app.hungerdesolate.hub.ssh`，后续更改原文件需重新导入；不要选择 `.pub` 公钥文件。
4. 点击“验证登录”。管理目录不存在时显示“未初始化”；已存在则只读校验 `/var/lib/famine-hub/host.json` 并绑定 hostId，不创建目录、不部署服务。
5. 旧凭据清理失败时，可使用“清理旧凭据”重试；失败项不会被标为已删除。

操作只验证当时的登录与身份，结束后关闭 SSH 会话；不代表持续连接或游戏运行正常。当前不实现跳板机、SSH agent、keyboard-interactive 或任意远端命令输入。macOS 构建通过；系统钥匙串授权交互与 Windows 实机行为仍需在目标机器验证。

可选本机 SSH 集成测试（需 OpenSSH 工具）：

```bash
python3 scripts/test-local-ssh.py
```

脚本只启动临时 loopback SSH 服务并使用临时密钥，结束清理；不改用户 SSH 配置或 authorized_keys。普通 cargo test 跳过这个需要服务的测试，脚本单独运行它。

先阅读 [实施入口](spec/README.md) 和 [当前骨架状态](spec/scaffold-status.md)。

如果添加时主机已创建但凭据保存失败，可在原弹窗修正并重试（沿用原记录），或关闭后到该主机连接设置补充凭据。关闭弹窗会清空敏感输入。

后续开发顺序见 [后续功能清单](spec/next-features.md)。

## 专服列表

桌面环境选择主机 → 专服列表，会通过已确认 SSH 连接读取 `/var/lib/famine-hub/registry.json`，并核对活动实例 manifest。可以刷新、选择实例和进入真实身份概览。未初始化、注册表缺失/损坏、权限不足或身份不符均明确报错，不自动创建远端文件。

当前是只读发现：运行状态、玩家数与实际版本保持未知，直到接入容器/进程运行探测。创建失败且没有 manifest 的记录保留在表中；已归档实例不显示。新建、启停、更新和远端注册写入仍在后续部署工作包中。

最小读取 schema 示例见 `src-tauri/tests/fixtures/registry-v1.json` 与 `manifest-v1.json`，仅用于测试参考；它们不代表存在可运行 DST 专服，请勿把示例覆盖到已有管理目录。本轮 SSH fixture 脚本已同时覆盖 registry/manifest 读成功及损坏/缺失/符号链接/错误归属等场景。

## 主机环境检查

桌面环境选择已添加主机 → 主机环境，进入时自动执行只读检查，可点击“重新检查”。未确认指纹或凭据缺失时，页面提供连接设置入口。

结果包括系统/架构、UID、内存、管理目录所在盘、Docker 客户端/服务端、Compose、Docker 数据盘、必要工具及权限。缺失/权限/不可访问分开表示，未知资源不作为 0。展开阻断项查看恢复步骤；环境检查本身只读；初始化与 Docker 安装通过各自确认入口执行。

当前检查固定本机 rootful socket `/var/run/docker.sock`，不采用远端 Docker context；rootless 自定义 socket 尚未支持。检查通过不代表阶段 0 三专服联网/更新/恢复验证完成，也不保证资源足以运行任意实例。

隔离探测测试：`python3 scripts/test-host-environment.py`，使用临时目录、命令 fixture 和 Unix socket，不操作真实 Docker。`python3 scripts/test-local-ssh.py` 同时验证脚本通过已认证 SSH 通道执行。

## 新建专服预览

专服列表读取成功后，点击“新建专服”，填写专服名称、启动策略、自动端口策略，再填写世界模式、人数、洞穴、PVP、说明和 Token。网络路线固定为公网。点击“预览”后，桌面后端真实核对远端注册表、已有端口保留、IPv4/IPv6 占用和 Docker 发布端口，展示建议目录与完整同号端口表。

此功能只预览，不创建目录、不预留端口、不安装/启动专服，也不收集 Klei Token。阶段 0 游戏镜像和部署兼容性尚未验证时显示真实阻断；Docker 安装成功不等于游戏部署门禁通过。隔离测试：`python3 scripts/test-instance-create-preview.py`。
