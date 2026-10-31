# 荒野中枢 · 前端重构方案

- 版本：2026-10-31
- 输入样例：[ui-redesign.html](ui-redesign.html)
- 范围：排除 DST/MOD 更新功能；不改现有代码，仅输出设计与实施计划

---

## 0. 结论

`ui-redesign.html` 是一版完成度较高的**视觉与交互原型**，适合作为页面布局、信息密度、状态语义和对话框规范的参考，但不能直接当作生产架构使用。

应当保留：

- 语义 Token 和亮/暗主题；
- 桌面端侧栏 + 顶栏 + 底部操作栏的三段式外壳；
- 表格优先的高密度信息布局；
- 统一的状态标签、确认对话框、危险操作输入确认；
- 操作栏与任务详情的基本形态；
- 960 宽度下的列降级和响应式策略；
- 原生对话框、键盘操作、`prefers-reduced-motion` 等可访问性基础。

不能照搬：

- 硬编码的“上海服务器 / 生存一服”；
- 只按页面名切换的 hash 导航；
- 模块级变量 + 定时器模拟的任务状态；
- 随机百分比进度（与“无证据不生成模拟进度”的要求冲突）；
- 数据全部静态、没有加载/失败/过期/权限/离线状态；
- 创建专服的“预览”中间步骤；
- 世界配置的“应用预览”弹窗；
- 仍然存在的“更新”页面；
- MOD 页面缺少“应用更改”主操作。
- 样例仍按**实例级 MOD**设计（实例内添加、下载、检查更新），而当前项目已把 MOD 提升为**主机级内容库**；这一点必须在重构中纠正。

建议的最终形态是：**保留当前 Vue 3 + TypeScript + Vue Router + Tauri 的技术栈，吸收该原型的视觉和交互语言，重构应用外壳、查询层、任务层和页面边界。**

---

## 1. 样例拆解

### 1.1 规模与结构

| 项目 | 内容 |
| --- | --- |
| 文件 | 单文件 HTML，约 3,500 行、210 KB |
| 样式 | 内联 CSS，语义 Token + 组件类，约 1,000 行 |
| 脚本 | 原生 JS，模块级状态，约 1,200 行 |
| 图标 | SVG sprite，约 60 个 Lucide 图标 |
| 页面 | 12 个 `page-view`：主机列表、专服列表、Release、主机环境、连接设置、专服概览、世界、MOD、备份、更新、诊断、应用设置 |
| 对话框 | 16 个：确认、输入名称删除、任务详情、添加主机、新建专服、SSH 指纹、SSH 凭据、世界凭据、世界应用预览、创建备份、添加 MOD、MOD 配置、更新预览、Release 运行验证、Docker 安装、主机初始化 |
| 全局组件 | 顶栏面包屑、主题切换、底部操作栏（默认/世界/任务三态）、Toast |
| 响应式 | 最小宽度 960；1100px 隐藏部分表格列/折叠网格；1000px 再隐藏一列并改为双列摘要 |
| 主题 | `data-theme` + localStorage + `prefers-color-scheme` |

### 1.2 页面模式

该原型形成了几种可复用的页面骨架：

1. **列表页**：`PageHeader` + `summary` 指标 + `table-wrap` + 行操作。
2. **详情页**：`PageHeader` + `identity-strip` / `facts` + 分栏内容 + 表格。
3. **配置页**：`tabs` + 表单分区 + 底部操作栏的保存/应用动作。
4. **任务页**：状态摘要 + 时间线 + 诊断 + 清理/恢复动作。
5. **设置页**：`setting-row` 纵向列表。

### 1.3 视觉系统

可复用的设计决策：

- **Token 完整**：canvas/sidebar/surface/surface-muted/text/border/accent/status/overlay/shadow/radius/space。
- **状态色 + 文本双编码**：不只用颜色表达成功/失败。
- **表格密度**：12–13px 字号、紧凑行高、可选列降级。
- **按钮层级**：`primary` / 默认 / `ghost` / `danger` / `danger-solid` / `link-button` / `icon-button`。
- **确认模式**：普通确认 + 输入目标名称的危险确认。
- **对话框**：固定 head/body/foot，宽度分级 `sm/md/lg/xl`，原生 `<dialog>`。
- **操作栏**：底部固定 60px，承载“当前状态的下一步动作”，避免主操作散落。
- **可访问性**：`focus-visible`、`aria-label`、`role=tablist` 方向键、`aria-live`、`prefers-reduced-motion`。

### 1.4 交互模型的局限

| 样例行为 | 问题 |
| --- | --- |
| `setPage(page)` 切换 `hidden` section | 没有真实路由、没有 serverId/instanceId 参数；刷新只保留页面名 |
| 主机/专服名称写死在 HTML / `pages` 元数据 | 无法切换主机或实例；没有目标身份 |
| `startTask()` 用 `setInterval` 随机增加进度 | 伪造进度；与后端任务阶段/证据不一致 |
| 任务只存在于操作栏和单个任务弹窗 | 没有全局任务中心、历史、排队、重连对账 |
| 页面状态全部是静态 DOM | 没有加载、刷新、stale、错误、空、离线、权限状态 |
| 更新页完整但后端未实现 | 会承诺不存在的功能 |
| 创建专服有“预览 → 创建容器”两步 | 与当前“检查并创建”单命令流程冲突 |
| 世界配置有“预览应用”弹窗 | 与当前“保存并应用”直接检查/应用的流程冲突 |
| MOD 页面没有“应用更改” | 关键任务入口缺失 |
| MOD 采用实例级添加/下载模型 | 当前项目已是主机级 MOD 库：内容在主机准备一次，实例只选择版本 |

### 1.5 保留 / 改造 / 移除

| 处理 | 内容 |
| --- | --- |
| 保留 | Token 与亮暗主题、侧栏+顶栏+操作栏外壳、摘要+表格布局、状态标签、原生对话框交互、输入名称确认、可选列降级、键盘/减少动画支持 |
| 改造 | 导航改为两级上下文、操作栏改为全局数据驱动、任务详情升级为任务中心、创建专服改为单步提交、世界配置改为保存并应用、MOD 改为“主机库选择 + 实例配置/应用”、诊断接入真实证据 |
| 移除 | 更新页、演示数据徽标、硬编码主机/实例、只按名称的 hash 导航、随机任务进度、页面级 `setInterval`、主机/实例导航同时展开、与后端流程冲突的预览步骤 |

### 1.6 关键前提：MOD 已提升为主机级

样例的设计前提是“**MOD 属于实例**”：实例页直接按 Workshop ID 添加、查询、下载、配置和检查更新，没有主机级 MOD 库页面。这与当前项目不一致。

当前模型：

| 能力 | 样例 | 当前项目 |
| --- | --- | --- |
| 添加内容 | 实例页 `dialog-mod-add` | 主机 MOD 库 `/hosts/:serverId/mods` |
| 下载/固定内容 | 实例级“添加/下载” | 主机级 `prepare_host_mods` 持久任务，一次准备供所有实例使用 |
| 实例配置 | 在实例页选择并配置 | 从主机库选择 `contentDigest`/版本，再保存实例期望 |
| 实例下载 | 有 | **无** |
| 检查更新 | 实例级 | 主机库内容候选；实例页只显示所选版本/候选状态 |
| 应用 | 样例缺失 | 实例页“应用更改”持久任务 |
| 删除 | 无 | 主机库删除前检查实例、备份、任务引用 |

因此重构必须明确：

- 样例的 `dialog-mod-add`、实例级“添加 MOD / 下载产物 / 检查更新”不能照搬；
- 实例 MOD 页 = 从主机库**选择/更换版本** + 实例配置 + 启用/禁用 + 应用；
- 主机 MOD 库页 = Workshop 查询、内容准备任务、版本/引用管理、删除；
- 任务中心要同时承载**主机级准备任务**（HostTarget）和**实例级应用任务**（InstanceTarget），不能混用 target；
- 空状态引导用户去主机 MOD 库添加内容，而不是在实例页直接输入 Workshop ID。

---

## 2. 与当前仓库的对比

当前仓库已经具备原型所缺少的工程基础：

- Vue Router 的真实路由和 `serverId/instanceId` 参数；
- `useWorkspace` 的主机/实例列表缓存；
- 多个页面的 `epoch/sequence/latestRequestId` 查询守卫；
- `useTasks` 任务 store 和 `famine://task-updated` 事件；
- 真实 IPC/Rust 命令与错误映射；
- `AppDialog`、`PageHeader`、`StatusLabel`、`AppSelect` 等基础组件。

当前仓库的不足：

- 页面各自实现轮询和任务状态（创建、MOD、备份、主机环境、Release 构建）；
- 没有统一任务中心和全局操作栏；
- 页面卸载会丢弃迟到响应，长同步命令的结果无法恢复；
- 查询/任务/草稿/选择状态边界不清晰；
- 部署与校验信息散落在创建弹窗、概览、世界、MOD、备份页面；
- 锁冲突被展示成红色错误，而不是排队状态；
- 更新页仍是占位。

因此，重构不是“用 HTML 重写”，而是**把原型的表现层映射到现有的 Vue 组件和 Store 架构上**。

---

## 3. 重构目标与边界

### 3.1 目标

1. 用户在任何页面都不会因为“别的页面正在执行任务”而看到无意义的锁错误。
2. 任何超过数秒的操作只提交一次，之后由全局任务中心观察；切页、关闭当前页面不中断。
3. 每个页面都有明确的加载、刷新、过期、错误、空状态。
4. 部署/任务/校验信息只在一个地方解释，不在多个页面重复。
5. 保留现有视觉风格，但组件化、可测试、可访问。

### 3.2 范围

**P0 包含**：主机、连接、环境、Release、专服列表、概览、世界、MOD、主机 MOD 库、备份、诊断、设置、任务中心。

**P0 排除**：DST/MOD 更新。更新页从导航和路由中移除，不提供占位入口；相关设计保留在文档中，待后端更新能力落地后再接入。

**不包含**：重写后端、改协议、改锁实现。前端通过状态语义和任务中心承接后端现状；后端配套改造另列依赖。

---

## 4. 目标架构

```text
┌──────────────────────────────────────────────────────────────┐
│ App Shell                                                    │
│  Sidebar / HostContext / Topbar / OperationBar / TaskDrawer   │
├──────────────────────────────────────────────────────────────┤
│ Pages                                                        │
│  Hosts / Environment / Releases / Connection / Instances      │
│  Overview / World / Mods / Backups / Diagnostics / Settings   │
│  TaskCenter                                                   │
├──────────────────────────────────────────────────────────────┤
│ App State                                                    │
│  SelectionStore  QueryClient  DraftStore  OperationStore      │
├──────────────────────────────────────────────────────────────┤
│ Data Access                                                  │
│  typed IPC wrappers + response guards + error mapping         │
├──────────────────────────────────────────────────────────────┤
│ Tauri / Rust / SSH                                           │
└──────────────────────────────────────────────────────────────┘
```

### 4.1 路由与目标身份

保留当前 `/hosts/:serverId/...` 结构，并补充任务中心：

```text
/hosts
/hosts/:serverId/instances
/hosts/:serverId/environment
/hosts/:serverId/releases
/hosts/:serverId/mods                 # 主机 MOD 库
/hosts/:serverId/connection
/hosts/:serverId/instances/:instanceId/overview
/hosts/:serverId/instances/:instanceId/world
/hosts/:serverId/instances/:instanceId/mods
/hosts/:serverId/instances/:instanceId/backups
/hosts/:serverId/instances/:instanceId/diagnostics
/tasks                                # 新增：全局任务中心
/settings
```

原则：

- 路由参数只决定“看什么”，后端寻址始终使用完整 target；
- 主机/实例切换由显式选择驱动，不从“当前页面”推断；
- 页面刷新、深链接、跨页面任务都保留同一身份；
- `/updates` 在 P0 不注册、不显示。

### 4.2 SelectionStore

职责：

- 当前主机、当前实例、可选实例列表；
- 最近访问的主机/实例；
- 选择变化时只失效相关查询，不清空任务；
- 与路由参数双向同步，但业务命令仍使用显式 target。

当前 `useWorkspace` 已经承担了服务器列表、实例缓存和选择清理的一部分职责，可以在其上升级，而不是新建一套并行状态。

### 4.3 QueryClient

把现有各页面的 `epoch/sequence/requestId` 逻辑抽象为通用查询：

```ts
useQuery({
  key: ["instance", target, "world"],
  target,
  expectedRevision,
  load: () => getWorldConfig(...),
  validate: validateWorldResponse,
  staleTime: 15_000,
})
```

行为：

- 同 key 请求可去重；
- 旧请求的 success/error/finally 不覆盖新请求；
- 导航离开时停止 UI 订阅，不取消任务；
- 保留最后一份成功数据，刷新失败显示 `stale`，不清空；
- 错误按 `code/category/field` 分类，交给页面决定展示形态；
- 不做全局 loading 遮罩，页面按区域展示。

建议按领域拆分 key：

| Key | 数据 |
| --- | --- |
| `workspace.servers` | 主机列表 |
| `host.environment` | 主机环境 |
| `host.releases` | Release catalog |
| `host.mods` | 主机 MOD 库 |
| `instance.list` | 专服列表 |
| `instance.overview` | 概览/运行状态 |
| `instance.world` | 世界配置 |
| `instance.mods` | 实例 MOD |
| `instance.backups` | 备份 |
| `instance.diagnostics` | 诊断 |
| `task.active` / `task.history` | 任务 |

### 4.4 DraftStore

草稿必须从页面组件中抽离：

- 键：`hostId + instanceId + domain`；
- 保存：本地应用数据目录或 localStorage（非秘密字段）；
- 关联 `baseRevision`；
- 导航后恢复；
- 远端 revision 变化时显示冲突比较，不静默覆盖；
- 保存成功后更新 base，不丢弃未提交部分；
- Token/密码不进入草稿。

### 4.5 OperationStore（任务中心）

当前 `useTasks` 只是内存 Map，需要升级为应用级任务状态：

```ts
type OperationState =
  | "submitting"   // 接纳请求在途
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "interrupted"
  | "cancelled";
```

职责：

- 提交长操作，拿到 `taskId/operationId`；
- 统一轮询/事件接收，页面不各自 `setInterval`；
- 按 target + operation 索引；
- 记录 `stage/sequence/journal/result/error`；
- 页面卸载不停止；
- 应用启动时按领域 `list_*_tasks` 对账；
- 失败保留现场，不把“回滚成功”写成“更新成功”；
- 终态保留历史，供任务中心筛选；
- 不生成随机进度；不确定时显示阶段文字/spinner。

现有任务是分领域的（创建、MOD、备份、恢复、下载、主机 MOD、构建）。P0 可以先聚合现有接口：

| 任务类型 | 现状接口 | 前端统一模型 |
| --- | --- | --- |
| 创建专服 | `get_instance_create_status` | `TaskSnapshot` |
| 应用 MOD | `get_instance_mod_apply_task` / list | `TaskSnapshot` |
| 备份 | `get_instance_backup_task` / list | `TaskSnapshot` |
| 恢复 | `get_instance_restore_task` / list | `TaskSnapshot` |
| 下载 | `get_backup_download_task` / list | 本地任务模型 |
| 主机 MOD | `get_host_mod_task` / list | `TaskSnapshot` |
| 构建 | `get_build_status` | 构建状态模型 |

后续可以推动后端统一 `list_tasks/get_task`，但前端先做适配层。

### 4.6 操作栏

把原型的底部操作栏提升为**全局操作栏**：

- 左侧：当前上下文状态（连接/实例/当前任务）；
- 中间：当前任务阶段/队列位置；
- 右侧：当前页面的主操作 + “任务中心”入口；
- 优先级：
  1. 当前实例有活动任务 → 展示任务进度；
  2. 当前页面有未保存草稿 → 展示保存/应用；
  3. 当前上下文有可执行主操作 → 展示主操作；
  4. 否则展示连接/同步状态。

不要让操作栏和页面头部同时提供重复主操作。每个页面只保留一个主操作来源。

### 4.7 任务中心

新增 `/tasks` 页面和一个可展开的侧边任务抽屉：

- 进行中：目标、操作、阶段、耗时、状态、观察/取消；
- 排队：队列位置、阻塞原因；
- 历史：成功/失败/中断，支持按主机/实例/操作筛选；
- 失败：失败阶段、现场是否保留、恢复动作、诊断入口；
- 顶部统计：进行中、排队、失败待处理。

页面内只展示“与此实例相关”的任务摘要，并链接到任务中心；不复制完整任务列表。

---

## 5. 设计系统落地

### 5.1 样式组织

当前 `src/style.css` 已经是语义 Token + 全局组件类。建议拆分为：

```text
src/styles/tokens.css        # 亮/暗 Token、字体、间距、圆角
src/styles/base.css          # reset、排版、焦点、滚动条
src/styles/layout.css        # app shell、sidebar、topbar、operation bar
src/styles/components.css    # button/status/table/form/dialog
```

保留现有 Token 值，不引入其他视觉组件库。新增组件优先复用现有类，不新增一次性颜色/间距。

### 5.2 组件清单

| 类别 | 组件 | 来源/动作 |
| --- | --- | --- |
| 外壳 | `AppShell` / `AppSidebar` / `HostContext` / `Topbar` / `Breadcrumb` | 从 `App.vue` 拆出 |
| 外壳 | `OperationBar` | 新增 |
| 外壳 | `TaskDrawer` | 新增 |
| 页面 | `PageHeader` | 保留，补充说明插槽 |
| 数据 | `SummaryBar` / `Metric` | 从各页表格上方摘要抽取 |
| 数据 | `DataTable` / `TableToolbar` | 新增，统一空/加载/stale |
| 状态 | `StatusLabel` / `TaskStatus` / `QueueBadge` | 保留/扩展 |
| 反馈 | `EmptyState` / `ErrorBanner` / `StaleNotice` / `InlineNotice` / `ToastHost` | 新增 |
| 表单 | `Field` / `TextInput` / `Textarea` / `Select` / `Switch` / `FormActions` | 基于现有 `AppSelect` 扩展 |
| 对话框 | `AppDialog` | 保留 |
| 对话框 | `ConfirmDialog` / `TypedConfirmDialog` / `TaskDialog` | 新增 |
| 任务 | `TaskProgress` / `TaskTimeline` / `TaskList` | 新增 |
| 其他 | `FactList` / `CodeBlock` / `LogViewer` | 新增 |

### 5.3 状态模式

每个数据页面至少覆盖：

- **initial loading**：骨架/占位，不显示伪造数据；
- **refreshing**：保留旧数据 + 顶部/行内刷新提示；
- **stale**：显示“上次读取”时间和过期原因，写操作禁用；
- **error**：错误码/可执行恢复步骤/重试；
- **empty**：明确空状态和唯一 CTA；
- **partial/degraded**：部分字段未知，不伪装为 0；
- **busy/queued**：任务进行中，禁用重复提交，展示队列位置；
- **offline/disconnected**：连接状态与重连入口。

### 5.4 状态语义

| 场景 | 颜色/标签 | 行为 |
| --- | --- | --- |
| 运行中、验证通过 | success | 允许停止/重启 |
| 待应用、需要处理 | warning | 给出下一步，不自动执行 |
| 失败、校验失败 | danger | 保留现场，提供恢复/诊断 |
| 排队、等待锁 | neutral/warning | 显示队列位置，不显示红色错误 |
| 未知、未采集 | muted | 显示“未知”，不显示 0 |
| 过期 | warning + banner | 禁用写操作，允许刷新 |

### 5.5 对话框规范

- 使用现有 `AppDialog` 封装，不复制原型的原生 `<dialog>` JS；
- 所有对话框：标题、简短说明、影响的明确列表、主/次操作；
- 危险删除要求输入名称，确认按钮不提前启用；
- 关闭对话框不取消已提交任务；
- 任务对话框只展示任务快照，不阻塞任务；
- 对话框内不重复展示操作栏已表达的状态。

### 5.6 可访问性与响应式

- 960×680、1480×920 两档验收；
- 亮/暗主题都要检查；
- Tab 顺序、Escape 关闭、焦点返回触发按钮；
- 表格表头 `scope`、状态文本不只靠颜色；
- 图标按钮提供 `aria-label`；
- 不使用只有 hover 才能发现的操作；
- `prefers-reduced-motion` 下关闭动画；
- 表格在 1100/1000 宽度按优先级隐藏可选列，而不是横向溢出。

---

## 6. 信息架构

### 6.1 导航模型

建议采用**两级上下文**，而不是原型中同时展开主机组和实例组：

- 主机上下文：主机列表、专服列表、主机环境、Release、主机 MOD 库、连接设置、任务；
- 实例上下文：概览、世界、MOD、备份、诊断、任务；
- 全局：任务中心、应用设置。

理由：

- 减少误把实例操作指向另一台主机的风险；
- 当前路由已经按 `serverId/instanceId` 分层；
- 主机切换通过显式的“当前主机”选择器完成；
- 实例切换通过面包屑/“返回专服列表”完成。

### 6.2 页面职责

| 页面 | 主要职责 | 主操作 |
| --- | --- | --- |
| 主机列表 | 主机发现、连接状态、进入主机 | 添加主机 |
| 专服列表 | 实例摘要、创建、删除、进入 | 新建专服 |
| 主机环境 | 初始化、Docker、容量、阻断 | 初始化/安装 Docker/重新检查 |
| Release | 构建、导入、基础/运行验证、删除 | 构建/导入 |
| 连接设置 | SSH 信任、凭据、登录验证 | 保存/验证 |
| 主机 MOD 库 | Workshop 查询、内容准备任务、版本/引用管理 | 添加内容/准备 |
| 概览 | 实例身份、分片、端口、当前部署、运行操作 | 启动/停止/重启 |
| 世界 | 期望配置、草稿、应用 | 保存并应用 |
| MOD | 从主机库选择版本、实例配置、应用 | 应用更改 |
| 备份 | 列表、创建、下载、恢复、删除 | 创建备份/恢复 |
| 诊断 | 日志、健康、失败任务 | 刷新证据/查看任务 |
| 任务中心 | 跨实例任务观察与恢复 | 观察/取消/恢复 |
| 应用设置 | 主题、数据位置、版本 | 切换主题 |
| ~~更新~~ | P0 不注册、不展示 | — |

### 6.3 概览作为运行/部署枢纽

在更新功能落地前，不新增独立的“部署”一级页面。把概览升级为运行与部署枢纽：

- 身份与当前部署：实例 ID、DST build/Release、MOD Release、世界 revision、实际容器状态；
- 分片与端口；
- 当前任务/最近任务；
- 首次部署检查清单；
- 主操作：部署中观察、已停止启动、运行中停止/重启、失败恢复。

当更新功能实现后，再把“版本计划/切换 Release”提升为独立的“部署”页；现在不提前承诺。

> 与上一版评审的差异：上一版建议直接新增“部署”页，但该页的核心价值依赖版本切换/更新能力。本轮明确排除更新，因此先由概览承担运行与部署枢纽，避免新增一个无法完成主目标的页面。

---

## 7. 页面级重构

### 7.1 主机列表

保留原型的三层结构：摘要、表格、添加主机。

新增/调整：

- 主机行显示连接状态、最近检查、实例数、当前任务；
- 添加主机后停留在列表，提示进入连接设置；
- 主机行使用显式 `serverId` 跳转；
- 删除连接记录使用输入名称确认，说明不影响远端；
- 空状态：没有主机时直接给出“添加主机”。

### 7.2 专服列表

- 列：名称、模式、状态、玩家、实际版本、最近同步、任务、操作；
- 任务列显示创建/启动/部署任务阶段，不用状态色替代任务信息；
- 删除入口只在可删除时出现，进入预览/确认；
- 新建专服改为**单步表单 + 提交**，不再保留预览步骤；
- 表单包含名称、Release、启动策略、洞穴开关；
- 提交后关闭对话框，任务进入操作栏/任务中心。

### 7.3 主机环境

- 保留“检查结果 + 阻断项 + 操作”结构；
- 把 Docker 安装、主机初始化作为独立任务，展示阶段，不使用模拟进度；
- 所有操作绑定 `recordVersion`/`expectedRevision`；
- 任务失败时保留诊断和重查入口；
- 连接断开/主机身份变化时显示恢复路径。

### 7.4 Release 管理

- 保留构建进度面板，但进度来自真实阶段/日志，不随机生成；
- 列表列：DST build、image digest、基础验证、运行验证、引用实例、操作；
- 构建、导入、运行验证均为任务；
- 删除 Release 使用输入 build 确认，检查实例/任务/镜像引用；
- 构建状态轮询由任务中心统一接管，页面只订阅。

### 7.5 连接设置

- 信任状态、指纹确认、凭据保存、登录验证分开呈现；
- 指纹确认必须用户勾选，不能自动确认；
- 登录验证是短命令，但需要显示进行中/失败/重试；
- 删除连接记录不影响远端，说明清楚；
- 保存新主机后不自动跳转，给出下一步入口。

### 7.6 概览

- 身份区块：实例 ID、当前 Release/build、配置版本、运行验证等级；
- 分片区块：Master/Caves 状态、端口、最近同步；
- 部署区块：DST、MOD、世界 revision、最近备份、公网连通性；
- 端口表：宿主机/容器监听/发布状态；
- 主操作按真实状态二选一：启动或停止/重启；
- 任务区块：当前任务 + 最近任务，链接任务中心；
- 所有未知状态显示“未知”，不伪装为 0。

### 7.7 世界

- 保留 tabs：基本设置、访问与联机、世界生成/规则；
- 草稿与 base revision 分离；
- 底部操作栏显示：有未保存更改 / 待应用 / 已应用；
- **保存并应用**是唯一主操作：保存后由后端检查并应用；
- 不再打开“应用预览”对话框；阻断项内联显示；
- 离开页面保留草稿；冲突时显示比较，不覆盖。

### 7.8 MOD（实例）

**前提：内容来自主机级 MOD 库；实例不再下载或准备 MOD 内容。**

- 页头操作：`主机库`、`刷新`、`应用更改`；
- 摘要：已启用、待应用、配置版本；
- 列表列：MOD、所选内容/版本、期望状态、实际部署、运行验证、操作；
- 行操作：从主机库选择/更换版本、启用/禁用、编辑配置、移除；
- 选择版本对话框：列出主机库已有内容（Workshop ID、contentDigest、大小、准备时间），选择后写入实例期望；
- 配置编辑只写实例期望，不修改主机库；
- “应用更改”检查差异与阻断项；应用前可选“创建冷备份”，默认关闭，并说明失败恢复含义；
- 应用任务进入任务中心，失败保留诊断；
- **不提供**：实例页按 Workshop ID 添加、实例级下载/准备、实例级内容更新检查；
- 空状态引导“前往主机 MOD 库添加内容”，而不是在实例页直接输入 ID。

### 7.9 备份

- 摘要：备份数、可恢复、校验失败、占用空间；
- 列表：时间、模式、原因、build、大小、校验、恢复能力、操作；
- 创建备份对话框：冷/准热选择 + 影响说明 + 预计耗时；
- 恢复：确认对话框显示目标实例、备份、版本、影响、保护点；
- 下载：本地任务，显示进度/断点/校验；
- 删除：输入确认；保护点/唯一恢复点不可删；
- 任务进度统一进操作栏/任务中心。

### 7.10 诊断

- 分片日志：Master/Caves 切换、cursor、刷新、只读；
- 健康摘要：进程、监听、容器、磁盘、最近备份、公网连通性；
- 失败任务：阶段、错误码、现场是否保留、恢复入口；
- 日志脱敏；不展示秘密；
- 刷新证据是查询，不阻塞其他页面。

### 7.11 主机 MOD 库

原型未覆盖但当前项目已有，重构时必须补齐为独立页面：

- 路由：`/hosts/:serverId/mods`，属于主机上下文；
- 页头：`添加 MOD`；
- 列表列：Workshop ID、contentDigest、大小、使用实例、准备时间、操作；
- 添加流程：输入 Workshop ID/链接 → 查询元数据 → 选择候选 → `prepare_host_mods` 持久任务 → 固定内容到主机库；
- 任务：显示准备阶段/失败/重试；任务 target 是 HostTarget，不绑定实例；
- 删除：检查实例期望/部署引用、备份引用、任务引用；输入内容摘要或名称确认；
- 空状态：引导添加第一条主机内容；
- 不在实例 MOD 页重复“添加/下载”能力，避免出现两套内容来源。

### 7.12 应用设置

- 主题切换（亮/暗/跟随系统）；
- 数据位置、数据库、应用标识、版本；
- 不放置远端主机设置；连接设置在主机上下文。

---

## 8. 核心流程

### 8.1 首次创建/部署

```text
专服列表 → 新建专服（名称 + Release + 启动策略 + 洞穴）
→ 提交（检查并创建，单命令）
→ 对话框关闭
→ 操作栏/任务中心显示创建任务
→ 创建成功后进入专服概览
→ 概览显示“已部署/已停止”和首次启动入口
```

要点：

- 不再有独立预览窗口；
- 创建成功后不自动启动，除非用户明确选择“创建后启动”（若后端支持）；
- 创建任务阶段：queued → preflight → reserve → prepareDirectories → download → stagingVerify → publish → verify → finalize；
- 页面只订阅任务，不阻塞。

### 8.2 启动/停止/重启

```text
概览主操作 → 确认影响 → 提交任务（或短命令，取决于后端改造）
→ 操作栏显示运行任务
→ 完成后刷新概览
```

- 运行中 → 停止/重启；已停止 → 启动；
- 显示影响分片、玩家断开、是否创建备份；
- 页面卸载不取消；
- 任务失败保留阶段和诊断。

### 8.3 世界保存并应用

```text
编辑 → 底部操作栏出现“保存并应用”
→ 保存期望 → 后端检查并应用
→ 阻断项内联 → 成功后更新 revision
```

- 不打开预览窗口；
- 草稿离开页面保留；
- 应用期间显示任务；
- 失败保留已保存期望和现场。

### 8.4 MOD 应用

```text
主机 MOD 库：添加/准备内容（一次，供所有实例选择）
→ 实例 MOD 页：从主机库选择/更换版本
→ 保存实例期望
→ 应用更改
→ 检查差异/阻断项
→ 可选创建冷备份（默认关闭）
→ 提交实例应用任务
→ 任务中心观察
```

- 实例不执行 per-instance 下载或准备；
- 无备份不阻止应用；
- 未备份时失败不自动恢复，明确提示；
- 应用任务阶段：preflight → prepare → stop → backup（可选）→ publish → verify → finalize；
- 主机级准备任务和实例级应用任务是两类 target，任务中心按 HostTarget / InstanceTarget 分组展示。

### 8.5 备份/恢复

```text
创建备份 → 选择冷/准热 → 提交任务
恢复 → 确认目标/影响/保护点 → 提交任务
下载 → 选择路径 → 本地任务
删除 → 输入名称确认 → 提交
```

### 8.6 删除专服

```text
列表删除 → 预览/确认（容器、网络、数据、归档、端口）
→ 输入专服名称 → 提交任务
→ 成功后刷新列表
```

---

## 9. 任务与操作栏规范

### 9.1 任务状态

```text
submitting → queued → running → succeeded
                           ├→ failed
                           ├→ interrupted
                           └→ cancelled
```

- `submitting` 是前端接纳阶段，超时后必须查询而不是重复提交；
- `queued` 显示队列位置；
- `interrupted` 不显示为失败，提示先对账；
- `failed` 必须显示阶段、错误码、现场保留情况、恢复动作；
- 终态任务不被旧事件覆盖（按 sequence）。

### 9.2 进度

- 有确定总数才显示百分比；
- 否则显示阶段文字 + spinner/不确定进度条；
- 不使用随机数生成进度；
- 不把“预计耗时”显示为已用时间。

### 9.3 操作栏

- 同一时刻只展示一个“当前关注任务”；
- 多任务时显示“+N 个任务”并链接任务中心；
- 队列状态用 neutral/warning，不用 danger；
- 关闭操作栏不影响任务；
- 任务详情从操作栏/任务中心打开，不在页面复制。

### 9.4 忙/排队语义

| 后端结果 | 前端文案 | 行为 |
| --- | --- | --- |
| `*_BUSY` / `INSTANCE_BUSY` | 该专服有维护任务进行中 | 显示当前任务，不展示红色失败 |
| `REGISTRY_BUSY` | 主机注册表正在更新，已排队 | 保留输入，稍后重试 |
| `CONNECTION_BUSY` | 连接正在建立 | 等待/重试；P0 先做前端排队提示 |
| `REVISION_CONFLICT` | 远端已变化 | 比较/刷新，不自动覆盖 |
| `STALE` | 结果已过期 | 禁用写操作，刷新 |

---

## 10. 迁移计划

### 阶段 0：基线与设计系统（1 周）

交付：

- 抽取 Token/基础样式文件；
- 确认 `AppDialog`、`PageHeader`、`StatusLabel`、`SummaryBar`、`EmptyState`、`ErrorBanner`、`StaleNotice`；
- 建立 960/1480、亮/暗截图基线；
- 不改变业务行为。

退出标准：

- 现有页面视觉不回退；
- `npm run check` 通过；
- 组件在至少 2 个页面复用。

### 阶段 1：外壳与信息架构（1 周）

交付：

- `AppShell`、`AppSidebar`、`HostContext`、`Topbar`、`OperationBar`；
- 两级导航与主机切换；
- 新增 `/tasks` 路由与任务中心骨架；
- 移除更新入口；
- 保留现有路由参数与守卫。

退出标准：

- 主机/实例深链接、刷新、返回可用；
- 导航不会把实例操作指向另一台主机；
- 960 宽度无横向溢出。

### 阶段 2：数据层（1–2 周）

交付：

- 通用 `QueryClient/useQuery`；
- `OperationStore` 扩展（轮询、事件、对账、历史）；
- `DraftStore`；
- 统一错误分类和忙/排队语义；
- 页面不再各自实现 `setInterval`。

退出标准：

- 同一查询并发不会互相覆盖；
- 页面切换不停止任务；
- 任务可以从列表/任务中心恢复；
- 所有查询有 stale/error 路径。

### 阶段 3：页面迁移（2–3 周）

顺序：

1. 主机列表、专服列表、概览；
2. 世界、MOD、备份；
3. Release、主机环境、连接设置、主机 MOD 库；
4. 诊断、设置、任务中心。

每个页面：

- 建立 Query/Draft/Operation 边界；
- 补齐状态；
- 移除重复轮询；
- 迁移主操作到操作栏；
- 保留现有命令和协议。

退出标准：

- 页面无独立 `setInterval`；
- 页面卸载不丢任务；
- 同一操作只有一个主入口；
- 表格/对话框/表单通过可访问性检查。

### 阶段 4：清理与验收（1 周）

- 移除更新占位页、演示数据、旧摘要组件；
- 删除重复样式和死代码；
- 更新文档和测试；
- 完成 960/1480、亮/暗、键盘/焦点/Escape 验收。

---

## 11. 测试策略

### 11.1 单元测试

- `QueryClient`：乱序响应、缓存、stale；
- `OperationStore`：接纳、轮询、终态、sequence、重连对账；
- `DraftStore`：恢复、冲突、清除；
- 错误映射：BUSY/QUEUED/REVISION_CONFLICT；
- 状态文案：未知不显示 0。

### 11.2 组件测试

- `OperationBar`：多任务、队列、任务详情入口；
- `DataTable`：加载/空/错误/stale；
- `ConfirmDialog` / `TypedConfirmDialog`：确认禁用、Escape、焦点返回；
- `TaskTimeline`：阶段/失败/恢复。

### 11.3 集成测试（mock IPC）

沿用仓库现有 `scripts/test-*-ui.mjs` 模式：

- 创建任务在切页后继续；
- 任务列表对账不重复执行；
- 查询乱序不覆盖；
- 主机切换后迟到响应被丢弃；
- 忙状态显示排队而不是错误。

### 11.4 视觉与可访问性

- 1480×920、960×680；
- 亮/暗；
- 键盘 Tab/方向键/Escape；
- 焦点可见、焦点返回；
- 表格可选列降级；
- 减少动画偏好。

---

## 12. 风险与决策

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 后端任务接口不统一 | 任务中心需要适配多套接口 | 先做 `TaskAdapter` 聚合层；后续推动统一 |
| 锁冲突仍会返回 BUSY | UI 仍可能出现忙状态 | P0 显示排队/等待，不显示红色错误 |
| 移除预览步骤信息不足 | 用户看不到端口/影响 | 把信息移入表单摘要/确认/任务详情，而不是独立预览页 |
| 更新页移除 | 可能影响既有链接 | `/updates` 重定向到概览或 404；不显示入口 |
| 设计原型双导航 | 目标身份混淆 | 采用两级上下文导航，不照搬同时展开 |
| 表格列过多 | 960 宽度溢出 | 可选列 + 行详情；按优先级降级 |
| 页面级轮询迁移成本 | 回归风险 | 先迁移任务型页面，再迁移查询型页面 |

---

## 13. 实施顺序建议

1. 先做外壳、导航、任务中心和操作栏，不动业务页；
2. 再把 `useTasks` 升级为 OperationStore，接管创建/MOD/备份任务轮询；
3. 接着迁移概览、专服列表、世界、MOD、备份；
4. 最后迁移 Release、环境、连接、主机 MOD、诊断、设置；
5. 移除更新入口和演示数据；
6. 完成后端任务统一/锁队列改造（另立工作包）。

---

## 附录 A：样例页面 → 目标路由/组件

| 样例页面 | 目标路由 | 目标页面/组件 |
| --- | --- | --- |
| 主机列表 | `/hosts` | `HostsPage` |
| 专服列表 | `/hosts/:serverId/instances` | `InstanceListPage` |
| Release | `/hosts/:serverId/releases` | `ReleaseManagementPage` |
| 主机环境 | `/hosts/:serverId/environment` | `HostEnvironmentPage` |
| 连接设置 | `/hosts/:serverId/connection` | `ConnectionSettingsPage` |
| 专服概览 | `/hosts/:serverId/instances/:instanceId/overview` | `InstanceOverviewPage` |
| 世界 | `.../world` | `WorldPage` |
| MOD | `.../mods` | `ModsPage` |
| 备份 | `.../backups` | `BackupsPage` |
| 诊断 | `.../diagnostics` | `DiagnosticsPage` |
| 应用设置 | `/settings` | `SettingsPage` |
| 更新 | 不注册 | 待更新功能 |
| （样例缺失） | `/hosts/:serverId/mods` | `HostModsPage` |
| （样例缺失） | `/tasks` | `TaskCenterPage` |

## 附录 B：样例组件 → 现有实现

| 样例 | 现有实现 | 动作 |
| --- | --- | --- |
| `dialog.dialog` | `AppDialog.vue` | 保留封装，迁入新视觉 |
| `button` | `.button` 样式 | 保留/抽组件 |
| `status` | `StatusLabel.vue` | 扩展 tone/尺寸 |
| `table` | 各页面内联 | 抽 `DataTable` |
| `summary` | 各页面内联 | 抽 `SummaryBar` |
| 操作栏 | 无 | 新增 |
| 任务详情 dialog | 部分页面内联 | 抽 `TaskDialog` |
| 输入名称删除 | 各页内联 | 抽 `TypedConfirmDialog` |
| 主题 | `useTheme.ts` | 保留 |
| 图标 | `@lucide/vue` | 保留，不引入 sprite |

## 附录 C：当前不应复制的实现细节

- 模块级 `pages` 元数据和 `setPage`；
- `startTask` 的随机进度和 `setTimeout`；
- 硬编码的“上海服务器/生存一服”；
- `demoInstances`/静态表格数据；
- 同时展开主机/实例导航；
- 独立更新页；
- 创建预览步骤；
- 实例级 MOD 添加/下载/检查更新（当前项目已改为主机级 MOD 库）；
- 世界应用预览弹窗；
- 只靠 `hidden` 切换的页面；
- 没有 loading/error/stale 的静态表格。
