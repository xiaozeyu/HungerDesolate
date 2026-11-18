# 前端实施文档

- 版本：1.3.8
- 日期：2026-10-14；更新：2026-11-17
- 状态：已实现并持续维护；UI 使用 Naive UI 与自有 CSS Token，导航与数据所有权按 target 重建
- 技术：Vue 3 / TypeScript / Vite / Vue Router / Tauri 2、Naive UI、自有 CSS Token、`@lucide/vue`
- 契约：[共享接口契约](api-contract.md)

## 1. 交付目标与目录

前端需要让用户始终清楚操作的主机和专服，且任何查询、草稿或任务都不会因切换和乱序进入错误页面。第一批页面可以用契约 fixture 开发，但不能用 fixture 完成取代真实后端联调验收。

```text
src/
  api/{invoke,servers,hosts,instances,world,mods,backups,updates,tasks}.ts
  types/{contracts,viewModels}.ts
  context/{activeContext,navigationGuard}.ts
  composables/
    useServers.ts             # 连接记录与连接状态
    useInstanceList.ts        # hostId 归属的摘要
    useInstanceQueries.ts     # 目标/代次/序号统一校验
    useWorldDraft.ts
    useModDraft.ts
    useInstanceBackups.ts
    useTasks.ts               # 应用级，与当前详情解耦
  components/
    shell/{AppSidebar,AppTopbar,OperationBar,TaskDetails}.vue
    common/{Dialog,Select,Menu,Tooltip,Button,EmptyState,StatusLabel}.vue
    instance/{InstanceTable,ShardStatus,ChangePreview}.vue
  pages/
    HostsPage.vue
    InstanceListPage.vue
    HostEnvironmentPage.vue
    ConnectionSettingsPage.vue
    InstanceOverviewPage.vue
    WorldPage.vue
    ModsPage.vue
    BackupsPage.vue
    UpdatesPage.vue
    DiagnosticsPage.vue
    SettingsPage.vue
  lib/{queryGuard,draftMerge,taskReducer,commandError}.ts
  fixtures/{multiHost,delayedResponses,taskEvents}.ts
  router.ts
  style.css
```

默认采用 composable，不为规划新增状态库。API DTO 与页面 view model 分开；状态标签由明确 enum 派生，禁止根据中文文字判断状态。统一 invoke wrapper 负责参数/响应校验和 CommandError 归一化，生产禁止打印敏感请求。

## 2. 路由与导航

采用 hash history 适配桌面资源路径。路由包含本地连接 serverId 和专服 instanceId；hostId 从已信任连接和远端发现解析，不能把 URL 当操作权限。

| 路由 | 页面与上下文 |
| --- | --- |
| `/hosts` | 无主机时的连接列表/添加入口 |
| `/hosts/:serverId/instances` | 当前主机专服列表，HostTarget |
| `/hosts/:serverId/environment` | 主机环境，HostTarget 或初始化前已信任连接 |
| `/hosts/:serverId/mods` | 主机 MOD 库，HostTarget |
| `/hosts/:serverId/connection` | 当前连接设置 |
| `/hosts/:serverId/instances/:instanceId/overview` | 专服概览，InstanceTarget |
| 同前缀 `/world`、`/mods`、`/backups`、`/updates`、`/diagnostics` | 当前实例各领域 |
| `/settings` | 应用设置，保留已有对象作为偏好，不提供无身份业务写入 |

根路由进入最后有效主机的专服列表，无有效主机进入 /hosts。不恢复“最后 MOD 页面”而跳过专服选择。无身份 `/mods` 等旧地址统一引导到列表，不匹配默认实例。

### 2.1 路由守卫

1. 校验路由 ID 格式与本地连接存在性。
2. 连接/信任未完成时先完成连接流程，期间不挂载实例查询。
3. 解析 hostId，从该主机列表验证 instanceId 的归属与 lifecycle。
4. 进入详情需要本次列表选择建立的 selection grant，包含 hostId/instanceId/连接版本；同实例内部页面可复用。应用冷启动、外部 deep link、跨实例浏览器历史必须先回列表显示待选择对象，再由“进入”完成，不自动执行写入。
5. 删除、retired 或主机身份变化时失效 selection grant；显示原因，返回正确主机列表。历史备份另以原身份只读访问，不能当作可运行详情。
6. 离开实例先处理未保存草稿，确认后再递增 contextEpoch 并完成导航。

selection grant 是产品导航约束，后端仍根据 target 做权限校验。列表选择 A 后只允许 A 的详情路由；选择 B 必须先回列表。任务详情的“定位目标”也先回目标主机列表并高亮对象，不绕过列表直达 MOD。

### 2.2 两层外壳

主机层侧栏：主机切换器、专服列表、主机环境、连接设置，底部应用设置。尚未选实例不显示世界/MOD/备份入口。

实例层侧栏：同一个主机切换器、返回专服列表、当前专服名、概览/世界/MOD/备份/诊断、底部应用设置。顶部只有主机/专服/页面面包屑，正常 SSH/分片状态不重复常驻。

任务操作栏在两层与应用设置页均保留，任务与页面导航独立。主机切换即时解除活动实例并进入目标列表，不停游戏。返回列表解除详情上下文，不卸载应用任务订阅。

## 3. 布局、组件与可访问性

2026-10-17 文案约束：页面标题不附带常驻功能说明；表单、选项和页脚不重复描述流程或实现。字段使用可见标签，必要占位符仅给短格式示例。读取失败、版本冲突、部分保存等信息按发生条件显示；指纹核对和删除后果放在确认弹窗内。示例页面只保留简短“演示数据”标识，不堆叠开发进度说明。

沿用语义 Token：canvas/sidebar/surface/text/text-secondary/border/accent，以及 success/warning/danger。亮暗主题仅覆盖语义值，页面组件不硬编码原始颜色。

| 项目 | 实施值 |
| --- | --- |
| 外壳 | 侧栏 236px；顶部 56px；底部操作栏至少 58px；内容最大宽 1120px |
| 页面 | 边距 24px；标题 20px；区块标题 16px；正文/控件 14px；元数据 12px |
| 间距 | 4/8/12/16/24/32px；区块通常 24 或 32px |
| 形状 | 控件圆角 6px；必要面板 8px；Dialog 10px |
| 字体 | 本地 Inter；中文系统回退；日志/端口/ID/路径用本地 JetBrains Mono |
| 响应布局 | 最小 960×680；小于 1100px 的双栏转单栏；禁止整页水平滚动 |
| 动效 | hover 120ms、弹层 160ms、Dialog 180ms、操作栏 200ms；只 opacity/transform |

主内容与 Dialog body 各自正确滚动，底部操作栏和 Dialog footer 不被长表单推走。长实例名/路径用受控截断与 Tooltip，不压缩操作列。表格文字左对齐、数字右对齐、操作右对齐，关键操作始终可见。

Dialog/Select/Menu/Tooltip/Table 使用 Naive UI；主题映射在 `src/naive/theme.ts`，结构覆盖在 `src/naive/overrides.css`。表单逐项可见 label，错误紧贴字段。图标统一 16/18px、Lucide 1.75px 描边；图标按钮有 aria-label 和 Tooltip。状态同时使用文字/图标，不单靠红绿颜色。

危险确认显示真实主机/专服名、影响、保护备份与恢复限制；简单确认不堆大卡片。键盘支持 Tab/方向键/Escape、弹层外部关闭、焦点锁定与关闭后回触发器。失效任务错误/现场保留时关闭 Dialog 只关闭观察，不取消业务任务。

减少动态效果时近乎即时切换。禁止渐变、玻璃效果、嵌套卡片、营销式大标题、第二套任务抽屉和装饰性循环动画。旧样式与规划尺寸有差异时以第四版规划为准；按组件迁移，不整份复制旧 style.css 的例外与历史残留。

## 4. 状态归属与请求正确性

### 4.1 状态结构

```ts
interface ActiveContext {
  serverId: string;
  hostId: string;
  instanceId: string | null;
  contextEpoch: number;
  connectionVersion: number;
}
interface QueryTicket {
  target: InstanceTarget;
  contextEpoch: number;
  connectionVersion: number;
  requestSeq: number;
  requestId: string;
  queryKey: string;
  revisionFloor: number | null;
}
```

领域缓存 key 为 hostId/instanceId/domain/参数摘要；route 和连接使用 serverId。相同 host 的别名连接共享草稿和脏标记；一次活动会话中仍校验具体 serverId/连接版本，避免旧连接错误覆盖新连接结果。主机级查询使用 hostId/domain 与主机代次；无 hostId 的连接级查询按 serverId/连接版本隔离。

只有一个活动详情 subscription；后台仅维持任务观察和必要主机摘要。不同专服的完整配置/MOD/备份不预加载。缓存可有限保留，设计上限最近 10 个实例轻量快照，草稿与任务不跟快照 LRU 一起丢弃。

### 4.2 每次查询的提交规则

进入/离开实例、主机切换、连接版本变化都会使 contextEpoch 增加，包括 A → B → A。启动 query 时增加该 queryKey 的 requestSeq，捕获 immutable ticket 并 set loading。

响应提交必须同时满足：

1. 当前 target 与 ticket 的 serverId/hostId/instanceId 一致。
2. 当前 contextEpoch/connectionVersion 与 ticket 一致。
3. 当前 queryKey requestSeq 仍等于 ticket.requestSeq。
4. response.requestId/target 与请求一致，schemaVersion 支持。
5. 有配置 revision 的返回不早于本实例 revisionFloor。

示意流程（所有分支共用同一个 guard）：

```ts
const ticket = beginQuery(context, domain, params);
try {
  const response = await api.read(ticket.target, ticket.requestId, params);
  if (!isCurrent(ticket)) return;
  if (!validateResponse(response, ticket)) {
    commitContractError(ticket); // 仅当前 ticket 才能显示身份/schema 错误
    return;
  }
  if (!meetsRevisionFloor(response, ticket)) {
    scheduleGuardedRefetch(ticket.queryKey);
    return;
  }
  commitSnapshot(ticket, response);
} catch (error) {
  if (isCurrent(ticket)) commitQueryError(ticket, error);
} finally {
  if (isCurrent(ticket)) finishQuery(ticket);
}
```

成功、error 和 finally 都校验；旧 finally 不能清掉新请求 loading。取消是优化，不能代替 guard；Tauri invoke 无法取消时让其返回并丢弃。revision 低于下限不写入 data/error，执行一次受控重查；连续异常转明确契约/对账问题，禁止无限重查循环。

### 4.3 刷新与只读快照

新进入 B 先显示 B 的 skeleton/empty，不用 A 数据占位。重返 A 可以显示 A 自己的已标时间缓存，并同时重查；相同对象过期快照明确 stale。

默认设计：活动实例 runtime 每 5 秒刷新；主机下的专服列表不自动轮询，进入页面读取一次，用户点击“刷新”时重新读取。手动刷新替换同 query 最新序号，旧响应不得提交。任务观察不依赖页面可见性；列表读取失败时保留同对象 stale 与手动刷新入口，不自动重试。

world/mods/backups/updates 进入时查，之后由手动刷新和精确失效触发；不每次 runtime 都重新覆盖全部领域。Workshop 元数据按独立采集时间缓存；“检查更新”读取外部事实，UI 显示检查时间，不伪装安装/更新任务。

当前主机失联保留同对象快照/时间，标未知并禁写。不能把最后一次成功继续显示为实时运行，也不能把请求失败转成 0 玩家或空备份列表。

## 5. 草稿、冲突与保存应用

四种状态独立：用户草稿、已保存期望、实际部署、运行事实。useWorldDraft/useModDraft 的 key 为 hostId/instanceId/domain；baseRevision 记录草稿开始时的已保存版本。

### 5.1 草稿生命周期

- 第一次进入：从已保存期望建立 base；已存在本实例草稿则恢复非敏感字段。
- 后台刷新：更新查询快照；dirty 草稿不覆盖，revision/digest 变化则标冲突待比较。
- “保存草稿”：CAS 保存期望配置，成功更新 baseRevision/revisionFloor；仍可显示“待应用”。
- “保存并应用”：先保存得到新 revision → 调用 `apply_world_config`，后端在同一命令内先检查后应用。保存成功但检查/部署失败，保留已保存期望与待应用状态，不能回退显示原草稿未保存；前端不打开应用预览窗口或单独调用检查命令。
- “应用已保存配置”：只应用后端期望，不在任务启动后读取当前草稿。
- 成功部署：重新读取/比较实际摘要，再清除已完成对应草稿；任务期间新编辑产生 draftVersion 时不能清除新草稿。

写入 ticket 同样固定 target/epoch。短写入成功即使离开页面仍更新原实例 revisionFloor/失效标记，但不覆盖当前其他实例的表单或跳转。草稿清理要核对提交时 draftVersion 与 payloadDigest，不能按“任务成功”清整个实例草稿。

### 5.2 离开与版本冲突

离开 dirty 实例提供“保留草稿并切换 / 丢弃更改 / 留在当前页”。保留仅持久化非敏感数据；Token 输入属于临时内存，切换时另行提示需要重新填写，绝不写草稿。导航确认完成前不改变活动身份。

REVISION_CONFLICT 读取最新配置，展示 base / 我的更改 / 远端现状。用户选择逐项保留/使用远端，生成基于最新 revision 的新草稿，再预览保存；禁止自动覆盖或无声 last-write-wins。MOD 配置按 workshopId 和字段比较，不能按数组位置 merge。

## 6. 任务 store 与唯一操作栏

useTasks 在应用启动注册一次事件监听，先 subscribe 再 list/reconcile；按 taskId+sequence 合并。确定终态不被旧 running 覆盖；interrupted 的更正只接受更高序号且带对账证据。事件丢失通过 getTask 重查，事件本身不充当成功权威。

任务 target 与创建时主机/专服名快照用于展示；实时名称可更新辅助显示，但底层业务 target 不变。删除连接后仍可查看本地任务历史，重新添加并确认同 hostId 后可对账。

底部操作栏显示实际目标“上海服务器 · 生存一服 · 更新 MOD”、阶段、真实进度、详情入口；queued 显示排队原因，断线显示“连接中断，等待核实”，未知进度用 indeterminate。按钮只 disabled/loading，不再重复第二份阶段与百分比。

多任务选择规则：最近由用户发起的未完成任务优先；允许在同一个操作栏切换任务或查看数量。TaskDetails 为该栏入口打开的一个 Dialog，不新增全局抽屉。失败终态保留可执行恢复入口；纯查询刷新不抢占任务栏。

A 任务在 B 页面完成时：更新 A 任务历史 → 给 A domains 和所在主机列表标 dirty → B 页面不变。当前活动 target 匹配才立即重查；事件不携带世界/MOD/备份数组直接写页面。完成不会自动导航、选择对象、重启其他实例。

取消按钮以 cancelAllowed 为准，发送请求后显示“等待安全结束”，收到确认才显示 cancelled。恢复动作通过契约白名单打开对账/预览/备份恢复，不执行后端字符串命令。

环境准备在 hostId 尚未建立时显示连接名与固定 SSH 指纹归属；完成后关联 preparedHost，不把未知 hostId 伪造为默认主机。备份下载显示为本地传输任务，桌面退出后允许中断；远端更新/安装/恢复继续运行的承诺不套用到下载。

## 7. P0 页面实施说明

### 7.1 主机连接与环境

连接表单分连接信息/认证/高级选项；保存不等于连接成功。首次/变化指纹展示旧新值与明确确认；不提供“忽略信任”开关。凭据不回填明文，输入只送专用 API。

环境页先展示部署阻断项、可用空间/内存和 Docker/Compose 能力，再展开技术详情。准备环境需预览目录、安装和权限变化；失败展示任务阶段与恢复入口。未初始化主机可以只读检查和准备，不能创建实例。保存新主机后停留在列表，行内主操作直接“连接”；连接时若未信任或指纹变化，立即弹出指纹确认，确认后继续验证登录并进入主机环境或专服列表。添加主机对话框提供“测试连接”，保存后执行同一连接流程，不把连接设置作为前置步骤。连接设置只用于编辑连接信息、替换凭据或重新扫描指纹。

### 7.2 专服列表与创建

表格：名称、状态、玩家/人数、已安装版本、最近同步、进入；低频操作放菜单。无实例显示新建入口；无响应显示同对象 stale 与刷新/连接入口。列表不提供批量更新/删服，不拿轻摘要代替完整健康结论。

新建 Dialog 只有一个主操作“创建专服”。字段包含名称、DST Release 选择、启动策略和洞穴开关，网络路线固定公网且自动分配端口；世界字段和 Token 在容器创建后进入世界配置页编辑。Release 下拉框只列出通过基础验证的候选，标签包含 DST build 和 Release ID 前缀，以区分同版本的多次构建；只有一个候选时自动选中，多个候选时必须显式选择。列表加载、失败与空状态可见；加载失败时不能沿用旧候选提交创建。

点击“创建专服”后只调用一次 `create_instance`：后端先完成身份、注册表、release、端口与 Docker/Compose 检查，通过后在同一命令内接纳持久创建任务并按任务轮询。前端没有预览页、预览响应或独立的“重新检查”动作；检查阻断项（`CREATE_BLOCKED` 的 `details.blockers`）以白名单文案显示在同一对话框。提交期间表单控件禁用；任务开始后表单替换为任务状态与失败诊断，不再展示建议端口、目录或镜像摘要等未持久化的预览信息。创建成功且用户仍在本次创建上下文时在列表提示进入世界配置；若已切换主机/页面，只更新原列表和任务。失败列表保留 createFailed，进入后提供“继续创建/清理现场”。创建表单不提供“创建后启动”。

### 7.3 概览

显示当前实例身份、Master/Caves、实际 build、阻断项、期望/实际差异。启动/停止主动作按真实状态决定；未知时禁写并先对账。restart 二次明确停服影响，不顺带更新 DST/MOD。

开机启动是实例策略，保存后需要任务验证。洞穴未启用显示“未启用”，失联显示“未知”。高级端口与目录只读折叠，普通用户不编辑容器名/目录。

### 7.4 世界

高频项：房间名、Token 状态/替换、游戏模式、人数、PVP、洞穴。高级世界规则折叠并复用现有选项组件。前端字段范围来自统一 schema，不自创 DST 参数。

显示已保存期望与部署差异，主操作“保存并应用”：先 CAS 保存得到新 revision，再调用 `apply_world_config`；后端先检查期望摘要、Token、部署、Release 与渲染条件，阻断项以 `WORLD_APPLY_BLOCKED` 内联显示，通过后直接部署。前端没有应用预览窗口或“预览应用”入口。“仅保存”作为次要操作。禁用洞穴明确“停止洞穴，保留原存档”；普通开关不作为重置世界动作。后台 runtime 刷新不能改输入。警告（已有存档时世界生成选项只对新世界生效）随应用结果返回并显示在操作反馈中。

### 7.5 MOD

主机级“MOD 库”负责按 Workshop ID 查询并展示图片、名称、作者与可用状态，确认后下载内容，同时提供版本列表、使用实例和删除；实例 MOD 页面通过“主机库”选择 `artifactDigest` 加入期望，不提供实例级下载路径。列表分期望启用/配置、实际部署、更新与异常，显示最后检查时间。普通元数据缓存可以共享，勾选/配置/下载状态不可共享。更新任务成功后自动清理未被引用的旧版本，页面不提供手动清理入口，只显示清理结果。

配置 Dialog 按 safe config schema 展示字段。实例添加 MOD 只通过“主机库”选择版本并保存 `artifactDigest`，不提供实例级下载或准备入口。已有 MOD 的变更使用“应用更改”，预览通过后直接提交应用任务，出现阻断项时展示应用预览对话框。页面提供“应用前创建冷备份”开关，默认关闭；关闭时提示应用失败无法自动恢复原部署。启用、移除和配置改变先进入当前实例期望；应用仍展示清单差异和停服影响。分别呈现“已部署”“运行验证通过”，任务失败保留到达阶段。

不能把检查更新触发成全局实例更新；不能默认给所有实例添加一个 MOD。复制模板为 P2，不在首期加跨实例入口。

### 7.6 备份

表格按时间倒序，显示时间、模式、备注、大小、校验/恢复能力。时间与备注固定宽度，超出省略并在悬浮时显示全文，避免长内容把表格撑出横向滚动。恢复按钮根据 verified、归档可用和 target 归属决定；不可恢复有具体原因，不用统一灰色按钮隐藏信息。备注可创建时填写、创建后逐行编辑；编辑使用 `expectedNote` CAS，成功后只更新当前行。

创建备份预览停服与空间；下载经系统文件选择获取 destinationHandle，保存对话框默认建议 `.tar.gz` 文件名。下载状态不进入备份列表：右上角临时通知显示文件名、进度条和字节数，失败可在通知内重试，成功后自动消失；同一页面同一时间只发起一个下载。导入经系统文件选择取得本地归档，只允许同 host + 同 instance，页面显示一条本地导入记录（进行中/完成/失败），失败可重试并沿用原 operationId。包含秘密的备份明确提示内容范围与本地保存责任。恢复前展示原实例、备份 build/MOD、停服影响，并提供“是否创建恢复前保护点”的选择；不允许普通跨实例选择。备份归档只含世界存档，metadata 只记录 MOD 引用作为提示；恢复使用当前活动 Release 和主机库最新 MOD，缺失 MOD 时在恢复前和结果中警告但允许继续。详见 [主机级 MOD 库](../docs/host-mod-library.md)、[备份导入与备注设计](backup-import-note-plan.md)。

retired 实例备份以明确“已删除专服的历史备份”入口只读查看/下载；不把其“恢复”变成隐式重建实例。首期没有永久备份清理的快捷批量操作。

### 7.7 更新

Release 管理页显示主机最新 DST build、检查时间、候选 Release 和“检查 DST 更新”入口；未知时说明尚未成功检查，不显示最新。检查状态从主机读取，`checking` 时显示阶段并每 3 秒轮询，切换页面后重新进入可恢复；检查中按钮禁用，重复点击只恢复轮询。Release 缺失时在本页提供构建入口。“清理磁盘”也在本页：一个对话框同时展示构建缓存和可回收 Release，确认后先删除可回收 Release，再清理构建上下文、执行受控 builder prune 和未被 catalog 引用的 dangling 镜像；构建中禁用。

专服概览页显示当前 build，并提供“切换 DST 版本”：只列 build 高于当前、已验证的 Release；预览固定目标 Release、分片、世界存档保护点和回滚能力。按钮“创建备份并切换”，失败显示阶段与恢复动作。已恢复旧版本也保留“更新失败，已恢复原版本”，不能显示更新成功。旧产物缺失时明确不支持自动回滚。

### 7.8 诊断

分 Master/Caves 日志、健康摘要、失败任务及恢复步骤。日志固定读取最近 200 行，切分片后失效旧查询；内容转义，禁止把日志作为 HTML。只渲染有界窗口，不无界增长 DOM。

P0 提供有限诊断与任务详情；历史曲线、脱敏诊断包属于 P1，能力未实现时不露空入口。失败任务跳任务详情；定位实例必须经过列表。

### 7.9 删除专服与删除连接

删除专服只在实例内打开确认，显示名称、目录、备份保留和后果，要求输入当前显示名。默认创建/确认有效保护备份，失败阻断。开始后只观察任务；完成返回所属主机列表，若用户已在其他主机则不导航。

删除连接的文案明确仅移除本机连接记录。存在活动任务时提示远端仍继续、历史保留、重新连接可核实；不调用 deleteInstance，不自动停止游戏。

## 8. P1 页面增量

定时备份在备份页策略区：启用、时区/计划、保留、最近/下次执行与冲突说明；以远端实际策略为准，不用桌面定时器模拟。

健康历史放诊断页子区，有限证据/时间，避免增加独立主导航。诊断包导出先显示范围与脱敏结果，再选择位置。

旧部署迁移从当前主机列表进入只读发现/迁移预览，明确来源、目标、新旧端口、停服、保护备份、失败回退。旧凭据重新填写或专门授权导入，不默读旧 keyring。成功不自动删除旧目录。

## 9. 验证与交付

单元/组件回归覆盖：queryGuard（成功/error/finally）、A→B→A、同页乱序、revisionFloor、连接别名、草稿冲突、taskReducer 事件乱序/丢失、目标创建成功后上下文变化、取消原子阶段、页内授权与 deep link。

联调至少覆盖：真实完整 target、未信任 SSH、两个客户端 revision 冲突、A 任务在 B 页面完成、离线快照、恢复错误实例备份、列表 createFailed/retired、只读检查不创建任务。

```bash
npx vue-tsc --noEmit
npm run test:frontend
npm run build
git diff --check
```

工程骨架需建立上述 scripts；不因新仓库当前没有 package.json 声称检查已通过。四组视觉验收：亮/暗 × 960×680/1480×920，包含长名称、未知状态、长 Dialog、任务排队/失败、日志长行、空表；鼠标和键盘/Escape/焦点回退/减少动效逐项验证。

交付路由、composable、DTO/wrapper、契约 fixture、截图证据、交互场景记录。构建通过不能替代真实联调、视觉或数据隔离验收。
