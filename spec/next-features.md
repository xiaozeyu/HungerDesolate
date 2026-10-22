# 后续功能清单

- 更新时间：2026-10-21
- 依据：当前生产代码、[实施计划](implementation-roadmap.md)、[接口契约](api-contract.md)
- 原则：按真实功能与完成条件核对进度，页面存在不等于功能已完成。

## 0. 2026-10-21 实机进展

用户已确认应用创建并启动的 DST 专服容器可以正常运行、进入游戏并游玩。概览实时运行状态、DST build、UDP 端口采集与重新进入刷新，以及 Release 基础验证放行和按 Release 隔离镜像已接入。该结果证明当前单实例主路径可用；三实例隔离、外网/LAN/Steam 全路径、断线/主机重启和恢复仍需阶段 0 验收。

## 0.1 2026-10-22 恢复故障诊断

用户提供的 Master/Caves 日志显示两项独立启动条件失败：游戏运行用户不能写入恢复后的 `Cluster_1` 配置目录；DST 同时报告 `E_INVALID_TOKEN` / `No auth token could be found`。恢复实现现将归档目录和文件改为运行用户 UID/GID 1000 所有并使用可写私有权限；`cluster_token.txt` 不从归档读取，而按备份期望配置中的 token 引用从本机受保护的 `desired-secrets` 重新生成。若本机 token 引用或文件缺失，恢复预检阻断。日志已经证明当前令牌被 Klei 拒绝，用户需要重新生成并通过应用更新令牌后再验证；自动恢复不能让失效令牌变有效。

已核实身份但 Master/Caves 处于退化状态时，冷备份先停净分片并在采集后保持停服；恢复也保持停服，避免旧的自动重启策略形成循环。该行为尚未在用户 Linux amd64 主机复验。

## 1. 当前可用与本轮修复

2026-10-22 开始 MOD 管理实现：新增受限 `modinfo.lua` 求值器、实例 MOD 只读查询和真实 MOD 页面。求值器使用目标游戏兼容的 Lua 5.1 运行时，注入 `folder_name`、`locale`、`ChooseTranslationTable`，限制源码大小、内存、指令和表结构；执行失败、确实无配置项、产物未准备和部分字段不支持分别返回。远端查询只读取当前实例的 `desired/mods.json`、`deployed-mods.json`、活动 MOD 产物和 manifest，所有请求绑定完整 `InstanceTarget`。本轮没有实现 Workshop 搜索、下载、保存配置、应用、更新或删除，MOD 页面不显示示例数据。

随后新增 Workshop 元数据查询、实例期望列表保存和启用/禁用/移除控制：保存使用实例锁、全局 configRevision CAS、operationId 重放和 pending journal 恢复，不修改活动 UGC。跨领域 revision 允许世界领域版本落后于全局版本，并按 world desired/deployed digest 判断一致性。

新增 MOD 内容准备任务：固定 Release 镜像携带 SteamCMD fetch wrapper 和 `steamcmd-workshop-v1` 能力标记；准备任务从本地已验证 Release 中选工具镜像，并单独绑定目标实例活动 DST Release。远端持久任务以隔离 Docker 容器下载，校验内容后把产物改为 root 管理、只读目录树（目录 `0555`、文件 `0444`、manifest `0600`）；MOD 探针逐级验证 owner/mode、拒绝符号链接并以 `O_NOFOLLOW` 读取，固定摘要不存在时不回退其他版本。对照原项目后修正 SteamCMD HOME 缓存发现：wrapper 覆盖 SteamCMD 安装目录与 HOME 下常见 Steam 缓存布局，并支持 modinfo 位于条目子目录；对应脚本模拟测试通过。该 wrapper 已打包在 Release 镜像内，需重新构建 Release 才能用于后续准备任务。MOD 页可监视后台任务并在完成后读取配置 schema。没有能力镜像时明确阻断。Release 构建现将 daemon registry mirrors 显式传给专属 BuildKit builder，并原子合并 daemon.json 设置；仍需真实 Linux 主机验证镜像源可达。SteamCMD 匿名下载和真实 Workshop 样本仍须实机验收；配置编辑、更新检查、应用发布和删除仍待实现。

已实现独立桌面工程、主机记录增删改、SSH 指纹扫描/确认、系统凭据库接口、登录验证、固定 host.json 只读发现，以及两层导航和部分查询/任务隔离基础。

本轮修复：添加主机选择“密码”后可输入 SSH 密码；选择“SSH 密钥”后可输入/选择本机私钥文件路径和可选口令。路径支持绝对路径与 `~/`，Rust 读取非空普通文件，限制 64 KiB 并识别 PEM/OpenSSH 格式，拒绝公钥。私钥内容导入系统凭据库，路径和内容均不写 SQLite。

添加流程先保存元数据，再保存本地凭据。失败保留已创建主机，同一表单重试沿用该记录；关闭后可在连接设置补充。首次确认指纹保留预先保存的凭据，登录仍要求先确认指纹。已信任公钥变化经用户重新确认后保留已有凭据，清除旧主机身份绑定并重新验证登录。

当前仍不能创建/启动真实 DST 专服。世界、MOD、备份、更新和运行列表主要是明确开启的界面预览；登录验证不等于持续连接或部署成功。

## 2. 立即完成：认证验收与主机只读检查（P0）

| 顺序 | 功能 | 待完成与验收条件 |
| --- | --- | --- |
| A1 | 认证实机验收（部分通过） | 用户已验证添加、获取指纹与登录成功； macOS/Windows 文件选择与原生凭据库；密码、普通/加密私钥、拒绝授权、坏路径、首次信任与更换指纹。添加到登录全流程成功，失败不重复创建、不发送未验证凭据 |
| A2 | 主机环境检查（已实现） | 已接入 SSH 只读探测、系统/Docker/资源/权限结果和页面；待真实 Linux Docker 主机验收。 Linux 发行版/架构、Docker/Compose、磁盘/内存、权限与所需命令；显示真实阻断项和采集时间，权限错误不当作未安装，失败不填示例值 |
| A3 | 注册表只读发现（已实现） | registry/manifest schema、hostId/instanceId 归属、受控路径、主机别名连接对账已接入；待真实远端数据验收 |
| A4 | 连接结果与 UI 联调 | 区分未验证、最近验证通过、失败/离线；返回页面重查，切换 X→Y、A→B→A 不接受旧响应，查询不覆盖编辑 |

A1 已得到用户人工验证的主路径证据，平台/异常矩阵仍需补齐；A2/A3 代码及隔离/SSH 集成测试通过；下一步是阶段 0 Docker 三专服实验。只读检查可与 Docker 实验同步推进，不意味着提前放行部署写入。

## 3. 阶段 0：Docker 验证（P0 门禁）

| 项目 | 必须证明的结果 |
| --- | --- |
| 首发环境 | 固定 Linux amd64 发行版、Docker/Compose 版本、依赖、镜像 digest 与资源预算 |
| 三专服隔离 | 同机 A/B/C 独立 Compose 项目、bridge 网络、配置、存档、MOD/UGC、完整内外同号对外端口 |
| 完整联网 | 分别验证公网列表、玩家进入、直连、Steam 查询、洞穴双向迁移与 LAN 发现 |
| 可控更新与恢复 | staging 不影响活动产物，A 发布切换不影响 B/C；旧程序+MOD+配置+存档共同恢复，缺产物时拒绝自动回滚 |
| 任务可靠性 | SSH/桌面断开继续；worker/daemon/主机故障可对账，不重复破坏性步骤 |

这些实验尚未完成。Docker Engine 安装器用于准备干净 Ubuntu 26.04 验收主机，并在阶段 0 中验收；三专服游戏镜像/脚本安装、部署和恢复仍须阶段 0 报告通过后放行。保持单一 Docker Compose 路线。管理目录元数据初始化不代表专服部署就绪。

## 4. 部署基础与多专服（P0）

| 顺序 | 功能 | 关键交付/验收 |
| --- | --- | --- |
| B1 | 环境准备（实现完成，实机验收待做） | 管理目录初始化、Ubuntu 26.04 root-only Docker/Compose APT 安装、固定包清单、冲突阻断、apt removal simulation、systemd 持久任务与断线对账已实现；仍需 Ubuntu 26.04 干净机安装/重启/断线验收，再由阶段 0 验证三专服部署 |
| B2 | 注册表、manifest、端口计划 | 已落地创建接纳内核：注册锁、不可变身份、完整端口预留、实际占用重查与 operation 幂等；尚未接入持久 controller，不能创建真实专服 |
| B3 | 远端任务与对账 | 已完成创建任务持久接纳、request/status/journal、systemd controller 启动、sequence、interrupted 对账和恢复动作；真实部署 worker、维护锁租约、取消与主机重任务队列仍待完成 |
| B4 | 创建专服与生命周期 | 已完成受控目录、Compose/Docker 创建、容器验证、终态注册表对账；新增持久任务阶段记录查看及受控删除预览/执行，数据归档到 retired；继续创建恢复和真实 DST/联网实验仍待完成 |
| B5 | 启停/重启/开机策略 | 启动及本机 DST 进程/UDP 监听核验已实机确认；停止/重启命令与概览 UI 已接入并通过隔离测试；停止/重启真实主机验收、开机策略和掉线/主机重启对账待做 |
| B6 | 完整任务 UI | 单一底栏、详情/错误/恢复动作、应用级对账；A 完成不会覆盖 B 页或抢导航 |

B3 必须先于安装/恢复等长任务成熟。现有 task reducer 和本地凭据操作记录并不等于远端任务系统已实现。

## 5. 核心运维（P0）

| 顺序 | 功能 | 关键交付/验收 |
| --- | --- | --- |
| C1 | 世界配置 | 基础/联机字段、Master/Caves DST 选项、期望配置 CAS 保存与应用影响预览已实现；实际 apply 仍由备份与配置发布任务阻断，待实现保护备份、渲染、发布和运行验证。洞穴拓扑只读，拓扑变更需专门迁移并保留存档 |
| C2 | 备份与恢复（代码闭环已接入，Linux 实机故障验收未通过） | 冷备份/准热备份、SFTP 断点下载、本机 checksum 与原子发布、已应用世界配置快照、恢复前点击门禁、保护点/提交/回滚、中断对账和普通备份删除已接入。列表会复核既有备份的 checksum、配置快照、Release 产物和镜像并更新恢复能力；仅最新恢复前保护点不可删除。旧备份仅当当前已应用配置 revision 与备份 revision 一致时兼容恢复。已加入 UID/GID 1000 权限修复和本机受保护令牌引用重建；实机日志仍显示 `E_INVALID_TOKEN`，需更新 Klei 令牌后复测。拒绝跨实例恢复；磁盘满、checksum 错误、错误实例、停服/重启失败、强制中断、SSH/桌面重启等 Linux amd64 实机验收完成前，不开放现存存档世界配置应用 |
| C3 | MOD 管理 | Workshop 元数据、实例启用/配置、受限 Lua 解析、检查更新、staging 下载/应用、分片 UGC 一致性 |
| C4 | DST/MOD 更新 | 固定目标版本、保护备份、发布切换、实际版本验证与完整恢复点回滚 |
| C5 | 日志与诊断 | 有界分片日志/游标、脱敏、健康摘要、失败任务证据与恢复入口 |
| C6 | 保护删除专服 | 有效备份、停止并清理受管运行资源、retired 保留，确认残留消失后才释放端口 |

世界/MOD 的编辑与预览可先开发；实际写入放行时依赖可用保护备份。回滚成功仍应保留原更新失败结果。

## 6. 发布与后续优先级

P0 发布还需全量契约、并发/故障注入、三实例实机回归、四组视觉与键盘验收、本地字体/图标、Windows/macOS 安装签名、升级迁移与恢复手册。现有部分测试和 macOS debug 编译不能替代双平台发布验收。

P1：远端定时备份/保留策略、健康历史、脱敏诊断包、旧部署发现/迁移/失败回退。

P2：跨实例 MOD 配置复制、模板、通知、自愈与高级故障归因。

## 7. 2026-10-18 进展

用户已确认 Docker 可以成功安装，此证据覆盖用户当前 Ubuntu 26.04 主机的安装主路径；尚未提供精确版本、重启/断线和阶段 0 三专服实验结果，不扩大为完整部署验收。

新增“新建专服 → 创建预览”：当前只收集实例名称、启动策略、容器拓扑（是否创建洞穴容器）和自动端口策略。世界模式、人数、PVP、说明和 Token 不出现在创建窗口或预览请求；创建容器和验证游戏环境完成后，才进入世界配置流程。创建路线固定为公网；绑定完整 HostTarget、连接版本和注册表版本，真实读取远端身份/注册表/manifest、Linux IPv4/IPv6 占用与 Docker 发布配置，计算完整同号 UDP 建议端口，显示目录、项目和可用磁盘。

当时尚未接入创建执行，结果固定携带“游戏镜像与部署兼容性尚未完成验证”阻断；不宣称可安装/启动真实专服。随后已完成 B2 接纳和 B3 持久任务基础；当前仍需阶段 0 的镜像/SteamCMD/双分片/联网与隔离验证，以及 release/Compose/容器部署，再开放创建任务。

## 8. 2026-10-19：创建接纳内核

新增 `remote-scripts/actions/instance-create-control.py`，实现 B2 的短临界区：在远端 `registry.lock` 内重新校验 hostId、registryRevision、预览端口计划、registry 预留、Linux socket 与 Docker 发布端口，原子写入 `creating` 实例、完整 UDP 端口预留和受限 operation 回执。锁 owner 记录 token、generation、心跳和租约时间；相同 operationId/输入重放原结果，不同输入返回 `OPERATION_ID_REUSED`。

该脚本仍未注册 Tauri command，也不创建实例目录、写 Token、生成 Compose 或启动 Docker；持久任务 controller/worker 完成后才可接入 `create_instance`。隔离测试入口为 `python3 scripts/test-instance-create-control.py`。

## 9. 2026-10-19：持久创建任务基础设施

新增 `remote-scripts/actions/instance-create-task.py` 与 `remote-scripts/actions/instance-create-worker.py`，实现 B3 的基础闭环：任务接纳按 operationId 幂等写入 `request.json`、`admission.json`、`status.json` 和 `journal.jsonl`，通过 systemd transient unit 脱离 SSH 启动 worker；状态使用单调 sequence，任务进程消失时对账为 `interrupted`，并保留继续创建、清理现场和查看日志等恢复动作。

没有 release 时 worker 在 `download` 阶段返回 `CREATE_RELEASE_REQUIRED`；release/runtime/容器验证失败会返回对应任务错误并保留现场。创建任务不写世界配置、Token 或启动游戏；隔离测试入口为 `python3 scripts/test-instance-create-task.py`。

## 10. 2026-10-19：实例目录与初始 manifest

创建 worker 已进入 B4 前置阶段：使用固定 `/var/lib/famine-hub/instances/<instanceId>` 路径创建 `config`、`desired`、`data/Master`、可选 `data/Caves`、`mods/releases`、`staging/<taskId>`、`tasks/<taskId>`、`locks` 等受控目录，并原子发布初始 `manifest.json`。DST 二进制由 digest 固定的 Release 镜像提供，不创建会遮蔽 `/srv/dst` 的实例 bind mount。manifest 只包含 host/instance 身份、名称、容器拓扑、端口和期望 revision，不包含世界配置、Token 或其他秘密。

目录或 manifest 冲突会失败并保留现场；没有 release 时任务在 `download` 返回 `CREATE_RELEASE_REQUIRED`。有合格 release 时只生成 staging Compose，仍不创建网络/卷或启动容器。

## 11. 2026-10-19：release 校验与 Compose staging

新增 `remote-scripts/actions/instance-release.py`，固定校验 `linux/amd64`、UUID releaseId、image digest、产物摘要、脚本版本、Master/Caves 启动命令和 verified 标记，并生成 digest pin 的确定性 Compose 文本。worker 接收到任务级 `release.json` 后只将 `compose.yaml` 和 release 摘要写入 `staging/<taskId>`，不会覆盖活动 `compose.yaml` 或启动 Docker。

缺少 release 返回 `CREATE_RELEASE_REQUIRED`；release 或 manifest 无效返回明确失败。staging Compose 会先执行受控 `docker compose config --quiet`，成功后写入不含秘密的 network、bind mount 和端口资源计划；校验失败保留现场并返回 `CREATE_COMPOSE_CONFIG_FAILED`。config 成功后任务进入 `publish`，以 `CREATE_DOCKER_RUNTIME_REQUIRED` 阻断，等待真实镜像、产物、权限、网络/卷和容器验证。隔离测试入口为 `python3 scripts/test-instance-release.py`。

## 12. 2026-10-19：Docker 资源创建内核

新增 `remote-scripts/actions/instance-docker.py`，只有 release 的基础验证 `verified=true` 时才允许执行资源创建：网络名称固定为 `<composeProject>_instance`，创建/复用必须匹配 hostId、instanceId 和 schema 标签；容器使用 `docker compose create` 创建但不启动，随后验证容器 labels、挂载源和端口发布。验证通过后 worker 发布活动 `compose.yaml`、`active-release.json`、`runtime.json` 并完成创建任务；不自动启动游戏。

运行时验证保留为可选证据，不再阻断创建；基础验证失败时仍阻断。隔离测试入口为 `python3 scripts/test-instance-docker.py`；真实 network/container 创建与阶段 0 产物验证仍待完成。下一步是容器就绪后的世界配置与 Token 流程。

## 13. 2026-10-19：DST release catalog 只读发现（✅ 已完成）

新增 `list_instance_releases` command 和远端 catalog probe。它只读取 `/var/lib/famine-hub/releases/catalog.json`，验证 hostId、release 摘要、镜像 digest、产物 digest、启动命令和基础验证状态；catalog 缺失或没有基础验证合格 release 时返回 `CREATE_RELEASE_REQUIRED`。

- ✅ 后端：`list_instance_releases` 注册并实现
- ✅ 远端：`probes/instance-release-catalog.py` 校验完成
- ✅ 测试：`scripts/test-instance-release-catalog.py` 通过
- ⚠️ 前端：命令已接入但 UI 未展示 release 列表

## 14. 2026-10-19：release 准备前置校验（✅ 已完成）

新增 release prepare probe，按 catalogDigest/releaseId 检查 catalog 是否变化、release 是否 verified，以及固定 `/var/lib/famine-hub/releases/staging/<releaseId>` 是否存在。

- ✅ 远端：`probes/instance-release-prepare.py` 实现
- ✅ 测试：`scripts/test-instance-release-prepare.py` 通过
- ⚠️ 真实产物下载与 staging 仍等待阶段 0

## 15. 2026-10-19：release 导入任务（历史实现）

新增 `preview_instance_release` / `import_instance_release`。导入任务验证 staged `release.json` 与 catalog 完全匹配；隔离运行验证成功后原子更新 catalog release 的 `runtimeVerified`，但该字段不决定创建资格。

- ✅ 后端：两个命令已注册并实现
- ✅ 远端：`actions/instance-release-import.py` 验证完成
- ✅ 测试：隔离测试通过
- 后续已在 §18 补齐基础/运行时验证入口；真实主机实验仍需阶段 0。

## 16. 2026-10-19：创建任务前端状态同步（⚠️ 部分完成）

`InstanceCreateDialog` 已实现完整表单与预览调用，但创建任务提交与状态轮询尚未接入。

- ✅ 表单：参数收集、预览调用、端口计划展示
- ✅ 后端：`create_instance` 命令已注册
- ❌ 前端：未调用 `create_instance` 提交任务
- ❌ 前端：未实现任务状态轮询或事件订阅
- ❌ 前端：未展示创建进度（下载、配置、Docker 资源）
- ❌ 前端：未实现失败重试、查看日志、清理现场

**下一步优先级**：

1. **补齐创建任务集成**（P0 阻断）：前端调用 `create_instance`、轮询状态、展示进度
2. **Release 验证 UI**（P0 阻断）：Release 管理页显示基础/运行时验证操作、进度和结果；创建窗口只选择可用 Release
3. **任务恢复入口**（P0 重要）：失败任务重试、清理、日志查看
4. **视觉验收准备**（P0 门禁）：light/dark × 960×680/1480×920 截图、键盘交互记录

## 18. 2026-10-19：DST Release 两阶段验证与实例固定版本

- 构建登记前核对本地主机 Docker image ID、镜像内 `version.txt` 与 DST binary；基础验证通过才标记 `verified=true`。已登记但未验证的 Release 可从创建流程重新执行基础验证。
- 运行验证接收一次性 Klei 配置 ZIP，在 `/run` 下创建不发布宿主端口的 LAN-only Master/Caves 集群，核对实际 DST 进程和配置 UDP listener；验证容器、网络和临时配置清理后，写入不含 token 的 digest 绑定 evidence。
- 创建流程显式选择单个 Release；提交时携带 releaseId/catalogDigest，远端 admission 在 registry 锁内重验并将确切 Release 快照写入任务。Compose 通过 image digest 使用镜像内 DST binary，不再将空实例目录 bind mount 到 `/srv/dst`。
- 测试：`scripts/test-register-built-release.py`、`scripts/test-verify-release-runtime.py`、`scripts/test-instance-create-task.py`。真实 DST/Klei 配置兼容、UDP listener 及网络要求仍需 Linux amd64 阶段 0 实机验证。

## 19. 2026-10-19：基础验证 Release 可创建

- Release 构建登记后的基础验证（镜像 digest、`version.txt`、DST 可执行文件与动态依赖）通过后即可用于创建容器；`runtimeVerified` 不再是创建门槛。
- Klei 配置 ZIP 的 Master/Caves 隔离启动和 UDP listener 检查保留为可选增强证据。创建任务只创建未启动的容器并验证 Docker 身份标签、挂载和端口，不把容器创建成功表述为游戏已启动。
- 前后端 catalog、创建 admission、worker 与 UI 均以 `verified=true` 为放行条件；`runtimeVerified` 仍独立记录，不能由基础验证伪造。
- 测试：`scripts/test-instance-release-catalog.py`、`scripts/test-instance-create-task.py`、`scripts/test-instance-create-ui.mjs`。

## 20. 2026-10-20：B4 删除与任务阶段记录

- 新增 `preview_instance_delete` / `delete_instance`：预览绑定 registry revision、停止容器 ID/状态、network endpoint 和实例目录快照；执行时在 registry lock 内重验，并按 operationId 幂等。
- 仅删除身份标签匹配的 stopped/created/exited/dead 容器和 network；运行容器、活动/待核实任务、外部 network endpoint、路径异常和计划变化均阻断。Master/Caves 世界目录有文件时要求保护备份；删除后实例目录归档到 retired，再释放端口。
- 创建任务状态查询附带有界持久 journal 阶段记录。专服列表恢复 creating/createFailed 的远端任务，并可查看失败摘要、诊断和阶段时间线；不声称采集容器标准输出日志。
- 继续创建仍未实现；真实删除需 Linux Docker 主机实测，隔离测试入口为 `scripts/test-instance-delete.py`。
