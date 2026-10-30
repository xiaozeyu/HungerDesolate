# 荒野中枢实施文档

- 编写日期：2026-10-14
- 文档状态（2026-10-22）：主机管理、Docker 安装、创建与生命周期、备份创建/下载/恢复代码闭环已接入；备份包含已应用世界配置快照，恢复校验并重建 DST 可写目录权限；恢复前保护点可由用户选择是否创建，保护点不再禁止删除。Linux amd64 恢复故障验收仍未通过：用户实机日志确认 DST 配置目录此前为不可写，且 Klei 拒绝当前令牌（`E_INVALID_TOKEN`）；代码已修复权限处理并保留实例凭据库中的令牌，令牌需由用户更新后重新验证。阶段 0 与真实恢复/故障矩阵仍未验收
- 产品基线：[新项目规划第四版](../new-product-plan.md)
- 历史依据：原对话 `01a1382c-3f4c-7311-873a-678b8b2c3715`，以最后确定的 Docker Compose 路线为准

本目录把规划拆为可分工、可联调、可验收的实施说明。“后端”指桌面应用内的 Rust/Tauri 核心及通过 SSH/SFTP 执行的远端脚本、Docker 资源，不新增公网 HTTP 服务。

## 1. 文档导航

| 文档 | 使用对象 | 内容 |
| --- | --- | --- |
| [前端实施](frontend-implementation.md) | 前端开发、交互评审 | 路由、页面、组件、状态归属、请求乱序、草稿、任务反馈、视觉与交互验收 |
| [后端实施](backend-implementation.md) | Rust、远端部署开发 | 模块、数据库、manifest、Compose、锁、任务、配置、MOD、备份更新恢复、安全边界 |
| [共享接口契约](api-contract.md) | 前后端共同维护 | target、DTO、命令、任务事件、revision、幂等、错误、刷新范围 |
| [实施顺序与验收](implementation-roadmap.md) | 开发负责人、测试 | 阶段门禁、工作包、依赖、Docker 实验、异常注入、发布验收 |
| [后续功能清单](next-features.md) | 开发排期、进度核对 | 2026-10-17 的剩余功能、依赖与完成条件 |
| [MOD 管理分析](mod-management-plan.md) | MOD 开发、方案评审 | Workshop 获取、下载与固定产物、Lua 配置机制、更新发布、移除与验收建议 |

推荐先读共享契约，再按职责阅读前端或后端实施，最后按工作包推进。接口字段、枚举或命令变化必须同步修改两端类型、调用点、fixture 和本文档。

## 2. 已冻结的约束

1. Windows/macOS 桌面端，Vue 3 + TypeScript + Vite + Tauri 2 + Rust；SSH/SFTP 管理 Linux amd64 主机。
2. 仅实现 Docker Compose 专服部署：一实例一项目、一 bridge 网络、一套可写配置/存档/MOD/备份目录。所有对外端口配置、容器监听、宿主发布同号。
3. 主机 → 专服列表 → 进入专服 → 世界/MOD/备份/更新/诊断；同时只有一个详情上下文。切换专服经过列表。
4. 查询、草稿、写入、备份、任务都绑定身份。后端不能从“当前选中”或默认目录推断操作对象。
5. 前端保留自有 Vue 组件、语义 CSS Token、Reka UI、`@lucide/vue`；操作进度只进入一个底部操作栏。
6. 首期同主机最多一个重型安装/更新任务，同实例写入互斥。远端锁与注册表决定冲突，本地状态不能替代。
7. 新项目采用独立数据库、应用数据目录与 keyring 命名空间。旧部署通过预览和保护备份迁移。

规划编写时工作区只有规划文件和 `spec/`；现已建立独立 Vue/Tauri 工程，当前实现范围见 [骨架状态](scaffold-status.md)。另对旧模板目录 `../Famine-ECS` 的 `AGENTS.md`、依赖、样式和路由进行了只读核对；这些用于确认复用边界，不代表新工程已安装相关依赖。具体版本随新工程锁文件固定，不在实施文档中宣称“最新版”。用户提供的 Maven 仓库路径为 `/opt/homebrew/Cellar/maven/3.9.15/repository`；本方案沿用 Vue/Rust，没有 Java/Maven 构建步骤。

## 3. 设计决策与待验证项

| 事项 | 本版实施决定 | 放行条件 |
| --- | --- | --- |
| 游戏容器拓扑 | 优先验证 Master/Caves 两个服务，共享实例只读二进制，各自可写 UGC | 阶段 0 双向迁移、MOD 一致性及权限通过后冻结；未通过则在同一 Docker 路线内修订拓扑 |
| 后台任务 | Docker worker 处理数据，主机受控 controller 执行 Docker 编排；worker 不挂 socket | SSH/桌面断开后 controller、worker、锁和状态可持续并对账 |
| 首发环境 | Linux amd64；发行版、Docker/Compose 下限待实测 | 阶段 0 输出精确版本、镜像 digest、依赖与资源数据 |
| P0/P1 边界 | P0 是核心部署运维；定时备份、健康历史、诊断包、旧部署迁移为 P1 | P0 验收后分别放行；规划阶段 4 是发布候选范围，不把 P1 偷换成 P0 |
| 删除与保护 | 默认备份，数据进入 retired，备份继续关联原实例 | 清理运行资源与监听后才释放端口 |

文档中的目录、默认刷新间隔、任务时限和容量上限是新项目设计值；性能预算、完整 DST 参数和兼容性需要实验确认。待验证项都有工作包与证据要求，不能用文档完成替代实机通过。

## 4. 需求追踪

| 规划章节 | 实施位置 |
| --- | --- |
| §1–4 范围、对象、身份 | 共享契约 §1–4；后端 §2–4 |
| §5–6 导航与流程 | 前端 §2–8；共享契约 §7–9 |
| §7–8 Docker、目录、端口、锁 | 后端 §4–7；验收计划 §3 |
| §9–10 数据、任务、后端边界 | 前端 §4–6；共享契约 §3–6；后端 §3、§7–8 |
| §11 迁移 | 后端 §12；前端 §8；P1 工作包 |
| §12–14 阶段与验收 | 实施顺序与验收全文 |

## 5. 外部依据与使用方式

沿用产品规划中的 Docker/Klei 参考，并补充网络、重启策略和 Tauri command/event 的官方入口供实现时核对。本次环境网络未能读取官方网页全文，下面是参考入口，不宣称完成了最新平台行为核验；DST 参数与联网兼容性继续作为阶段 0 待核验项。

| 代号 | 官方入口 | 用途 |
| --- | --- | --- |
| D-PROJECT | `https://docs.docker.com/compose/how-tos/project-name/` | 项目隔离、项目名字符与优先级 |
| D-NETWORK | `https://docs.docker.com/compose/how-tos/networking/` | 项目内部服务寻址 |
| D-SERVICE | `https://docs.docker.com/reference/compose-file/services/` | ports、bind mount、secrets、服务属性 |
| D-SECRET | `https://docs.docker.com/compose/how-tos/use-secrets/` | 文件秘密挂载，仍须核验宿主权限 |
| D-RESTART | `https://docs.docker.com/engine/containers/start-containers-automatically/` | 手动停止与 daemon 重启后的行为 |
| T-COMMAND | `https://v2.tauri.app/develop/calling-rust/` | invoke、异步 command 与序列化 |
| T-EVENT | `https://v2.tauri.app/develop/calling-frontend/` | 事件特点、订阅解绑；事件不充当持久化结果 |

所有本文“必须”都作为后续实现验收要求。技术门禁失败时修订对应设计并复测，不扩展为未经验证的部署备用实现。
