# 定时备份（准热）功能设计

- 版本：1.0.5（规划）
- 日期：2026-11-02
- 状态：设计规划；当前只有手动 `backupCreate`，没有策略、远端 scheduler 或保留策略
- 关联：[运维增强总规划](ops-enhancements-plan.md)、[备份与恢复文件边界](../docs/backup-restore-boundaries.md)、[接口契约](api-contract.md)、[后端实施](backend-implementation.md#12-p1策略诊断与旧部署迁移)

## 1. 目标与边界

### 目标

1. 每个实例独立配置定时备份策略：启用、间隔、保留数量/天数、最小剩余空间。
2. 计时发生在远端 Linux 主机，不依赖桌面应用、SSH 会话或用户登录。
3. 定时任务固定使用 `quasiHot`（准热）模式：不停服，逐文件读取当前已落盘数据。
4. 备份进入现有列表、metadata、checksum/verification 和恢复链路，不新建第二套备份格式。
5. 自动保留只清理“定时来源”的旧备份；手动备份和保护点不自动删除。
6. 每次执行、跳过、失败、清理都有真实状态和最近错误；不把“有压缩包”当成功。
7. 主机重启后 timer 恢复；错过的窗口最多补一次，不重复洪水执行。

### 非目标（v1）

- 不做每日 `HH:mm` + 时区计划；v1 只支持固定间隔（分钟/小时）。
- 不做冷备份定时任务；冷备份仍由用户手动创建，或由更新/恢复/MOD 应用创建保护点。
- 不做云备份、异地复制、对象存储、备份加密。
- 不做自动恢复、自动清理手动备份、批量删除。
- 不用桌面端 `setInterval`、系统 cron 或 SSH 常驻循环模拟远端定时。
- 不承诺准热备份跨文件时间点一致，也不把它当作破坏性操作的保护点。
- 不在实例创建/删除/更新/恢复/MOD 应用期间插入定时备份。

## 2. 准热备份语义

现有 `quasiHot` 行为：

- 不要求服务同步，不停止分片；
- 逐文件读取 `data/Master`、`data/Caves`、`config/Cluster_1`、active-release、manifest、Compose、desired/deployed；
- 记录开始/结束时间和文件级摘要；
- metadata：`captureMode=quasiHot`、`captureSemantics=onDiskBestEffort`、`runtimeStatusAtCapture`；
- 文件可能在采集期间变化，因此不宣称跨文件一致；
- 不满足破坏性操作的保护点要求（更新/恢复前必须冷备份）。

定时备份沿用这些语义，不新增“热备份”这种更弱的说法。UI 只显示“准热”并说明不停服。

实例停止时，同一模式读取的是停止状态文件，实际更接近一致；metadata 仍记录 `runtimeStatusAtCapture=stopped`，不改成 `cold`，避免伪造一致性等级。

## 3. 远端调度架构

### 3.1 方案比较

| 方案 | 桌面关闭后执行 | 主机重启后恢复 | 能否运行 controller/停服类任务 | 结论 |
| --- | --- | --- | --- | --- |
| 桌面 `setInterval` | 否 | 否 | 不适用 | 拒绝 |
| SSH 常驻循环 | 否（断线即停） | 否 | 不可靠 | 拒绝 |
| 远端 systemd timer + oneshot | 是 | 是（Persistent） | 是（root，走现有 controller） | 采用 |
| 远端 scheduler 容器（不挂 socket） | 是（restart policy） | 是 | 不能直接执行 Docker/停服，只能写请求 | 不采用；仍需另一个宿主执行器 |

结论：使用一个全局 systemd timer（不是每实例一个 timer）扫描所有实例策略。timer 只负责“到点运行 scheduler 脚本”，具体备份仍走现有 `backupCreate` 任务、controller 和锁。

与既有 [后端实施](backend-implementation.md#12-p1策略诊断与旧部署迁移) §12 的差异：原文写“远端受控 scheduler 容器，不挂 socket”。本设计改为 systemd timer，因为不挂 Docker socket 的容器不能自己执行 controller/停服类任务，仍需另一个宿主执行器；多一个容器并不会减少宿主组件。实现前必须先修订后端实施文档，不能让两份设计同时生效。

### 3.2 进程与文件

```text
宿主 systemd
├─ famine-backup-scheduler.timer        每 5 分钟触发（Persistent=true）
└─ famine-backup-scheduler.service      oneshot，执行 backup-scheduler.py tick
       │
       ├─ 读取 /var/lib/famine-hub/backup-policies/*.json
       ├─ 读取/推进 /var/lib/famine-hub/backup-schedules/*.json
       ├─ 需要执行时调用 backup-task.py start（与桌面命令同一实现）
       └─ 结果写入 schedule state 和 backup-events.jsonl

/var/lib/famine-hub/
├─ backup-policies/<instanceId>.json    # 用户策略，policyRevision CAS
├─ backup-schedules/<instanceId>.json   # 下次/上次/上次错误/错过窗口
├─ backup-events.jsonl                  # 有界事件：执行、跳过、清理、失败
├─ scheduler/
│  ├─ current/
│  │  ├─ backup-scheduler.py
│  │  ├─ backup-task.py
│  │  ├─ backup-archive.py
│  │  └─ version.json
│  ├─ state.json                       # 全局 lastTickAt/版本/上次错误
│  └─ install.lock
├─ backup-tasks/<operationId>/          # 复用现有任务目录
└─ locks/
   ├─ backup-scheduler.lock             # scheduler 单实例
   └─ heavy.lock                        # 与更新/恢复/MOD 共用
```

### 3.3 systemd 单元

```ini
# /etc/systemd/system/famine-backup-scheduler.service
[Unit]
Description=Hunger Desolate scheduled backup tick
After=network-online.target docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /var/lib/famine-hub/scheduler/current/backup-scheduler.py tick
```

```ini
# /etc/systemd/system/famine-backup-scheduler.timer
[Unit]
Description=Hunger Desolate scheduled backup timer

[Timer]
OnCalendar=*:0/5
Persistent=true
AccuracySec=1min
RandomizedDelaySec=20

[Install]
WantedBy=timers.target
```

要求：

- timer 全局一个，策略按实例独立；实例创建/删除不需要创建/删除 timer。
- `Persistent=true` 让主机停机错过的 tick 在启动后补一次；scheduler 自己再做窗口去重。
- 单元文件 root:root 0644，脚本 0700；内容带版本/digest，应用升级后由 `ensure` 原子更新。
- 如果主机没有 systemd，`save_backup_policy(enabled=true)` 返回 `BACKUP_SCHEDULER_UNAVAILABLE`，不写 enabled=true 的策略。
- timer 只执行只读策略扫描和任务接纳；真正备份由 controller/worker 执行，scheduler 不直接打包归档。

### 3.4 安装与校验

新增内部动作 `ensure_backup_scheduler`，只在保存策略或显式修复时调用；`get_backup_policy` 是只读查询，只检查并报告，不安装单元、不写文件：

1. 校验 root、systemd、`/var/lib/famine-hub` 归属。
2. 上传/更新 `scheduler/current` 到 staging，校验脚本摘要和语法，再原子切换。
3. 写单元文件（内容变化时），`systemctl daemon-reload`；单元内容变化后重启 timer，避免旧 OnCalendar 继续生效。
4. `systemctl enable --now famine-backup-scheduler.timer`。
5. 校验 `is-active`、`list-timers` 的下次触发时间、`version.json` digest。
6. 失败返回固定错误，不把策略标记为已启用。

保存策略的顺序必须是：先确保 scheduler 可用，再原子写 `enabled=true`；避免“策略已启用但永远不会跑”。禁用策略不要求 scheduler 可用。

## 4. 策略模型

```ts
interface BackupPolicy {
  enabled: boolean;
  intervalMinutes: number;   // 60..10080
  retention: {
    keepCount: number;       // 1..100
    keepDays: number;        // 1..365
    minFreeBytes: number;    // 0..1 TiB
  };
}
```

默认值（设计值，可后续按实测调整）：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | false | 新建/已有实例默认不自动备份，需用户开启 |
| `intervalMinutes` | 360 | 每 6 小时 |
| `keepCount` | 7 | 最多保留 7 份定时备份 |
| `keepDays` | 14 | 超过 14 天的定时备份可清理 |
| `minFreeBytes` | 2 GiB | 低于此剩余空间先清理，仍不足则跳过 |

规则：

- `keepCount` 和 `keepDays` 任一条超出即进入清理候选；始终保留至少一份 verified 备份。
- `minFreeBytes` 是清理目标，不是“磁盘只剩这么多才失败”；清理后仍低于目标则跳过本次备份。
- 策略按实例独立；同一主机不同实例互不影响。
- 策略 revision 独立于 `configRevision`，保存策略不推进世界/MOD revision。
- 禁用策略保留历史状态和已有备份，只停止后续执行；不自动删除任何备份。

## 5. 执行流程

```text
timer tick（每 5 分钟）
  │
  ├─ 获取 backup-scheduler.lock（非阻塞）；已有 tick 运行则退出
  ├─ 扫描 backup-policies/*.json
  │
  └─ 对每个实例
       ├─ 校验 hostId/instanceId/policyRevision/字段范围；损坏则记录事件并跳过
       ├─ enabled=false → 跳过
       ├─ now < nextRunAt → 跳过
       ├─ 核对上次 pending 任务
       │    ├─ succeeded → 更新 lastSuccessAt/lastBackupId，安排下次
       │    ├─ failed/interrupted → 记录 lastError，安排下次
       │    └─ queued/running → 仍在执行，跳过
       ├─ 检查实例 lifecycle=ready；否则记录跳过
       ├─ 检查本实例活动任务（backup/restore/modApply/update）
       │    └─ 有 queued/running → 记录 BUSY，跳过本次，不排队
       ├─ 非阻塞检查 heavy.lock；被占用 → 记录 BUSY，跳过
       ├─ 估算 archive 大小并检查可用空间
       │    ├─ 不足 → 只做满足空间所需的最小清理（只删 scheduled，保留最后一份 verified）
       │    │         ├─ 清理后足够 → 继续
       │    │         └─ 仍不足 → 记录 SPACE，跳过；不删除最后一份
       │    └─ 足够 → 继续
       ├─ 生成 operationId/backupId/slotKey
       ├─ 写 schedule state（pendingOperationId、lastAttemptAt、slotKey）
       ├─ 调用 backup-task.py start（captureMode=quasiHot, origin=scheduled）
       │    ├─ 成功接纳 → 记录 queued，退出本次
       │    └─ BACKUP_TASK_BUSY/INSTANCE_BUSY → 记录 skip，推进 nextRunAt，退出
       ├─ 不等待备份完成；下一次 tick 对账
       └─ 上次任务成功后，按 keepCount/keepDays 执行常规保留清理
```

设计要点：

- scheduler 每次 tick 只处理一批实例；单实例失败不影响其他实例。
- 定时备份不排队等待重型锁；遇到忙碌直接跳过并记录，下一次窗口再试。这样不会在更新/恢复后突然插入一个旧时间点的备份。heavy 检查与任务接纳之间存在极窄竞态；即使竞态命中，备份也只会等待重型锁，不会停服或改变实例。
- 任何跳过（busy/space/lifecycle）都推进 `nextRunAt` 到下一个间隔，避免每 5 分钟重试造成噪音；用户仍可手动触发备份。
- 空间清理发生在备份前，只做“足够本次写入”的最小删除；`keepCount/keepDays` 的常规清理在备份成功后的对账中执行，避免“先删旧备份、新备份又失败”。无论哪种清理都保留最后一份 verified。
- 不在 scheduler 内停服、打包、删除归档；这些动作复用现有任务和锁定实现。
- 备份任务的 `reason` 使用固定短文案“定时备份”，metadata 增加机器可读 `origin=scheduled` 和 `scheduleSlot`。
- 结果由任务状态给出；scheduler 只负责接纳和状态汇总，不伪造 verified。

## 6. 时间与幂等

### 6.1 时间基准

- 所有状态时间用 UTC ISO（`...Z`）；比较用 Unix 秒。
- 首次保存启用策略时，`nextRunAt = now + interval`；不在保存瞬间立即备份，避免误触。
- 间隔按“上次计划槽位 + interval”推进，不按“上次完成 + interval”，避免备份耗时导致漂移。
- `nextRunAt = max(now, previousSlot + interval)`；如果备份耗时超过一个间隔，下一次从当前时间重新起算，避免连续补跑。
- 主机时钟回拨：`now < previousSlot` 时记录 `CLOCK_SKEW`，不执行，等待时钟恢复；不能因为回拨重复跑。
- 主机时钟前跳：最多执行一次，记录 `missedWindows`（按间隔计算，封顶 1000），不补跑 N 次。

### 6.2 幂等与去重

- 每个到期窗口生成 `slotKey = instanceId + ":" + slotUnixSeconds`；任务 metadata 的 `scheduleSlot` 使用同一时刻的 UTC ISO 字符串。
- 接纳前扫描 `backup-tasks/*/request.json` 和 `status.json`：同一 instanceId + `scheduleSlot` 已存在则不再创建。
- `operationId` 随机 UUID v4；`backupId` 随机 UUID v4。
- `save_backup_policy` 的 `operationId` 用于短写入幂等：同一 operationId + 相同请求摘要返回原策略/状态；摘要不同返回 `BACKUP_POLICY_CONFLICT`，不重复安装 timer。
- 先写 schedule state 的 `pendingOperationId`，再调用 start；如果 start 成功但写状态前崩溃，下次 tick 通过 slotKey 扫描任务去重。
- 如果 start 返回 `OPERATION_ID_REUSED` 且请求摘要一致，按原任务继续；摘要不一致则记录错误。
- 任务被 cancel 后，scheduler 记录 `cancelled`，按失败处理并安排下一次；不在同一窗口重试。

## 7. 保留策略

### 7.1 保护集合

以下备份永不自动删除：

- `origin=manual`（用户在备份页创建）；
- `origin=protection`（更新/恢复/MOD 应用保护点）；
- 任何 `verification != verified` 且仍有活动任务引用的备份；
- 每个实例最新的一份 verified 备份（无论来源）；
- 被活动任务（queued/running）引用的备份 ID。

只有 `origin=scheduled` 且 `verification=verified` 的备份进入自动清理候选。

### 7.2 清理顺序

常规清理（备份成功后）先处理数量/天数；空间清理（备份前）只做满足本次写入的最小删除。两者使用同一保护集合和删除实现。

```text
candidates = scheduled + verified, 按 createdAt 升序（最旧优先）
protected = 最新一份 verified + 活动任务引用 + 非 scheduled

常规清理（成功后）:
for candidate in candidates:
    if candidate in protected: continue
    if scheduledCount > keepCount or age > keepDays:
        delete(candidate)
        scheduledCount -= 1
（删除后重新统计）

空间清理（备份前，仅在 freeBytes < need 时）:
while freeBytes < need:
    if 还有可删的 scheduled（且不在 protected）:
        delete(最旧)
    else:
        break
```

`need = 估算归档大小 + minFreeBytes`；估算不可得时使用现有备份预览的保守估算，不猜测为 0。

- 删除复用 `instance-backups.py` 已有的 metadata/归属/checksum 校验和原子移入删除现场实现；scheduler 不经过 Rust command，也不复制第二套删除规则。
- 删除失败记录事件，保留索引和文件；不能“从列表移除但文件还在”。
- 如果清理失败导致空间不足，本次备份跳过并记录 `BACKUP_SPACE_INSUFFICIENT`，不删除唯一恢复点凑空间。
- 自动保留不删除手动备份和保护点；磁盘被这些备份占满时，定时备份只能跳过并提示用户手动处理。
- 每个删除事件记录 backupId、sizeBytes、reason（count/age/space）、时间；不记录玩家数据。

### 7.3 失败备份清理

- 失败的定时备份可能只留下 staging 或 `verification=failed` 目录；由任务/列表流程处理，不在本功能中自动删除。
- 待后续任务清理时，必须校验归属、checksum（若有）、无活动引用和锁；不在 scheduler 中做宽泛 `rm`。

## 8. 接口

### 8.1 命令

| command | 类型 | 输入 | 返回 |
| --- | --- | --- | --- |
| `get_backup_policy` | Q | target、expectedRecordVersion | `BackupPolicyResponse`（读策略、调度状态、scheduler 健康） |
| `save_backup_policy` | M | target、expectedRecordVersion、expectedPolicyRevision、policy、operationId | `BackupPolicyResponse` |
| `ensure_backup_scheduler` | 内部动作 | 无（仅由 save/显式修复调用） | scheduler 安装/校验结果；不单独暴露给前端 |
| `list_instance_backups` | Q | 现有 target | `BackupSummary` 增加 `origin`/`scheduleSlot` |
| `delete_instance_backup` | M | 现有 target/checksum | 现有删除；保留策略复用同一路径 |

`save_backup_policy` 是短写入，不创建任务；它只写策略和确保 timer。备份本身仍是 `backupCreate` 任务，前端不需要新任务类型。

### 8.2 BackupPolicyResponse

```ts
interface BackupPolicyResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  policyRevision: number;
  policy: BackupPolicy;
  state: {
    nextRunAt: string | null;
    lastAttemptAt: string | null;
    lastSuccessAt: string | null;
    lastBackupId: string | null;
    lastResult: "queued" | "succeeded" | "failed" | "skipped" | "cancelled" | null;
    lastSkipReason: string | null;
    lastError: { code: string; message: string; at: string } | null;
    missedWindows: number;
    pendingOperationId: string | null;
  };
  scheduler: {
    installed: boolean;
    active: boolean;
    version: string | null;
    lastTickAt: string | null;
  };
  backups: {
    scheduledCount: number;
    scheduledBytes: number;
  };
}
```

语义：

- `scheduler.installed=false` 时，`policy.enabled` 不能为 true；保存 enabled=true 返回错误。
- `nextRunAt=null` 表示未启用或无法计算；UI 显示“未启用”，不显示“即将执行”。
- `lastTickAt` 是 scheduler 最近一次成功 tick 时间；长期不更新表示 timer 异常，UI 显示 warning。
- `lastResult=queued` 时任务仍在执行，不显示成功。

### 8.3 BackupSummary 扩展

```ts
interface BackupSummary {
  // ...现有字段
  origin: "manual" | "scheduled" | "protection" | "legacy";
  scheduleSlot: string | null;
}
```

旧 metadata 兼容：

- 新备份写入 `origin`。
- 旧备份没有 `origin` 时按 `reason` 推断：包含“保护点”/“恢复前” → `protection`；否则 `manual`。
- `legacy` 仅用于无法推断的旧记录，永不自动删除。
- `scheduleSlot` 仅 scheduled 有。

### 8.4 策略 CAS 与错误

- `expectedPolicyRevision` 首次为 0；不匹配返回 `BACKUP_POLICY_CONFLICT`，前端刷新后重新编辑。
- `policy` 字段必须完整；不做部分更新，避免 `keepCount` 和 `keepDays` 组合歧义。
- 保存成功后返回新 revision 和重新计算的 `nextRunAt`。
- 禁用策略仍然成功，即使 scheduler 不存在；返回 warning。

| 错误码 | 场景 |
| --- | --- |
| `BACKUP_POLICY_INVALID` | 间隔/保留/空间字段越界或类型错误 |
| `BACKUP_POLICY_CONFLICT` | policyRevision CAS 失败 |
| `BACKUP_SCHEDULER_UNAVAILABLE` | systemd/timer 不可用，不能启用 |
| `BACKUP_SCHEDULER_INSTALL_FAILED` | 单元写入/daemon-reload/enable 失败 |
| `BACKUP_SCHEDULER_INVALID` | 已安装脚本/版本/digest 不匹配 |
| `BACKUP_SCHEDULE_STATE_INVALID` | 调度状态损坏、hostId/instanceId 不符 |
| `BACKUP_SCHEDULE_BUSY` | 本实例已有任务或重型锁占用（跳过事件，不一定是命令错误） |
| `BACKUP_SCHEDULE_SPACE_INSUFFICIENT` | 清理后仍低于空间目标 |
| `BACKUP_RETENTION_FAILED` | 自动清理失败，保留索引并记录 |
| `BACKUP_SCHEDULE_TASK_FAILED` | 定时备份任务失败，读取任务错误 |

## 9. 实现落点

| 文件 | 责任 |
| --- | --- |
| `remote-scripts/actions/backup-policy.py` | 策略/状态读写、CAS、scheduler 安装校验 |
| `remote-scripts/actions/backup-scheduler.py` | systemd oneshot tick、到期/跳过/去重/对账 |
| `remote-scripts/actions/backup-task.py` | 现有备份任务；增加 origin/scheduleSlot、保留清理动作 |
| `remote-scripts/probes/instance-backups.py` | 列表 origin 兼容、删除校验复用 |
| `src-tauri/src/backup_policy.rs`（或 `backups.rs` 内聚） | DTO、command、远端响应校验 |
| `src-tauri/src/commands.rs`、`lib.rs` | 注册 `get/save_backup_policy` |
| `src/api/instances.ts`、`BackupsPage.vue` | 策略表单、调度状态、来源标签 |
| `scripts/test-backup-policy.py`、`test-backup-scheduler.py`、`test-backup-retention.py` | 策略、时间/幂等、保留 fixture |

## 10. 界面

定时备份放在备份页策略区，不新增主导航。

```text
备份
┌──────────────────────────────────────────────────────────────┐
│ 定时备份                                         [已启用]    │
│ 每 6 小时 · 保留 7 份 / 14 天 · 最小剩余 2 GB               │
│ 下次 11-03 05:00 · 上次 11-02 23:00 已校验                  │
│                                      [编辑策略]              │
├──────────────────────────────────────────────────────────────┤
│ 时间        来源    DST build   大小     校验     操作       │
│ 11-02 23:00 定时    639491      1.1 GB   已校验   下载 恢复   │
│ 11-02 17:10 手动    639491      1.1 GB   已校验   下载 恢复   │
└──────────────────────────────────────────────────────────────┘
```

规则：

- 策略编辑用短表单：启用、间隔（小时）、保留份数、保留天数、最小剩余空间。
- 显示 `scheduler.active` 和 `lastTickAt`；异常时显示“主机定时器未运行”与“重新安装”入口；该入口通过重新保存当前策略触发 `ensure_backup_scheduler`，不新增独立前端写命令。
- 显示下次/上次执行、上次结果、最近错误；失败不隐藏旧的成功时间。
- 列表/策略区只用“准热 · 不停服”短标签；跨文件一致性风险放在恢复确认或 Tooltip，不作为常驻长文案。
- 备份列表增加“来源”：定时/手动/保护点；旧备份显示“备份”或“手动”。
- 自动清理删除后，列表刷新并显示事件/最近清理；不把失败删除的项从列表移除。
- 禁用策略需要确认“停止后续定时备份，已有备份保留”。
- 没有定时备份时，不显示空的大段说明，只显示“未启用”和编辑入口。
- 任务栏显示“上海服务器 · 生存一服 · 定时备份”，不因自动任务抢占用户刚发起的操作。

## 11. 失败与边界

| 场景 | 行为 |
| --- | --- |
| 桌面关闭 | timer 继续；备份正常 |
| SSH 断开 | 与定时备份无关；备份仍由远端执行 |
| 主机重启 | timer `Persistent` 补一次 tick；scheduler 去重，不补跑 N 次 |
| 实例停止 | 准热备份仍执行；metadata 记录 stopped |
| 实例运行 | 不停服；`onDiskBestEffort`；文件可能不一致 |
| 实例创建/删除中 | 跳过并记录 |
| 手动备份/更新/恢复/MOD 应用执行中 | 跳过并记录 busy，不排队 |
| 同一实例多个 tick 重叠 | scheduler 锁 + 任务状态防止重复 |
| 磁盘不足 | 先清理 scheduled，仍不足则跳过；不删唯一 verified |
| 归档校验失败 | 任务 failed；不进入 verified；scheduler 记录错误 |
| 策略损坏 | 不执行该实例；记录事件；其他实例继续 |
| hostId/instanceId 不符 | 拒绝策略/状态；不跨实例执行 |
| timer 被停用 | `get_backup_policy` 显示 active=false；保存启用时重新安装 |
| 主机无 systemd | 启用失败；禁用/只读仍可用 |

## 12. 与更新、恢复、MOD 的关系

- 定时备份使用 `quasiHot`，不能作为更新/恢复/MOD 应用前的保护点；这些操作仍必须创建 cold 保护点。
- 更新/恢复/MOD 应用任务存在时，scheduler 跳过；不会在停服窗口抓取半状态。
- 更新完成后，下一次定时备份采集新 DST build；备份 metadata 的 `dstReleaseId/dstBuild` 由现有逻辑从 active-release/catalog 取得。
- 恢复前保护点、手动备份、定时备份都进入同一列表；恢复能力仍按现有 Release 产物/Availability 校验。
- 删除实例时清理该实例的策略和 schedule state；已有备份按实例删除流程处理（当前删除会永久删除实例备份，产品文案需保持一致）。
- 删除 Release 时，定时备份的 `dstReleaseId` 也计入引用；不能因为“只是定时备份”就允许删除其依赖 Release。

## 13. 安全与数据

- 不读取 Token、密码、`desired-secrets`；备份归档的现有清洗规则不变。
- scheduler 不接收用户路径、shell 片段或任意文件列表；只读固定目录、固定 schema。
- `backup-events.jsonl` 只含 backupId、instanceId、时间、大小、错误码/跳过原因，不含玩家名称、世界内容或凭据；按文件大小/行数有界（例如 2 MiB 或 10000 行），超限时原子重写保留最近记录。
- 策略/状态文件 root:root 0700；备份归档权限沿用现有规则。
- 自动清理必须校验 target、checksum、归属、锁；不能按文件名删除。
- 不把策略或调度状态写进 SQLite；远端是唯一权威，桌面缓存只用于展示。

## 14. 难度与工作量

综合难度：**6/10（中高）**。

| 工作项 | 人日 |
| --- | --- |
| 策略 DTO/远端读写/CAS、错误映射 | 2–3 |
| scheduler 脚本、tick/去重/错过窗口/状态 | 3–4 |
| systemd 单元安装/校验/版本更新 | 2 |
| 保留策略、删除复用、空间检查 | 2–3 |
| 备份 metadata origin/scheduleSlot、列表兼容 | 1 |
| UI 策略区、状态、来源标签、确认 | 1–2 |
| fixture、Rust/前端测试、桌面关闭/重启真机验收 | 3–5 |
| 合计 | 14–20 人日 |

估算已包含 systemd 兼容、时钟/重启和删除失败的返工。主要风险不在 UI，而在“远端真的会跑、不会重复跑、不会误删”。

### 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| systemd 版本/权限差异 | timer 不运行 | `ensure` 校验 is-active/list-timers；失败不启用策略 |
| 主机重启后重复补跑 | 磁盘/负载 | Persistent + slotKey + 任务扫描去重；最多一次 |
| 时钟跳变 | 重复/漏跑 | UTC + 回拨保护 + missedWindows 封顶 |
| 准热备份不一致 | 恢复后状态异常 | UI 明确语义；不作为保护点；保留冷备份路径 |
| 自动清理误删 | 数据丢失 | 只删 scheduled verified；保留最新；引用/锁校验；失败保留索引 |
| 与更新/恢复并发 | 抓到半状态/锁冲突 | 活动任务和 heavy 锁非阻塞检查；忙碌跳过 |
| 空间估算不准 | 备份中途失败 | 预检 source size + 余量；失败保留可见错误 |
| 策略启用但 timer 未装 | 用户以为在备份 | 先 ensure 再写 enabled；状态显示 active/lastTick |

## 15. 测试与验收

### 自动化

- `scripts/test-backup-policy.py`：字段范围、CAS、首次 revision=0、损坏策略、跨实例拒绝、enabled 与 scheduler 关系。
- `scripts/test-backup-scheduler.py`：到期/未到期、错过窗口只补一次、时钟回拨/前跳、busy skip、slotKey 去重、pending 任务对账、单实例失败不影响其他实例。
- `scripts/test-backup-retention.py`：count/age/space 组合、只删 scheduled、保留最新 verified、保护点/手动不删、删除失败保留索引、空间不足跳过。
- `scripts/test-backup-task.py` 扩展：origin/scheduleSlot、reason、scheduled 任务 metadata、失败状态。
- `scripts/test-instance-backups.py` 扩展：旧 metadata origin 推断、列表排序、删除引用。
- 单元测试 systemd 单元渲染/digest 校验（不实际安装）。
- Rust：DTO、CAS、错误映射、命令注册、远端响应身份校验。
- 前端：策略表单校验、revision 冲突、下一/上次执行、scheduler inactive、来源标签。

### 真实主机矩阵

| 场景 | 期望 |
| --- | --- |
| 桌面退出 24h | 远端按间隔产生 verified scheduled 备份 |
| 主机重启 | timer 恢复；错过窗口最多补一次；无重复任务 |
| 关闭策略 | 不再新增；已有备份保留 |
| 手动备份执行中 | 定时 tick 跳过并记录 busy；不排队 |
| 更新/恢复执行中 | 跳过；不会插入停服窗口 |
| 实例停止 | 准热备份成功；metadata stopped |
| 磁盘不足 | 保留清理后仍不足则跳过；不删最后一份 |
| 删除 scheduled 备份 | 通过现有 checksum/锁校验；列表/磁盘一致 |
| 删除 Release | 被定时备份引用时拒绝 |
| 删除本地主机连接 | 不停止远端 timer/策略；需先在实例上禁用策略；文档和删除连接确认中说明 |
| 卸载桌面应用 | 远端 timer/策略仍存在；需先在应用内禁用或手动移除 systemd 单元；不能承诺卸载即停止备份 |
| 删除实例 | 策略/状态清理；备份按实例删除流程处理 |
| timer 手动 stop | `get_backup_policy` 显示 inactive；保存启用可修复 |

### UI 验收

- 亮/暗主题、960×680/1480×920；长间隔/多备份/错误状态。
- 键盘 Tab/Enter/Escape；策略编辑 Dialog 焦点锁定。
- 无策略、未启用、scheduler 异常、上次失败、保留清理失败五类状态。
- 不把“定时备份”显示成“保护点”；来源标签清晰。

## 16. 开放问题

1. v1 是否只支持固定间隔？若必须支持“每天 HH:mm”，需要增加时区、夏令时和跨日窗口设计，工作量约 +3 人日。
2. 定时备份默认关闭还是默认开启？本规划默认关闭，避免在用户不知情时占用磁盘。
3. 自动清理是否允许删除用户手动创建的 scheduled 备份？本规划允许，因为它们来源是定时；手动创建的备份不受影响。
4. 是否需要在备份页展示 `backup-events.jsonl` 历史？v1 只展示最近一次错误/清理，不新增日志页面。
5. 每主机只跑一个重型任务时，定时备份与更新冲突是跳过还是排队？本规划选择跳过，避免更新后立刻插入旧窗口备份。
