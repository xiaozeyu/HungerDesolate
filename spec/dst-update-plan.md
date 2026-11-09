# 饥荒服务器更新功能设计

- 版本：1.0.5（规划）
- 日期：2026-11-02
- 状态：设计规划；`instanceUpdate` 仅有契约枚举，尚未实现 command 或远端任务
- 关联：[运维增强总规划](ops-enhancements-plan.md)、[接口契约](api-contract.md)、[后端实施](backend-implementation.md#111-更新)、[Release 准备](../docs/dst-release-setup.md)

## 1. 目标与边界

### 目标

1. 从目标主机检查 DST Dedicated Server（Steam App `343050`）的最新 build。
2. 明确展示实例当前 build、最新 build、可用于更新的已验证 Release。
3. 在一个实例上执行受控更新：冷保护备份 → 停分片 → 切换活动 Release → 重建容器 → 启动 → 验证实际 build。
4. 更新只影响目标实例；同主机其他实例的 manifest、Compose、运行状态和文件不改变。
5. 失败时可使用更新前保护点恢复旧 Release + 旧存档 + 旧 MOD 组合；恢复结果必须真实可见。
6. 不在更新排队后静默升级到更晚版本；预览固定的目标 Release 就是执行目标。

### 非目标（v1）

- 不做批量更新、定时自动更新、重启自动更新。
- 不从官方重新下载任意旧 build；只使用主机 catalog 中仍存在且产物可验证的 Release。
- 不更新 MOD；MOD 更新继续走现有主机 MOD 库和实例 MOD 应用。
- 不在更新任务内自动构建 Release。缺少目标 Release 时引导到 Release 管理页构建，构建完成后重新检查。
- 不支持 degraded/unknown 运行状态下直接更新；先要求用户对账或修复。
- 不承诺更新后玩家一定可连接；验证层级仍区分进程、版本和网络事实。

## 2. 当前实现事实

| 事实 | 位置 | 对更新的影响 |
| --- | --- | --- |
| Release 由主机 catalog 管理，镜像 digest 固定 | `/var/lib/famine-hub/releases/catalog.json`、`staging/<releaseId>/` | 更新目标必须是一个已验证 Release，不能使用浮动 tag |
| 实例活动发布在 `active-release.json` | 实例目录 | 更新提交时原子替换 `dstReleaseId`，并同步 manifest |
| Compose 由 `render_compose` 确定性生成 | `remote-scripts/actions/instance-release.py` | 切换 Release 后必须重新渲染并 `create --force-recreate` |
| 运行验证读取容器内 `version.txt` 和进程 | `instance-runtime-start.py`、`instance-runtime.rs` | 更新后必须重新采集实际 build，不能只信 metadata |
| 备份已有 cold/quasiHot、metadata、checksum、恢复 | `backup-task.py`、`restore-task.py` | 更新前保护点复用 cold 备份，不新写一套归档 |
| 普通恢复要求备份 Release 与活动 Release 相同 | `instance-backups.py`、`restore-task.py` | 更新回滚需要额外的受控“切回旧 Release”路径 |
| 删除 Release 只检查实例/任务/容器引用 | `delete-instance-release.py` | 需增加备份 metadata 引用保护，否则旧保护点会变成不可恢复 |
| `instanceUpdate` 已在任务枚举中 | `spec/api-contract.md` | 不新增任务枚举，但必须补齐 command、阶段、DTO 和前端 |

## 3. 概念模型

```text
已安装 build        = 活动 Release 的 dstBuild / 容器 version.txt
最新 build          = 主机级检查缓存中的 SteamCMD buildid
可用 Release        = catalog 中 verified=true 且 dstBuild == 最新 build 的 Release
更新目标            = 预览时固定的某个 Release（不按“最新”模糊寻址）
保护点              = 更新前创建的 cold 备份，包含旧 Release、旧存档、旧 MOD/配置
自动回滚            = 用保护点恢复旧文件并切回旧 Release，启动并验证
```

关键区分：

- “有可用 build”不等于“有可用 Release”。没有已验证 Release 时不能更新。
- “容器启动”不等于“更新成功”。必须验证容器镜像、版本文件和分片进程。
- “已恢复原版本”不等于“更新成功”。任务终态仍是 failed，结果额外说明已恢复。

## 4. 用户流程

```text
进入 更新 页
  │
  ├─ 读取缓存状态（不联网）
  │    ├─ 已安装版本 / 最近检查时间 / 上次错误
  │    └─ 有候选 Release / 缺少 Release / 未检查
  │
  ├─ 点击“检查更新”
  │    ├─ 远端 SteamCMD app_info_print 343050
  │    ├─ 成功：写主机级检查缓存，刷新候选 Release
  │    └─ 失败：保留上次成功时间，显示固定错误，不显示“已是最新”
  │
  ├─ 缺少 Release
  │    └─ 引导到 Release 管理页构建最新 Release；构建完成后返回刷新
  │
  ├─ 点击“更新到 build X”
  │    ├─ 服务端预览：当前 build、目标 Release、分片、停服影响
  │    ├─ 冷保护备份空间、旧 Release 产物、自动回滚能力
  │    ├─ MOD 兼容性 warning（不阻断）
  │    └─ 固定 planId，10 分钟有效
  │
  ├─ 确认“创建备份并更新”
  │    └─ 执行 instanceUpdate 持久任务
  │
  └─ 观察任务
       ├─ 成功：显示新 build、保护点、其他实例未变化
       ├─ 失败且已回滚：显示失败阶段 + “已恢复原版本”
       └─ 失败且未回滚：显示失败阶段 + 人工恢复入口
```

## 5. 检查更新

### 5.1 检查方式

使用目标实例当前活动 Release 镜像内的 SteamCMD，执行只读 app info 查询：

```text
docker run --rm --network bridge --user 1000:1000 \
  --env HOME=/home/famine <imageDigest> \
  /opt/famine/steamcmd/steamcmd.sh \
  +@ShutdownOnFailedCommand 1 +@NoPromptForPassword 1 \
  +login anonymous +app_info_update 1 +app_info_print 343050 +quit
```

要求：

- 只读，不执行 `app_update`，不写实例目录，不挂宿主目录，不用 host network。
- 输出有上限（例如 256 KiB），超时 180 秒；超时/退出码/格式错误分别返回固定错误码。
- 从 VDF 的 `branches.public.buildid` 取数字 build；缺失、多个冲突或非数字都失败。
- 运行镜像的 digest 必须与活动 Release 的 `imageDigest` 一致；不一致时不使用该镜像检查。
- 没有活动 Release 时，可任选一个 catalog 中 verified 的 Release 镜像；没有则 `DST_UPDATE_CHECK_UNAVAILABLE`。
- 每次检查使用唯一容器名/`--cidfile`，超时或异常后强制清理该容器，不留下运行资源；解析 stdout/stderr 前做有界截断。
- 检查失败不能覆盖上次成功的 `latestBuild`，只能写 `lastCheckError` 和 `checkedAt`。

### 5.2 主机级缓存

所有实例共享一次检查结果，避免每实例重复拉 Steam：

```text
/var/lib/famine-hub/updates/dst-latest.json
{
  "schemaVersion": 1,
  "hostId": "...",
  "checkedAt": "2026-11-02T09:00:00Z",
  "source": "steamCmd",
  "sourceImageDigest": "sha256:...",
  "latestBuild": "639491",
  "result": "ok" | "failed",
  "errorCode": null
}
```

- TTL 设计值 6 小时；页面进入先读缓存，不自动联网。
- 用户点击“检查更新”强制执行一次；同一主机并发检查用 `updates/check.lock` 串行化。
- 缓存损坏、hostId 不匹配、build 非数字：忽略缓存并返回 `DST_UPDATE_CACHE_INVALID`，不猜测最新版本。
- 缓存是主机级事实，不写 SQLite；检查失败不删除旧缓存。

### 5.3 版本比较

- build 为十进制数字字符串；按数值比较，不按字符串。
- `latestBuild == installedBuild` → 无更新。
- `latestBuild > installedBuild` → 有更新；若有同 build 的 verified Release，列出候选。
- `latestBuild < installedBuild` → 不提示降级；返回 warning `DST_UPDATE_REMOTE_BUILD_OLDER`，不自动执行。
- 多个候选 Release：按 `createdAt` 降序，UI 默认选最新；用户可显式选择，预览固定 `releaseId`。

## 6. Release 准备

更新不负责构建 Release，但必须明确阻断：

- 没有与最新 build 匹配的 verified Release：`DST_UPDATE_RELEASE_REQUIRED`。
- 有 Release 但 catalog 条目损坏、镜像不存在、staging metadata 与 catalog 不一致：`DST_UPDATE_RELEASE_UNAVAILABLE`。
- Release 仅基础验证、未运行时验证：允许，但预览 warning `DST_RELEASE_RUNTIME_UNVERIFIED`。

Release 管理页已有构建能力；更新页只提供跳转，不把 host 级构建塞进实例任务。构建是重型任务，可能与其他实例更新共享 heavy 锁；构建完成后用户回到更新页重新检查。

## 7. 预览

### 7.1 输入

```ts
interface PreviewInstanceUpdateRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
  scope: "dst" | "mods" | "both"; // v1 只实现 dst
  targetReleaseId: string;   // 预览固定的目标
}
```

`scope` 保留契约中的 `dst/mods/both` 形状，但 v1 对 `mods`/`both` 返回 `DST_UPDATE_SCOPE_UNSUPPORTED`，不返回假计划。

### 7.2 预览内容

```ts
interface UpdatePreview {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  planId: string;
  operation: "instanceUpdate";
  baseRevision: number;
  current: {
    releaseId: string;
    build: string;
    imageDigest: string;
    runtimeStatus: "running" | "stopped";
    runningShards: ShardKind[];
  };
  targetRelease: {
    releaseId: string;
    build: string;
    imageDigest: string;
    runtimeVerified: boolean;
    createdAt: string | null;
  };
  shards: ShardKind[];
  stopRequired: boolean;
  backup: {
    required: true;
    captureMode: "cold";
    estimatedBytes: number | null;
    availableBytes: number;
  };
  rollback: {
    available: boolean;
    reason: string | null;   // 旧 Release 产物/镜像缺失，或保护点无法创建等
  };
  warnings: string[];        // MOD 可能不兼容、Release 未运行时验证、无自动回滚
  blockers: string[];        // 阻断项；有值则 allowed=false
  allowed: boolean;
  expiresAt: string;         // 设计值 10 分钟
}
```

### 7.3 阻断项

- `UPDATE_TARGET_RELEASE_NOT_FOUND`
- `UPDATE_TARGET_RELEASE_NOT_VERIFIED`
- `UPDATE_TARGET_RELEASE_IMAGE_MISSING`
- `UPDATE_TARGET_RELEASE_ARTIFACTS_MISMATCH`
- `UPDATE_SAME_RELEASE`
- `UPDATE_RUNTIME_UNKNOWN`
- `UPDATE_RUNTIME_DEGRADED`
- `UPDATE_INSTANCE_BUSY`（活动更新/备份/恢复/MOD 任务）
- `UPDATE_SPACE_INSUFFICIENT`
- `UPDATE_CONFIG_REVISION_CHANGED`
- `UPDATE_TARGET_NOT_APPLICABLE`（instance 不存在/正在删除/未 ready）

无自动回滚不默认阻断，但必须同时出现在 `rollback.available=false` 和 warning；确认文案要求用户明确接受“失败需人工恢复”。

### 7.4 预览过期

- planId 在远端记录，绑定 target、规范化输入、revision、current release、target release、环境证据和创建时间。
- 执行时重新检查：target、configRevision、目标 Release、镜像、运行状态、活动任务、空间、保护点可行性。
- 任一变化返回 `PLAN_STALE`，不按旧计划执行。
- 同一 planId 重放且条件未变时返回原任务；operationId 重放返回原任务。

## 8. 执行任务

### 8.1 阶段

| 阶段 | 动作 | 证据 |
| --- | --- | --- |
| `queued` | 接纳任务、写 request/journal、进入 heavy 队列 | taskId、operationId、target、planId |
| `preflight` | 复核 plan、revision、target Release、镜像、运行状态、活动任务、空间 | 复核摘要、current/target、blockers |
| `stagingVerify` | 校验 catalog 条目、staging metadata、镜像 digest、命令格式 | release digest、image ID、artifact 校验 |
| `stop` | 停止目标分片并确认全部 stopped；原本 stopped 则跳过 | 停前状态、容器 ID、停后状态 |
| `backup` | 创建 cold 保护点，metadata 记录旧 Release 和原因 | backupId、checksum、verification |
| `publish` | 原子写 `active-release.json`、`manifest.json`、`compose.yaml` | 新旧 deploymentId、releaseId、compose digest |
| `start` | `compose create --force-recreate`，按原状态启动 | 容器 ID、labels、启动结果 |
| `verify` | 进程、版本文件、镜像 digest、端口、其他实例不变性、桥接 warning | 实际 build、运行分片、证据 |
| `finalize` | 释放队列/锁，写成功结果 | result、invalidation |
| `rollback` | 仅在失败分支：恢复保护点并切回旧 Release，启动/验证 | 恢复是否 verified、旧 build |

`download` 阶段 v1 不适用（Release 已在主机），按契约“可省略不适用阶段”处理，不伪造该阶段。任务开始前必须写 `transaction.json`：旧 active-release、manifest、compose、运行状态、保护点 ID、目标 Release、remoteChanged 标记。

失败恢复规则：

- 进入 `stop` 之后、`publish` 之前的任何失败，都必须按 `runtimeBefore` 恢复原运行状态；不能留下一个已停的分片。
- `publish` 涉及多个文件，不是单文件原子事务；按 transaction 顺序写 staging + rename，并在失败时按 transaction 回退已写文件，避免 active-release 与 manifest/Compose 不一致。
- `start`/`verify` 失败且保护点已创建时进入 `rollback`；回滚失败保留现场，不自动重复。
- 其他实例摘要不一致时记录差异并判失败，不声称成功；该问题不一定能靠目标实例回滚修复，需人工排查。

### 8.2 锁与 I/O

- 获取顺序：heavy → instance maintenance → instance lock；与备份/MOD/世界应用一致。
- 预览只读，不持有写锁；执行时在锁内重查。
- 远端 worker 执行 Docker/文件操作；Rust 只通过 SSH 提交和查询，阻塞 I/O 进入 `spawn_blocking`。
- 持有内存锁时禁止网络/文件 I/O；worker 的任务状态写使用原子 JSON。
- 保护点在 update worker 内复用现有 cold 备份辅助函数（归档、清洗、校验）创建，不递归创建 `backupCreate` 任务；同一锁 owner 不重复获取自己的 flock。

### 8.3 切换发布

1. 读取 catalog 中的目标 Release，校验 `releaseId`、`imageDigest`、`dstBuild`、`commands`。
2. 读取当前 `active-release.json` 和 `manifest.json`；保留 `modsReleaseId` 不变。
3. 生成新 `deploymentId`，写新的 active-release：
   - `dstReleaseId = targetReleaseId`
   - `modsReleaseId` 保持
   - `verifiedAt` 先不写成功；验证后再写或以任务结果为准
4. 更新 manifest 的 `dstReleaseId`、`deploymentId` 和必要的 release 摘要字段；不改端口、拓扑、MOD、世界配置。
5. `configRevision` 默认不推进：DST 更新不改变世界/MOD 期望，推进 revision 会让世界/MOD 草稿无谓冲突。更新身份由 `deploymentId + dstReleaseId` 表达；如果实现选择推进 revision，必须同步备份/恢复 rebase、前端 revisionFloor 和所有 CAS 调用点，不能只改 manifest。
6. 用 `render_compose(manifest, targetRelease, instancePath)` 重新生成 Compose；文件权限 0600。
7. `docker compose -p <project> -f compose.yaml create --force-recreate master [caves]`。
8. 原状态 running → `start`；原状态 stopped → 保持 stopped，不做进程验证但验证容器镜像/版本文件可读。

不允许：

- 在运行中直接覆盖活动 Release 目录。
- 用浮动 tag 或 `docker pull` 替换镜像。
- 改动其他实例的 manifest、Compose、active-release 或可写目录。
- 在更新中顺手改 MOD、世界配置或端口。

### 8.4 验证

成功必须同时满足：

- 所有启用分片的容器都是新 image digest，labels 的 hostId/instanceId/role 正确。
- 容器内 `/srv/dst/version.txt` 数字等于目标 Release 的 `dstBuild`。
- 原 running 实例：分片进程存在、必要 UDP 端口监听、运行采集为 running；原 stopped 实例：容器保持 stopped。
- `active-release.json` 和 `manifest.json` 的 `dstReleaseId`/`deploymentId` 一致。
- 其他实例的 `active-release.json`、`manifest.json`、`compose.yaml` 摘要与更新前一致。
- 若玩家桥接已启用：在超时内出现新的 `state.json`；失败只记 warning `PLAYER_BRIDGE_UNVERIFIED`，不把更新判失败。

结果示例：

```json
{
  "updated": true,
  "previousReleaseId": "...",
  "targetReleaseId": "...",
  "previousBuild": "630000",
  "targetBuild": "639491",
  "protectionBackupId": "...",
  "runtimeVerified": true,
  "runningShards": ["master", "caves"],
  "otherInstancesUnchanged": true,
  "publicConnectivityVerified": false
}
```

`publicConnectivityVerified` 保持 false；更新不宣称公网/玩家连接已验证。

## 9. 回滚

### 9.1 触发

- `publish`、`start`、`verify` 失败且保护点已验证。
- 用户在执行中取消，且已进入 `stop`/`backup`/`publish` 阶段。
- 进程崩溃/SSH 断开后，controller 对账发现任务未完成且存在保护点。

### 9.2 回滚内容

必须恢复一致组合，不能只切 active Release 字段：

- 旧 `active-release.json`、`manifest.json`、`compose.yaml`；
- 旧世界存档 `data/Master`、`data/Caves`；
- 旧世界配置、MOD desired/deployed、活动 MOD Release 引用；
- 旧 DST Release 的镜像和 staging 产物必须仍存在且校验通过；
- 按更新前运行状态创建/启动容器，并验证旧 build 和进程。

### 9.3 实现路径

当前普通恢复会拒绝“备份 Release ≠ 活动 Release”。更新回滚不能直接调用普通恢复命令，否则无法切回旧版本。设计选择：

- 在 `restore-task.py` 中复用 `restore_paths_from_backup`、权限归一化、`render_compose`、`wait_for_runtime_processes` 等既有函数，增加一个仅内部使用的“允许 Release 切换”模式；
- 该模式不接受前端直接调用，只由 update worker 在 `rollback` 阶段使用；
- 普通 `restore_instance_backup` 仍保持同版本限制，避免用户在普通恢复中误切版本；
- 回滚完成后任务终态是 `failed`，结果写 `rollback: {performed: true, verified: true}`，不把失败改成成功。

如果实现成本过高，替代方案是让 update worker 自己完成文件恢复和发布切换，但必须复用同一套路径校验、归档校验、权限处理和 Compose 渲染，禁止复制第二套规则。

### 9.4 回滚不可用

- 保护点创建失败：任务在 `backup` 阶段失败，未改发布；按 `runtimeBefore` 恢复原运行状态后结束，不能留下已停分片。
- 旧 Release 镜像/staging 在生产后缺失：预览 warning；任务失败后不做自动回滚，返回 `DST_UPDATE_ROLLBACK_UNAVAILABLE` 和人工恢复入口。
- 回滚验证失败：`DST_UPDATE_ROLLBACK_FAILED`，保留 staging/恢复现场，禁止继续自动重试或反复重启。
- 旧存档被新版本升级后，只有恢复旧保护点才能回滚；仅切二进制不允许。

### 9.5 Release 引用保护

更新前必须确保旧 Release 不会被删除：

- 扩展 `delete_instance_release` 的引用检查：扫描所有实例备份 metadata 的 `dstReleaseId`；被引用则阻断删除。
- 更新任务在 `publish` 后写 `retained-releases.json`（instance 级）记录旧 Release，作为额外证据；任务结束仍保留，直到保护点删除。
- 删除保护点时同步检查是否有其他保护点引用该 Release；没有才允许清理 retained 引用。
- 预览读取旧 Release 的产物和镜像；任一缺失则 `rollback.available=false`。

### 9.6 备份 restorability

更新后，旧版本保护点不应被显示为“永久不可恢复”：

- `BackupSummary` 增加 `restoreRequiresDstSwitch: boolean` 和 `restoreMode: "sameRelease" | "switchRelease"`（字段名实现前再按契约统一）。
- 若备份 Release 与当前活动 Release 不同，但 Release 产物/镜像存在、归档校验通过，`restorability` 返回 `restorable`，并标记需要版本切换。
- 普通恢复命令仍拒绝跨版本；备份页对需要切换版本的项显示“需通过更新回滚”，恢复按钮不调用会必然失败的普通恢复，或后续提供独立的跨版本恢复确认流程。
- 若产物缺失，仍返回 `missingArtifacts`，不伪装可恢复。

## 10. 接口

### 10.1 命令

| command | 类型 | 输入 | 返回 |
| --- | --- | --- | --- |
| `get_update_status` | Q | target、expectedRecordVersion | `InstanceUpdateStatus`（读缓存，不联网） |
| `check_instance_updates` | Q | target、expectedRecordVersion | `InstanceUpdateStatus`（联网检查，可能 30–180 秒） |
| `preview_instance_update` | P | target、expectedRecordVersion、expectedRevision、scope、targetReleaseId | `UpdatePreview` |
| `run_instance_update` | T | target、expectedRecordVersion、expectedRevision、planId、operationId | `TaskSnapshot` |
| `get_task` / `list_tasks` | Q | 现有 HostTarget/InstanceTarget | 复用任务查询；不新增重复任务查询命令 |

`check_instance_updates` 是查询不是任务：检查失败不产生任务记录，但写主机级 `lastCheckError`；UI 显示失败时间和固定错误码。

### 10.2 InstanceUpdateStatus

```ts
interface InstanceUpdateStatus {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  configRevision: number;
  installed: {
    releaseId: string | null;
    build: string | null;
    imageDigest: string | null;
    runtimeVerified: boolean;
    activeDeploymentId: string | null;
  } | null;
  latest: {
    build: string;
    checkedAt: string;
    source: "steamCmd";
  } | null;
  updateAvailable: boolean | null;
  candidateReleases: Array<{
    releaseId: string;
    build: string;
    imageDigest: string;
    runtimeVerified: boolean;
    createdAt: string | null;
  }>;
  lastCheckError: { code: string; message: string; at: string } | null;
  protectionBackup: {
    backupId: string;
    createdAt: string;
    verification: "verified";
  } | null;
  rollback: { available: boolean; reason: string | null };
}
```

字段语义：

- `latest=null` 表示从未成功检查；不能显示“已是最新”。
- `updateAvailable=null` 表示无法比较（未检查/已安装版本未知/build 非法）。
- `candidateReleases` 只包含 verified 且 build 匹配的 Release，最多 16 个（catalog 上限）。
- `protectionBackup` 是最近一次成功的 cold 保护点，不是任意备份。

### 10.3 任务阶段与状态

- `operation` 固定 `instanceUpdate`。
- 阶段：`queued → preflight → stagingVerify → stop → backup → publish → start → verify → finalize`；失败进入 `rollback`。
- `cancelAllowed`：`stop` 之前 true；`stop`/`backup`/`publish`/`start` 期间按安全边界返回 false 或“取消将回滚”；一旦保护点存在，取消必须走回滚。
- 任务结果包含 `previousReleaseId/targetReleaseId/previousBuild/targetBuild/protectionBackupId/runtimeVerified/otherInstancesUnchanged`。
- 成功/失败失效范围：`updates`、`overview`、`runtime`、`backups`、`diagnostics`；世界/MOD 期望不被改写时不需要清空其草稿。
- 任务事件不超过 64 KiB，不携带完整日志、玩家名单或世界配置。

## 11. 远端文件

```text
/var/lib/famine-hub/
├─ updates/
│  ├─ dst-latest.json              # 主机级检查缓存
│  └─ check.lock                   # 并发检查
├─ releases/
│  ├─ catalog.json
│  └─ catalog.lock
├─ staging/<releaseId>/
│  ├─ release-metadata.json
│  └─ artifact.tar                 # 若存在则为镜像 tar；更新校验以 catalog + image digest + metadata 为准
├─ update-tasks/<operationId>/
│  ├─ request.json
│  ├─ status.json
│  ├─ journal.jsonl
│  ├─ transaction.json             # 旧 active/manifest/compose/运行状态
│  ├─ plan.json                    # 预览固定内容
│  ├─ worker.py
│  └─ update-source.py             # worker 依赖的共享脚本快照
└─ update-plans/<planId>.json      # 10 分钟有效的预览
```

要求：

- `updates/`、`update-tasks/`、`update-plans/` 由 root 持有 0700；不接收用户路径。
- 任务目录只含 JSON/脚本，不含 Token/密码/玩家数据。
- 旧 Release 产物和镜像在保护点存在期间保留；清理由显式 Release 删除流程处理。

## 12. 实现落点

| 文件 | 责任 |
| --- | --- |
| `remote-scripts/probes/dst-update-check.py` | SteamCMD 只读检查、VDF buildid 解析、缓存读写 |
| `remote-scripts/actions/update-plan.py` | 预览、planId、阻断项、Release/空间校验 |
| `remote-scripts/actions/update-task.py` | 持久任务：停服、保护点、发布切换、验证、回滚 |
| `remote-scripts/actions/restore-task.py` | 复用恢复辅助函数；内部允许 Release 切换的回滚模式 |
| `remote-scripts/actions/delete-instance-release.py` | 备份/保护点的 Release 引用保护 |
| `remote-scripts/probes/instance-backups.py` | 跨版本备份 restorability 标记 |
| `src-tauri/src/updates.rs` | DTO、command、SSH 编排、任务响应校验 |
| `src-tauri/src/commands.rs`、`lib.rs` | 注册已实现 command；未实现不注册 |
| `src/api/updates.ts`、`src/composables/useInstanceUpdates.ts` | 前端调用、query guard、轮询 |
| `src/pages/UpdatesPage.vue`、`router.ts` | 更新页、实例侧栏入口 |
| `scripts/test-*.py`、Rust/前端测试 | 契约、fixture、回滚和隔离验收 |

## 13. 错误

| 错误码 | 场景 | 处理 |
| --- | --- | --- |
| `DST_UPDATE_CHECK_FAILED` | SteamCMD 网络/退出码/超时 | 保留旧缓存，显示失败时间，可重试 |
| `DST_UPDATE_CHECK_INVALID` | buildid 缺失/非数字/多值 | 不更新缓存，提示格式异常 |
| `DST_UPDATE_CHECK_UNAVAILABLE` | 没有可运行检查的镜像/SteamCMD | 提示先准备 Release |
| `DST_UPDATE_CACHE_INVALID` | 缓存损坏/hostId 不符 | 忽略缓存，要求重新检查 |
| `DST_UPDATE_RELEASE_REQUIRED` | 没有匹配 build 的 verified Release | 引导构建 Release |
| `DST_UPDATE_RELEASE_UNAVAILABLE` | catalog/staging/镜像不一致 | 阻断预览 |
| `DST_UPDATE_SCOPE_UNSUPPORTED` | scope=mods/both | 明确不支持，不返回假计划 |
| `DST_UPDATE_BLOCKED` | 预览有 blockers | `details.blockers` 白名单展示 |
| `DST_UPDATE_PLAN_STALE` | plan 过期或条件变化 | 重新预览 |
| `DST_UPDATE_REVISION_CONFLICT` | configRevision 变化 | 刷新后重新预览 |
| `DST_UPDATE_RUNTIME_INVALID` | unknown/degraded | 先对账/修复 |
| `DST_UPDATE_STOP_FAILED` | 停分片失败 | 不备份/不发布，恢复原状态 |
| `DST_UPDATE_BACKUP_FAILED` | 保护点失败 | 不发布，任务失败 |
| `DST_UPDATE_PUBLISH_FAILED` | 写 active/manifest/compose 失败 | 回滚保护点 |
| `DST_UPDATE_START_FAILED` | 重建/启动失败 | 回滚保护点 |
| `DST_UPDATE_VERIFY_FAILED` | build/进程/端口验证失败 | 回滚保护点 |
| `DST_UPDATE_ROLLBACK_UNAVAILABLE` | 旧 Release 产物缺失 | 不自动回滚，给人工入口 |
| `DST_UPDATE_ROLLBACK_FAILED` | 回滚恢复/启动/验证失败 | 保留现场，禁止自动重试 |
| `DST_UPDATE_CONFLICT` | 活动任务/锁冲突 | 排队或提示等待 |
| `DST_UPDATE_TASK_INVALID` | 任务状态/摘要损坏 | 拒绝继续，要求对账 |

错误消息必须包含真实阶段和可执行下一步；不能只返回“更新失败”。

## 14. 界面

新增实例页路由 `/hosts/:serverId/instances/:instanceId/updates`，侧栏“更新”。实例列表不提供批量更新入口。

```text
更新
┌──────────────────────────────────────────────────────────────┐
│ 已安装  build 630000 · Release 1a2b…                         │
│ 可用    build 639491 · 检查于 11-02 17:20                    │
│                                        [检查更新]            │
├──────────────────────────────────────────────────────────────┤
│ 目标版本                                                     │
│ build 639491 · Release 3c4d… · 运行时已验证                  │
│ 冷保护备份约 1.2 GB · 可用 42 GB · 将停止 Master / Caves     │
│ ⚠ MOD 可能不兼容；失败按回滚能力处理                         │
│                                        [创建备份并更新]       │
└──────────────────────────────────────────────────────────────┘
```

规则：

- 未检查时显示“尚未检查”，不显示“已是最新”。
- 检查失败时显示上次成功时间 + 当前错误；不清空候选 Release。
- 缺少 Release 时主操作是“前往 Release 管理”，不是禁用按钮无说明。
- 没有自动回滚时，确认弹窗明确“失败后需人工恢复”，并要求用户确认。
- 任务阶段用真实阶段名；`queued` 显示排队原因。
- 成功页显示 `targetBuild`、保护点、运行验证结果；失败页显示阶段和回滚结果。
- 更新任务在用户离开页面后继续；任务栏提示真实目标，不自动跳回。
- 不显示常驻流程说明；警告只在条件成立时出现。

## 15. 难度与工作量

综合难度：**8/10（高）**。

| 工作项 | 人日 |
| --- | --- |
| 更新检查探针、VDF 解析、缓存、Rust command/DTO | 3–5 |
| 预览、plan、阻断项、Release 校验 | 2–3 |
| update task worker：锁、停服、保护点、发布切换 | 5–7 |
| 验证证据、其他实例不变性、桥接 warning | 2–3 |
| 回滚路径、旧 Release 保留、备份 restorability 调整 | 3–5 |
| UI 页面、任务展示、错误文案 | 2–3 |
| fixture、Rust/前端测试、真机三实例验收 | 4–6 |
| 合计 | 21–32 人日 |

估算已包含返工余量；主要不确定性在回滚、旧 Release 产物和真实主机网络/磁盘场景。

### 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| SteamCMD app_info 输出格式变化 | 检查失败 | 严格解析 + fixture；失败不伪造最新 |
| 新 DST 存档不能回旧版本 | 回滚无效 | 必须恢复保护点存档 + 旧 Release，不只切二进制 |
| 旧 Release 被删除 | 无法回滚 | 删除引用保护 + 预览检查 + retained 记录 |
| 回滚实现复制恢复规则 | 行为分叉 | 复用 restore 辅助函数，单一权限/校验实现 |
| 磁盘不足 | 更新/回滚失败 | 预览空间预算；保留保护点；失败不删唯一恢复点 |
| 其他实例被误改 | 隔离破坏 | 只写目标实例；结果记录其他实例摘要不变 |
| 更新后 MOD 不兼容 | 游戏异常 | 预览 warning；保护点回滚；不宣称 MOD 兼容 |
| 桥接随更新失效 | 玩家页降级 | 更新验证 warning，不误判更新失败 |

## 16. 测试与验收

### 自动化

- `scripts/test-dst-update-check.py`：VDF fixture、build 比较、超时/退出码/格式错误、缓存保留。
- `scripts/test-update-preview.py`：same release、未验证 Release、镜像缺失、degraded/unknown、空间不足、revision 变化、plan 过期。
- `scripts/test-update-task.py`：阶段顺序、锁、停服失败、保护点失败、发布失败、启动失败、验证失败、回滚成功/失败、取消、重复 operationId、目标变化。
- `scripts/test-delete-instance-release.py`：备份引用、活动 Release、任务引用、共享镜像。
- Rust：DTO 校验、命令注册、任务响应身份/阶段/序号校验、错误映射。
- 前端：query guard、任务合并、旧响应丢弃、无候选/未检查/失败/回滚状态。

### 真实主机矩阵

| 场景 | 期望 |
| --- | --- |
| A/B/C 三实例，更新 A | A 新 build；B/C manifest/active/compose 摘要不变 |
| A 运行中更新 | 停服、备份、切换、启动、版本验证通过 |
| A 停止中更新 | 保持停止；文件/版本正确；不自动启动 |
| A degraded/unknown | 预览阻断，不执行 |
| 更新中 SSH 断开 | controller 继续；重连按 taskId 对账；未知不当成功 |
| 更新中桌面退出 | 远端继续；重开显示真实终态 |
| 主机重启 | 任务中断后对账；保护点不丢；不重复发布 |
| 启动失败 | 自动回滚旧版本并验证；终态 failed + 已恢复 |
| 旧镜像缺失 | 预览提示无自动回滚；失败不伪装恢复 |
| 磁盘不足 | 预览阻断或保护点阶段失败；不删唯一恢复点 |
| 删除被备份引用的 Release | 拒绝并给出引用实例/备份 |

### UI 验收

- 亮/暗主题；960×680 和 1480×920；长 Release ID、长 build、错误/回滚状态。
- 键盘 Tab/Enter/Escape；确认弹窗焦点锁定和关闭后回到触发器。
- 无候选 Release、检查失败、任务排队、失败已回滚、失败未回滚五类状态都有真实文案。

## 17. 开放问题

1. 是否允许“无自动回滚也更新”？本规划允许但要求显式确认；若产品要求绝对安全，可改为阻断。
2. 更新任务是否允许取消？建议 `stop` 前允许直接取消，`backup` 后取消走回滚。
3. 是否在更新页提供“构建最新 Release”快捷入口？本规划只跳转 Release 管理。
4. 更新后是否自动检查 MOD 更新？本规划只 warning，不自动改 MOD。
5. 普通备份页是否允许跨版本恢复？本规划只在更新回滚中允许；后续可单独设计。
