# 前后端共享接口契约

- 日期：2026-10-14
- 契约版本：设计版 v1，DTO `schemaVersion: 1`
- 适用范围：Tauri command、前端 fixture、Rust DTO、远端协议转换
- 依赖：[产品规划](../new-product-plan.md)、[后端实施](backend-implementation.md)、[前端实施](frontend-implementation.md)

## 1. 通信与命名

前端只能通过 `src/api/` 调用 Tauri；页面不得自行 `invoke`、执行 Shell 或读写 SQLite。Rust command 使用 snake_case，TypeScript wrapper 使用 camelCase，参数统一为 `invoke(command, { request })`，DTO 为 camelCase。远端 JSON 同样使用 camelCase，Rust 内部使用 snake_case 并明确 serde 转换。

所有可失败命令返回 `Result<T, CommandError>`。查询直接返回 `QueryResponse<T>`；短写入返回 `MutationResult<T>`；长任务只返回 `TaskAccepted`，最终结果从任务查询获得。接受任务不等于操作完成。

请求结构、对象 ID、enum、长度和响应 schema 均须校验。必填 target/schema 不通过 `serde(default)` 补齐；不支持的版本返回 `SCHEMA_UNSUPPORTED`。前端校验只服务交互，后端与远端仍独立守住权限和身份边界。

时间统一为 UTC RFC 3339，UI 按本地时区展示。UUID 使用规范小写带连字符形式；revision/sequence 为非负安全整数，后端超过 JavaScript 安全整数范围前明确拒绝。未知值使用 `null` 或显式 unknown，不能用 0、空数组或 success 代替。

## 2. 身份与请求

```ts
type ServerId = string;   // 本客户端连接记录 UUID
type HostId = string;     // 远端管理根持久 UUID
type InstanceId = string; // 不随名称变化的 UUID
type TaskId = string;
type OperationId = string;
type ShardKind = "master" | "caves";

interface HostTarget {
  serverId: ServerId;
  hostId: HostId;
}
interface InstanceTarget extends HostTarget {
  instanceId: InstanceId;
}
interface ConnectionTaskTarget {
  serverId: ServerId;
  connectionVersion: number;
  verifiedFingerprint: string;
}
type TaskTarget = HostTarget | InstanceTarget | ConnectionTaskTarget;
interface ReadRequest<TTarget> {
  schemaVersion: 1;
  requestId: string;
  target: TTarget;
}
interface WriteRequest<TTarget> extends ReadRequest<TTarget> {
  operationId: OperationId;
  expectedRevision: number; // 创建时由具体命令改为 expectedRegistryRevision
}
```

`contextEpoch`、`requestSeq` 是前端本地控制字段，不依靠后端维护。`requestId` 用于对应请求与响应、诊断，不承担幂等。`operationId` 用于一次已确认的业务动作，网络重试保持不变，修改输入或发起新动作重新生成。

连接之前 `hostId` 未知，只允许连接级命令；SSH 信任通过后读取 `host.json`。管理根不存在时返回 `uninitialized` 能力状态，用户通过环境准备流程创建一次远端身份。若读取身份发生变化，阻断旧目标写入、清除绑定，重新确认管理根。不得按 IP/名称合并身份，也不得把 hostId 当 SSH 信任凭证。

同一管理根的多个 serverId 指向同一 hostId。路由和连接会话使用 serverId，业务缓存、草稿、锁、幂等按 hostId + instanceId 归属。任务持久化保存 HostId/InstanceId 与最初 serverId；通过另一连接对账时只更新查询所用连接，不改变业务目标。

尚无远端 hostId 的环境准备任务是唯一例外：使用 ConnectionTaskTarget 固定连接版本与已确认 SSH 指纹，operationId 的 bootstrap 索引存于受控准备目录；完成后 result 返回新 HostTarget，原任务 target 不变。准备过程不允许更换端点/指纹或改用其他连接。现有管理根的准备任务直接用 HostTarget。任务查询允许 ConnectionTaskTarget 仅查询这类 bootstrap 任务。

复制管理目录导致不同真实主机出现同 hostId 时，不自动合并。出现不兼容的主机证据或用户声明克隆时进入 `HOST_IDENTITY_AMBIGUOUS`，只读发现并要求重新初始化克隆身份；如何检测的可靠范围在阶段 0 记录，不能承诺仅凭 UUID 识别所有克隆。

## 3. 返回模型与版本

```ts
interface QueryResponse<T, TTarget = InstanceTarget> {
  schemaVersion: 1;
  requestId: string;
  target: TTarget;
  snapshotId: string;
  capturedAt: string;
  source: "remote" | "cache";
  freshness: "fresh" | "stale";
  configRevision: number | null;
  data: T;
}
type QueryDomain =
  | "instances" | "overview" | "runtime" | "world" | "mods"
  | "backups" | "updates" | "diagnostics" | "environment";
interface Invalidation {
  hostId: HostId;
  instanceId: InstanceId | null;
  domains: QueryDomain[];
}
interface MutationResult<T> {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  operationId: OperationId;
  verifiedRevision: number;
  data: T;
  invalidate: Invalidation[];
}
```

`configRevision` 是实例期望配置的全局递增版本，世界、MOD、名称、端口、开机启动等保存共用写冲突边界。任务在真正执行前重新检查，不以提交时的检查代替。不同客户端编辑不同领域仍可能冲突，首期接受显式比较后重试。

`registryRevision` 单独描述主机实例注册和端口预留；`snapshotId/capturedAt` 描述运行采样。`configRevision` 不用于比较日志、玩家人数或任务事件的新旧。

实际部署用 `deploymentId` 与领域摘要表述：`worldDigest`、`modsDigest`、`dstReleaseId`、`modsReleaseId`，同时记录 `worldAppliedRevision` 和 `modsAppliedRevision`。判断待应用时比较领域规范化 digest；不能因为另一个领域使全局 revision 增加就把已经部署的世界标记为改变。

保存期望配置后返回的新 revision 是前端该实例配置读取的下限。旧查询即使 requestSeq 有效，只要 revision 低于已确认版本也不得提交。任务失效通知只触发重查，不直接覆盖领域数据。

## 4. 领域 DTO

### 4.1 连接与主机

| DTO | 必备字段与规则 |
| --- | --- |
| ServerSummary | serverId、displayName、host、sshPort、username、authKind、hostId/null、connectionState、recordVersion；不返回密码、私钥或 keyring 引用 |
| FingerprintChallenge | challengeId、serverId、algorithm、fingerprint、previousFingerprint/null、expiresAt；challenge 绑定本次扫描对象与端点 |
| HostConnection | serverId、hostId/null、managementState、connectionState、connectedAt/null；managementState 为 uninitialized/ready/ambiguous |
| HostEnvironment | os、architecture、dockerVersion、composeVersion、disk、memory、checks、checkedAt、environmentCheckId |
| EnvironmentCheck | code、level(blocker/warning/info)、message、recoverySteps、observedValue/null |
| PortPlan | shard、purpose(game/steamAuth/steamMaster)、protocol(udp/tcp)、bindAddress、hostPort、containerPort；首期每项同号 |

连接状态为 disconnected/connecting/connected/unreachable/trustRequired。未完成信任不允许实例发现、环境准备或任意远端写入。

### 4.2 实例、运行、世界

| DTO | 必备字段与规则 |
| --- | --- |
| InstanceSummary | hostId、instanceId、displayName、gameMode/null、cavesEnabled/null、lifecycle、runtimeStatus、players/null、maxPlayers、installedBuild/null、lastSyncedAt/null、activeTaskId/null |
| InstanceOverview | identity、manifestSummary、runtime、blockers、desiredVsDeployed；不返回整个 MOD 或备份数组 |
| InstanceRuntime | snapshotId、capturedAt、status、shards[]、players/null、build/null、verificationLevel、evidence[] |
| ShardRuntime | shardKind、enabled、status、containerId/null、actualBuild/null、observedPorts、logAvailable、players/null |
| WorldConfig | revision、desired、deployed/null、desiredDigest、deployedDigest/null、deployedRevision/null、tokenStatus、serverPasswordStatus、validationWarnings |
| WorldSettings | roomName、description、gameMode、maxPlayers、pvp、language、pauseWhenEmpty、cavesEnabled、advanced、worldgen.master/caves；每分片包含固定 preset 与 worldgen/worldSettings overrides |

`lifecycle`：creating/ready/createFailed/deleting/deleteFailed/retired。`runtimeStatus`：unknown/stopped/starting/running/degraded/stopping/failed。创建生命周期与运行状态分开。

`verificationLevel`：processOnly/localVerified/networkVerified。容器 running 仅能证明进程层，不等价玩家可进入。玩家数不可确认时 `null` 显示“未知”；停止时显示“—”。启用洞穴但洞穴失败时专服为 degraded，不能因为 Master running 就显示全部正常。

Token、服务器密码与分片密钥普通 DTO 只暴露 configured/missing/unknown；不回填明文。Token 与服务器密码更换请求分别使用 `tokenChange`、`serverPasswordChange`，形状为 `{ action: "replace", value: string }` 或 `{ action: "keep" }`；敏感输入只保留在组件内存并经专用命令写入远端受保护文件，禁止进入通用草稿、任务摘要、日志或诊断。首期不提供删除凭据按钮；更换为空白保持已配置值不变。

### 4.3 MOD、备份、更新、日志

| DTO | 必备字段与规则 |
| --- | --- |
| WorkshopMetadata | workshopId(十进制字符串)、title、author、description、sourceUrl、metadataUpdatedAt、configSchema/null、fetchStatus |
| InstanceMod | workshopId、desiredEnabled、desiredConfig、deployedEnabled/null、deployedConfigDigest/null、desiredDigest、installedVersion/null、availableVersion/null、checkedAt/null、status |
| InstanceMods | revision、items[]、desiredDigest、deployedDigest/null、releaseId/null、lastCheckedAt/null、warnings[] |
| BackupSummary | backupId、hostId、instanceId、createdAt、reason、captureMode(cold/quasiHot)、captureSemantics、runtimeStatusAtCapture、sizeBytes、checksum/null(仅 pending/failed)、verification、restorability、dstBuild、dstReleaseId、modsReleaseId、configRevision、containsSecrets |
| UpdateStatus | installedBuild/null、availableBuild/null、checkedAt/null、dstUpdateState、modUpdateCounts、activeReleases、rollbackCapability、warnings[] |
| LogPage | shardKind、streamId、cursor/null、nextCursor/null、lines[{sequence,time/null,text}]、truncated、rotated |

WorkshopMetadata 不含启用、实例路径或安装状态。版本未知时不显示“已最新”；检查失败保留旧检查时间和失败原因。Lua 配置解析使用安全结构解析，禁止前端或远端执行来自 MOD 的任意 Lua。

`verification`：pending/verified/failed。`restorability`：restorable/missingArtifacts/incompatible/unknown。每个备份明确归属；校验通过不等价在所有版本均可恢复。备份包含秘密时下载和导出需明确提示且不生成无保护副本；诊断包必须排除秘密。

集合分页统一 `{ items, nextCursor: string | null }`，默认 limit 50、最大 200；日志默认 200 行、最大 1000 行且单次 UTF-8 内容最多 512 KiB。cursor 是后端不透明值，绑定目标、筛选与数据流，跨实例使用拒绝。排序由服务端确定，不能使用前端数组位置作为恢复/删除标识。

## 5. 任务协议

```ts
type TaskStatus = "queued" | "running" | "succeeded" | "failed"
  | "cancelled" | "interrupted";
interface TaskAccepted {
  schemaVersion: 1;
  taskId: TaskId;
  operationId: OperationId;
  target: TaskTarget;
  status: "queued" | "running";
  reused: boolean;
}
interface TaskSnapshot {
  schemaVersion: 1;
  taskId: TaskId;
  operationId: OperationId;
  target: TaskTarget;
  operation: string; // 实现为固定 TaskOperation enum
  status: TaskStatus;
  stage: string;     // 每种 operation 的白名单阶段
  lastCompletedStage: string | null;
  progress: { kind: "determinate"; completed: number; total: number }
    | { kind: "indeterminate" };
  sequence: number;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  cancelAllowed: boolean;
  observation: "live" | "stale" | "reconciling";
  journal?: Array<{ sequence: number; status: TaskStatus; stage: string; at: string }>;
  result: TaskResult | null;
  error: CommandError | null;
}
interface TaskResult {
  preparedHost: HostTarget | null; // 仅主机初始化任务返回
  verifiedRevision: number | null;
  verification: "passed" | "notVerified";
  remoteChanged: boolean;
  backupId: string | null;
  deploymentId: string | null;
  invalidate: Invalidation[];
  recoveryActions: RecoveryAction[];
}
interface RecoveryAction {
  action: "reconcile" | "retryPreparation" | "restoreBackup"
    | "resumeCreate" | "cleanupCreate" | "inspectLogs";
  label: string;
  requiresPreview: boolean;
}
```

恢复动作只是一份可用操作清单，前端点击后进入相应预览/命令；禁止把后端返回内容当命令字符串执行。

P0 TaskOperation 枚举固定为 hostPrepare / instanceCreate / instanceCreateResume / instanceCleanup / instanceStart / instanceStop / instanceRestart / instanceAutostart / worldApply / modsApply / instanceUpdate / backupCreate / backupDownload / backupRestore / instanceDelete。具体 stage 以对应操作的阶段表定义；禁止前后端各自猜测同义字符串。

| operation 分组 | 有效 stage（执行顺序；可省略不适用阶段） |
| --- | --- |
| hostPrepare | queued / preflight / prepareDirectories / prepareDocker / installScripts / verify / finalize |
| instanceCreate / instanceCreateResume | queued / preflight / reserve / prepareDirectories / download / stagingVerify / configure / publish / start / verify / finalize |
| instanceStart / instanceStop / instanceRestart / instanceAutostart | queued / preflight / stop / configure / start / verify / finalize |
| worldApply / modsApply / instanceUpdate | queued / preflight / download / stagingVerify / stop / backup / publish / start / verify / rollback / finalize |
| backupCreate | queued / preflight / stop / backup / backupVerify / start / verify / finalize |
| backupRestore | queued / preflight / stagingVerify / stop / backup / restoreCommit / start / verify / rollback / finalize |
| instanceDelete / instanceCleanup | queued / preflight / backup / stop / removeRuntime / retireData / verify / releasePorts / finalize |
| backupDownload | queued / preflight / transfer / checksumVerify / localPublish / finalize |

rollback 只在失败恢复分支执行，cancelled/interrupted 为状态而非伪造的新业务阶段；恢复分支必须保留 lastCompletedStage 证据。实现增加业务阶段时先更新此表、Rust enum 与前端标签，不允许未定义字符串。

backupDownload 是本地传输任务：远端仅只读提供固定备份，本地 task/journal/operation 索引作为下载执行权威，关闭桌面后可 interrupted，重开按本地临时文件/摘要对账。它不承诺像远端安装/更新/恢复一样在桌面退出后继续；重试关联原 operationId，恢复传输是否支持续传必须真实验证。

事件名固定 `famine://task-updated`，payload 为 TaskSnapshot，单条不超过 64 KiB，不含全量业务数组和日志正文。Tauri 事件只承担通知；客户端启动先订阅再 list_tasks/reconcile，按 sequence 合并，漏事件通过 get_task 获取持久状态。页面卸载不能解绑应用级任务监听，应用监听只注册一次。

任务状态规则：queued → running → succeeded/failed/cancelled；queued 可 cancelled。只有安全边界允许 running → cancelled。远端执行进程已确认消失且无法断言结果时转 interrupted；普通 SSH 断线仅 observation=stale，不擅自改远端状态。

interrupted 表示待核实：对账有证据时可转回 running，或转 succeeded/failed；这类更正必须有更高 sequence、记录 reconcile 证据，不重跑破坏性阶段。succeeded/failed/cancelled 为确定终态，旧事件不能覆盖；新业务重试创建新 taskId，并关联原任务。

最终 succeeded 必须有 `result.verification=passed`。恢复后的旧版本重新运行可以验证恢复成功，但原更新失败仍记录 failed，不能把“回滚成功”伪装成“更新成功”。无百分比证据时使用 indeterminate，不生成模拟进度。

## 6. 幂等、预览、并发

写入的幂等索引由远端维护，唯一键 `(hostId, operationId)`，保存 operation、业务 target、规范化 payloadDigest、taskId 或短写入结果。payloadDigest 不含 serverId、requestId、纯展示名称副本；秘密使用受保护的内容摘要参与比对且不返回摘要。相同 operationId/输入返回原结果；输入或目标不同返回 `OPERATION_ID_REUSED`。

上条针对远端实例/主机变更。本地连接保存/删除、指纹信任以及 backupDownload 使用本地事务/幂等索引，不能为这些本地操作伪造远端 task/status。get_operation 根据 operation 归属查询本地或受信任远端，但返回相同明确结果结构。

主机初始化使用固定 bootstrap 归属的 operationId 索引，建立 hostId 后保存关联但不产生第二个任务。索引与 task/status 必须可相互发现；接受响应丢失时，前端按原 operationId 调用 get_operation 查询，不依赖重新拼出秘密输入。

先校验身份，再查幂等命中，再对新操作校验 revision 和权限。已接受的任务重复关联不能因 revision 已改变而误报新任务冲突。网络超时后只能先对账，不能自动生成新 operationId。

危险写入先 preview：创建、部署世界/MOD、DST/MOD 更新、恢复、删除、迁移。`PlanPreview` 包含 planId、target、operation、baseRevision、registryRevision/null、inputDigest、impact、paths(只读展示)、portPlan、estimatedSpaceBytes/null、backupRequirement、warnings、expiresAt、allowed。

planId 在远端记录，绑定目标、规范化输入、revision、环境证据；设计有效期 10 分钟。执行携带 planId、operationId、expectedRevision 和确认字段。后台排队后仍重新检查当前身份、revision、端口、空间与备份；过期或条件改变返回 `PLAN_STALE`，不能按旧预览执行。幂等命中已有任务优先返回已有任务，不因预览过期再创建一个任务。

短保存也走实例锁和 revision CAS。排队不提前承诺已保存配置；同实例只允许一个已接受的变更任务，后续写入返回 `INSTANCE_BUSY`。不同实例的重型任务进入主机队列。

创建预览使用 HostTarget，可提供建议 instanceId/端口但不占用资源；执行在注册锁内分配不可变身份、真实预留与创建任务。返回 `TaskAccepted.target` 为最终 InstanceTarget；端口与预览不一致时返回 PLAN_STALE，重新预览，不能悄悄换号。

## 7. Command 目录

下表名称作为本版实现基线。只读写 R/Q 为查询，M 为短写入，T 为持久任务，P 为只读预览。请求都包含 schemaVersion/requestId；target 列写 connection 的仅携带 serverId 或新连接输入。

### 7.1 连接、环境、实例

| Rust command / TS wrapper | 类型/target | 业务输入 | 返回与要求 |
| --- | --- | --- | --- |
| list_servers / listServers | Q/local | 无 | ServerSummary[] |
| save_server / saveServer | M/local | 连接字段、认证输入、记录版本 | ServerSummary；秘密进 keyring，更新失败不得保留假记录 |
| scan_server_fingerprint / scanServerFingerprint | Q/connection | serverId | FingerprintChallenge；仅扫描，不隐式信任 |
| confirm_server_fingerprint / confirmServerFingerprint | M/connection | challengeId、fingerprint | 信任结果；确认后重握手验证未变化 |
| connect_server / connectServer | Q/connection | serverId | HostConnection；读取远端身份，不初始化目录 |
| disconnect_server / disconnectServer | M/connection | serverId | 断开客户端会话，不停游戏/远端任务 |
| delete_server / deleteServer | M/connection | serverId、记录版本 | 仅删除连接及其凭据引用；保留任务历史与远端数据 |
| get_host_environment / getHostEnvironment | Q/HostTarget | 无 | HostEnvironment；未初始化时允许已信任的连接级只读检查 |
| preview_docker_installation / previewDockerInstallation | P/connection（已实现） | ConnectionRequest | 只读核对 Ubuntu 26.04/root/amd64、APT 冲突、固定官方源路径与连通性；不写远端 |
| start_docker_installation / startDockerInstallation | T/connection（已实现） | planId、operationId、snapshotDigest、expiresAtUnixSeconds | Ubuntu 26.04 root-only；重新核验快照后以 transient systemd unit 运行固定 worker，task 状态远端持久化；不删除冲突包 |
| get_docker_installation_status / getDockerInstallationStatus | Q/connection（已实现） | ConnectionRequest、operationId（可选） | 指定 operationId 时只查询该任务，否则返回最近任务；无对应任务返回 task:null，禁止退回其他历史任务；断线后重连对账 |
| preview_host_initialization / previewHostInitialization | P/connection（已实现） | ConnectionRequest | 受控管理目录、host.json、空 registry 的预览；固定 Linux 路径和 10 分钟 plan |
| initialize_host / initializeHost | M/connection（已实现） | planId、ConnectionRequest | 原子创建管理目录身份与空 registry；不会安装 Docker/脚本，不覆盖现有数据；结果需再次读取并绑定 hostId |
| get_host_initialization / getHostInitialization | Q/connection（已实现） | ConnectionRequest | 按原连接版本和待处理 plan 查询初始化回执；响应丢失时用于对账 |
| preview_host_preparation / previewHostPreparation | P/connection（规划） | environmentCheckId | Docker/脚本/镜像安装预览，阶段 0 后实现 |
| prepare_host / prepareHost | T/connection（规划） | planId、operationId | 完整环境准备任务，依赖 controller 与阶段 0；尚未注册 |
| list_instances / listInstances | Q/connection（已实现） | ConnectionRequest | InstanceListResponse，具体边界见 §7.1.5 |
| preview_instance_create / previewInstanceCreate | P/HostTarget（只读已实现） | CreateInput、expectedRecordVersion、expectedRegistryRevision | 创建建议预览；尚无执行计划，allowed=false，端口不预留 |
| create_instance / createInstance | T/HostTarget（已注册） | 容器字段、预览端口、planId、operationId、expectedRegistryRevision；不接收 worldSettings 或 Token | TaskSnapshot；完成容器创建和 runtime 验证后结束，世界配置由后续 world command 处理 |
| resume_instance_create / resumeInstanceCreate | T/InstanceTarget | planId、expectedRevision、operationId | 原实例安全阶段继续；不分配第二个 ID |
| cleanup_failed_instance / cleanupFailedInstance | T/InstanceTarget | planId、expectedRevision、operationId | 进入受控删除清理；有世界数据时适用备份要求 |
| preview_instance_recovery / previewInstanceRecovery | P/InstanceTarget | action(resumeCreate/cleanupCreate)、expectedRevision | 检查原创建 journal，生成绑定同一实例的恢复预览 |
| get_instance_overview / getInstanceOverview | Q/InstanceTarget（已实现） | expectedRecordVersion | 身份概览响应，当前运行证据未采集；见 §7.1.5 |
| start_instance / startInstance | T/InstanceTarget（已实现） | expectedRecordVersion、expectedRevision、operationId | 校验配置已应用与容器归属，启动分片并等待 DST 进程及容器内 UDP 监听；不代表公网可达 |
| get_instance_runtime / getInstanceRuntime | Q/InstanceTarget | 无 | InstanceRuntime |
| set_instance_running / setInstanceRunning | T/InstanceTarget | desiredRunning、expectedRevision、operationId | 启动或停止；已达期望仍校验身份，返回可验证结果 |
| restart_instance / restartInstance | T/InstanceTarget | expectedRevision、operationId、停服确认 | stop/start，不隐式更新 |
| set_instance_autostart / setInstanceAutostart | T/InstanceTarget | enabled、expectedRevision、operationId | 保存策略、更新容器策略并验证 |
| rename_instance / renameInstance | M/InstanceTarget | displayName、expectedRevision、operationId | revision 增加；ID/目录/项目不改 |
| preview_instance_delete / previewInstanceDelete | P/InstanceTarget | expectedRecordVersion、expectedRegistryRevision | 核对容器/网络归属与状态、待执行任务、世界数据和归档目标，返回绑定快照的 planId |
| delete_instance / deleteInstance | T/InstanceTarget | planId、typedName、expectedRecordVersion、expectedRegistryRevision、operationId | 二次核对后移除已停止且身份匹配的容器/网络；有世界文件时阻断并要求保护备份；其余实例目录归档到 retired，再释放端口 |

CreateInput：displayName、autostart、startAfterCreate、portPolicy(auto)、cavesEnabled。`cavesEnabled` 是容器拓扑字段，决定 Master/Caves 服务和完整端口计划；gameMode、maxPlayers、pvp、description 与 Token 属于容器环境完成后的世界配置，不进入创建容器预览 DTO。首期固定公网路线，创建请求不接收 networkMode。世界配置通过 `save_world_config`/`apply_world_config` 处理，人数范围以阶段 0 确认的 DST 支持范围固化。

2026-10-21 启动修复：运行工作目录为 `/srv/dst/bin64`，存档/MOD 挂载目录根由 UID/GID 1000 持有；不递归改动已有存档文件。首次启动旧版受管 Compose 时，仅允许与确定性旧模板完全相符的工作目录修复，原文件保留为 `compose.before-runtime-fix.yaml`；自定义差异返回 `INSTANCE_START_COMPOSE_DRIFT`。容器退出或反复重启立即返回 `INSTANCE_START_CONTAINER_EXITED`，仅暴露分片、退出码、重启次数和 OOM 布尔值，不回传容器日志或 `.State.Error`。运行镜像新增 `ca-certificates`，旧镜像不会由启动操作隐式升级。

运行就绪核验以容器内 `/proc/*/exe` 匹配真实 DST 可执行文件，不依赖宿主 `docker top`。Master/Caves 各要求配置中的游戏和 Steam 查询 UDP 端口；启用洞穴时 Master 还要求 `cluster.ini` 的 `SHARD.master_port`，所以 listeners 为 Master 3 项、Caves 2 项，单 Master 为 2 项。历史认证端口预留及发布保持不变，不把未监听固定认证端口单独视为本机启动失败。Release 隔离运行验证采用相同的进程/必需端口判定。超时只回传每分片进程状态、已观察端口和缺少端口；探测不可用时保持未知，不能当作“没有进程/没有端口”。该证据不代表公网可连接、Klei 认证或洞穴迁移已经验证。

Docker 控制器经 SSH 返回单层包络：成功为 `{ok:true,data:...}`，失败为 `{ok:false,code:...}`。Rust 的执行入口只解包一次并直接反序列化为对应 DTO；启动和状态查询不得再次查找 `ok/data`。仅状态查询允许 `data:null`，表示当前无安装任务，转换为前端 `task:null`。缺少 `data` 与显式 `null` 必须区分；预检、启动任务的 `data` 必须是有效对象。预检 `aptRunning`、`dpkgClean` 为必需布尔字段，Python 输出、Rust DTO 和前端校验保持一致。

Docker 仓库只读检查一次请求产生一次快照，不在弹窗静置时自动重试。失败使用固定阻断码 `DOCKER_REPOSITORY_TIMEOUT`、`DOCKER_REPOSITORY_DNS_FAILED`、`DOCKER_REPOSITORY_TLS_CERT_INVALID`、`DOCKER_REPOSITORY_TLS_FAILED`、`DOCKER_REPOSITORY_CONNECTION_FAILED`、`DOCKER_REPOSITORY_RESPONSE_INVALID`；HTTP 响应使用 `DOCKER_REPOSITORY_HTTP_<三位状态码>`，只接受 100–599。旧 `DOCKER_REPOSITORY_UNREACHABLE` 保留兼容。错误码通过 Rust 白名单/格式校验后由前端映射为短提示，不传输远端错误正文、响应头或异常文本。`repositoryReachable` 继续为布尔值，失败保持安装阻断。

Docker 安装确认提交期间显示“提交中…”并禁用重复提交；独立状态查询不能覆盖正在进行的接纳请求。接纳失败后重查任务时保留原错误，不因空任务结果清除失败原因。首次发布的任务 `sequence=1`，后续更新单调增加；前端继续拒绝 `sequence=0`。测试入口 `node --test scripts/test-docker-install-ui.mjs` 覆盖重复点击、错误保留、响应丢失后对账和切换主机后的迟到响应。

提交后的对账和已知任务轮询携带本次 operationId（同 taskId），Rust/前端均核对返回 taskId，不能把最近的历史失败记录显示为当前安装失败。查询不存在的任务返回 null；任务目录存在但缺失/损坏状态文件仍报告错误。只有查回同一任务时才清除提交通信错误并展示任务权威状态。主机页面初次加载允许不传 operationId 读取历史最近任务。

安装接纳重新检查时分开返回 `PLAN_EXPIRED`（计划期限无效/已过期）、`INSTALL_HOST_CHANGED`（主机身份变化）、`INSTALL_PREFLIGHT_BLOCKED`（当前条件有阻断）、`INSTALL_SNAPSHOT_CHANGED`（预检快照变化）；旧 `PLAN_STALE` 继续兼容。以上明确拒绝显示“重新检查”，禁止继续确认已失效的预览。重查生成新计划须由用户再次确认。

Docker worker 失败时保留当前 `phase`，不统一改写为 `failed`。阶段为 accepted / rechecking / configuringRepository / updatingPackageIndex / checkingPackageChanges / installingPackages / startingDocker / verifyingRuntime / verified；旧任务的 failed / interrupted 继续兼容。`status=failed` 与执行阶段独立，界面按二者展示失败状态及实际失败步骤。

worker 的签名密钥下载错误使用 `INSTALL_KEY_TIMEOUT`、`INSTALL_KEY_DNS_FAILED`、`INSTALL_KEY_TLS_CERT_INVALID`、`INSTALL_KEY_TLS_FAILED`、`INSTALL_KEY_CONNECTION_FAILED`、`INSTALL_KEY_RESPONSE_INVALID` 和 `INSTALL_KEY_HTTP_<100–599>`；密钥格式异常为 `INSTALL_SIGNING_KEY_INVALID`。文件系统错误使用 `INSTALL_PERMISSION_DENIED`、`INSTALL_DISK_FULL`、`INSTALL_FILESYSTEM_READ_ONLY`、`INSTALL_FILE_MISSING`、`INSTALL_FILE_CONFLICT`、`INSTALL_FILESYSTEM_ERROR`，缺少可执行程序为 `INSTALL_COMMAND_MISSING`，任务数据异常为 `INSTALL_TASK_DATA_INVALID`。仅持久化固定错误码或受限 HTTP 数字，异常原文、URL、代理信息不进入任务状态。旧 `INSTALL_FAILED` 表示未分类的安装脚本异常，不能据此判断控制器启动失败。

### 7.1.1 已落地的本地连接契约（2026-10-16）

本子节只规定元数据接口；ServerInput 不收集密码、私钥和 Token，并拒绝未知字段。SSH 与独立凭据接口见 §7.1.2。

```ts
interface ServerInput {
  displayName: string; host: string; sshPort: number;
  username: string; authKind: "password" | "key";
}
interface SaveServerRequest {
  schemaVersion: 1; requestId: string; operationId: string;
  serverId: string | null; expectedRecordVersion: number | null; input: ServerInput;
}
interface DeleteServerRequest {
  schemaVersion: 1; requestId: string; operationId: string;
  serverId: string; expectedRecordVersion: number;
}
```

新增时 serverId/expectedRecordVersion 均为 null，由 Rust 生成 UUID v4；编辑必须携带保存时的记录版本。返回 ServerSummary 新增必填 recordVersion（新增为 1，每次保存递增）。认证方式只是计划使用的方式，不表示已配置凭据。delete_server 返回 `{serverId}`，只删除本地记录，不执行远端命令。

本地保存/删除使用 operationId 唯一索引和 SQLite 即时事务；规范化输入相同则返回原结果，不同返回 OPERATION_ID_REUSED。操作与记录同事务提交，启动清理超过 90 天的幂等记录；过了该窗口必须先刷新核实，不能无条件重放。普通响应不暴露该内部索引。

recordVersion 是本地连接版本，区别于远端 configRevision；冲突返回 REVISION_CONFLICT/currentRevision。不存在返回 SERVER_NOT_FOUND；数据库失败返回 LOCAL_STORAGE_FAILED。只改名保留 hostId；地址、端口、用户名、认证方式变化清除 hostId。当前凭据引用的事务与清理见 §7.1.2，不因删除元数据而丢失待清理项。

当前 serverId/requestId/operationId 校验规范小写 UUID v4；名称 1–64 Unicode 字符；地址支持 IP（IPv6 不带方括号）或 ASCII DNS，不接受 URL/端口/路径；端口 1–65535；用户名 1–64 ASCII 字母、数字、`_-.@` 且不以 `-` 开头。后端负责完整校验，前端只做输入反馈。

### 7.1.2 已落地的 SSH 连接契约（2026-10-16）

以下命令均已实现。涉及连接的请求使用 ConnectionRequest；schemaVersion、requestId、serverId 与 expectedRecordVersion 必填。响应回显 schemaVersion/requestId/serverId/recordVersion，前端按上下文代次、最新序号及请求 ID 提交成功、错误和 finally。

```ts
interface ConnectionRequest {
  schemaVersion: 1; requestId: string; serverId: string; expectedRecordVersion: number;
}
type CredentialInput = { kind: "password"; password: string }
  | { kind: "privateKey"; privateKey: string; passphrase: string | null };
```

| command | 额外参数 | 结果与约束 |
| --- | --- | --- |
| get_ssh_connection_state | 无 | 指纹/公钥类型、credentialConfigured、cleanupPending；不含秘密或凭据引用 |
| scan_server_fingerprint | 无 | challengeId、algorithm、fingerprint、previousFingerprint、expiresAtUnixSeconds；只握手，不隐式信任 |
| confirm_server_fingerprint | challengeId、fingerprint | 第二次握手与公钥比较，挑战五分钟有效、单次使用；信任提交使 recordVersion +1 |
| save_server_credentials | operationId、credential | 本地保存无需先确认远端信任；凭据类型匹配认证方式，保存使 recordVersion +1；原输入重试关联原结果。登录时必须确认信任 |
| connect_server | 无 | 密钥验证后认证，以 SFTP 只读检查固定 host.json；返回 hostId/null、managementState(ready/uninitialized)、verifiedAtUnixSeconds |
| get_credential_cleanup_status | 无请求参数 | pendingCount；failedCount 为本次操作失败数，纯状态查询为 0 |
| cleanup_credentials | 无请求参数 | 清理未被引用的旧条目，失败保留队列与稳定错误码；返回 pendingCount/failedCount |

本增量带 UnixSeconds 后缀的字段是 UTC Unix 秒，前端转本地显示；不把秒当毫秒，也不与其余 RFC 3339 字段混用。登录验证结束即关闭 SSH 会话，不返回持续 connected 状态；当前不注册无实际会话可断开的 disconnect_server。

秘密请求类型不实现 Debug，不经过通用请求日志。系统凭据库服务名为 app.hungerdesolate.hub.ssh；条目使用随机 UUID 引用，SQLite 不存明文或密码摘要。替换先登记待清理候选、再写新条目、CAS 关联、排队旧条目；候选写入后崩溃仍可发现。元数据编辑/删除、凭据修改与清理通过同一文件锁协调，不在内存 Mutex 中执行 I/O。keyring 失败无文件/数据库秘密存储 fallback。

凭据 operationId 收据在本地保留 90 天，重试比较系统凭据库中的原内容；更换输入拒绝 OPERATION_ID_REUSED，原结果已被后续操作取代则返回 OPERATION_SUPERSEDED。不要在前端持久化秘密来实现重试；同一编辑会话只复用 operationId，关闭/切换清空输入。

只改连接名保留信任；地址、端口、用户名或认证方式变化清除信任、绑定，排队旧凭据清理。重复确认相同公钥保留绑定和凭据；确认不同公钥会清除绑定并要求重新配置凭据。确认提交与认证前均重读记录版本。

host.json 必须是普通文件且不超过 64 KiB，schemaVersion=1、hostId 为 UUID v4。仅 SFTP 文件不存在返回 uninitialized；权限/格式/版本错误明确拒绝。已绑定 hostId 不允许换成另一 ID 或把丢失文件当作原身份。整个流程不执行远端写入或任意 shell。

新增稳定错误：SSH_CHALLENGE_STALE、SSH_DNS_FAILED、SSH_HANDSHAKE_FAILED、SSH_AUTH_FAILED、SSH_SFTP_FAILED、SECRET_REQUIRED、SECRET_READ_FAILED、SECRET_WRITE_FAILED、SECRET_DELETE_FAILED、HOST_IDENTITY_INVALID、HOST_IDENTITY_READ_FAILED、CONNECTION_BUSY、OPERATION_SUPERSEDED。底层网络/凭据库错误文本不直接回传，避免泄露秘密。

### 7.1.3 主机环境检查契约（2026-10-17）

`get_host_environment` 使用 ConnectionRequest，返回 `HostEnvironment`。只读探测需要已确认 SSH 公钥和可用凭据；未初始化 hostId 允许返回 null，未确认身份/版本不允许结果提交。

```ts
interface HostEnvironment {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number;
  hostId: string | null; environmentCheckId: string; checkedAtUnixSeconds: number;
  os: string | null; distributionId: string | null; distributionVersion: string | null; distributionCodename: string | null;
  kernel: string | null; architecture: string | null; uid: number | null;
  disk: { path: string; totalBytes: number; availableBytes: number } | null;
  memory: { totalBytes: number; availableBytes: number | null } | null;
  docker: {
    clientVersion: string | null; serverVersion: string | null; composeVersion: string | null;
    engineState: "notInstalled" | "permissionDenied" | "unavailable" | "available";
    dataRoot: string | null; disk: DiskSnapshot | null;
  };
  checks: Array<{ code: string; level: "passed" | "warning" | "blocker"; label: string; message: string; recoverySteps: string[] }>; deploymentVerified: boolean;
}
```

探测脚本输出不是用户输入；Rust 仍按固定字段/状态/长度/编码解析。`os/architecture/kernel` 等缺失保持 null；Docker server 不能从 client 版本推断可用；Docker socket 权限错误单独返回；Compose 主版本低于 2 为 blocker；`deploymentVerified=false` 直到阶段 0 通过。检查结果是快照，不是持续 Docker/SSH 会话。

### 7.1.4 添加表单凭据与私钥文件（2026-10-17）

`save_server_credentials.credential` 增加 `{kind: "privateKeyFile", path: string, passphrase: string | null}`。请求来源类型只允许反序列化；后端将文件解析成原有 privateKey 内容后才写系统凭据库。旧 password/privateKey 请求继续支持，不需要数据库迁移。

path 为本机绝对路径或 `~/` 路径，支持 Windows 绝对路径；文件必须为非空普通文件、不超过 64 KiB、UTF-8 PEM/OpenSSH 私钥。目录、损坏格式、公钥、超大文件或不可读路径返回 PRIVATE_KEY_PATH_INVALID / PRIVATE_KEY_READ_FAILED / PRIVATE_KEY_FILE_INVALID / PRIVATE_KEY_FORMAT_INVALID，details.field=privateKeyPath。错误不带文件内容或原始路径。

新增 `pick_private_key_file` command（无参数），调用 Tauri 官方 Dialog 插件的本机单文件选择器，返回路径字符串或取消时 null，不读取内容。文件读取和凭据库操作仍通过阻塞线程执行，不提供通用前端文件读取 API。

添加主机时使用独立的 save_server 元数据请求、save_server_credentials 凭据请求：密码不进入元数据、operation payload 或草稿。元数据成功/凭据失败时显式保留原记录，重试沿用原 serverId 和幂等 ID；记录输入被冻结，凭据仍可修正。用户关闭后须从已创建记录继续，不能自动再建同名记录。文件内容变化等输入变化可能需要重新发起凭据保存，不以相同 operationId 静默覆盖。

首次信任确认（之前没有已确认公钥）保留本地预存凭据；已信任公钥改变并经用户核验确认后更新信任、清除旧 hostId 绑定并保留系统凭据库中的密码/私钥，后续连接会使用新信任重新认证并读取主机身份。保存凭据不代表已确认主机，connect_server 仍在任何认证之前验证信任与版本。未确认的新指纹绝不发送凭据。

### 7.1.5 注册表与专服发现（2026-10-17 已实现）

`list_instances` 当前采用 ConnectionRequest（schemaVersion/requestId/serverId/expectedRecordVersion），通过本地记录与受信任 SSH 主机解析远端 hostId。首次发现可绑定原来为空的 hostId；已有绑定改变则拒绝。响应为：

```ts
interface InstanceListResponse {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number;
  hostId: string; registryRevision: number; capturedAtUnixSeconds: number;
  instances: InstanceSummary[];
}
```

本增量一次读取最多 200 个登记项；不是分页协议，超过上限明确返回 REGISTRY_READ_LIMIT，不截断为成功。registry 上限 1 MiB，manifest 上限 64 KiB，总读取预算 4 MiB；读取过程设置 25 秒预算，单次 SFTP 操作仍受 SSH 超时限制。未来扩展分页需要更新两端契约与远端写入实现。

registry v1 必填 schemaVersion、hostId、registryRevision、instances[]。entry 必填 instanceId、lifecycle；可选 relativePath、displayName、pendingTaskId。relativePath（如果存在）必须等于 `instances/<instanceId>`，retired 时为 `retired/<instanceId>`。ID 为规范 UUID v4，revision 为 JS 安全整数；重复实例拒绝。其他业务元数据（如 portReservations/heavyQueue）在此只读流程中不执行、不回传。创建接纳可追加有界 `operations[]` 回执项供远端幂等查询；该字段不进入 `InstanceListResponse`，且 payloadDigest 不含秘密。

`operations[].payloadDigest` 新写入统一为 `sha256:<64 位小写十六进制>`。兼容历史删除操作写入的裸 64 位小写十六进制摘要；校验和幂等比较允许该历史格式，不因此清空或改写既有回执。创建和删除继续拒绝其他摘要格式，复用 operationId 时仍核对完整输入摘要。

manifest v1 必填 schemaVersion、hostId、instanceId、displayName、configRevision、composeProject、cavesEnabled、autostart；容器创建阶段只写入实例/拓扑/端口摘要，不写 worldSettings 或 Token。容器环境验证完成后，世界配置通过独立领域配置流程写入 desired/deployed 摘要，其中 maxPlayers 为 1–128 的字段接收范围（不代表已经验证 DST 支持所有人数）。composeProject 必须为 `fh_<去连字符的instanceId>`。列表只解析身份与非敏感摘要字段，其余配置不原样返回。

InstanceSummary 新增 manifestAvailable。maxPlayers 允许 null，未知不能用 0 代替。retired 不进当前列表；creating/createFailed 且无 manifest 时保留登记名称（无名称则显示 ID），manifestAvailable=false。ready 等记录缺 manifest 明确拒绝，不能悄悄漏掉。runtimeStatus=unknown、players=null、installedBuild=null、lastSyncedAt=null；不提升 registry/runtime.json 内的旧声明为真实运行证据。pendingTaskId 仅作为登记引用，不等价任务仍在执行。

读取后复查 registry 字节与 host.json，变化则 REGISTRY_CHANGED/HOST_IDENTITY_MISMATCH。拒绝管理根或子路径为符号链接、路径穿越、非普通文件、超大文件、格式损坏；不存在与权限/读取错误分别处理。文件目录仍需受控权限，此快照校验不构成远端文件系统事务。

`get_instance_overview` 请求为 `{schemaVersion,requestId,target:InstanceTarget,expectedRecordVersion}`，重新发现并验证实例归属，返回 schemaVersion/requestId/target/recordVersion、snapshotId、capturedAtUnixSeconds、configRevision、source/freshness 和 data。data 含 identity、manifestSummary（配置版本/项目名/洞穴/开机策略）、verificationLevel=unverified、desiredVsDeployed=unknown。尚未采集运行证据，不承诺分片运行。未完成创建的详情返回 MANIFEST_NOT_FOUND，归档/不存在返回 INSTANCE_NOT_FOUND。

前端缓存按 hostId 归属，并记录提供数据的 serverId/recordVersion；接收前验证请求/目标/版本/唯一 ID。比已确认 registryRevision 更旧的响应不提交。选择授权绑定当时连接版本，刷新失败/切换/离开详情会失效，过期列表不可继续进入；深链接不能跳过列表选择。真实领域尚未实现时展示空状态，不复用演示世界/MOD/备份数组。

### 7.1.6 创建预览（2026-10-18）

请求 `{schemaVersion,requestId,target:{serverId,hostId},expectedRecordVersion,expectedRegistryRevision,input}`。input 仅有 displayName、autostart、startAfterCreate、portPolicy=auto 和 cavesEnabled；未知字段（包括 worldSettings、networkMode 和 token）与密码或路径拒绝接收。当前固定公网路线，预览阶段不收集或存储世界配置和秘密。

响应绑定 requestId/HostTarget/recordVersion/registryRevision，含 previewId、capturedAtUnixSeconds、expiresAtUnixSeconds（10 分钟）、规范化 input、proposedInstanceId、composeProject、storagePath、ports、blockers、availableBytes、dockerVersion/null、composeVersion/null、allowed=false、portReservation=unreserved。UUID/目录/项目仅为本次建议，不持久化，不是执行授权或端口预留；未来创建任务须重新预览并在远端锁内分配不可变身份及端口，不复用本增量 previewId 执行。

只读远端脚本通过受信任 SSH stdin 执行，不修改远端文件或 Docker。文件拒绝符号链接、不安全权限、异常类型、超大文件与身份不符；前后重读 registry/host/manifest 排除读期间变化。实例上限 200，单 manifest 64 KiB、registry 1 MiB、manifest 汇总预算 4 MiB。固定读取本机 rootful Docker socket，查询仅投影版本和 HostConfig.PortBindings，避免读取/输出容器秘密配置；Docker 命令结果最多 1 MiB、容器数量上限 400。

端口检查保守合并全部 IPv4/IPv6 UDP/TCP 本地占用、已有 registry 预留及活动实例 manifest 端口（停止实例仍保留），加上 Docker 配置中的发布端口；首期固定公网路线、IPv4 通配、同号 UDP，Master 和可选 Caves 各 game/steamAuth/steamMaster 三项。自动规划范围为 game 11000–19999、steamAuth 8766–9765、steamMaster 27016–28015。LAN 发现仍是阶段 0 的独立验证项，不进入当前创建请求；真实 DST 端口含义和联网仍须阶段 0 验证，不从预览推断可连接。任一必要端口证据缺失/格式错误均禁止乐观分配。

预览不再固定添加游戏镜像阻断；它只报告注册表、端口、Docker/Compose 和管理目录条件。固定阻断项为 `PORT_RESERVATIONS_MISSING`、`EXISTING_PORT_PLAN_INCOMPLETE`、`PORT_EVIDENCE_UNAVAILABLE`、`DOCKER_UNAVAILABLE`、`DOCKER_PLATFORM_UNSUPPORTED`、`COMPOSE_UNAVAILABLE`、`MANAGEMENT_ROOT_UNWRITABLE`、`PORT_PLAN_EXHAUSTED`。release 缺失、Compose config、runtime 和容器验证属于 `create_instance` 持久任务阶段，分别返回任务错误并保留现场。

前端编辑参数、切换主机/连接版本、注册表变化和关窗使在途 success/error/finally 失效；响应必须匹配本次输入、完整目标与版本，并验证同号/用途唯一/范围，不信任远端返回的任意路径或文案。

### 7.1.7 创建任务完整契约（2026-10-19）

创建请求拒绝时保留受限远端错误码，Release catalog 变化明确提示刷新并重新预览。已持久化任务保存自身的 worker/Docker 脚本快照；构建新镜像或更新桌面应用不会改写旧任务的脚本和失败记录。容器创建命令为 `docker compose ... create`，不传 `--no-start`。创建窗口收到失败任务后直接展示阶段、错误码、任务编号及受限 Docker/Compose 诊断。

#### 创建请求 DTO

```ts
interface CreateInstanceRequest {
  schemaVersion: 1;
  requestId: string;
  target: HostTarget; // { serverId, hostId }
  expectedRecordVersion: number;
  expectedRegistryRevision: number;
  operationId: string;
  input: {
    displayName: string; // 1–64 字符
    autostart: boolean;
    startAfterCreate: boolean;
    portPolicy: "auto"; // 首期固定
    cavesEnabled: boolean;
  };
  // 预览端口计划：仅用于本次提交绑定，不作为远端分配授权
  proposedPorts: Array<{
    shard: "master" | "caves";
    purpose: "game" | "steamAuth" | "steamMaster";
    protocol: "udp";
    port: number;
  }>;
  planId: string | null; // 当前不使用 plan 机制，保留字段
}
```

#### 创建任务接受响应

```ts
interface CreateInstanceAccepted {
  schemaVersion: 1;
  taskId: string;
  operationId: string;
  target: InstanceTarget; // 完整 { serverId, hostId, instanceId }
  status: "queued" | "running";
  sequence: number; // 初始为 1
  reused: boolean; // 同 operationId 重放时 true
  createdAtUnixSeconds: number;
}
```

#### 创建任务状态

```ts
interface InstanceCreationStatus {
  schemaVersion: 1;
  taskId: string;
  operationId: string;
  target: InstanceTarget;
  operation: "instanceCreate";
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  stage: "queued" | "preflight" | "reserve" | "prepareDirectories" 
    | "download" | "stagingVerify" | "configure" | "publish" 
    | "start" | "verify" | "finalize";
  lastCompletedStage: string | null;
  progress: { kind: "indeterminate" } 
    | { kind: "determinate"; completed: number; total: number };
  sequence: number;
  createdAtUnixSeconds: number;
  startedAtUnixSeconds: number | null;
  finishedAtUnixSeconds: number | null;
  observation: "live" | "stale" | "reconciling";
  cancelAllowed: boolean;
  result: InstanceCreationResult | null; // succeeded 时必填
  error: CommandError | null; // failed 时必填
}

interface InstanceCreationResult {
  verifiedRevision: number | null;
  verification: "passed" | "notVerified";
  remoteChanged: boolean;
  deploymentId: string | null;
  invalidate: Invalidation[];
  recoveryActions: Array<{
    action: "resumeCreate" | "cleanupCreate" | "inspectLogs";
    label: string;
    requiresPreview: boolean;
  }>;
}
```

#### 创建任务固定错误码

| 错误码 | 含义 | 恢复动作 |
|-------|------|---------|
| `CREATE_RELEASE_REQUIRED` | 缺少合格 DST release | 提示阶段 0 验证未完成 |
| `CREATE_RELEASE_CATALOG_MISSING` | catalog.json 不存在 | 同上 |
| `CREATE_RELEASE_NOT_VERIFIED` | release 未通过 verified 标记 | 同上 |
| `CREATE_RELEASE_NOT_RUNTIME_VERIFIED` | release 未通过 runtime 验证 | 提供"验证 release"入口 |
| `CREATE_COMPOSE_CONFIG_FAILED` | docker compose config 校验失败 | 查看日志、清理现场 |
| `CREATE_DOCKER_RUNTIME_REQUIRED` | 旧任务的 Docker runtime 前置门禁错误 | 新任务以 `verified=true` 基础验证放行；此码仅用于识别旧任务 |
| `CREATE_NETWORK_FAILED` | Docker network 创建失败 | 查看日志、重试或清理 |
| `CREATE_CONTAINER_FAILED` | docker compose create 失败 | 查看日志、重试或清理 |
| `CREATE_CONTAINER_VERIFICATION_FAILED` | 容器 labels/挂载/端口验证失败 | 查看日志、清理现场 |
| `CREATE_DIRECTORY_CONFLICT` | 实例目录已存在 | 清理现场或恢复 |
| `CREATE_MANIFEST_CONFLICT` | manifest 已存在且内容不符 | 清理现场 |
| `CREATE_PORT_CONFLICT` | 端口在执行时被占用 | 重新预览、清理现场 |

无容器专服提供专用 `cleanup_orphan_instance` 确认命令：远端 registry lock 内重查任务、受管容器和网络后才允许移出活动列表并释放端口；空目录会移除，目录内有文件时整体移入 `retired/<instanceId>` 保留。它不执行备份，也不替代正常运行实例的 `preview_instance_delete/delete_instance`。

`delete_instance` 只按 hostId/instanceId/schema 标签处理资源；不执行宽泛的 `compose down`、`prune` 或删镜像。预览计划绑定注册版本、容器状态/ID、网络端点和实例目录快照；执行时任一变化都要求重新预览。运行中的容器、活动或待核实任务、资源标签不匹配、归档冲突均阻断。世界数据目录存在文件时须先有保护备份；不含世界数据时移除容器与网络并将实例目录整体归档到 retired，最后才释放端口。operationId 重放返回原删除结果。

创建任务的 `journal` 只包含持久化阶段变更，不代表容器 stdout/stderr。Compose/Docker 校验诊断按错误详情单独返回并限长；不读取或保存完整游戏日志。

Release 删除通过 `preview_instance_release_delete/delete_instance_release` 执行。删除重查 catalog digest、活动实例登记及 manifest/active-release、未结束或待核实任务、容器引用及共享镜像。retired 登记只保留历史，不阻断 Release 删除；归档文件不修改，确认时说明恢复可能需重新导入原镜像。活动实例、未结束任务引用阻断删除；独占镜像被任意容器（含停止容器）引用时阻断。共用 digest 仅删除当前 Release 登记并保留镜像，其他 Release 的容器引用不阻断该登记删除。预览列出具体引用 ID，用户须输入 DST build 确认；执行时在锁内重查，镜像只按固定 digest 删除。
| `CREATE_REGISTRY_CHANGED` | 注册表在执行期间变化 | 重新预览 |
| `CREATE_ADMISSION_FAILED` | 接纳阶段失败（注册锁内检查） | 查看错误详情、重新预览 |

#### 查询创建任务状态

```ts
// 前端调用
interface GetInstanceCreationStatusRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  operationId: string | null; // 指定时只返回该任务，否则返回最近任务
}

// 响应
type GetInstanceCreationStatusResponse = {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  recordVersion: number;
  task: InstanceCreationStatus | null; // null 表示无对应任务
}
```

#### 前端集成要求（✅ 已完成）

1. **提交创建**：`InstanceCreateDialog` 预览成功后，点击"创建容器"调用 `createInstance`，显示提交中状态 ✅
2. **轮询状态**：接受后按 `taskId` 或 `operationId` 轮询，间隔 1.5 秒，直到 succeeded/failed/interrupted ✅
3. **展示进度**：根据 `stage` 显示当前阶段，`progress` 为 indeterminate 时使用不确定进度条 ✅
4. **错误处理**：`failed` 时展示 `error.message`，提供 `recoveryActions` 按钮 ✅
5. **成功完成**：`succeeded` 且 `verification=passed` 时关闭对话框，刷新实例列表 ✅
6. **断线对账**：轮询失败时保留 `stale` 状态，不自动重试，提供"重新查询"按钮 ⚠️（保留 stale 已实现，重新查询按钮待补充）

**当前实现状态**：核心路径完成，恢复动作按钮已添加（具体实现待后续工作包），列表刷新已接入。

### 7.2 世界、MOD、更新


| command / wrapper | 类型 | 业务输入 | 返回与要求 |
| --- | --- | --- | --- |
| get_world_config / getWorldConfig | Q | InstanceTarget | WorldConfig |
| save_world_config / saveWorldConfig | M | target、settings、tokenChange、serverPasswordChange、expectedRevision、operationId | MutationResult<WorldConfig>，只保存期望，不宣称已部署 |
| preview_world_apply / previewWorldApply | P/InstanceTarget | expectedRecordVersion、expectedRevision | 差异、新世界生成项、现有存档文件数和保护备份要求；绑定读到的期望配置摘要 |
| apply_world_config / applyWorldConfig | T | target、expectedRevision、desiredDigest、activeReleaseId、operationId | 发布已保存配置并按原运行状态重建分片；不读取前端隐式草稿；第一版仅对无现存世界文件开放，返回 `runtimeVerified=false`，隔离运行验证不作门槛 |
| get_workshop_metadata / getWorkshopMetadata | Q/global | workshopIds[] | 元数据；上限 100，不需要实例，不含启用状态 |
| search_workshop / searchWorkshop | Q/global | query、cursor、limit | 元数据搜索，外部来源能力待验证，失败允许手工输入 ID |
| list_instance_mods / listInstanceMods | Q | target | InstanceMods |
| save_instance_mods / saveInstanceMods | M | target、desiredMods[]、expectedRevision、operationId；编辑配置时附带 schemaBindings[] | CAS 保存实例期望配置；编辑字段绑定固定 contentDigest 与 modinfoSha256，后端复读当前 schema 并校验值与变更字段 |
| check_instance_mod_updates / checkInstanceModUpdates | Q | target | 只检查；有目标和请求顺序，失败保留最后检查时间 |
| preview_mods_apply / previewModsApply | P | target、expectedRevision | 实际差异、下载、停服、保护备份 |
| apply_instance_mods / applyInstanceMods | T | target、planId、expectedRevision、operationId | staging → 部署 → 运行验证 |
| get_update_status / getUpdateStatus | Q | target | UpdateStatus，已缓存的检查结果 |
| check_instance_updates / checkInstanceUpdates | Q | target | 采集可用 DST/MOD 版本；不改变活动产物 |
| preview_instance_update / previewInstanceUpdate | P | target、scope(dst/mods/both)、expectedRevision | 固定目标 build/Workshop 摘要与回滚能力 |
| run_instance_update / runInstanceUpdate | T | target、planId、expectedRevision、operationId | 保护备份后更新预览指定的目标产物 |

当前已实现的 `get_workshop_metadata` 在桌面端通过 HTTPS 查询 Steam Workshop，最多 100 个显式 Workshop ID；结果只包含有界元数据，不包含秘密或实例启用状态。`list_instance_mods` 只读响应包含 `snapshotId/capturedAt/source/freshness/configRevision/items`。每项绑定 `workshopId`，返回期望/已部署启用、期望/已部署配置、期望/已部署产物摘要、准备摘要/时间和 `schema`。schema 状态区分 `available`、`partial`、`noOptions`、`unavailable`、`failed`；求值失败或产物未准备时不得返回空配置并标记成功。`save_instance_mods` 使用全局 `expectedRevision` CAS、`operationId` 重放和实例维护锁。配置编辑请求的 `schemaBindings[]` 结构为 `{ workshopId, artifactDigest, modinfoSha256, changedFields[] }`；Rust 在 revision 未变化时重新读取实例快照，复求当前 schema，校验绑定摘要、可编辑字段、枚举候选值与基础类型，并确认未声明字段保持原值。游戏侧以 `name` 持久化配置，同名项会作为共享字段编辑；同名定义的共同候选集/类型由前后端共同校验，无法安全合并的组只读而不阻断其他字段。revision 已变化时由远端操作回执优先支持同一 operationId 幂等重放，新的过期操作仍由 CAS 拒绝。编辑保存会固定当前已准备 contentDigest，成功仅推进实例期望状态与全局 revision，不代表内容已部署或正在运行。配置表单只提交实际修改的字段；复杂/不支持字段与未知旧值保留不变。应用预览由 MOD、运行概览和备份三份绑定 target/revision 的只读响应组成，列出 MOD 变更/分片/冷保护点及阻断原因；不创建应用 plan，不调用发布 command。恢复任务只有在运行中实例恢复后重新启动并通过核验时才写入主机级恢复验收证据；未有该证据时预览继续阻断发布。MOD 更新检查使用已准备时间与 Workshop `timeUpdated` 做只读候选比较；时间仅是提示，不证明实际内容变化或内容版本，缺任一时间则返回未知，不下载也不修改 desired。Workshop 元数据查询不写入实例配置；用户显式选择“加入列表”后才保存。MOD 领域 revision 与世界领域 revision 各自推进，均不超过 manifest 全局 revision；领域间保存不能覆盖或强迫重写另一领域的 desired/deployed 数据。

MOD 更新检查将 Workshop `contentHandle` 与已准备产物的来源 handle 比较；变化项可由 `prepare_instance_mods` 通过 `sourceHandles` 请求获取，并在 `mods/candidates` 保存候选 digest/来源标识，不改 desired 或部署状态。候选 `modinfo.version` 与 deployed digest 对应版本分别返回；采用候选时才 CAS 更新 desired artifactDigest。source handle 缺失时为 unknown；旧产物首次获取一次作为基线，内容摘要相同后可显示已是最新。

`prepare_instance_mods` 接受完整目标、全局 `expectedRevision`、`operationId`、已启用 Workshop ID 和可选的 sourceHandles，返回持久任务。任务固定一个本地已验证、具备 `steamcmd-workshop-v1` 能力的工具镜像 digest，并单独核对/记录目标 DST Release；无符合条件的本地镜像时返回 `MOD_FETCH_CAPABILITY_UNAVAILABLE`。`get_instance_mod_task/list_instance_mod_tasks` 按完整 InstanceTarget 查询任务。成功结果包含准备好的 Workshop ID 与 contentDigest，仅表示内容固定在实例 `mods/prepared`；不表示已部署或运行。

### 7.3 备份、日志、诊断、任务

| command / wrapper | 类型 | 业务输入 | 返回与要求 |
| --- | --- | --- | --- |
| list_instance_backups / listInstanceBackups | Q | target、cursor、limit | BackupSummary 分页，包含 retired 实例历史 |
| preview_instance_backup / previewInstanceBackup | P | target、captureMode(cold/quasiHot)、expectedRevision | 一致性语义、停服影响、空间估算；准热备份不满足破坏性操作保护点 |
| create_instance_backup / createInstanceBackup | T | target、captureMode、planId、expectedRevision、operationId | verified 备份才可用于恢复；metadata 保留采集模式与运行状态 |
| download_instance_backup / downloadInstanceBackup | T | target、backupId、operationId、本地授权 destinationHandle | SFTP 下载及本地 checksum；不允许前端随意传远端路径 |
| restore_instance_backup / restoreInstanceBackup | T | target、backupId、expectedRevision、operationId | 用户确认恢复后，命令先同步检查归属、checksum、Release 产物、空间与运行状态；阻断时不创建任务，通过后才接纳恢复 worker；跨实例明确拒绝 |
| delete_instance_backup / deleteInstanceBackup | M | target、expectedRevision、backupId、expectedChecksum | checksum CAS、实例归属和维护锁校验后删除普通备份；按 createdAt/backupId 确定最新恢复前保护点并拒绝删除，较旧保护点允许删除 |
| read_instance_logs / readInstanceLogs | Q | target、shardKind、cursor、limit | LogPage，游标绑定 stream/target |
| get_instance_diagnostics / getInstanceDiagnostics | Q | target | 有限健康摘要、证据、失败任务 ID、恢复入口 |
| list_tasks / listTasks | Q | HostTarget 或 bootstrap ConnectionTaskTarget、instanceId 可选、cursor、limit | 固定目标 TaskSnapshot 分页，不依赖当前详情；合并本地下载/准备任务索引 |
| get_task / getTask | Q | HostTarget 或 bootstrap ConnectionTaskTarget、taskId | 校验远端任务归属，返回 TaskSnapshot |
| get_operation / getOperation | Q | HostTarget 或 bootstrap ConnectionTaskTarget、operationId | 返回 task/shortMutation/notFound 三种结果；恢复接受应答丢失，不重新执行 |
| read_task_logs / readTaskLogs | Q | HostTarget 或 bootstrap ConnectionTaskTarget、taskId、cursor、limit | 有界脱敏日志；本地下载读取本地日志 |
| cancel_task / cancelTask | M | HostTarget 或 bootstrap ConnectionTaskTarget、taskId、expectedSequence | 写取消请求；不是立即 cancelled |
| reconcile_host_tasks / reconcileHostTasks | Q | HostTarget | 对账摘要，必要时远端形成更高 sequence 的修复记录 |

P1 增补命令：get/save_backup_policy、get_health_history、export_diagnostics、discover/preview/run_legacy_migration。采用同一 target、schema、preview、operationId 和错误边界，详见后端 §12；P0 不注册未实现空壳 command。

## 8. 错误契约

```ts
interface CommandError {
  schemaVersion: 1;
  code: string;
  category: "validation" | "trust" | "connection" | "identity"
    | "conflict" | "environment" | "storage" | "task" | "security";
  operation: string;
  requestId: string | null;
  target: HostTarget | InstanceTarget | null;
  message: string; // 简短中文
  retryable: boolean;
  remoteChanged: boolean | null; // null 表示未确认，先对账
  taskId: TaskId | null;
  recoverySteps: string[];
  details: Record<string, string | number | boolean | null>; // 脱敏白名单
}
```

| code | 默认处理 |
| --- | --- |
| INPUT_INVALID / SCHEMA_UNSUPPORTED | 字段反馈或升级提示，不自动重试 |
| SSH_TRUST_REQUIRED / SSH_FINGERPRINT_CHANGED | 打开信任确认，禁止继续业务写入 |
| SSH_UNREACHABLE / SSH_TIMEOUT | 保留相同对象快照；存在 taskId 时先对账 |
| HOST_IDENTITY_MISMATCH / HOST_IDENTITY_AMBIGUOUS / INSTANCE_IDENTITY_MISMATCH | 写入阻断，重新发现与确认；不切默认对象 |
| INSTANCE_NOT_FOUND / INSTANCE_RETIRED | 返回当前主机列表；保留可读取历史入口 |
| REVISION_CONFLICT | 显示 currentRevision，保留草稿，重新读取和比较 |
| INSTANCE_BUSY | 拒绝写入，展示已接受的实际目标任务；主机资源排队用 TaskAccepted.status=queued，不作为错误 |
| OPERATION_ID_REUSED / PLAN_STALE | 前者禁止自动换 ID 重试；后者重新预览 |
| PORT_CONFLICT / LAN_PORT_RANGE_EXHAUSTED | 展示用途/协议/端口，重新分配或调整发现方式 |
| ENVIRONMENT_UNSUPPORTED / PERMISSION_DENIED / IMAGE_UNVERIFIED | 打开环境阻断与恢复说明 |
| DISK_SPACE_INSUFFICIENT / BACKUP_VERIFY_FAILED | 阻断停服或破坏性阶段，保留现场 |
| BACKUP_TARGET_MISMATCH / BACKUP_ARTIFACTS_MISSING / RESTORE_INCOMPATIBLE | 普通恢复拒绝，明确缺项与支持的恢复选择 |
| LOCK_OWNER_MISMATCH / TASK_STATE_UNKNOWN | 不夺锁、不重跑，进入对账 |
| CANCEL_NOT_ALLOWED | 告知原子阶段，继续观察，不制造 cancelled 状态 |
| REMOTE_PATH_INVALID / ARCHIVE_UNSAFE / SECRET_WRITE_FAILED | 阻断变更，记录脱敏安全事件 |
| VERIFY_FAILED / ROLLBACK_FAILED / CLEANUP_FAILED | 保存阶段、remoteChanged 和恢复动作，无假成功 |

`retryable=true` 只表示可以按恢复步骤尝试，不授权 UI 自动重跑写入。错误分类来自产生点的稳定 code，禁止根据英文/中文文本匹配。

## 9. 失效范围与联调场景

| 操作 | 同实例 domains | 主机 domains |
| --- | --- | --- |
| 创建、删除、创建清理 | overview/runtime/world/mods/backups/updates/diagnostics | instances/environment |
| 启动、停止、重启 | overview/runtime/diagnostics | instances |
| 保存世界/名称/开机启动 | world/overview（按实际字段），策略验证后 runtime | instances |
| 保存 MOD 期望 | mods/updates/overview | 无 |
| 应用世界 | world/overview/runtime/backups/diagnostics | instances |
| 应用/更新 MOD | mods/updates/overview/runtime/backups/diagnostics | instances |
| DST 更新、恢复 | overview/runtime/world/mods/backups/updates/diagnostics | instances/environment |
| 创建备份 | backups/overview，停服后 runtime | instances（运行摘要变化时） |
| 下载备份 | 无 | 无 |

失效按 hostId + instanceId 匹配，所有本地连接别名共享脏标记；只有活动页面匹配且可连接时立即重查，其他对象延迟至进入时重查。草稿从不作为失效目标。

联调 fixture 必备：两个主机各三个实例；A/B 同名；空洞穴；未知玩家；stale 快照；revision 冲突；queued 重任务；下载完成但部署失败；A 任务在 B 页面完成；重复 operationId；retired 备份；事件丢失与乱序。完整场景编号见[验收计划](implementation-roadmap.md)。
