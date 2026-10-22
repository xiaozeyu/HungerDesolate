# MOD 管理实现分析与建议

- 分析日期：2026-10-21。
- 状态：设计建议，未注册新 command，未改变生产行为；文中的新 DTO、目录和限额均为拟议值。
- 范围：DST（饥荒联机版）服务端 Workshop MOD；沿用 Vue/Rust、SSH 与 Docker Compose 路线。
- 依据：当前工作区、只读核对的旧项目 `../Famine-ECS`、Steam 官方文档和游戏 Lua 脚本镜像。代码完成、实验通过与发布放行分别记录。

## 1. 结论与当前边界

下一步开发 MOD 管理符合现有 C3 排期。应先实现获取、产物准备、配置求值、期望配置保存，再开放影响现有世界的应用和更新。

最关键的修正是：`modinfo.lua` 是 Lua 程序，`configuration_options` 是程序执行后得到的表；它不是只能静态读取的声明文件。游戏也有配置约定，并非“完全没有规范”。作者可以在约定字段之外，用局部函数、循环、条件和字符串运算生成这些字段。自行实现一部分 Lua 语法并持续补规则，很难达到游戏的语义覆盖。

推荐采用：官方来源获取内容 → 固定实例产物 → 在隔离进程内按目标游戏的 `modinfo` 环境求值 → 输出有类型的配置 schema → 保存期望配置 → 冷备份和可恢复发布 → 游戏运行验证。静态分析用于诊断与数据文件导入，不再作为猜测最终配置的权威来源。

本轮发现的实际前置缺口：

| 当前实现 | 对 MOD 开发的影响 |
| --- | --- |
| `ModsPage.vue` 仍为预览，未实现 MOD 后端 | 需要完整领域实现，不能把禁用按钮直接接到示例状态 |
| 旧项目已经有 lexer/parser/evaluator、mlua 沙箱和 legacy 合并 | 问题不止是“原来用正则”；需要修正环境、语义和结果投影 |
| `backup-task.py` 只采集世界数据与集群配置，`modsReleaseId` 仍为空、`restorability` 为 unknown | 有可校验压缩包不代表可恢复 MOD 组合；正式发布前补产物引用与恢复能力 |
| `instance-release.py` 用 DST releaseId 选择可写 UGC 目录 | 要拆开 DST 与 MOD 发布身份，避免升级游戏时换到空 MOD 目录 |
| 世界 desired 的 revision 必须等于全局 configRevision；运行摘要也以 revision 判待应用 | 保存 MOD 后只提升 manifest 会破坏世界读取；按领域 digest 判差异前必须完成兼容改造 |

现有文档 §9.3 的“不执行 modinfo.lua 任意代码”保持为禁止获得宿主能力的安全边界。本文提出受限 Lua 求值，会改变原有“仅安全静态解析”的实现解释；正式开发时应同步修订后端实施与接口契约，明确允许的纯计算、隔离和拒绝条件。本分析不静默修改既有规范。

## 2. 饥荒如何读取配置

### 2.1 三类文件的职责

| 文件 | 职责 | 管理器的处理方式 |
| --- | --- | --- |
| MOD 中的 `modinfo.lua` | 元数据、兼容标记、配置定义及默认值 | 读取固定产物中的原始字节，在兼容环境内求值；不修改作者文件 |
| 下载环境中的 `dedicated_server_mods_setup.lua` | 声明需获取的 Workshop 条目 | 由受控程序按 ID 白名单生成；它不代表启用或已经下载成功 |
| 每分片的 `modoverrides.lua` | 声明启用和配置覆盖值 | 由期望配置生成纯数据 Lua；通过部署发布，而不是写回 modinfo |

客户端还可能有 `mod_config_data` 保存的历史选择。游戏 UI 显示出配置，不足以证明“本次、本版本 modinfo 已正确加载”：要排除历史配置、客户端/服务端不同版本、语言差异及其他 MOD 对加载环境的修改。

### 2.2 已查到的加载链

游戏脚本 `modindex.lua` 的 `InitializeModInfo` 创建环境，显式加入 `folder_name`、`locale`、`ChooseTranslationTable`，经 `kleiloadlua` 读取文件，再交给 `RunInEnvironment`。`util.lua` 中后者通过 `setfenv` 指定环境并用 `xpcall` 捕获错误。成功后从环境读取 `configuration_options` 等字段，并做 API/必要元数据检查。[G1][G2]

因此可以执行局部函数、数字循环、表构造和语言条件；不需要把这些语法重新写成一个半成品解释器。函数的 return 值也不是配置定义的唯一来源，关键是执行后的环境字段。

这里不能套用 `modmain.lua` 的完整环境。所核对的 `InitializeModInfo` 环境没有 `_G` 回退，也没有显式开放 `GLOBAL`、`TheWorld`、`TheNet`、`require` 或 `modimport`。不能因为解析失败就把这些能力全部补上；那会得到游戏本来不接受的结果。字符串实例方法还可能依赖 Lua 内部字符串元表，与是否暴露 `string` 全局是不同问题，需在目标 build 上对比。[G1][G2]

翻译函数的规则是“当前 locale 的值，否则下标 1 的值”。不能自行替换为 zh → zht → en 回退。UI 可以额外请求中文显示，但语言切换若改变字段名、候选值或默认值，应返回 schema 差异，而非混合两个语言版本。[G1]

本轮使用的较新镜像仓库 HEAD 为 `97c19e2a5d53d28b208e0f176bb36dc7487283f2`，提交时间 2026-10-01，提交说明为 `756039`。网页缓存和镜像不是当前实例游戏版本的认证来源；实现前必须从该实例固定 Release 镜像中的 `scripts.zip`/实际脚本提取对应文件、记录 DST build 与摘要，再冻结兼容行为。另一个常见镜像本轮查到的提交停留在 2024-09-09，不能当作 2026 年目标 build 的依据。

### 2.3 覆盖值如何生效

`ApplyConfigOptionOverrides` 按配置 `name` 将 `modoverrides.lua` 中的值放入对应项的 `saved`。`GetModConfigData` 再读取已保存选择，缺省时回到默认值；是否为客户端配置还涉及 `saved_client/saved_server` 分支。[G1][G3]

这带来三个具体约束：

1. 面板使用 `label/description` 展示，保存与迁移使用 `name` 和 `data`，不能保存中文标签或下拉框下标。
2. `false`、`0`、空字符串都是值，不能用 truthy 判断是否有用户设置；“取消覆盖”与设置 false 必须分开。
3. 不存在于新配置定义中的旧 key，不保证会被游戏采纳；管理器保留它用于比较和恢复，但要标记为已移除，不能宣称已生效。

### 2.4 旧项目已确认的差异

| 位置 | 已确认事实 | 处理建议 |
| --- | --- | --- |
| `src-tauri/Cargo.toml:23` | mlua 编译为 Lua 5.4；游戏加载代码使用 5.1 环境模型 | 使用匹配目标 build 的 5.1 兼容 VM，具体实现/数值行为做对照，不把 5.4 当同义替代 |
| `mod_parser/sandbox.rs` | 没注入 `folder_name` | 注入真实 `workshop-<id>`；文件夹身份纳入 schema 缓存键 |
| 同上 | 自定义翻译函数按语言链选择，没有游戏的数字下标 1 回退 | 实现目标 build 的准确语义 |
| 同上 | `StdLib::NONE`，额外模拟 T/L/GLOBAL 与仅支持追加的 table.insert | 与游戏环境对比；既不能缺少真实可见行为，也不能用额外假函数制造成功 |
| `commands/mods.rs` 的 schema 入口 | 主要把单个 modinfo 文本送入解析器 | 绑定完整产物身份；仅在目标 build 确实允许文件依赖时，提供产物内受控读取 |
| `mod_parser/schema.rs` | 对复杂值不支持；字段以 filter_map 投影，部分异常项可能被丢弃 | 输出逐项诊断，不把丢掉项后的表标为完整可编辑 |
| `mod_parser/mod.rs` | 静态、沙箱、legacy 合并，部分路径会留下 NoOptions | 区分执行成功且无配置、执行失败、值不支持；禁止局部残留冒充完整结果 |
| 同上 | 是否执行沙箱依赖源文本出现 configuration_options | 不以子串检查决定 Lua 程序语义；有界执行后读取字段 |

这些是代码层面的缺口，不等于已定位用户曾遇到的所有失败 MOD。需要取得那些 MOD 的精确 Workshop ID、原始产物版本及语言，用同一份字节同时对照游戏和新求值器，才能判断每个实际失败的原因与覆盖率。

## 3. 获取、搜索与添加

### 3.1 获取入口

P0 支持数字 Workshop ID 和 Steam Workshop 条目链接，可兼容规范化 `workshop-<id>` 输入。只从允许的 Steam 链接提取 ID，不把任意 URL 交给服务器下载。ID 按十进制 uint64 校验，以字符串贯穿 TypeScript/JSON/Rust，避免 JS 大整数精度丢失。

元数据由桌面 Rust HTTPS 查询 Steam，包含标题、作者、应用归属、可见性、条目类型、更新时间、预览与可取得的内容标识。批量 `GetPublishedFileDetails` 的官方请求参数不要求用户 API key；逐项检查 result，不能把 HTTP 200 当全部条目成功。[S1]

客户端游戏 Workshop app 与专服安装 app 应分开建常量并实测：DST Workshop 通常对应 `322330`，专服安装为当前项目使用的 `343050`。校验消费应用，不用作者的 creator_app_id 简单代替；下载后的 `dst_compatible/client_only_mod/api_version` 再补服务端可用性检查。没有显式兼容字段时保留 unknown/warning，不能自创比目标游戏更宽的兼容结论。

名称搜索可后续接 `IPublishedFileService/QueryFiles`，官方文档要求 Web API 用户 key，Service 接口按文档使用 `input_json` 参数。首期没有该能力时保留按 ID/链接添加，提供打开 Workshop 的入口；不将 HTML 抓取作为必须成功的核心依赖。key 若支持，使用独立 keyring 命名空间并从请求/日志脱敏。[S2]

合集通过 `GetCollectionDetails` 读取候选项，去重、有界展开、防循环并显示预览；合集不是一个可启用 MOD。内容依赖与合集关系分别处理。依赖解析必须基于目标游戏语义，显示将增加的条目，不擅自删除仍被依赖的 MOD。[S1]

### 3.2 添加的状态转换

输入 → 查询元数据 → 加入本实例草稿 → 准备产物 → 求配置 schema → 编辑 → 保存期望 → 预览应用。

添加不等于下载，下载不等于启用。未下载时可以保留待准备条目；不能显示空配置或伪造作者 version。私有/下架条目若已有合法保留产物，可保留当前部署，远端不可访问不等于要自动移除。

公共缓存只存 Workshop 元数据；启用、配置、下载状态、schema 与产物归属都按实例保存。缓存失败保留同一条目的旧快照和时间，不返回“没有更新”。

## 4. 下载与产物准备

### 4.1 单一路线的选择

在远端 Docker worker 中准备内容，桌面端不先下载再转传大包。优先对 SteamCMD 候选流程做兼容实验：

```text
steamcmd +login anonymous +workshop_download_item 322330 <workshopId> validate +quit
```

这是待验收的获取候选，不是所有 DST Workshop 条目都可匿名下载的保证。实验必须覆盖实际失败样本、不同 Workshop 内容格式、网络失败/限流和大包。不能把 SteamCMD 退出 0、日志成功词或缓存目录存在作为下载成功的唯一依据。

若该流程不能覆盖目标条目，比较同一固定 DST Release 的官方专服 MOD 下载流程；`ServerModSetup`、独立 UGC 与 `-only_update_server_mods` 等具体行为必须在该二进制实测，不把网上的参数组合直接写成已验证能力。实验后冻结一种覆盖需求的生产流程；没有证据时不加自动切换下载器、第三方 MOD 镜像、factory 或 registry。

内容协议/打包差异尽量交给 Steam/游戏官方工具，避免自行实现旧 UGC URL 解包和新 Workshop 协议。`file_url` 等元数据不能视为每个条目都有的通用下载地址。

### 4.2 worker 的输入输出

输入固定 target、taskId、requestedIds、复用产物集合和目标 DST Release。worker 只挂本实例 staging、task 输出与需要读取的产物，不挂 Docker socket、生产世界或秘密；controller 在宿主编排，桌面断开后仍继续。

下载阶段仅有网络；求值阶段独立 worker 禁网络。使用非 root、只读根文件系统、cap-drop、no-new-privileges、CPU/内存/PID/磁盘/时间限制；输出有界。SteamCMD 的缓存与 HOME 同样放在任务专用目录，防止多个任务互相改写缓存。

完成条件：工具结果可解释、条目归属匹配、所需文件完整、`modinfo.lua` 可读取、文件类型与大小符合限制、下载器已退出且文件集静止、逐文件清单和 SHA256 完成。拒绝逃逸路径、设备、外部符号链接、异常硬链接、超额包。特殊无 modmain 条目先按已验证格式分类，不能一概认定全部内容类型都必须有相同入口。

产物标识至少包括：workshopId、取得时间、可用的 Steam 内容/manifest 标识、作者显示 version、modinfoSha256、完整 contentSha256、下载器版本/镜像 digest。显示 version 是作者文本，不是可靠更新序号。

### 4.3 先固定内容，再确认发布

Workshop 提示有更新 → 准备具体内容 → 生成清单与摘要 → 用户看到该产物的应用预览 → 应用时只复用这份产物。

检查更新时间只能证明“外部元数据有变化”。即使下载前后各查一次时间，也不能据此严格证明内容属于某个历史版本。若无法从下载证据关联某个 Steam revision，保留上游版本 unknown，用已下载字节摘要固定本次部署。

预览携带准备产物 digest、desiredModsDigest、当前 deploymentId、expectedRevision、目标/连接版本和过期时间。排队后任何一个变了都要求重新预览。已固定内容在应用时不得无声重下为“最新”。历史版本恢复只依赖已保留产物；没有实证时不承诺从 Steam 任意下载旧版本。

## 5. 配置求值与编辑

### 5.1 推荐求值器

P0 在远端专用容器中运行一个单一实现的 Lua 求值 helper，版本随工具镜像固定；Rust 负责有界输入、结果验证与 DTO，不在 Tauri 主进程里执行第三方脚本。

1. 输入只读 modinfo 源码、真实 folder_name、目标 build/branch、locale 和预算。
2. 使用目标 build 验证过的 5.1 兼容运行时；拒绝预编译字节码。
3. 按该 build 的 InitializeModInfo 建环境，不加宿主 `_G` 回退，不开放文件、网络、进程、原生扩展或任意模块加载。
4. 有界执行完整文件，读取环境中的元数据与 configuration_options，再校验游戏的 API/兼容字段。
5. 按原配置顺序遍历纯数据；检测循环、函数、userdata、非有限数、重复 key 和不支持的表形状，不调用作者的 tostring/__pairs 等元方法来导出结果。
6. 返回完整性状态和字段诊断。只有执行成功且结果确实缺失/为空，才返回 noOptions。

起始实验预算可设每文件 2 MiB、VM 内存 32 MiB、100 万指令、2 秒墙钟、输出 2 MiB、嵌套深度 16；均需样本调整，不能作为已经验证的容量保证。Lua 指令 hook 不能中断所有 C 层操作，因此还需进程/容器硬超时；超时销毁本次 VM，不复用污染环境。

`require/modimport` 仅在目标 build 明确允许的情况下按原语义提供产物内只读白名单；否则失败就是失败。不应为“提高成功率”伪造 TheWorld 或把任何未知函数变成空函数。

配置 schema 缓存键使用 contentSha256 + modinfoSha256 + folderName + locale + DST build/branch + evaluatorVersion + schemaVersion。仅用 Workshop ID 或作者 version 缓存会把新旧配置串起来。

### 5.2 游戏自身作对照

从目标 Release 提取实际加载脚本与环境，对同一份 modinfo 分别执行兼容 helper 和游戏加载路径，比较字段、顺序、默认值、候选值和失败情况。

若 helper 仍有不能等价的部分，再制作隔离的游戏引擎提取 PoC：同一镜像、一次性配置/UGC、无正式 Token/存档、禁网，仅在 modinfo 加载完成时由受控代码导出纯数据并退出，不启动作者 modmain。游戏没有已核实的通用“导出 MOD schema”命令；注入点、是否提前执行作者代码和稳定性均为 PoC 的验收内容，不能宣称已有现成接口。

游戏路径用于校准和诊断，不因为一句“游戏可以解析”就运行完整 MOD 到生产世界。不同 build、locale、文件内容或历史缓存的结果不作为等价对照。

### 5.3 schema 与表单

拟议 schema：status（available/partial/noOptions/unsupported/failed）、evaluatorVersion、gameBuild、locale、artifactDigest、schemaDigest、metadata、fields、diagnostics。

字段包含 name、label、hover、kind、defaultPresent/defaultValue、options（description/data/hover）、editable、reason；纯标题/分隔项要按目标游戏行为识别，不能全部误报为坏业务字段。

- 普通候选值优先用 select；data 保持 boolean/number/string 类型，可以同一选项集混合类型，不统一转字符串。
- 明确支持的无候选字段才生成输入控件，不因缺 options 就推断“任意字符串可用”。
- 复杂值先只读保留；普通 JSON 不能无损表示稀疏 Lua 表、非字符串 key、函数或表身份。只有验证过可往返的子集才开放结构化高级编辑。
- 执行失败不采信部分初始化残留为完整 schema。静态诊断可辅助定位，不自动与历史/正则结果拼出一个“成功”表单。
- schema 更新时按 name 比较：保留仍有效的用户覆盖；新字段默认不写覆盖；删除/改类型/候选值变化需要确认。不能把缺失项批量重置成默认值。

自动表单不可用不等于 MOD 本身不可用。求值器尚不兼容、结果类型不支持、游戏实际加载失败要分别返回。前两种情况下保留已有覆盖，允许按默认配置进入独立的游戏兼容验证；不能自动清空配置或把解析失败转换成“该 MOD 无配置”。高级覆盖可后续支持有类型的标量映射，或仅含字面量的 modoverrides 导入；不支持的表达式拒绝，不执行用户上传 Lua。没有 schema 可核对的覆盖必须标记“未验证”，以实际游戏结果为准。

### 5.4 保存和应用文件

界面改动先进入 hostId + instanceId 的草稿；保存使用全局 configRevision CAS 和 operationId，仅更新 desired。后端按所绑定 schemaDigest 再校验，拒绝把标签、字符串 true 或非法枚举当真实配置。

部署由统一序列化器生成 Master 与 Caves 各自的 modoverrides；默认两分片使用同一版本和配置，首期不开放任意按分片拆分依赖，保留未来 shard scope 扩展位置。文件是纯数据 return table，所有 ID/key/string 做 Lua 转义，不允许用户输入可执行表达式。

```lua
return {
  ["workshop-123456789"] = {
    enabled = true,
    configuration_options = {
      ["enabled_feature"] = false,
      ["difficulty"] = 2,
      ["mode"] = "normal",
    },
  },
}
```

示例 ID/key 仅为示意。保存文件前以同版本数据解析器回读，确认类型、key 和摘要；发布后再次重读。实际目录沿用 `config/Cluster_1/<Shard>`，不臆造默认集群来寻址。

MOD 可能有 Token/webhook 等敏感配置：标记为秘密的字段不能写入普通草稿、SQLite、事件或宣称无秘密的备份。支持时走独立凭据引用及受保护部署文件；该路径未实现时明确阻断此类值，不能为了兼容自由文本而绕过项目秘密约束。仅凭字段名称不能保证识别所有秘密。

## 6. 更新、发布与运行验证

### 6.1 检查更新

check 是只读查询，不占维护操作栏，不修改 desired/deployed。批量采集每项外部可见更新时间和可用内容标识，返回 checkedAt、latestKnown、updateHint、error；失败保留上次成功时间，并记录本次失败。

有可靠上游内容标识时比较标识；只有时间时标为候选更新；只有作者 version 时不能严格判断。准备后完整 contentSha256 相同则无需内容更新，元数据变化独立展示。启用且实际部署的 MOD 是默认检查集合；禁用但保留条目可显式检查。

### 6.2 发布阶段机

```mermaid
flowchart LR
  A[检查与准备产物] --> B[配置迁移与预览]
  B --> C[锁内复核目标与摘要]
  C --> D[停止全部目标分片]
  D --> E[冷备份与恢复能力验证]
  E --> F[发布 MOD、配置与 Compose]
  F --> G[按原状态启动或保留停止]
  G --> H[实际产物与运行验证]
```

同主机一个重任务、同实例维护互斥，复用持久 task/status/journal/sequence/operationId。下载在停服前完成。备份在同一锁 owner 下执行子阶段，不能递归接纳独立备份任务造成自锁，也不能在发布前自动把已停止游戏重启。

最终 plan 固定完整部署组合：DST release、MOD release、世界/配置 digest 与 appliedRevision、两个分片的 UGC 和 Compose 摘要。MOD-only 发布复用已部署世界配置，不将未应用的世界草稿顺带上线。文件、Compose、active-release 和 manifest 不是一次 rename 就全部原子；必须记录逐步 intent/complete，在全部分片停止状态提交，并能在每个故障点对账。

若原来停止，提交后维持停止，可验证“已部署，运行未验证”；不能启动来制造运行通过，也不能给 verification=passed 的运行结论。若原来运行，验证每分片的新进程/启动身份、就绪与端口、启用 MOD 集合、配置摘要、无新加载错误及观察窗口内稳定性。日志只作为有界的组合证据，不以一句 Loading 或 applying 就报告成功；若需游戏内导出实际启用/有效配置，先验证受控诊断 hook，不假设引擎已有稳定 JSON 接口。

### 6.3 UGC 与启动自动更新

同一实例的 Master/Caves 使用两份可写 UGC，从同一已固定种子产物复制/快照得到；发布前分别校验内容清单一致。两个进程不能共同写一个 UGC 目录，也不把可写硬链接连接到保留产物或其他实例。

拟议的最小存储划分：

```text
instances/<instanceId>/
  desired/mods.json
  mods/releases/<modsReleaseId>/manifest.json
  mods/releases/<modsReleaseId>/content/...
  mods/runtime/<deploymentId>/Master/...
  mods/runtime/<deploymentId>/Caves/...
  staging/<taskId>/...
  deployed-mods.json
  active-release.json
```

release 内容逻辑不可变，运行 UGC 与它分离。UGC 的 Steam 元数据、下载目录、workshop 目录层次必须按所选下载流程实测适配；不能只拷 modinfo 或假设 `/srv/dst/mods` 与 `/srv/ugc` 互相替代。UGC 可能还含用户/运行缓存，分类后决定哪些进入恢复点。

启动时自动检查/下载若可改变产物，就会破坏确认版本和回滚。必须验证目标二进制的禁止自动更新能力，以及 Steam UGC 自身是否仍写入内容；单加一个未经验证的 skip 参数不算解决。发布后和重启后比较实际内容摘要，偏离时标 drifted/失败，不能仍报固定版本。阶段 0 未证明可控制这点前，不开放承诺“固定 MOD 版本”的生产更新。

### 6.4 失败和恢复

- 准备失败：活动部署不变，保留任务与可清理 staging。
- 停服或备份失败：不提交新部署；若曾运行，按证据恢复原运行状态。
- 发布部分提交/SSH 断开：controller 按 journal 与实际文件对账，禁止盲目重做 rename。
- 新 MOD 已启动后失败：停止新组合，恢复旧 MOD + 旧配置 + 冷备份世界 + 对应 DST 产物，再启动验证；不能只把 MOD 目录切回。
- 缺产物、保护点不完整或恢复失败：保留停止和现场，提供明确恢复动作，不循环启动新旧组合。
- 成功恢复旧组合仍保留原任务 failed，结果为“更新失败，已恢复原部署”。

## 7. 禁用、移除与清理

| 操作 | 期望变化 | 真正生效与保留 |
| --- | --- | --- |
| 禁用 | enabled=false，保留配置 | 应用后不加载；产物和历史配置保留 |
| 移除 | 从期望集合删除，先检查依赖 | 发布不含它的新组合；不直接删除运行目录 |
| 清理未引用产物 | 不改期望 | 独立预览，检查 active/备份/任务/待恢复记录后删除 |

移除可能使世界中的 MOD 物品、角色或数据无法读取，需在该次确认中说明具体目标与恢复点；不能保证重装就还原已经保存过的世界。移除不改作者账号订阅，也不删除存档中作者自建的任意文件。

GC 引用集合至少包括 active deployment、保留备份、运行/排队/interrupted 任务、待提交计划、恢复现场。不能只保留“最近两份”就删除唯一恢复点。外部下架不影响清理规则，尤其不能先删唯一保留的旧产物。

## 8. 接口、版本和代码接入

以下均为拟议能力；实现完成并核对 Rust/TS/fixtures/文档后才注册。

| 能力 | 建议接口与关键输入 |
| --- | --- |
| 公共元数据 | resolve_workshop_items / search_workshop_items；ID 或 query、locale；不返回实例启用状态 |
| 实例清单 | list_instance_mods；完整 InstanceTarget、连接版本、requestId |
| 准备内容 | prepare_instance_mods；target、expectedRevision、expectedRecordVersion、指定 ID/复用产物、operationId；返回 task |
| 读取配置定义 | get_instance_mod_schema；target、workshopId、artifactDigest、locale、DST release |
| 保存期望 | save_instance_mods；target、expectedRevision、schema/产物绑定、desiredMods、operationId |
| 更新检查 | check_instance_mod_updates；target、请求序号；查询，不修改配置 |
| 预览/应用 | preview_mods_apply / apply_instance_mods；preparedRelease、planId、expectedRevision、operationId |
| 物理清理 | preview_mod_artifact_cleanup / cleanup_mod_artifacts；引用快照、planId、operationId；可后置 |

保持现有全局 configRevision CAS，不临时另起一套 modsRevision。领域文档记录自身 changedAtRevision，读时允许其小于全局 revision；响应的 configRevision/写入 expectedRevision 仍取 manifest 的全局值。首次保存 MOD 不应强迫世界 desired 文件已经存在；从尚未编辑过的默认世界读取也要有明确语义。

最小联动改造：世界 load_desired 不再要求其上次变更 revision 永远等于全局值；世界事务回放仍必须核对提交时 revision 与日志身份，不能靠删除校验解决；运行摘要改为比较 world/mods 的规范化 digest，而不是 deployed.revision == configRevision。所有配置领域写入共用可对账事务边界，世界与 MOD 互相保存后均应可读，旧操作回执不能回滚新领域数据。

ModsSnapshot 分离 desired、deployed、prepared、observed、updateCheck；至少区分未准备、已准备、待应用、已部署、运行未验证、运行已验证、漂移、未知/失败。单个 installed=true 或 restartRequired 不能表达这些事实。

建议按现有模式实现 `src/api/mods.ts`、`src/types/mods.ts`、`useInstanceMods`、Rust mods 领域文件及远端 probe/controller/worker。纯 ID 规范化、schema 校验、迁移与 Lua 数据序列化保持可独立测试；不要复制旧项目巨型 commands/mods.rs，不为单一后端加 trait 或 registry。

前端按 hostId + instanceId 归属缓存和草稿，所有 success/error/finally 检查 contextEpoch + 最新 requestSeq + revisionFloor。任务固定提交时 target；A 任务在 B 页面完成，只使 A 的领域失效。列表保持简洁，真实错误按需展示；所有进度进入现有单一底栏。

## 9. 实施顺序与验收

| 阶段 | 交付与门禁 |
| --- | --- |
| M0：证据与基础修正 | 收集失败 MOD 固定样本；从目标镜像提取加载脚本；选定下载流程；验证 UGC/自动更新；修复跨领域 revision 与 digest |
| M1：获取和准备 | ID/链接解析、元数据、真实实例列表、持久准备任务、产物清单与摘要；正式世界不改 |
| M2：配置 | 隔离 Lua helper、schema 状态、表单、升级差异、CAS 保存；与同 build 游戏对照 |
| M3：保护和发布 | 备份包含 MOD 产物引用/必要内容、完整恢复实验通过；停止/发布/启动验证与失败恢复 |
| M4：管理闭环 | 更新检查、固定准备产物更新、禁用/移除；再做未引用产物清理和可选搜索/合集入口 |

必须覆盖的语义样本：静态表、局部函数生成、数字循环、长字符串/转义、folder_name 条件、locale 与数字下标 1 翻译回退、字符串方法、false/0/空字符串、混合候选类型、标题项、重复 name、稀疏/复杂表、无配置、语法错、执行中途错、无限循环、大内存、C 层耗时、循环引用及无权限 API 调用。每种结果都明确区分游戏拒绝、helper 拒绝、schema 不支持和真正无选项。

业务/故障验收：世界与 MOD 交替保存、两个客户端 CAS 冲突；下载后作者再次更新、预览失效；Master/Caves 版本不一致；一个 MOD 更新不带动其他条目；重新启动不偷偷更新；删除被依赖 MOD；下架但保留产物；备份缺失/空间不足；每个发布步骤注入崩溃；SSH/桌面退出与主机重启对账；A 更新时 B/C 静态配置/产物及正常运行不受影响；GC 不清理被备份或 interrupted 引用的内容。

代码变更执行 `npm run check`，Rust fmt/check/test/clippy（`-D warnings`）及对应 Python 隔离测试；UI 核对亮暗主题、960×680/1480×920、键盘/焦点/Escape。本文仅设计分析，不把上述待验收项标为已通过。

## 10. 来源与复核入口

外部 URL 记录为原始来源入口；Steam 文档已核对，游戏部分是游戏代码镜像而非 Klei 官方源码仓库，最终以目标已安装 Release 为准。

| 代号 | 来源 | 本文用途 |
| --- | --- | --- |
| G1 | `https://raw.githubusercontent.com/CN-DST-DEVELOPER/scripts/main/modindex.lua` | InitializeModInfo、ApplyConfigOptionOverrides、LoadModConfigurationOptions |
| G2 | `https://raw.githubusercontent.com/CN-DST-DEVELOPER/scripts/main/util.lua` | RunInEnvironment 的 setfenv/xpcall 行为 |
| G3 | `https://raw.githubusercontent.com/CN-DST-DEVELOPER/scripts/main/modutil.lua` | GetModConfigData 及向运行环境注入的配置读取接口 |
| G4 | `https://www.lua.org/manual/5.1/manual.html` | Lua 5.1 环境、语言和值语义 |
| S1 | `https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage` | GetPublishedFileDetails、GetCollectionDetails 参数 |
| S2 | `https://partner.steamgames.com/doc/webapi/IPublishedFileService` | QueryFiles 与 API key 要求 |

本地关键入口：`src/pages/ModsPage.vue`、`src/types/contracts.ts`、`remote-scripts/actions/instance-release.py`、`instance-create-worker.py`、`world-config.py`、`instance-runtime-start.py`、`backup-task.py`、`backup-archive.py`；旧项目 `src-tauri/src/mod_parser/{mod,evaluator,sandbox,schema}.rs`、`commands/mods.rs` 与 `Cargo.toml`。
