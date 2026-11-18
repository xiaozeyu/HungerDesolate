# 饥荒服务器更新功能设计

- 版本：1.3.8（已实现核心流程）
- 日期：2026-11-02
- 状态：主机检查、实例预览、`instanceUpdate` 切换任务和前端入口已实现；实现差异见 §19
- 关联：[运维增强总规划](ops-enhancements-plan.md)、[接口契约](api-contract.md)、[后端实施](backend-implementation.md#111-更新)、[Release 准备](../docs/dst-release-setup.md)

## 1. 目标与边界

### 目标

1. 在 Release 管理页检查主机 DST Dedicated Server（Steam App `343050`）的最新 build。
2. 明确展示最新 build、检查时间、可用的已验证 Release。
3. 在专服功能页执行受控切换：冷保护备份（仅世界存档）→ 停分片 → 切换活动 Release → 重建容器 → 启动 → 验证实际 build。
4. 更新只影响目标实例；同主机其他实例的 manifest、Compose、运行状态和文件不改变。
5. 失败时可使用更新前保护点恢复旧 Release + 旧存档组合（MOD/世界配置不变）；恢复结果必须真实可见。
6. 不在更新排队后静默升级到更晚版本；预览固定的目标 Release 就是执行目标。
7. 历史备份只包含世界存档，可在任意不早于备份 build 的当前 Release 上直接恢复；恢复不切换 Release，也不恢复服务器身份、端口、Compose 或配置。

### 非目标（v1）

- 不做批量更新、定时自动更新、重启自动更新。
- 不从官方重新下载任意旧 build；只使用主机 catalog 中仍存在且产物可验证的 Release。
- 不更新 MOD；MOD 更新继续走现有主机 MOD 库和实例 MOD 应用。
- 不在更新任务内自动构建 Release。缺少目标 Release 时引导到 Release 管理页构建，构建完成后重新检查。
- 不支持 degraded/unknown 运行状态下直接更新；先要求用户对账或修复。
- 不承诺更新后玩家一定可连接；验证层级仍区分进程、版本和网络事实。
- 不支持降级到更低 build；不通过备份恢复服务器身份、端口、Compose、Release 引用、访问策略或凭据。

## 2. 当前实现事实

| 事实 | 位置 | 对更新的影响 |
| --- | --- | --- |
| Release 由主机 catalog 管理，镜像 digest 固定 | `/var/lib/famine-hub/releases/catalog.json`、`staging/<releaseId>/` | 更新目标必须是一个已验证 Release，不能使用浮动 tag |
| 实例活动发布在 `active-release.json` | 实例目录 | 更新提交时原子替换 `dstReleaseId`，并同步 manifest |
| Compose 由 `render_compose` 确定性生成 | `remote-scripts/actions/instance-release.py` | 切换 Release 后必须重新渲染并 `create --force-recreate` |
| 运行验证读取容器内 `version.txt` 和进程 | `instance-runtime-start.py`、`instance-runtime.rs` | 更新后必须重新采集实际 build，不能只信 metadata |
| 备份已有 cold/quasiHot、metadata、checksum、恢复 | `backup-task.py`、`restore-task.py` | 更新前保护点复用 cold 备份，不新写一套归档 |
| 备份归档已收窄为世界存档 | `backup-task.py` | 旧归档仍兼容读取；metadata 只保留 `dstBuild`/MOD 引用等提示 |
| 普通恢复使用当前活动 Release | `instance-backups.py`、`restore-task.py` | 恢复只写世界存档，不切换 Release、不覆盖服务器配置 |
| 删除 Release 检查实例/任务/容器和更新 retained 引用 | `delete-instance-release.py` | 普通备份不再引用 Release，旧版本镜像可清理 |
| 实例 registry 的 `dstReleaseId` 只在创建实例时写入 | `instance-create-control.py` | 更新必须同步 registry；否则删除 Release 会出现陈旧占用引用 |
| `instanceUpdate` 已在任务枚举中 | `spec/api-contract.md` | 不新增任务枚举，但必须补齐 command、阶段、DTO 和前端 |

## 3. 概念模型

```text
已安装 build        = 活动 Release 的 dstBuild / 容器 version.txt
最新 build          = 主机级检查缓存中的 SteamCMD buildid
可用 Release        = catalog 中 verified=true 且 dstBuild == 最新 build 的 Release
切换目标            = 预览时固定的某个已验证 Release（不按“最新”模糊寻址）
保护点              = 更新前创建的 cold 备份，仅含世界存档；Release/manifest/Compose 由任务 transaction 快照保护
自动回滚            = 用 transaction + 保护点 + 旧 Release 产物恢复旧组合，启动并验证
历史备份恢复        = 只把备份中的世界存档恢复到当前 Release；不改变 Release、manifest、Compose、配置和凭据
```

关键区分：

- “有可用 build”不等于“有可用 Release”。没有已验证 Release 时不能更新。
- “容器启动”不等于“更新成功”。必须验证容器镜像、版本文件和分片进程。
- “已恢复原版本”不等于“更新成功”。任务终态仍是 failed，结果额外说明已恢复。
- 历史备份不含 Release 身份，不能用来切换版本；恢复只写世界存档，版本由当前活动 Release 决定。

## 4. 用户流程

```text
Release 管理页（主机级）
  ├─ 读取缓存（不联网）：最新 build / 最近检查时间 / 上次错误
  ├─ 点击“检查 DST 更新”
  │    ├─ 远端 SteamCMD app_info_print 343050
  │    ├─ 成功：写主机级检查缓存，刷新候选 Release
  │    └─ 失败：保留上次成功时间，显示固定错误，不显示“已是最新”
  ├─ 有匹配 build 的已验证 Release：标记为可切换
  └─ 缺少 Release：留在本页构建最新 Release

专服概览页（实例级）
  ├─ 显示当前 build / 活动 Release
  ├─ 点击“切换 DST 版本”
  │    ├─ 列出 build 高于当前、已验证的 Release
  │    ├─ 服务端预览：当前 build、目标 Release、分片、停服影响
  │    ├─ 冷保护备份空间、旧 Release 产物、自动回滚能力
  │    ├─ MOD 兼容性 warning（不阻断）
  │    └─ 固定 planId，10 分钟有效
  ├─ 确认“创建备份并切换”
  │    └─ 执行 instanceUpdate 持久任务
  └─ 观察任务
       ├─ 成功：显示新 build、保护点、其他实例未变化
       ├─ 失败且已回滚：显示失败阶段 + “已恢复原版本”
       └─ 失败且未回滚：显示失败阶段 + 人工恢复入口
```

## 5. 检查更新

### 5.1 检查方式

检查是主机级操作，不绑定实例。优先使用 catalog 中 Release 的 `scriptVersion` 推导 `hungerdesolate/dst-build-base:<scriptVersion>`，该镜像已完成 SteamCMD bootstrap；只有 build-base 不存在时才回退到 Release 运行时镜像：

```text
docker run --rm --name famine-dst-check-<uuid> --network bridge --user 1000:1000 \
  --env HOME=/tmp/steam-home -v famine-dst-check-tool-<hash>:<toolDir> <buildBaseImage> \
  /home/steam/steamcmd.sh \
  +@ShutdownOnFailedCommand 1 +@NoPromptForPassword 1 \
  +login anonymous +app_info_update 1 +app_info_print 343050 \
  +app_info_print 343050 +logoff +quit
```

要求：

- 不执行 `app_update`，不写实例目录，不挂宿主机业务目录，不用 host network；用一个 Docker 命名卷同时缓存 SteamCMD 工具与 HOME（bootstrap/package/appcache），检查结束后删除 Steam config、ssfn 等登录状态，避免重复下载并避免持久化登录凭据。
- 优先 build-base；回退 Release 镜像时使用 `/opt/famine/steamcmd/steamcmd.sh`，工具目录持久化到命名卷，`HOME=/tmp/steam-home`；卷初始化失败时回退为不持久化。
- Docker Engine 29.4.2 会阻断 32 位 SteamCMD，检查直接返回 `DST_UPDATE_CHECK_DOCKER_INCOMPATIBLE`。
- 输出保留最后 256 KiB，另保留最后 2 KiB 作为失败详情；SteamCMD 无输出 180 秒或总时长 3600 秒后终止，Rust 整体等待 4000 秒；超时/退出码/格式错误分别返回固定错误码并携带输出尾部。
- 从 VDF 的 `branches.public.buildid` 取数字 Steam buildid；缺失、多个冲突或非数字都失败。该值与 Release 的 `version.txt`（Klei 游戏版本 `dstBuild`）不是同一体系，候选匹配只使用 Release catalog 的 `steamBuildId`。
- 只使用 catalog 中 verified 且 `imageDigest` 与本地 Docker 镜像一致的 Release 镜像；build-base 只用于 SteamCMD 工具容器。
- 没有可用 Release 镜像时返回 `DST_UPDATE_CHECK_UNAVAILABLE`，不绑定任何实例。
- 每次检查使用唯一容器名 `famine-dst-check-<uuid>`，超时或失败后 `docker rm -f`；检查开始前清理残留的同前缀容器；检查期间把最后一条 SteamCMD 进度写入缓存 `stageDetail`。
- 检查失败不能覆盖上次成功的 `latestBuild`，只能写 `lastCheckError`（含 `detail` 输出尾部）和 `checkedAt`。

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

- TTL 设计值 6 小时；Release 管理页进入先读缓存，不自动联网。
- 用户点击“检查更新”强制执行一次；同一主机并发检查用 `updates/check.lock` 串行化。
- 缓存损坏、hostId 不匹配、build 非数字：忽略缓存并返回 `DST_UPDATE_CACHE_INVALID`，不猜测最新版本。
- 缓存是主机级事实，不写 SQLite；检查失败不删除旧缓存。

### 5.3 版本比较

- build 为十进制数字字符串；按数值比较，不按字符串。
- Release 管理页：`latestBuild` 只与主机 catalog 比较；有同 build 的 verified Release 时标记“可切换”，没有时引导构建。
- 专服切换预览：`targetRelease.dstBuild == installedBuild` → `UPDATE_SAME_RELEASE`；`targetRelease.dstBuild < installedBuild` → `UPDATE_TARGET_BUILD_OLDER`，不提供降级。
- `latestBuild < installedBuild` 只返回 warning `DST_UPDATE_REMOTE_BUILD_OLDER`，不提示降级。
- 专服切换候选按 build 降序；同一 build 有多个 Release 时按 `createdAt` 降序。用户可显式选择，预览固定 `releaseId`。

## 6. Release 准备

更新不负责构建 Release，但必须明确阻断：

- 没有与最新 build 匹配的 verified Release：`DST_UPDATE_RELEASE_REQUIRED`。
- 有 Release 但 catalog 条目损坏、镜像不存在、staging metadata 与 catalog 不一致：`DST_UPDATE_RELEASE_UNAVAILABLE`。
- Release 仅基础验证、未运行时验证：允许，但预览 warning `DST_RELEASE_RUNTIME_UNVERIFIED`。

检查与构建都在 Release 管理页完成；专服页只选择已存在的已验证 Release，不把 host 级构建塞进实例任务。构建是重型任务，可能与其他实例更新共享 heavy 锁；构建完成后在 Release 管理页重新检查。

## 7. 预览

### 7.1 输入

```ts
interface PreviewInstanceUpdateRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
  targetReleaseId: string;   // 预览固定的目标
}
```

v1 只切换 DST 版本；MOD 更新继续走现有 MOD 应用流程，不并入此命令。

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
    scope: "worldSaves";
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
- `UPDATE_TARGET_BUILD_OLDER`（不支持降级）
- `UPDATE_TARGET_BUILD_UNKNOWN`
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
| `backup` | 创建 cold 保护点（仅世界存档），metadata 记录原因 | backupId、checksum、verification |
| `publish` | 原子写 `active-release.json`、`manifest.json`、`compose.yaml` | 新旧 deploymentId、releaseId、compose digest |
| `start` | `compose create --force-recreate`，按原状态启动 | 容器 ID、labels、启动结果 |
| `verify` | 进程、版本文件、镜像 digest、端口、其他实例不变性、桥接 warning | 实际 build、运行分片、证据 |
| `finalize` | 释放队列/锁，写成功结果 | result、invalidation |
| `rollback` | 仅在失败分支：恢复 transaction 与保护点并切回旧 Release，启动/验证 | 恢复是否 verified、旧 build |

`download` 阶段 v1 不适用（Release 已在主机），按契约“可省略不适用阶段”处理，不伪造该阶段。任务开始前必须写 `transaction.json`：旧 active-release、manifest、compose、运行状态、保护点 ID、目标 Release、remoteChanged 标记；服务器身份和 Compose 只由 transaction 保护，不进入备份归档。

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
- 保护点在 update worker 内复用现有 cold 备份辅助函数（世界存档归档、校验）创建，不递归创建 `backupCreate` 任务；同一锁 owner 不重复获取自己的 flock。

### 8.3 切换发布

1. 读取 catalog 中的目标 Release，校验 `releaseId`、`imageDigest`、`dstBuild`、`commands`。
2. 读取当前 `active-release.json` 和 `manifest.json`；保留 `modsReleaseId` 不变。
3. 生成新 `deploymentId`，写新的 active-release：
   - `dstReleaseId = targetReleaseId`
   - `modsReleaseId` 保持
   - `verifiedAt` 先不写成功；验证后再写或以任务结果为准
4. 更新 manifest 的 `dstReleaseId`、`deploymentId` 和必要的 release 摘要字段；不改端口、拓扑、MOD、世界配置。
5. `configRevision` 默认不推进：DST 更新不改变世界/MOD 期望，推进 revision 会让世界/MOD 草稿无谓冲突。更新身份由 `deploymentId + dstReleaseId` 表达；如果实现选择推进 revision，必须同步前端 revisionFloor 和所有 CAS 调用点，不能只改 manifest（窄备份恢复不再依赖 revision 快照）。
6. 用 `render_compose(manifest, targetRelease, instancePath)` 重新生成 Compose；文件权限 0600。
7. `docker compose -p <project> -f compose.yaml create --force-recreate master [caves]`。
8. 原状态 running → `start`；原状态 stopped → 保持 stopped，不做进程验证但验证容器镜像/版本文件可读。
9. 同步 `registry.json` 中该实例的 `dstReleaseId`；不同步会让删除 Release 出现陈旧占用引用，同步后必须依赖 §9.5 的更新保护点引用保护兜底。

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

- 旧 `active-release.json`、`manifest.json`、`compose.yaml`（来自任务 `transaction.json`）；
- 旧世界存档 `config/Cluster_1/<分片>/save`（来自保护点归档）；
- 旧 DST Release 的镜像和 staging 产物必须仍存在且校验通过；
- 更新不改世界配置和 MOD desired/deployed，因此不恢复它们；transaction 中的副本只作为现场证据；
- 按更新前运行状态创建/启动容器，并验证旧 build 和进程。

### 9.3 实现路径

普通恢复不再有版本限制：备份归档只含世界存档，恢复始终使用当前活动 Release，不读取也不校验备份记录的 Release。更新回滚是 update worker 的独立分支：

- `rollback` 阶段用 `transaction.json` 恢复 `active-release.json`、`manifest.json`、`compose.yaml`；
- 用保护点归档恢复 `config/Cluster_1/<分片>/save`；
- 复用既有 `extract_to_staging`、归档校验、权限归一化、`render_compose`、`wait_for_runtime_processes`，按旧 Release 重建/启动/验证；
- 不调用普通恢复命令，也不引入 `allowDstSwitch` 或 `restoreMode`；
- 回滚完成后任务终态是 `failed`，结果写 `rollback: {performed: true, verified: true}`，不把失败改成成功。

禁止让 update worker 复制第二套归档校验、权限处理和 Compose 渲染规则。

### 9.4 回滚不可用

- 保护点创建失败：任务在 `backup` 阶段失败，未改发布；按 `runtimeBefore` 恢复原运行状态后结束，不能留下已停分片。
- 旧 Release 镜像/staging 在生产后缺失：预览 warning；任务失败后不做自动回滚，返回 `DST_UPDATE_ROLLBACK_UNAVAILABLE` 和人工恢复入口。
- 回滚验证失败：`DST_UPDATE_ROLLBACK_FAILED`，保留 staging/恢复现场，禁止继续自动重试或反复重启。
- 旧存档被新版本升级后，只有恢复旧保护点才能回滚；仅切二进制不允许。

### 9.5 Release 引用保护

更新期间必须确保旧 Release 不会被删除，但保护范围只覆盖回滚窗口：

- 扩展 `delete_instance_release` 的引用检查：registry 实例、实例 `manifest.json`/`active-release.json`、活动任务、容器，以及更新任务的保护点/`retained-releases.json`；被引用则阻断删除。
- 普通备份不再引用 Release，删除旧备份不会影响 Release，删除旧 Release 也不会让历史备份失效。
- 更新任务在 `publish` 后写 `retained-releases.json`（instance 级）记录旧 Release；保护点删除后，若无其他引用则允许清理。
- 预览读取旧 Release 的产物和镜像；任一缺失则 `rollback.available=false`。
- 更新成功后旧 Release 不再被实例引用，可按保留策略删除，释放镜像和 staging 磁盘。

### 9.6 历史备份恢复

备份归档的目标边界是**仅世界存档**，因此恢复与 DST 版本解耦：

- 归档只包含 `config/Cluster_1/<分片>/save` 世界存档（归档内 `data/<分片>/save`），并排除运行日志和临时文件（`server_log.txt`、`server_chat_log.txt`、`client_log.txt` 等，具体清单用 fixture 固化）；不包含 `active-release.json`、`manifest.json`、`compose.yaml`、`config/Cluster_1` 的其余配置、`desired/deployed` 状态、Release 引用或凭据。
- 现有 `sanitize_cluster_ini`、`sanitize_world_snapshot`、`preserve_access_policy`、`rebase_world_config` 等清洗/合并逻辑在新边界下不再需要；旧归档恢复时忽略对应文件。
- 恢复始终使用当前活动 Release：不切换 Release，不改 manifest/Compose/端口/世界配置/MOD/访问策略/凭据；只替换世界存档并按当前配置启动验证。
- `restorability` 只取决于归档归属、checksum、结构与当前空间，不再依赖 Release 产物；保留现有枚举值以兼容前端：`missingArtifacts` 仅表示归档文件缺失，`incompatible` 表示归档损坏或身份不符。
- 备份 metadata 可记录采集时的 `dstBuild` 与 MOD 引用作为提示，但不作为恢复门禁。若 `dstBuild` 高于当前 build，返回阻断 `BACKUP_BUILD_NEWER_THAN_INSTANCE`（旧服务端不能可靠读取新存档）；缺失或低于当前 build 时允许恢复。
- MOD 与当前实例不同时返回 `modsWarnings`，允许继续；不自动改写当前 MOD。
- 旧归档兼容：历史归档可能包含服务器信息文件，恢复时只提取世界存档，忽略其余文件；不读取其中的 Release 身份，也不触发版本切换。
- 更新任务创建的保护点同样是世界存档备份；更新成功后仍可在当前 Release 上恢复。
- 普通恢复和 DST 更新的保护点使用世界存档窄归档；MOD 应用的完整回滚快照存放在 `mod-apply-tasks/<taskId>/protection/`，不再进入用户备份列表。
- 恢复任务结果不新增版本切换字段；成功后失效 `backups`、`runtime` 域。

实现状态（2026-11-13）：用户备份与恢复保护点已收窄为世界存档；旧归档恢复只提取世界存档；MOD 应用保护点已移到任务目录；恢复不再校验或切换 Release。新 metadata 不再写入 Release/revision；旧 metadata 字段仍可读取，但不参与恢复门禁。

## 10. 接口

### 10.1 命令

| command | 类型 | 输入 | 返回 |
| --- | --- | --- | --- |
| `get_dst_update_status` | Q | HostTarget、expectedRecordVersion | `HostDstUpdateStatus`（读状态缓存，不联网；返回 `checking/stage/startedAt`） |
| `check_dst_updates` | Q | HostTarget、expectedRecordVersion | `HostDstUpdateStatus`（联网检查，SteamCMD 最长 600 秒；已有检查运行时直接返回当前状态） |
| `preview_instance_update` | P | target、expectedRecordVersion、expectedRevision、targetReleaseId | `UpdatePreview`（专服页切换） |
| `run_instance_update` | T | target、expectedRecordVersion、expectedRevision、planId、operationId | `TaskSnapshot` |
| `get_task` / `list_tasks` | Q | 现有 HostTarget/InstanceTarget | 复用任务查询；不新增重复任务查询命令 |

`check_dst_updates` 是主机级查询不是任务：检查开始即写入 `running` 状态，失败写 `lastCheckError`；检查锁是非阻塞的，已有检查运行时直接返回当前 `checking` 状态，不排队、不重复运行 SteamCMD。状态缓存为 `running/ok/failed`，包含 `stage`、`startedAt`、上一次成功 build 和错误码；缓存声称 running 但锁已释放时返回 `DST_UPDATE_CHECK_INTERRUPTED`。它不接收实例 target，也不根据某个实例的已安装版本推断主机状态。

历史备份恢复沿用现有 `restore_instance_backup` 命令，不新增 `allowDstSwitch`/`restoreMode` 字段；恢复使用当前活动 Release。

### 10.2 HostDstUpdateStatus 与 InstanceUpdateStatus

```ts
interface HostDstUpdateStatus {
  schemaVersion: 1;
  requestId: string;
  target: HostTarget;
  latest: {
    build: string;
    checkedAt: string;
    source: "steamCmd";
  } | null;
  candidateReleases: Array<{
    releaseId: string;
    build: string;
    imageDigest: string;
    runtimeVerified: boolean;
    createdAt: string | null;
  }>;
  lastCheckError: { code: string; message: string; at: string } | null;
}

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
  candidates: Array<{
    releaseId: string;
    build: string;
    imageDigest: string;
    runtimeVerified: boolean;
    createdAt: string | null;
  }>;
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
- `candidateReleases` 只包含 verified 且 build 等于最新 build 的 Release，最多 16 个（catalog 上限）。
- 实例页的 `candidates` 只包含 verified 且 `dstBuild` 高于已安装 build 的 Release；同 build 或更低 build 不列出。
- `protectionBackup` 是最近一次成功的 cold 保护点，不是任意备份。
- 历史备份恢复不使用这两个结构。

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
├─ dst/<releaseId>/                 # P2 DST 安装目录，Compose 只读挂载到 /srv/dst
├─ staging/<releaseId>/
│  ├─ release-metadata.json
│  └─ artifact.tar                 # 旧导入路径；构建流程不再生成
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
| `remote-scripts/probes/dst-update-check.py` | 主机级 SteamCMD 只读检查、VDF buildid 解析、缓存读写 |
| `remote-scripts/actions/update-plan.py` | 实例级切换预览、planId、阻断项、Release/空间校验 |
| `remote-scripts/actions/update-task.py` | 持久任务：停服、世界存档保护点、发布切换、registry 同步、验证、回滚 |
| `remote-scripts/actions/backup-archive.py`、`backup-task.py` | 归档边界收窄为世界存档；metadata 只保留提示信息 |
| `remote-scripts/actions/restore-task.py` | 只恢复世界存档到当前 Release；不切换 Release；兼容旧归档 |
| `remote-scripts/actions/mods-apply.py`、`world-config.py` | MOD 应用保护点移到任务目录，不再进入用户备份列表 |
| `remote-scripts/actions/delete-instance-release.py` | 更新保护点/retained Release 引用保护；普通备份不再引用 Release |
| `remote-scripts/probes/instance-backups.py` | restorability 去掉 Release 依赖；backup build 高于当前 build 时阻断 |
| `src-tauri/src/updates.rs` | Host/Instance 两级 DTO、command、SSH 编排、任务响应校验 |
| `src-tauri/src/backups.rs` | BackupSummary 精简、恢复预检错误映射、任务结果校验 |
| `src-tauri/src/commands.rs`、`lib.rs` | 注册已实现 command；未实现不注册 |
| `src/api/updates.ts`、`src/lib/taskRefresh.ts` | 主机检查、实例切换预览与任务轮询 |
| `src/pages/ReleaseManagementPage.vue` | 检查 DST 更新、候选 Release、构建入口 |
| `src/pages/InstanceOverviewPage.vue` | 当前 build、切换 DST 版本入口与确认弹窗 |
| `src/pages/BackupsPage.vue`、`src/api/instances.ts` | 恢复只显示世界存档语义；build 过高时阻断提示 |
| `remote-scripts/probes/build-cache.py`、`build-dst-image.sh`、`src-tauri/src/image_build.rs` | 构建缓存清理、空间预检与 Release 保留策略 |
| `scripts/test-*.py`、Rust/前端测试 | 契约、fixture、回滚、备份兼容和隔离验收 |

## 13. 错误

| 错误码 | 场景 | 处理 |
| --- | --- | --- |
| `DST_UPDATE_CHECK_FAILED` | SteamCMD 网络/退出码/超时 | 保留旧缓存，显示失败时间，可重试 |
| `DST_UPDATE_CHECK_INVALID` | buildid 缺失/非数字/多值 | 不更新缓存，提示格式异常 |
| `DST_UPDATE_CHECK_UNAVAILABLE` | 没有可运行检查的镜像/SteamCMD | 提示先准备 Release |
| `DST_UPDATE_CACHE_INVALID` | 缓存损坏/hostId 不符 | 忽略缓存，要求重新检查 |
| `DST_UPDATE_RELEASE_REQUIRED` | 没有匹配 build 的 verified Release | 引导构建 Release |
| `DST_UPDATE_RELEASE_UNAVAILABLE` | catalog/staging/镜像不一致 | 阻断预览 |
| `DST_UPDATE_BLOCKED` | 预览有 blockers | `details.blockers` 白名单展示 |
| `DST_UPDATE_PLAN_STALE` | plan 过期或条件变化 | 重新预览 |
| `DST_UPDATE_REVISION_CONFLICT` | configRevision 变化 | 刷新后重新预览 |
| `DST_UPDATE_TARGET_BUILD_OLDER` | 目标 build 低于当前 build | 不提供降级，选择更高 build |
| `DST_UPDATE_RUNTIME_INVALID` | unknown/degraded | 先对账/修复 |
| `DST_UPDATE_STOP_FAILED` | 停分片失败 | 不备份/不发布，恢复原状态 |
| `DST_UPDATE_BACKUP_FAILED` | 保护点失败 | 不发布，任务失败 |
| `DST_UPDATE_PUBLISH_FAILED` | 写 active/manifest/compose 失败 | 回滚保护点 |
| `DST_UPDATE_START_FAILED` | 重建/启动失败 | 回滚保护点 |
| `DST_UPDATE_VERIFY_FAILED` | build/进程/端口验证失败 | 回滚保护点 |
| `DST_UPDATE_ROLLBACK_UNAVAILABLE` | 旧 Release 产物缺失 | 不自动回滚，给人工入口 |
| `DST_UPDATE_ROLLBACK_FAILED` | 回滚恢复/启动/验证失败 | 保留现场，禁止自动重试 |
| `BACKUP_BUILD_NEWER_THAN_INSTANCE` | 备份 build 高于当前 build | 阻断恢复，先切换到不早于备份 build 的 Release |
| `IMAGE_BUILD_SPACE_INSUFFICIENT` | 构建前可用空间低于预估峰值 | 清理缓存/旧 Release 后重试，不开始下载 |
| `DST_UPDATE_CONFLICT` | 活动任务/锁冲突 | 排队或提示等待 |
| `DST_UPDATE_TASK_INVALID` | 任务状态/摘要损坏 | 拒绝继续，要求对账 |

错误消息必须包含真实阶段和可执行下一步；不能只返回“更新失败”。

## 14. 界面

不新增路由。检查更新放在 Release 管理页，切换 DST 版本放在专服概览页；实例列表不提供批量更新入口。

Release 管理页：

```text
最新 DST build 639491 · 检查于 11-02 17:20        [检查 DST 更新]
┌──────────────────────────────────────────────────────────────┐
│ 639491  已验证 · 可切换   hungerdesolate/dst-runtime…  11-02  │
│ 630000  已验证            hungerdesolate/dst-runtime…  10-20  │
└──────────────────────────────────────────────────────────────┘
```

专服概览页：

```text
DST 版本 630000 · Release 1a2b…                     [切换 DST 版本]
┌──────────────────────────────────────────────────────────────┐
│ 目标版本  build 639491 · Release 3c4d… · 已验证              │
│ 世界存档保护点约 1.2 GB · 可用 42 GB · 将停止 Master / Caves │
│ ⚠ MOD 可能不兼容；失败按回滚能力处理                         │
│                                        [创建备份并切换]       │
└──────────────────────────────────────────────────────────────┘
```

规则：

- 未检查时显示“尚未检查”，不显示“已是最新”。
- 检查失败时显示上次成功时间 + 当前错误；不清空候选 Release。
- 缺少 Release 时 Release 页主操作是“构建最新 Release”，不是禁用按钮无说明。
- 专服页只列出 build 高于当前、已验证的 Release；同 build/更低 build 不提供入口。
- 没有自动回滚时，确认弹窗明确“失败后需人工恢复”，并要求用户确认。
- 任务阶段用真实阶段名；`queued` 显示排队原因。
- 成功页显示 `targetBuild`、保护点、运行验证结果；失败页显示阶段和回滚结果。
- 更新任务在用户离开页面后继续；任务栏提示真实目标，不自动跳回。
- 备份页不显示版本切换或“需通过更新回滚”；恢复确认只说明将替换世界存档、保留当前服务器配置和 MOD。
- 不显示常驻流程说明；警告只在条件成立时出现。

## 15. 难度与工作量

综合难度：**8/10（高）**。

| 工作项 | 人日 |
| --- | --- |
| 更新检查探针、VDF 解析、主机缓存、Rust command/DTO | 2–4 |
| 预览、plan、阻断项、Release 校验 | 2–3 |
| update task worker：锁、停服、保护点、发布切换、registry 同步 | 5–7 |
| 验证证据、其他实例不变性、桥接 warning | 2–3 |
| 回滚路径、旧 Release 保留、备份 restorability 调整 | 3–5 |
| 备份边界收窄、restore 简化、旧归档兼容 | 3–4 |
| 磁盘治理（缓存、Retention、空间预检） | 4–6 |
| UI 页面、任务展示、错误文案 | 2–3 |
| fixture、Rust/前端测试、真机三实例验收 | 4–6 |
| 合计 | 27–41 人日 |

估算已包含返工余量；主要不确定性在回滚、旧 Release 产物和真实主机网络/磁盘场景。

### 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| SteamCMD app_info 输出格式变化 | 检查失败 | 严格解析 + fixture；失败不伪造最新 |
| 新版本存档不能回旧版本 | 降级/回滚失败 | 回滚必须恢复保护点存档 + 旧 Release；备份 build 高于当前时阻断恢复 |
| 旧 Release 被删除 | 无法回滚 | 删除引用保护 + 预览检查 + retained 记录 |
| 回滚实现复制恢复规则 | 行为分叉 | 复用归档校验/权限/Compose 辅助函数，单一实现 |
| 备份边界收窄后旧归档仍含服务器信息 | 恢复误覆盖 | 只提取世界存档，忽略归档其余文件 |
| Release 镜像/构建缓存占满磁盘 | 构建或更新失败 | §18 自动清理、保留策略、构建前空间预检 |
| 其他实例被误改 | 隔离破坏 | 只写目标实例；结果记录其他实例摘要不变 |
| 更新后 MOD 不兼容 | 游戏异常 | 预览 warning；保护点回滚；不宣称 MOD 兼容 |
| 桥接随更新失效 | 玩家页降级 | 更新验证 warning，不误判更新失败 |

## 16. 测试与验收

### 自动化

- `scripts/test-dst-update-check.py`：主机级 VDF fixture、build 比较、超时/退出码/格式错误、缓存保留。
- `scripts/test-update-preview.py`：same build、更低 build、未验证 Release、镜像缺失、degraded/unknown、空间不足、revision 变化、plan 过期。
- `scripts/test-update-task.py`：阶段顺序、锁、停服失败、保护点失败、发布失败、启动失败、验证失败、回滚成功/失败、取消、重复 operationId、目标变化。
- `scripts/test-delete-instance-release.py`：活动 Release、任务引用、更新保护点/retained 引用、共享镜像；普通备份不再阻断删除。
- `scripts/test-backup-archive.py`、`test-backup-task.py`：归档只含世界存档、旧归档兼容读取、metadata 不含恢复门禁字段。
- `scripts/test-instance-backups.py`：restorability 不依赖 Release、`BACKUP_BUILD_NEWER_THAN_INSTANCE`、旧归档。
- `scripts/test-restore-task.py`：恢复到当前 Release、不切换 Release、忽略旧归档中的服务器文件、MOD warning。
- `scripts/test-build-cache.py`、`test-image-build-commands.py`：构建后自动清理、空间预检、Release 保留策略。
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
| 更新后恢复旧备份 | 直接恢复到当前 Release；manifest/Compose/配置/MOD 不被备份覆盖 |
| 更新后删除旧 Release | 删除成功；历史备份仍可恢复 |
| 恢复 build 高于当前的备份 | 预检阻断 `BACKUP_BUILD_NEWER_THAN_INSTANCE`，不启动任务 |
| 磁盘不足 | 构建前阻断或预览阻断；不删唯一恢复点 |
| 构建成功/失败后 | BuildKit 缓存与构建上下文自动回收，可用空间回到基线附近 |

### UI 验收

- 亮/暗主题；960×680 和 1480×920；长 Release ID、长 build、错误/回滚状态。
- 键盘 Tab/Enter/Escape；确认弹窗焦点锁定和关闭后回到触发器。
- Release 页：未检查、检查失败、无匹配 Release、有可切换 Release 四类状态都有真实文案。
- 专服页：无更高 build、切换确认、任务排队、失败已回滚、失败未回滚五类状态都有真实文案。
- 备份页：恢复确认只说明替换世界存档；build 过高时显示阻断原因。

## 17. 开放问题

1. 是否允许“无自动回滚也更新”？本规划允许但要求显式确认；若产品要求绝对安全，可改为阻断。
2. 更新任务是否允许取消？建议 `stop` 前允许直接取消，`backup` 后取消走回滚。
3. 构建完成后是否自动重新检查并选中新 Release？本规划先要求用户手动刷新。
4. 更新后是否自动检查 MOD 更新？本规划只 warning，不自动改 MOD。
5. 旧 Release 默认保留几个？建议当前活动 + 最近 1 个；其余在无引用时由用户确认删除（见 §18）。

## 18. 磁盘占用治理

### 18.1 占用来源

按当前实现，DST 本身不是 40G 需求的主要来源，重复层和未回收缓存才是：

| 来源 | 现状 | 单次/单版本成本 |
| --- | --- | --- |
| builder stage | `docker/runtime/Dockerfile` 安装 apt 依赖、SteamCMD 并下载完整 DST | 约 1 份 DST + 工具链，留在 BuildKit 缓存 |
| runtime stage | 再安装运行库并 `COPY --from=builder` 整个 DST | 约 1 份 DST，进入最终镜像 |
| 每个 Release 镜像 | `hungerdesolate/dst-runtime:release-<id>`，catalog 上限 16 | 每个保留 Release 约 1 份 DST |
| 构建缓存 | 成功/失败后不自动清理，只有 Release 页手动清理 | 可能长期保留多轮 builder 层 |
| 构建上下文 | `image-build/dst-runtime-build` 每次重建后留在磁盘 | 小，但可无限累积日志/临时文件 |
| SteamCMD 下载 | 无跨任务缓存，每次重新下载完整 App 343050 | 时间和带宽成本，不直接多占一份稳态空间 |
| 备份/MOD 库 | 备份当前含配置和服务器 JSON；MOD 库按版本保留 | 备份边界收窄后备份体积下降 |

当前并不是“在当前 Release 镜像上叠加一层”，而是每次从 `debian:12-slim` 重新构建并重新下载完整 DST；因此没有跨版本的层复用，旧镜像和构建缓存同时占空间。一次构建的峰值至少是“最终镜像 + builder 缓存”两份 DST；再叠加保留的旧 Release，40G 主机很容易被占满。

现场先量一遍，避免盲目清理：

```bash
docker system df
docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}} {{.ID}}'
du -sh /var/lib/famine-hub/{image-build,staging,backups,mods,releases} 2>/dev/null
```

### 18.2 P0：立即回收（1–2 人日，不改镜像模型）

1. **构建结束自动清理**：成功或失败都在持有 `image-build/build.lock` 时删除 `dst-runtime-build` 上下文，并执行 `docker builder prune --all -f`、`docker image prune -f`；保留 `debian:12-slim` 和已登记 Release 镜像。清理失败只记 warning，不覆盖构建结果。代价是下一次构建重新下载基础层；磁盘优先场景默认全清，若构建速度优先可改为保留 24 小时内缓存并定时 `docker builder prune --filter until=24h -f`。
2. **构建前空间预检**：读取 `statvfs(ROOT)` 和 `docker system df`，要求可用空间 ≥ 预估峰值；峰值按“1 份 DST 镜像 + 1 份构建缓存 + 2 GiB 余量”计算，DST 大小取上一 Release 的镜像/安装大小。不足返回 `IMAGE_BUILD_SPACE_INSUFFICIENT`，不开始下载。
3. **Release 保留策略**：默认保留“所有实例当前活动 Release + 最近 1 个已验证 Release”。Release 页标记“使用中/可回收”，用户确认后可批量删除可回收项；删除时同时清理镜像 tag、镜像和 staging。
4. **可视化与手动清理**：Release 页显示每个镜像大小、BuildKit 可回收空间、构建上下文大小；沿用 `manage_build_cache` 的一键清理。
5. **遗留文件清理**：删除导入路径遗留的 `staging/<releaseId>/artifact.tar`；`build-<releaseId>.log` 只保留最近 20 个。
6. **MOD 库同理**：删除未被实例 desired/deployed、活动 MOD Release 或备份 metadata 引用的旧内容版本；备份只存引用，不复制 MOD 内容。

预期：40G 主机从“多份镜像 + 构建缓存”降到约 10–12G（活动镜像 + 一个保留镜像 + 基线系统）。

### 18.3 P1：构建路径瘦身（2–4 人日）

1. **拆分稳定基础镜像**：建立 `dst-build-base:<scriptVersion>`（Debian + apt + SteamCMD bootstrap）和 `dst-runtime-base:<scriptVersion>`（Debian + 运行库 + UID 1000 + workshop 脚本）。仅在 `scriptVersion` 变化时重建；每个 Release 只构建 DST 层，不再重复 apt/SteamCMD 层。
2. **DST 下载移出镜像构建**：用一次性容器把 App 343050 下载并校验到宿主目录 `image-build/dst-download`，再 `COPY` 进最终镜像；构建结束删除下载目录。BuildKit 不再保存包含 SteamCMD + DST 的 builder 层。
3. **可选增量缓存**：保留 `steamapps/` 内容缓存供下一次 `app_update` 增量下载，设固定上限（如 6 GiB），在“清理磁盘”中一并清理；缓存失效时回退全量下载。
4. 只保留最终镜像，不保留中间 stage；构建失败也执行同样的清理。

预期：单次构建峰值约 1 份 DST（下载目录）+ 1 份镜像层，约 6–8G；稳态为一个基础镜像 + 每个保留 Release 约 3–4G。

### 18.4 P2：共享运行时镜像 + 每 Release DST 目录（可选，5–8 人日）

1. 固定一个共享运行时镜像（OS + 运行库 + SteamCMD + entrypoint），所有 Release 共用。
2. 每个 Release 只保存 `/var/lib/famine-hub/dst/<releaseId>/` 的 DST 安装目录；构建 = 增量下载到新目录 + 校验 + 登记，不再构建镜像。
3. Compose 将目录只读挂载到 `/srv/dst`；Release 身份改为 `dstBuild + dstContentDigest` 加共享镜像 digest。
4. 删除 Release = 删除目录，旧版本立即释放；稳态为共享镜像约 1G + 每个保留 Release 约 3–4G，峰值是旧目录 + 新目录（可配合 reflink/CoW 降低）。
5. 代价是 catalog、Compose、Release 验证模型变更，需要兼容旧 Release；建议 P0/P1 落地并观察真实磁盘后再评估。

### 18.5 验收指标

- 构建成功或失败后，`docker system df` 的 Build Cache 回到基线，构建上下文为 0。
- 40G 主机连续构建两个 Release 不失败，构建前可用空间不低于预估峰值。
- 更新成功后删除旧 Release，历史备份仍可恢复（见 §9.6）。
- 空间不足在下载前阻断，不产生半成品镜像或半登记 Release。
- Release 页显示的数字与 `docker system df`、目录实际大小一致。

### 18.6 实现进度

| 项 | 状态 |
| --- | --- |
| 构建后自动清理上下文、BuildKit 缓存 | 已实现（`build-dst-image.sh`，成功/失败都执行） |
| 构建前空间预检，不足返回 `IMAGE_BUILD_SPACE_INSUFFICIENT` | 已实现 |
| 清理磁盘合并构建缓存与可回收 Release | 已实现：只清理未被 catalog 引用的 dangling 镜像；构建中禁止删除 |
| 构建日志保留 7 天 | 已实现 |
| 注册成功后删除遗留 `artifact.tar` | 已实现（`register-built-release.py`） |
| Release 列表显示镜像大小 | 已实现（catalog probe + Rust DTO + Release 页） |
| Release 保留提示“所有活动 + 最近 1 个” | 已实现：使用中/可回收标记和清理磁盘确认 |
| DST 更新检查状态持久化与跨页面轮询 | 已实现：running/ok/failed、非阻塞锁、checking/stage/startedAt、中断识别 |
| MOD 库旧版本自动清理 | 已实现：更新任务成功后自动删除未被引用版本，不再提供手动入口 |
| P1 基础镜像拆分 / 宿主下载 | 已实现：共享 build-base/runtime-base，DST 下载到宿主机后构建 release 层 |
| P1 增量 SteamCMD 缓存 | 已实现：新 Release 复制最近 `dst/<releaseId>` 作为增量基线后执行 `app_update` |
| P2 共享运行时镜像 + 每 Release DST 目录 | 已实现：`dst/<releaseId>/` 按需挂载；旧镜像 Release 继续兼容；增量下载缓存未实现 |

## 19. 实现进度与差异

已实现：

- `remote-scripts/probes/dst-update-check.py`：主机级 SteamCMD 检查、buildid 解析、`running/ok/failed` 状态、非阻塞锁、阶段和中断识别。
- `ReleaseManagementPage.vue`：检查状态跨页面恢复，`checking` 时每 3 秒轮询并在按钮上禁用重复检查。
- `remote-scripts/actions/update-plan.py`：实例切换预览、阻断项、10 分钟 plan。
- `remote-scripts/actions/update-task.py`：持久任务、世界存档保护点、发布切换、registry 同步、启停验证和回滚。
- `src-tauri/src/updates.rs`：主机检查、预览、任务启动/查询/取消/列表 command。
- `ReleaseManagementPage.vue`：检查 DST 更新和结果展示。
- `InstanceOverviewPage.vue`：切换 DST 版本、预览确认和任务提交。
- `taskRefresh.ts`：`instanceUpdate` 任务轮询。
- 测试：`test-dst-update-check.py`、`test-update-preview.py`、`test-update-task.py`。

实现差异（相对前文设计）：

1. **任务调度**：update worker 使用一次性 `systemd-run` unit 和同一 heavy/instances 锁，不写入 `registry.heavyQueue`，也不经过共享 queue controller。多实例更新通过 heavy 锁串行，任务状态仍在 `update-tasks`。
2. **取消**：只在阶段边界检查 `cancel.request`；Docker stop/create/start 期间不会中断。
3. **plan 存储**：预览写入 `update-plans/<planId>.json`；start 和 worker 都重新校验 plan 目标、revision 和过期时间。
4. **保护点**：更新保护点是用户可见备份，按世界存档边界保存；任务运行期间在实例目录写 `retained-releases.json` 保护旧 Release，结束或回滚后删除，`delete_instance_release` 扫描该引用。
5. **发布验证**：验证新 build/进程/端口和其他实例 active/manifest/compose 摘要；不做公网玩家连接验证。
6. **Release 管理页**：检查按钮和候选展示已实现；构建仍走现有“构建镜像”按钮。

后续可补：共享 heavyQueue 调度、运行中取消。
