# 玩家桥接 M0 验证手册

- 状态：已完成真机验证（2026-11-17）；只读玩家列表、创建默认桥接、已有实例安装和玩家页已接入；踢出未实现
- 关联：[玩家列表设计](../spec/player-list-plan.md)
- 目的：在接入后端、玩家页和踢出之前，验证服务端桥接 MOD、文件协议、Compose 挂载和踢人 API。

## M0 结论（2026-11-17）

**只读玩家列表：通过。** `GLOBAL.TheSim:SetPersistentString`/`GetPersistentString` 可用；状态写到 `config/Cluster_1/<Shard>/save/hungerhub_bridge_state`，宿主可读取；文件格式为 `KLEI     1 ` + JSON；玩家加入/离开后 state 在 5 秒内更新。

**运行中踢出命令：阻断。** 宿主写入 `hungerhub_bridge_command` 后，MOD 的 `GetPersistentString` 仍返回启动时的空占位（`commandReadOk=true commandReadLen=0`），读不到宿主后来覆盖的 JSON。之前一次成功踢出是 command 在 MOD 首次读取之前就存在（遗留命令），不是运行中注入。因此：

- 不实现运行中踢出 command；页面 v1 只读。
- 踢出需要新的命令通道（不在本轮范围）。
- 不显示假踢出，不把 `unknown` 当成功。

**其他结论：**

- DST modmain 直接 `io` 为 nil；`GLOBAL.io` 能读 MOD 目录，但写 MOD 目录返回 `No such file or directory`，不能作为状态/命令通道。
- `TheSim` 的 state key 可被宿主读取；`mods-apply` 的保护/回滚快照必须排除 `save/hungerhub_bridge_*`。
- 反复 `docker compose up --force-recreate` 会在 Steam 服务器列表留下旧 session，表现为“多个世界”或加入后几秒断开；测试应使用直连当前服务器端口，或等旧 session 过期。
- 服务器无玩家时会暂停，state 停止更新；页面必须依赖 freshness，不能把旧 `players=1` 当在线。

## 1. 门禁与范围

M0 只使用一个一次性测试实例。验证通过前：

- 不注册 `get_instance_players` / `kick_instance_player` / 桥接启用 command。
- 不修改 Rust、Vue、创建流程、Compose 渲染或正式任务。
- 不在文档、日志、任务事件、诊断包或备份中记录玩家名称/userid。

禁止在生产实例上执行本手册。M0 会重开分片，在线玩家会断开。

## 2. 前置条件

- Linux amd64 主机，已安装 Docker/Compose，且有一个可启动的测试专服实例。
- 一个真实 DST 客户端；另准备一个未安装任何 MOD 的 vanilla 客户端。
- 测试实例至少有 Master 分片；需要验证洞穴时启用 Caves。
- 主机上可用 `/usr/bin/python3`（标准库即可）。
- 测试期间不要使用应用内的“启动/停止/MOD 应用”操作，避免与应用管理的 `compose.yaml` 交叉。

记录以下环境信息（不含玩家名/userid）：

- 主机发行版/内核/架构。
- `docker version`、`docker compose version`。
- 测试实例的 `releaseId`、DST build、image digest。
- 客户端版本和是否安装 MOD。
- 本仓库 commit / 桥接文件 digest。

## 3. 复制并安装

在开发机执行：

```bash
scp -r remote-scripts/player-bridge scripts/player-bridge-m0.py root@<host>:/tmp/
```

在主机上先做只读预览：

```bash
python3 /tmp/player-bridge-m0.py install \
  --instance /var/lib/famine-hub/instances/<instanceId> \
  --source /tmp/player-bridge \
  --allow-existing-mods \
  --dry-run
```

`--allow-existing-mods` 只在测试实例已有用户 MOD 时使用；没有用户 MOD 时去掉该参数。预览会打印 `compose.m0.yaml` 和桥接 digest；确认挂载行和分片正确后执行安装：

```bash
python3 /tmp/player-bridge-m0.py install \
  --instance /var/lib/famine-hub/instances/<instanceId> \
  --source /tmp/player-bridge \
  --allow-existing-mods
```

如果已经安装过，`install` 会返回 `M0_ALREADY_INSTALLED`；先执行本文的 `uninstall` 再重新安装。

安装会：

- 创建 `<instance>/bridge/<Master|Caves>`（0700、owner 1000），只放桥接代码；状态/命令/结果使用 `config/Cluster_1/<Shard>/save/` 下的 TheSim key 文件。
- 复制 `modinfo.lua` / `modmain.lua`（root:root 0644）。
- 备份并修改 `config/Cluster_1/<Shard>/modoverrides.lua`，加入 `["hungerhub_bridge"]={enabled=true}`。
- 生成 `<instance>/compose.m0.yaml`；**不修改** `<instance>/compose.yaml`。
- 写 `<instance>/bridge/.m0-install.json` 标记。

按输出中的 `start:` 命令重开分片：

```bash
docker compose -p <composeProject> -f /var/lib/famine-hub/instances/<instanceId>/compose.m0.yaml up -d --force-recreate
```

## 4. 检查挂载与日志

列出容器：

```bash
docker ps --filter label=com.hungerdesolate.instanceId=<instanceId> --format '{{.Names}}'
```

检查每个分片的挂载（source 应为实例 `bridge/<Shard>`，target 为 `/srv/dst/mods/hungerhub_bridge`，RW=true）：

```bash
docker inspect --format '{{json .Mounts}}' <containerName> | python3 -m json.tool
```

查看桥接日志（只保留桥接前缀，避免带出游戏加入/离开日志中的玩家名）：

```bash
docker logs <containerName> 2>&1 | grep '\[hungerhub_bridge\]' | tail -50
```

读取状态：

```bash
python3 /tmp/player-bridge-m0.py status --instance /var/lib/famine-hub/instances/<instanceId>
```

需要人工核对名称时使用 `--names`；不要把输出粘贴到记录中：

```bash
python3 /tmp/player-bridge-m0.py status --instance /var/lib/famine-hub/instances/<instanceId> --names
```

状态/结果 key 文件由游戏通过 `TheSim:SetPersistentString` 写入，实际权限取决于游戏进程的 umask；期望为 0644 或 0600（非 group/world-writable）。命令 key 由辅助脚本写入。若实际是 0664/0666，记录实际权限并停止：实例删除预览会拒绝 group/world-writable 文件。MOD 不再使用 `io.open` 写文件。

## 5. 十项检查

| # | 检查 | 步骤 | 通过标准 |
| --- | --- | --- | --- |
| 1 | MOD 加载 | 重开后看两个分片日志；客户端进入 | 日志有 `[hungerhub_bridge] started version=... shard=...`；无 Lua 加载错误；客户端不需要桥接 MOD |
| 2 | 挂载与写入 | `docker inspect` 核对挂载；`status`；`find <instance>/bridge -type f`；`find <instance>/config/Cluster_1/<Shard>/save -name 'hungerhub_bridge_*'` | 挂载指向 `bridge/<Shard>`；`save/hungerhub_bridge_state` 生成；MOD 不写其他路径 |
| 3 | 玩家名单 | 客户端加入、离开、换角色；每次等待 ≤10 秒后 `status --names` | `save/hungerhub_bridge_state` 有 `userId/name/character`；加入/离开/换角色在 10 秒内反映 |
| 4 | 分片独立 | 地面加入后 `status`；下洞/回地面后再次 `status` | Master/Caves 各自写独立 state key；迁移期间可能短暂同时出现，记录实际重叠窗口 |
| 5 | 踢出 | `kick --user-id <id> --shard both`；再用离线 userId 和 `--expired` 各测一次 | 在线玩家 5 秒内断开，结果 `kicked` 且记录 `api`；离线返回 `notFound`；过期返回 `expired` 且玩家仍在线 |
| 6 | 原版客户端 | 用未安装任何 MOD 的客户端加入 | 可正常加入和游戏；不需要客户端安装桥接 |
| 7 | 重启恢复 | 停止再启动分片；等待 10 秒；`status` | 重启后继续写 state key；重启前写入的命令不执行（`expired` 或不产生结果） |
| 8 | Unicode/长名称 | 用中文、空格、长名称加入；`status --names` | JSON 有效；名称显示正常或安全截断；不崩溃 |
| 9 | 禁用桥接 | 停分片；暂时移除 modoverrides 中的桥接项（或执行 `uninstall`）；用原 compose 启动 | 游戏正常启动；不产生假名单；玩家页未接入时无影响 |
| 10 | MOD 共存 | 在已有至少一个常用服务端 MOD 的测试实例上 `--allow-existing-mods` 安装 | 游戏正常启动；用户 MOD 与桥接同时加载；记录 MOD 名称/版本（不含玩家数据） |

补充验证：

- 实例命令包含 `-ugc_directory /srv/ugc` 时，本地 MOD 仍从 `/srv/dst/mods` 加载。
- 若 `docker compose ... up` 报 `failed to create mountpoint ... /srv/dst/mods/hungerhub_bridge: read-only file system`，说明共享 `/srv/dst` 以只读方式挂载，Docker 无法在只读父挂载下创建子挂载点。先在宿主共享 dst 根目录预建空目录，再重试：

```bash
release=$(python3 -c 'import json; print(json.load(open("/var/lib/famine-hub/instances/<instanceId>/active-release.json"))["dstReleaseId"])')
mkdir -p "/var/lib/famine-hub/dst/$release/mods/hungerhub_bridge"
ls -ld "/var/lib/famine-hub/dst/$release/mods/hungerhub_bridge"
```

  如果实例没有 `active-release.json`，改用 `manifest.json` 中的 `dstReleaseId`。空目录本身不会启用 MOD；只有 `compose.m0.yaml` 的 bridge 挂载覆盖它之后才会加载。重试前确认 `compose.m0.yaml` 中存在 `/srv/dst:ro` 和 `hungerhub_bridge:rw` 两条挂载。
- 这个前置条件必须记录到 M0 结论中；生产实现需要在渲染 Compose 或安装桥接前创建该空挂载点，或改用其他挂载策略。
- 若预建空目录后仍失败，记录错误；不要继续页面/踢出实现，评估 `TheSim:SetPersistentString` 备选方案或调整挂载点。

### TheSim key 协议（M0 已确认）

M0 实测结果：

- DST modmain 直接 `io` 为 nil；`GLOBAL.io` 能读 MOD 目录，但写 MOD 目录返回 `No such file or directory`，不能作为状态/命令通道。
- `GLOBAL.TheSim` 是 userdata，`SetPersistentString` / `GetPersistentString` 可用；写入后回读成功。
- `TheSim` 按 key 写独立文件：

```text
<instance>/config/Cluster_1/<Shard>/save/hungerhub_bridge_state
<instance>/config/Cluster_1/<Shard>/save/hungerhub_bridge_command
<instance>/config/Cluster_1/<Shard>/save/hungerhub_bridge_result
```

文件格式为 `KLEI     1 ` + 数据（11 字节头部），宿主可以直接读写。状态/结果由 MOD 写，命令由辅助脚本写；`TheSim` 读回调返回头部之后的数据。

因此 MOD 不再使用 `io.open` 写任何文件；桥接目录只保留 `modinfo.lua` / `modmain.lua` 代码。状态/命令/结果在 config 挂载下，不进入普通世界备份，但 `mods-apply` 的保护快照必须排除 `save/hungerhub_bridge_*`。

日志应出现：

```text
[hungerhub_bridge] loaded version=1.0.0 mode=thesim
[hungerhub_bridge] started version=1.0.0 shard=Master
```

## 6. 踢出命令细节

辅助脚本把命令写为 `save/hungerhub_bridge_command`（带 `KLEI     1 ` 头），MOD 用 `GetPersistentString` 读取；结果写到 `save/hungerhub_bridge_result`，辅助脚本读取并校验 requestId：

```bash
python3 /tmp/player-bridge-m0.py kick \
  --instance /var/lib/famine-hub/instances/<instanceId> \
  --user-id <KU_...> \
  --shard both
```

省略 `--expected-name` 时，辅助脚本从当前 state key 读取该 userId 的名称，不打印名称。`--expired` 写入一个已过期命令，用于验证 MOD 不执行旧命令。命令在收到结果或超时后由辅助脚本删除，避免重启后重复执行。

结果含义：

- `kicked`：MOD 找到了 userId 并调用了踢人 API；`api` 记录实际成功的候选 API。客户端是否断开仍以真实客户端为准。
- `notFound`：当前分片没有该 userId。
- `expired`：命令已过期或早于 MOD 启动时间。
- `failed`：候选踢人 API 全部失败。
- `unknown`：等待窗口内没有有效结果；不能当作成功。

## 7. 清理与回滚

先停掉使用 `compose.m0.yaml` 的容器，避免在桥接文件被删除后继续运行：

```bash
docker compose -p <composeProject> -f /var/lib/famine-hub/instances/<instanceId>/compose.m0.yaml stop
```

执行清理：

```bash
python3 /tmp/player-bridge-m0.py uninstall \
  --instance /var/lib/famine-hub/instances/<instanceId>
```

清理会恢复 `modoverrides.lua` 备份、删除 `bridge/`、`compose.m0.yaml`，以及 `config/Cluster_1/<Shard>/save/` 下的 `hungerhub_bridge_*` key 文件（其中 state 含玩家名单，必须删除）。随后按输出中的 `cleanup:` 命令用原 `compose.yaml` 重建分片：

```bash
docker compose -p <composeProject> -f /var/lib/famine-hub/instances/<instanceId>/compose.yaml up -d --force-recreate
```

确认应用内“启动专服”可以正常使用原 compose，且不再出现桥接 MOD。

## 8. 记录模板

```markdown
## 玩家桥接 M0 记录

- 日期：
- 主机/内核/架构：
- Docker / Compose：
- Release ID / DST build / image digest：
- 客户端版本（含 vanilla 客户端）：
- 桥接 digest：
- 测试实例 ID（脱敏）：
- 是否启用洞穴：
- TheSim 能力、key 文件路径和 KLEI 头格式：

| # | 检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | MOD 加载 | 通过/失败 | 日志摘要（脱敏） |
| 2 | 挂载与写入 | 通过/失败 | inspect 摘要/路径 |
| 3 | 玩家名单 | 通过/失败 | 延迟秒数/字段 |
| 4 | 分片独立 | 通过/失败 | 重叠窗口 |
| 5 | 踢出 | 通过/失败 | 结果枚举/api |
| 6 | 原版客户端 | 通过/失败 | 客户端说明 |
| 7 | 重启恢复 | 通过/失败 | 行为摘要 |
| 8 | Unicode/长名称 | 通过/失败 | 截断/显示说明 |
| 9 | 禁用桥接 | 通过/失败 | 启动结果 |
| 10 | MOD 共存 | 通过/失败 | MOD 名称/版本 |

- 阻断项与错误码：
- 备注/后续调整：
```

**隐私要求**：记录、截图、日志摘录中不得出现玩家名称、Klei userid、聊天或世界种子。日志中的加入/离开行请只保留时间与桥接前缀，或直接打码。

## 9. 停止条件

出现以下任一情况，停止后续实现并记录阻断原因：

- MOD 不加载或 Lua 报错。
- `TheSim:SetPersistentString` 不能写 state/result key，或宿主读不到 `config/Cluster_1/<Shard>/save/` 下的 key 文件。
- 宿主写 command key 后，MOD 的 `GetPersistentString` 读不到新内容（启动缓存）。
- `AllPlayers` 拿不到可靠的 `userid/name/prefab`。
- 嵌套 rw 挂载在只读 `/srv/dst` 下不可行，且预建目标目录仍失败。
- 启用 `-ugc_directory` 后本地 MOD 不加载。
- 玩家名称/ID 进入日志、任务、诊断或备份。

仅踢人 API 不可用时，不停止整个功能：后续先交付只读玩家列表，不注册假踢出。M0 记录必须明确写出“名单可用、踢出不可用”。
