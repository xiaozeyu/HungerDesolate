# 玩家信息列表与踢出功能设计

- 版本：1.3.0（只读玩家列表实现）
- 日期：2026-11-02；更新：2026-11-17
- 状态：只读玩家列表已实现；运行中踢出命令通道阻断，v1 不实现踢出。
- 关联：[运维增强总规划](ops-enhancements-plan.md)、[接口契约](api-contract.md)、[后端实施](backend-implementation.md)、[MOD 管理设计](mod-management-plan.md)、[M0 验证手册](../docs/player-bridge-m0.md)

## 已确认决策

1. 新建实例默认启用桥接；已有实例通过玩家页的一次安装操作启用，运行中的实例在下次重启后加载。
2. ~~踢出命令使用固定 TheSim key `hungerhub_bridge_command` / `hungerhub_bridge_result`~~ M0 实测不可用；v1 只写 `hungerhub_bridge_state`，不存在命令键。
3. 快照使用 RFC 3339 的 `capturedAt`；宿主以 Unix 秒计算 30/120 秒新鲜度阈值。
4. 启用任务沿用 `expectedRevision` + 只读 preview，不引入 planId；apply 时重新校验。
5. 查询响应采用现有 envelope + data 拆分；`PLAYER_BRIDGE_DISABLED` 在查询中是正常状态，不是错误。
6. `runtimePlayerCount` 不进入 v1；现有 A2S 人数只用于运行摘要。
7. 单个玩家数据非法时跳过该条目并返回 warning；文件级 schema/大小/路径非法仍按分片不可用处理。
8. M0 实测：`TheSim` 只能可靠导出状态；宿主覆盖 `hungerhub_bridge_command` 后，MOD 的 `GetPersistentString` 仍返回启动时的空占位，运行中命令注入不可用。v1 只实现只读玩家列表，不注册踢出 command；踢出需要新的命令通道，另行设计。

## v1 实现状态

- 桥接 MOD：`remote-scripts/player-bridge/`，只写 `hungerhub_bridge_state`，5 秒快照，无命令/结果键。
- 新建实例：创建 worker 验证桥接 payload 摘要，写 `bridge/<Shard>`、`modoverrides.lua` 和 manifest `playerBridge`；Compose 增加门控挂载并在共享 DST 目录预建挂载点。
- 已有实例：玩家页调用 `preview_instance_player_bridge` / `install_instance_player_bridge` 同步安装；不停止服务器、不提 revision，运行中的实例由用户重启后由启动流程加载。
- 查询：`get_instance_players` → `instance-players.py`，合并分片、去重、30/120 秒新鲜度、非法条目 warning。
- 前端：玩家页 15 秒轮询、页面隐藏暂停、手动刷新、亮暗主题；不显示踢出按钮。
- 隐私：桥接状态键排除在备份归档之外；诊断不读取；玩家名称/userid 不进入 SQLite、日志或任务事件。

## 1. 目标与边界

### 目标

1. 展示目标实例当前在线玩家：玩家名称、使用角色、所在分片（Master/Caves）。
2. ~~支持对单个在线玩家执行踢出；踢出后玩家可立即重新加入（不是封禁）。~~ M0 实测 TheSim 命令注入不可用；v1 不做踢出，页面只读。
3. 数据来自实例内部真实玩家状态，不用日志猜测、不用 A2S 人数冒充名单。
4. Master/Caves 合并、去重；迁移中的玩家不重复显示。
5. 数据过期、桥接未加载、服务器停止、查询失败时显示真实状态，不显示“0 人”或旧名单当实时。
6. 玩家名称和 Klei userid 不进入 SQLite、日志、任务事件、诊断包或备份归档。

### 非目标（v1）

- 不做封禁、解封、白名单、管理员名单编辑。
- 不做玩家历史、在线时长、聊天记录、IP/Steam 资料。
- 不做远程控制台、任意 Lua/命令执行、RCON。
- 不做跨实例玩家列表聚合、全主机在线总览。
- 不做自动踢人、按规则踢人、投票踢人。
- 不修改玩家存档或角色数据。

## 2. 为什么不能只用现有能力

| 方案 | 能拿到名称 | 能拿到角色 | 能踢出 | 结论 |
| --- | --- | --- | --- | --- |
| Steam A2S_INFO | 否（只有人数） | 否 | 否 | 现有运行采集只能证明人数，不够 |
| A2S_PLAYER | DST 通常不返回可靠名单 | 否 | 否 | 不可依赖 |
| 解析 `server_log.txt` 加入/离开 | 部分 | 不可靠（角色切换/重连/多分片） | 否 | 不能作为权威名单 |
| `docker attach` 控制台 | 可能拿到打印文本 | 需要解析 Lua 输出 | 可能 | 脆弱、阻塞、无结构化回执，拒绝 |
| 服务端桥接 MOD + 固定文件协议 | 是 | 是（`player.prefab`） | 是（服务端 API） | 采用，但必须先做技术验证 |

DST 没有内建 RCON。社区常见做法是服务端 MOD 或外部控制台；本项目不挂 Docker socket、不暴露控制台，因此选择“自带服务端桥接 MOD + 实例内固定目录”的结构化方案。

外部实践报告（2026-11-02 核对）指出 DST MOD 的 `io` 主要限于游戏/MOD 目录且写模式受限；本设计因此把桥接目录放在容器内 `mods/` 下，并由 M0 在真实主机验证。[Klei 论坛：Can a DST mod export data into a file?](https://kleiforums.com/forums/topic/154842-can-a-dst-mod-export-data-into-a-file/)

`TheNet:DropPlayer`/踢人 API、`AllPlayers` 字段和本地 MOD 加载行为必须在真实主机/客户端验证，不能只凭文档假定。

## 3. 技术验证（M0，必须先做）

在写页面和正式任务前，用一台真实 Linux amd64 主机、真实 DST 客户端完成以下验证，并记录版本号、命令、截图/日志摘要：

1. 把桥接 MOD 放到 `/srv/dst/mods/hungerhub_bridge`，在 `modoverrides.lua` 启用，两个分片都能加载，日志无 Lua 错误。
2. MOD 能通过 `TheSim:SetPersistentString` 写 `hungerhub_bridge_state`；宿主能读取 `config/Cluster_1/<Shard>/save/` 下的对应 key 文件；不能写其他路径；文件内容为 `KLEI     1 ` + 有效 JSON。`io` 写路径不作为方案。
3. `AllPlayers` 能拿到 `userid`、`name`、`prefab`；玩家加入/离开/换角色后 10 秒内反映。
4. Master 与 Caves 各自写独立状态；玩家下洞/回地面后两边合并去重正确。
5. 踢人 API 可用：对在线玩家执行后 5 秒内断开；离线目标返回 notFound；命令过期不执行。
6. 客户端不需要安装任何 MOD；vanilla 客户端可正常加入。
7. 服务器重启后状态文件恢复写入；命令/结果目录权限正确。
8. 玩家名称含中文/Unicode/空格/长名称时不崩溃，JSON 编码正确。
9. MOD 关闭/删除后，游戏正常启动，玩家页显示未启用而不是空名单。
10. 桥接 MOD 与至少一个常用服务端 MOD 同时启用时不冲突（可选，但建议）。
11. 新 Release 将 `/srv/dst` 只读挂载时，`<instance>/bridge/<Shard>` 到 `/srv/dst/mods/hungerhub_bridge` 的嵌套 rw 挂载可用；失败时记录预建目标目录的尝试结果。
12. 启用 `-ugc_directory /srv/ugc` 时，DST 仍从 `/srv/dst/mods` 加载本地 MOD。

M0 交付物与步骤见 [M0 验证手册](../docs/player-bridge-m0.md)。M0 使用固定 TheSim key `hungerhub_bridge_command` / `hungerhub_bridge_result`；不再验证 `command-*.json` 目录枚举。

退出条件：

- 任一核心项失败（MOD 不加载、不能写文件、拿不到角色、踢人 API 不可用），停止本功能实现，记录阻断原因和替代方案；不注册假 command。
- 通过后，把验证脚本/记录放到 `docs/` 或测试说明中，作为后续回归基线。

## 4. 桥接 MOD 设计

### 4.1 分发方式

- 桥接 MOD 是项目自带的服务端 MOD，不发布到 Steam Workshop，不进入主机 MOD 库，不出现在用户实例 MOD 列表。
- 源码放 `remote-scripts/player-bridge/modinfo.lua`、`modmain.lua`；Rust 用 `include_str!` 嵌入，按版本/digest 上传到实例。M0 阶段由 `scripts/player-bridge-m0.py` 复制到测试实例，生产接入前不注册 command。
- 版本固定，例如 `bridgeVersion=1`、`bridgeDigest=sha256:...`；不依赖第三方下载。
- 只在实例启用桥接时安装；新建实例默认安装，已有实例通过一次性启用任务安装。

### 4.2 目录与挂载

桥接目录只放代码；状态/命令/结果通过 `TheSim` key 写在 config 挂载的存档目录：

```text
<instance>/bridge/
├─ Master/                  UID 1000, 0700（代码挂载）
│  ├─ modinfo.lua          root:root 0644
│  └─ modmain.lua          root:root 0644
└─ Caves/
   └─ ...（启用洞穴时）

<instance>/config/Cluster_1/<Shard>/save/
├─ hungerhub_bridge_state     MOD 写（TheSim），0644/1000
├─ hungerhub_bridge_command   宿主写，0644/1000
└─ hungerhub_bridge_result    MOD 写（TheSim），0644/1000
```

三个 key 文件均为 `KLEI     1 ` + JSON；`TheSim` 写入/读取时自动处理该头部，宿主直接按同样格式读写。状态/结果由 MOD 写，命令由宿主写；`io` 不参与写文件。

Compose 挂载：

```yaml
master:
  volumes:
    - <instance>/bridge/Master:/srv/dst/mods/hungerhub_bridge:rw
caves:
  volumes:
    - <instance>/bridge/Caves:/srv/dst/mods/hungerhub_bridge:rw
```

为什么挂在 `mods/` 下：

- M0 确认本地 MOD 必须从 `/srv/dst/mods` 加载；桥接目录只放代码，不再承担状态写入。
- 挂载目录不参与游戏存档；MOD 代码和 overrides 一起加载。
- `TheSim` key 文件放在 config 挂载的 `save/` 下，宿主可直接读写，且不进入普通世界备份。

要求：

- 桥接目录由 `prepare_runtime_paths`/桥接安装任务创建，UID 1000 可读；代码文件 root 只读。
- 共享 dst 根目录 `<root>/dst/<releaseId>/mods/hungerhub_bridge` 必须存在为空目录；`/srv/dst` 只读挂载时 Docker 无法在只读父挂载下创建子挂载点。创建/桥接安装任务在渲染 Compose 前创建该空目录；空目录本身不启用 MOD。
- 状态/命令/结果只使用 `config/Cluster_1/<Shard>/save/hungerhub_bridge_*`；不要写 `data/`、`mods/releases/` 或桥接代码目录。
- `TheSim:SetPersistentString`/`GetPersistentString` 是唯一文件通道；MOD 不调用 `io.open` 写文件。宿主写入命令时使用相同的 `KLEI     1 ` 头部。
- 状态 key 只含当前在线玩家；玩家离开后下一次快照移除。
- 不写 Token、密码、世界种子或其他秘密。
- `mods-apply` 的保护/回滚快照必须排除 `config/Cluster_1/<Shard>/save/hungerhub_bridge_*`；普通世界备份只归档 `data/`，不包含这些 key。删除实例时随实例目录清理。
- 若 `TheSim` key 文件无法被宿主读写，M0 停止；不使用 `io` 写路径作为降级。

### 4.3 MOD 元数据

`modinfo.lua` 草案（字段以实机验证为准）：

```lua
name = "HungerDesolate Bridge"
description = "Local server bridge for the HungerDesolate desktop app."
author = "HungerDesolate"
version = "1.0.0"
api_version = 10
server_only_mod = true
all_clients_require_mod = false
client_only_mod = false
```

`modmain.lua` 职责：

1. 只在服务端运行（`TheNet:GetIsServer()`）。
2. `AddGamePostInit` 后启动周期任务，每 5–10 秒扫描 `AllPlayers`。
3. 生成 JSON：schemaVersion、instanceId（如可取得）、shard、sequence、capturedAt、players[]。
4. 通过 `TheSim:SetPersistentString("hungerhub_bridge_state", payload, false)` 写状态；不使用 `io.open`/`os.rename`。
5. 每秒通过 `TheSim:GetPersistentString("hungerhub_bridge_command", callback)` 读取固定 key；只接受 `action="kick"`、固定字段、未过期、userId 合法的命令。
6. 按 userId 查找在线玩家；找到后调用服务端踢人 API；通过 `SetPersistentString("hungerhub_bridge_result", ...)` 写结果，用 requestId 防止同一命令重复执行。
7. 忽略未知 action、缺字段、超长、过期、非固定 key 字段；不执行任意字符串/Lua。
8. 限制 state 大小和轮询频率；命令 key ≤4 KiB，state key ≤256 KiB；读取回调未完成时不重复发起。

需要实机确认的点：

- `AllPlayers` 在 Master/Caves 的实际内容；
- `player.userid` 的格式和稳定性；
- `player.name` 是否为显示名；
- `player.prefab` 是否总是角色 prefab，modded 角色是什么形态；
- `TheNet:DropPlayer(player)` 是否可用；是否需要 `DoTaskInTime`/其他服务端 API；
- `TheSim` key 文件在 `config/Cluster_1/<Shard>/save/` 下且宿主可读写（M0 已确认）；
- 宿主写入新命令 key 后，`GetPersistentString` 是否每次读取磁盘而非启动缓存（M0 验证）；
- 本地 MOD 在 `modoverrides.lua` 的 key 是否为目录名（`["hungerhub_bridge"]={enabled=true}`）；
- 洞穴迁移时两边 `AllPlayers` 的重叠窗口。

## 5. 文件协议

### 5.1 状态快照

```json
{
  "schemaVersion": 1,
  "instanceId": "uuid",
  "shard": "Master",
  "sequence": 42,
  "capturedAt": "2026-11-02T09:00:00Z",
  "players": [
    {
      "userId": "KU_xxxxxxxx",
      "name": "玩家名称",
      "character": "wilson"
    }
  ]
}
```

限制：

- `players` 最多 128 项；`userId` ≤ 64 字符；`name` ≤ 64 字符；`character` ≤ 64 字符。
- 所有字符串必须是合法 UTF-8；拒绝控制字符（除名称中的普通空格）。
- 文件 ≤ 256 KiB；超过视为无效，宿主不解析。
- `sequence` 在 MOD 进程内单调递增；服务重启后从 0 重新开始。宿主以 `capturedAt` 判断新鲜度，不把 sequence 当跨重启的全局序号。
- `capturedAt` 由服务器 UTC 时间生成；宿主不信任客户端时间，但用于新鲜度判断。
- `instanceId` 可选；MOD 能取得时必须与目标实例一致，不能取得时宿主按目标目录归属校验。

状态快照写入 `hungerhub_bridge_state` key；宿主读取：

```text
<instance>/config/Cluster_1/<Shard>/save/hungerhub_bridge_state
```

文件内容为 `KLEI     1 ` + 上述 JSON。宿主校验头部、schema、大小和 UTF-8 后解析。

### 5.2 踢出命令（M0 阻断，v1 不实现）

> M0 实测：宿主写入 command key 后，MOD 的 `GetPersistentString` 仍返回启动时的空占位，读不到宿主覆盖的 JSON。因此 v1 不实现运行中踢出，不注册 `kick_instance_player`。以下协议保留为后续新通道的设计参考。

宿主写固定 key 文件 `config/Cluster_1/<Shard>/save/hungerhub_bridge_command`：

```json
{
  "schemaVersion": 1,
  "requestId": "uuid",
  "action": "kick",
  "userId": "KU_xxxxxxxx",
  "expectedName": "玩家名称",
  "issuedAtUnix": 1793600400,
  "expiresAtUnix": 1793600430
}
```

MOD 通过 `TheSim:SetPersistentString` 写固定 key `hungerhub_bridge_result`（宿主读取同目录文件）：

```json
{
  "schemaVersion": 1,
  "requestId": "uuid",
  "result": "kicked",
  "nameMatched": true,
  "atUnix": 1793600403,
  "api": "DropPlayerUserId"
}
```

`api` 是 M0 诊断字段，记录实际成功的踢人候选 API；接入正式页面时保留为有界诊断字段或移除。

`result` 取值：

- `kicked`：找到并执行了踢出；`nameMatched` 表示 userId 对应的当前名称是否与 `expectedName` 一致；
- `notFound`：当前分片没有该 userId；
- `expired`：命令过期或早于 MOD 进程启动时间；
- `failed`：服务端 API 失败/异常。

限制：

- 命令 ≤ 4 KiB；只允许固定字段；宿主写 `save/hungerhub_bridge_command`，内容为 `KLEI     1 ` + JSON，最终 0644/1000，保证游戏可读。
- 命令 30 秒过期；`issuedAtUnix < bootAt` 或服务重启后不执行旧命令。
- 一次只处理一个踢出：MOD 读取 `hungerhub_bridge_command` key，若 `hungerhub_bridge_result` 的 requestId 相同则跳过（重试幂等）；不需要目录枚举、结果保留上限或清理任务。
- MOD 只处理 TheSim key 内容；拒绝超大文件、非 JSON；JSON 解码限定为固定扁平对象，不执行 Lua、不使用 `loadstring`。
- 宿主只接受与本次 requestId 匹配、在等待窗口内、字段合法、结果值在枚举内的结果；不把游戏日志当结果。
- 宿主在写入新命令前可读取 `hungerhub_bridge_result` key；若 requestId 相同则直接返回原结果，不重复写命令。命令 key 在收到结果或超时后删除。

### 5.3 为什么用文件而不是 HTTP/RCON

- 不暴露网络端口，不新增认证面。
- 不需要 Docker socket、不需要在游戏容器内运行网络服务。
- 不引入 RCON 依赖或第三方 MOD。
- 文件协议有明确请求/结果和超时，能表达“未知”而不是猜测成功。
- 代价是需要挂载目录和一次重开分片；这是可接受的一次性成本。

## 6. 安装与启用

### 6.1 新建实例

新建实例默认启用桥接（已确认）。

- 实例创建任务在 `prepareDirectories` 阶段创建 `bridge/Master`、`bridge/Caves` 目录并设置权限。
- 上传桥接 MOD 文件和版本/digest 清单。
- 在共享 dst release 目录预建空的 `mods/hungerhub_bridge` 挂载点（`/srv/dst` 只读挂载时 Docker 不能自行创建）。
- 初始 `modoverrides.lua` 包含桥接项；当前创建 worker 不写 overrides，需在接入时补上（即使没有用户 MOD 也要写桥接项）。Compose 渲染包含挂载。
- 容器创建后，玩家页读取桥接状态；首次启动可能需要 10–30 秒才有快照。

如果桥接安装失败，实例创建任务失败并清理受控文件；不允许“实例创建成功但玩家功能永久不可用”的假成功。

### 6.2 已有实例

新增 `playerBridgeApply` 持久任务（或等价的受控动作）：

1. 预览：当前桥接状态、桥接版本、将重开的分片、预计中断时间、不需要世界备份。
2. 校验：instance ready、无活动任务、桥接目录安全、Compose/manifest 可写、目标版本兼容。
3. 停止分片（如果运行中）。
4. 安装/更新桥接文件、目录权限、manifest `playerBridge` 字段。
5. 在 `modoverrides.lua` 中加入固定桥接项；不改变用户 MOD 的 desired/deployed。
6. 预建共享 dst release 目录下的空 `mods/hungerhub_bridge` 挂载点，重新渲染 Compose（增加挂载），`create --force-recreate`。
7. 按原状态启动。
8. 如果原实例运行中，等待 `save/hungerhub_bridge_state` 出现并通过 schema 校验（例如 60 秒）；失败则回滚文件/Compose/modoverrides 并恢复原状态，任务失败。如果原实例已停止，不启动分片，只校验文件、Compose 和 manifest；state key 留到首次启动后由玩家页显示未采集。
9. 记录桥接版本/digest、安装时间和验证结果；任务结果失效 `players`、`overview`、`runtime`。

失败回滚：

- 恢复旧 Compose、manifest、`modoverrides.lua`，移除新增目录（只移除本任务创建且归属明确的文件）。
- 如果原实例 running，重新创建/启动旧容器；验证原运行状态。
- 不删除用户 MOD 或世界文件。

### 6.3 与现有 MOD/世界/恢复流程的整合

桥接是“系统本地 MOD”，不是用户 desired MOD。需要统一处理：

| 流程 | 要求 |
| --- | --- |
| `mods-apply.py` 生成 `modoverrides.lua` | 在用户 MOD 项后追加固定桥接项；桥接不进入 desired/deployed MOD 列表 |
| `mods-apply.py` 保护/回滚快照 | 排除 `config/Cluster_1/<Shard>/save/hungerhub_bridge_*`；不得把 state（含玩家名）复制进任务保护目录 |
| `setup_script` | 不为桥接生成 `ServerModSetup`（本地 MOD 不需要 Workshop 下载） |
| `render_compose` | 当 `manifest.playerBridge.enabled` 时追加挂载；否则不挂载 |
| 实例创建 | 创建 bridge 目录、上传文件、初始 overrides、Compose 挂载 |
| 世界配置应用 | 不改 `modoverrides.lua`；如果现有实现会重写，必须保留桥接项 |
| 恢复 | 只替换 `config/Cluster_1/<分片>/save`；不覆盖 Compose、manifest、overrides；`bridge/` 保留，状态由 MOD 重建 |
| 更新 | 只换 DST 镜像；桥接目录/overrides 保留；验证阶段检查桥接是否仍写状态，失败只 warning |
| 删除实例 | 删除 bridge 目录；不保留玩家数据 |

单一实现原则：所有 `modoverrides.lua` 生成集中在现有函数，增加一个固定项，不在多个脚本各写一份字符串。

必须同步的实现点：

| 文件 | 改动 |
| --- | --- |
| `remote-scripts/actions/instance-release.py` | `render_compose` 在 `manifest.playerBridge.enabled` 时追加 bridge 挂载；共享 dst 根目录的空挂载点由调用方预建 |
| `remote-scripts/actions/instance-create-worker.py` | `resource_plan`/初始目录/Compose 包含 bridge；创建前安装桥接文件并预建共享 dst 挂载点 |
| `remote-scripts/actions/player-bridge-apply.py`（新增） | 预览/安装/回滚桥接文件、overrides、manifest、Compose，清理旧 key，并预建共享 dst 挂载点 |
| `remote-scripts/actions/mods-apply.py` | `modoverrides` 追加固定桥接项；`setup_script` 不追加；保护/回滚快照排除桥接 key |
| `remote-scripts/actions/restore-task.py` | 只替换世界存档；保留 bridge/Compose/overrides/config save key，补测试断言 |
| `remote-scripts/actions/instance-delete.py` | 无需额外路径；删除实例根目录即包含 bridge；确认预览接受 bridge 文件的常规权限 |
| `remote-scripts/actions/update-task.py` | 更新验证阶段读取桥接状态；失败只 warning |
| `src-tauri/src/instance_creation.rs`、`lib.rs`、`commands.rs` | 创建流程/命令注册/任务操作枚举 |
| `src/types/contracts.ts`、`src/api/players.ts`、`src/router.ts` | 类型、API、路由/侧栏 |

## 7. 读取玩家列表

### 7.1 流程

```text
get_instance_players(target)
  │
  ├─ 校验 target、hostId、connection、expectedRecordVersion
  ├─ 读取实例 manifest：playerBridge.enabled?
  │    ├─ 否 → 返回 enabled=false, players=[], onlineCount=null
  │    └─ 是
  ├─ 读取 config/Cluster_1/Master/save/hungerhub_bridge_state、Caves 同路径
  │    ├─ 缺失 → 该分片 unavailable
  │    ├─ JSON/schema/size/路径不合法 → 该分片 invalid
  │    └─ capturedAt 超过阈值 → stale
  ├─ 合并有效分片
  │    ├─ 按 userId 去重
  │    ├─ 重复时保留 capturedAt 较新的记录
  │    ├─ 同时间则优先 Caves，并返回 PLAYER_SHARD_AMBIGUOUS warning
  │    └─ 按名称/分片稳定排序
  └─ 返回快照、onlineCount、桥接健康、警告
```

### 7.2 新鲜度

| 状态 | 条件 | UI |
| --- | --- | --- |
| `fresh` | 最近快照 ≤ 30 秒 | 正常显示“更新于 …” |
| `stale` | 30–120 秒 | 显示旧时间和“可能已过期”，允许刷新 |
| `unavailable` | >120 秒或缺失/损坏 | 显示不可用，不显示 0 人；可提供“检查桥接” |
| `disabled` | manifest 未启用桥接 | 显示未启用和启用入口 |
| `serverStopped` | 运行状态 stopped | 显示“专服未运行”，不读取旧名单当在线 |

`onlineCount` 只在至少一个分片 fresh/stale 时给出；全部不可用为 `null`，不是 0。

### 7.3 角色映射

- 后端返回原始 `character` prefab（如 `wilson`、`willow`、`wx78`、`wathgrithr`）。
- 前端 `src/lib/characters.ts` 维护基础角色中文名映射；未知/modded prefab 直接显示 prefab，并用 Tooltip 说明“MOD 角色”。
- 不根据中文名反推角色；不把角色名写死在后端业务逻辑。
- 玩家名称按纯文本渲染，转义 HTML；不拼接到 HTML 字符串。

## 8. 踢出（M0 阻断，v1 不实现）

> M0 实测 TheSim 命令注入不可用。v1 页面只读，不显示踢出按钮，不注册 `kick_instance_player`。本节保留为后续通道设计。

### 8.1 流程

```text
用户点击“踢出”
  ├─ 确认弹窗：玩家名称、角色、分片、“踢出后玩家可立即重新加入”
  ├─ 校验 target/revision/snapshot/expectedName
  ├─ 生成 requestId/operationId
  ├─ 向 Master 和 Caves 各写同一个 hungerhub_bridge_command key（覆盖迁移中的情况）
  ├─ 等待结果窗口（8–10 秒）
  │    ├─ 任一 kicked → 成功；刷新名单
  │    ├─ 全部 notFound → 未找到；刷新名单
  │    ├─ expired/failed → 失败；显示原因
  │    └─ 超时 → 结果未知；不显示成功；提示刷新/重试
  └─ 返回 KickResult；hungerhub_bridge_result 保留最近一次结果供 requestId 匹配的幂等重试
```

结果聚合规则：

- 任一 `kicked` → `kicked`；
- 全部 `notFound` → `notFound`；
- 有 `failed` 且无 `kicked` → `failed`；
- 全部 `expired`/超时 → `expired`/`unknown`；
- 不得把部分成功或超时汇总成 `kicked`。

为什么同时发给两个分片：

- 玩家可能正在 Master/Caves 迁移，快照里的 shard 已过期。
- 只有一个分片会找到该 userId；另一个返回 notFound，不会误踢。
- 如果两个都找到（异常重叠），只执行一次即可；结果取 `kicked`。

踢出语义：

- 只断开当前连接，不写 blocklist，不封禁；玩家可立即重新加入。
- 不删除玩家存档、不修改角色、不踢出其他玩家。
- 不提供“原因”字段；避免把自由文本写进游戏侧。
- 确认按钮文案用“踢出”，不用“封禁”。

### 8.2 结果与幂等

- `KickResult.result`：`kicked`、`notFound`、`expired`、`failed`、`unknown`。
- `unknown` 表示等待窗口内没有收到有效结果；`retryable=true`；UI 不显示成功。
- 同一 `operationId` 重试：若 `hungerhub_bridge_result` 已存在且 requestId 匹配，返回原结果；不重复写命令。
- 固定 `hungerhub_bridge_result` 只保留最近一次结果；新的踢出会覆盖它。超时后不把迟到结果改写成成功；下一次刷新名单会反映真实在线状态。
- 若踢出后玩家立即重连，名单刷新会重新出现；UI 不把“仍在线”当踢出失败，提示“玩家已重新加入”。

### 8.3 结果写入与隐私

- `kick_instance_player` 是短写入，不创建持久任务，也不进入任务事件；如果写入通用 operation 索引，只记录 target、operationId、枚举结果和 userId 的必要校验，不记录名称。
- 结果文件只保留 requestId、枚举、`nameMatched`、时间；不写玩家名称正文、聊天或世界信息。
- 踢出结果不写诊断包、日志或 SQLite；页面刷新后以新的在线名单为准。

### 8.4 失败与安全

- target/revision 变化：拒绝，要求刷新名单后重试。
- 桥接不可用/过期：允许踢出但必须警告；命令过期后返回 `expired`，不猜测。
- userId 格式非法/长度超限：命令不写。
- 名称不匹配：按 userId 踢出，结果 `kicked` + `nameMatched=false`，UI 提示“名称已变化”；不因改名阻止管理员操作。
- 不记录玩家名称到任务事件/日志；短写入响应只保留 userId（用于确认）和枚举结果。

## 9. 接口

### 9.1 命令

| command | 类型 | 输入 | 返回 |
| --- | --- | --- | --- |
| `get_instance_players` | Q | target、expectedRecordVersion | `InstancePlayers` |
| `preview_instance_player_bridge` | P | target、expectedRecordVersion、expectedRevision | `PlayerBridgePreview`（重开影响、当前版本、阻断项；桥接版本由后端固定，不收前端 digest） |
| `apply_instance_player_bridge` | T | target、expectedRecordVersion、expectedRevision、operationId | `TaskSnapshot`（`playerBridgeApply`）；apply 时重新校验 revision/运行状态/路径，不依赖 preview 的 planId |
| `get_instance_player_bridge_task` | Q | target、expectedRecordVersion、operationId | `TaskSnapshot`；用于断线后按 operationId 对账 |
| `list_instance_player_bridge_tasks` | Q | target、expectedRecordVersion | 有界任务列表；不返回玩家数据 |
| `kick_instance_player` | M | target、expectedRecordVersion、expectedRevision、userId、expectedName、expectedShard、operationId | M0 阻断；v1 不注册，保留为后续新通道设计 |

`playerBridgeApply` 是新的 `TaskOperation` 枚举值；必须同步 Rust enum、前端类型、契约阶段表、fixture。阶段建议：

```text
queued → preflight → stop → publish → start → verify → finalize
```

失败进入 `rollback`（恢复旧 Compose/manifest/overrides 并恢复原运行状态）。不需要 `backup` 阶段，因为不改世界数据；但预览要明确“将重开分片”。

### 9.2 InstancePlayers

查询沿用现有实例查询的 envelope + data 拆分，避免与 `QueryResponse.freshness` 冲突：

```ts
interface InstancePlayersResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  recordVersion: number;
  snapshotId: string;
  capturedAtUnixSeconds: number;
  configRevision: number;
  data: InstancePlayers;
}

interface InstancePlayers {
  capturedAt: string | null;
  freshness: "fresh" | "stale" | "unavailable" | "disabled";
  runtimeStatus: "running" | "stopped" | "unknown";
  bridge: {
    enabled: boolean;
    version: string | null;
    digest: string | null;
    installedAt: string | null;
    shards: Array<{
      shard: "master" | "caves";
      capturedAt: string | null;
      freshness: "fresh" | "stale" | "unavailable" | "invalid";
      error: string | null;
    }>;
  };
  players: Array<{
    userId: string;
    name: string;
    character: string;       // prefab
    shard: "master" | "caves";
  }>;
  onlineCount: number | null;
  warnings: string[];
}
```

字段语义：

- 外层 `freshness` 仍是查询信封的 `fresh|stale`；`data.freshness` 是玩家名单自己的状态，两者不混用。
- `runtimeStatus` 只由桥接状态推导：有 fresh 状态时为 `running`，否则 `unknown`；不读取创建时写入、之后不更新的 `runtime.json`。页面按 `freshness` 决定显示名单、“暂无玩家数据”或“未启用”，不显示“服务器未运行”的假状态。
- `freshness=disabled` 表示 manifest 未启用桥接；`unavailable` 表示已启用但所有分片都不可用；`stale` 至少有一个分片在 30–120 秒内。
- `onlineCount` 只在至少一个分片 fresh/stale 时给出；全部不可用时为 `null`，不是 0。
- 不返回 `runtimePlayerCount`；A2S 人数继续只在运行摘要中出现，不能替代名单。
- `capturedAt` 取所有有效分片中最新的 `capturedAt`；无有效快照时为 `null`。
- `PLAYER_BRIDGE_DISABLED` 在查询中是 `freshness=disabled` 的正常数据；只有踢出命令才把它当错误。
- 单个玩家条目非法时跳过并返回 `PLAYER_ENTRY_INVALID` warning；文件级 schema/大小/路径非法才把该分片标为 `invalid`。

### 9.3 KickResult

```ts
interface KickResult {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  userId: string;
  expectedName: string;
  result: "kicked" | "notFound" | "expired" | "failed" | "unknown";
  nameMatched: boolean | null;
  shardResults: Array<{
    shard: "master" | "caves";
    result: "kicked" | "notFound" | "expired" | "failed" | "timeout";
  }>;
  capturedAt: string;
  retryable: boolean;
}
```

### 9.4 错误

| 错误码 | 场景 |
| --- | --- |
| `PLAYER_BRIDGE_DISABLED` | 未启用桥接；查询返回 `freshness=disabled` 的正常数据，踢出时返回此错误 |
| `PLAYER_BRIDGE_UNAVAILABLE` | 目录/文件缺失、MOD 未加载 |
| `PLAYER_BRIDGE_INVALID` | 状态 JSON/schema/大小/路径不合法 |
| `PLAYER_BRIDGE_STALE` | 快照过期（查询可成功但带 warning；踢出需确认） |
| `PLAYER_BRIDGE_INSTALL_FAILED` | 启用任务安装/启动失败 |
| `PLAYER_BRIDGE_ROLLBACK_FAILED` | 启用失败且恢复原状态失败 |
| `PLAYER_BRIDGE_VERSION_UNSUPPORTED` | 桥接版本与当前应用不兼容 |
| `PLAYER_NOT_FOUND` | 目标 userId 不在线 |
| `PLAYER_KICK_TIMEOUT` | 等待结果超时，结果未知 |
| `PLAYER_KICK_FAILED` | MOD/服务端 API 失败 |
| `PLAYER_KICK_CONFLICT` | target/revision/快照变化或重复 operationId 摘要不一致 |
| `PLAYER_INPUT_INVALID` | userId/name/长度/字符非法 |

## 10. 界面

新增实例页路由 `/hosts/:serverId/instances/:instanceId/players`，侧栏“玩家”。

```text
玩家
┌──────────────────────────────────────────────────────────────┐
│ 在线 3 / 6         更新于 17:42:10                  [刷新]    │
├──────────────────────────────────────────────────────────────┤
│ 玩家名称        角色          分片                            │
│ 小明            威尔逊        主世界                          │
│ 阿狸            薇洛          洞穴                            │
│ …                                                            │
└──────────────────────────────────────────────────────────────┘
```

规则：

- v1 **只读**：不显示踢出按钮，不注册 `kick_instance_player`。踢出在 M0 实测命令通道阻断，延后另行设计。
- 标题区只显示真实计数和时间；不显示常驻说明。
- 列：玩家名称、角色、分片。窄屏 960 下不水平滚动；长名称截断 + Tooltip。
- 角色显示中文名；未知 prefab 显示原值 + “MOD 角色”。
- 玩家名称为空时显示 userId 后 6 位，不显示空白行。
- 空状态分情况：
  - 未启用：显示“玩家列表未启用” + “启用（将重启分片）”；
  - 停止：显示“专服未运行”；
  - 无玩家：显示“暂无在线玩家”；
  - 不可用：显示错误 + 刷新/检查桥接；
  - 过期：显示旧时间和“可能已过期”，不显示“0 人”。
- 自动刷新：页面可见且实例运行中每 15 秒；隐藏暂停；手动刷新替换请求序号；旧响应丢弃。
- 离开页面不保留玩家名单到其他实例；不写入草稿/本地持久化。
- 任务栏显示“启用玩家列表”/“更新玩家桥接”，不显示玩家名称。
- 键盘/焦点/Escape 与亮暗主题、960×680/1480×920 按现有规范验收。

## 11. 安全与隐私

- 玩家名称、userId 只在远端桥接文件、当前查询响应和当前页面内存中存在。
- 不写 SQLite、不写日志、不写任务事件、不写诊断包、不写备份归档。
- 不把玩家名称拼进 shell/SQL/HTML；HTML 用文本插值，前后端都做长度/控制字符校验。
- 桥接文件路径固定，拒绝 `..`、符号链接、非普通文件、超大文件。
- 命令只允许固定 action 和 userId；不执行任意 Lua、控制台字符串或宿主命令。
- 桥接 MOD 与游戏同权限运行；它能读到自己目录，但不构成对宿主的额外隔离边界。安装用户 MOD 本来就拥有服务端代码执行能力，文档不宣称桥接能防御恶意 MOD。
- 踢出不等于封禁；不写 blocklist，不修改 `adminlist/whitelist/blocklist`。
- 删除实例/禁用桥接时清理桥接目录，避免残留玩家数据。

## 12. 难度与工作量

综合难度：**8/10（高）**。代码量不是最大，最大的是 DST MOD 行为不确定、真实客户端验收和 Compose/MOD 交叉改动。

| 工作项 | 人日 |
| --- | --- |
| M0 技术验证（MOD 加载/io/名单/踢人/洞穴） | 3–5 |
| 桥接 Lua MOD、文件协议、清理/限额 | 3–4 |
| 桥接安装/启用任务、Compose/modoverrides/恢复整合 | 3–5 |
| 玩家读取探针、合并/去重/新鲜度、Rust DTO/command | 2–3 |
| 踢出命令、结果等待/幂等/超时 | 2–3 |
| 玩家页、角色映射、状态/确认/刷新 | 1–2 |
| fixture、Rust/前端测试、真实客户端矩阵 | 3–5 |
| 合计 | 17–27 人日 |

估算包含 M0、桥接失败返工和真实客户端调试。M0 不通过时，本功能应停止，而不是降低验收标准。

### 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| DST MOD API/行为与文档不符 | 无法取名单/踢人 | M0 实机验证；失败即停 |
| `io` 限制导致不能写状态 | 无数据 | 挂在 `mods/` 下；M0 验证；备选 `SetPersistentString` |
| 本地 MOD 加载/启用方式不符 | MOD 不生效 | M0 验证两种目录/overrides 写法 |
| 踢人 API 不可用 | 无法踢出 | M0 验证；不做假成功；可拆分为“只读名单”先行 |
| 洞穴迁移重复/漏人 | 名单不准 | 双分片合并 + userId 去重 + 时间优先；实机迁移测试 |
| 桥接状态过期 | 误踢/空列表 | capturedAt + freshness；踢出命令过期；刷新 |
| Compose/MOD 流程覆盖 overrides | 桥接被关 | 统一生成函数；恢复/世界/MOD 应用逐项回归 |
| 重开分片影响玩家 | 体验中断 | 明确预览/确认；不在繁忙时自动执行 |
| 玩家名称/ID 泄露 | 隐私 | 不落盘/日志/事件/诊断/备份；有界响应 |
| 桥接与用户 MOD 冲突 | 启动失败 | 固定唯一名称；M0 共存测试；失败回滚 |

## 13. 测试与验收

### 自动化

- `scripts/test-player-bridge-files.py`：状态/命令/结果 schema、大小、UTF-8、控制字符、符号链接、过期、版本；M0 辅助脚本的挂载插入、modoverrides 追加、原子写和安装/卸载。
- `docs/player-bridge-m0.md`：真实主机 10 项检查、隐私要求、清理回滚和停用条件。
- `scripts/test-player-list.py`：Master/Caves 合并、去重、迁移重叠、fresh/stale/unavailable、部分分片失败、排序、空列表、未启用。
- `scripts/test-player-kick.py`：命令写入、双分片、kicked/notFound/expired/failed/timeout、nameMatched 变化、幂等重试、target/revision 变化。
- `scripts/test-player-bridge-apply.py`：启用任务阶段、Compose/overrides 写入、权限、失败回滚、启动验证、重复 operationId。
- `scripts/test-instance-release.py`/`test-mods-apply.py`/`test-restore-task.py` 扩展：桥接挂载/overrides 不被覆盖。
- Rust：DTO、命令注册、目标校验、响应身份、错误映射、玩家数据不落日志（测试敏感样本不出现在事件/诊断）。
- 前端：query guard、15 秒刷新旧响应丢弃、角色映射回退、踢出结果 unknown 不显示成功、空/未启用/过期状态。

### 真实主机/客户端矩阵

| 场景 | 期望 |
| --- | --- |
| 新建实例 | 桥接随实例创建；启动后 30 秒内玩家页可读 |
| 已有实例启用 | 预览显示重开影响；任务完成后玩家页可读；用户 MOD 不变 |
| 玩家加入/离开 | 10 秒内列表增减；名称/角色正确 |
| 玩家换角色 | 列表角色更新；不需要重连 |
| 玩家下洞/回地面 | Master/Caves 合并为一条；不重复 |
| 踢出在线玩家 | 5 秒内断开；结果 kicked；player 可立即重连 |
| 踢出已离线 | notFound；列表刷新 |
| 踢出时正在迁移 | 双分片命令；最终 kicked 或 notFound，不误踢他人 |
| 桥接未加载/崩溃 | 页面不可用，不显示 0 人；游戏本身仍运行 |
| 服务器停止 | 页面显示未运行；旧快照不当在线 |
| 服务器重启 | 桥接恢复；旧命令不执行 |
| 客户端无 MOD | 可正常加入；不要求客户端安装 |
| 中文/Unicode/长名称 | 正常显示/踢出；不崩溃/不截断 userId |
| 更新 DST 后 | 桥接仍工作；失败只 warning，不误判更新失败 |
| 恢复备份后 | 桥接设置保留，状态重建；不恢复旧玩家名单 |

### UI 验收

- 亮/暗主题；960×680/1480×920；长名称、长角色 prefab、空状态、过期、未启用、错误。
- 键盘 Tab/Enter/Escape；踢出确认焦点锁定；关闭后回到原行。
- 不把 `unknown` 显示成成功；不显示“0 人在线”当不可用。
- 玩家页不常驻实现说明；只在条件成立时显示风险/错误。

## 14. 开放问题

1. 新建实例默认启用桥接：**已确认**；已有实例需显式启用。
2. 是否提供“只读名单、不支持踢出”的降级模式？**已采用**：M0 证明名单可用、运行中命令通道不可用；v1 只读，踢出延后，页面不显示可点击的假踢出。
3. 玩家名称是否允许在本地短期缓存以减少 SSH 查询？**已确认不持久化**；页面内存缓存随上下文失效，离开页面清理。
4. 是否需要显示管理员标记？本规划不包含；`isAdmin` 的 DST API 需要额外验证，且不是用户要求。
5. 桥接是否需要支持关闭？v1 只做启用/更新；关闭可在后续加入，需同样处理 Compose/overrides/重开。
6. 是否允许踢出处于 `stale` 快照中的玩家？本规划允许但要求确认；超时/未找到不当作成功。
