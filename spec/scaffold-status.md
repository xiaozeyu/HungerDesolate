# 项目骨架状态

- 日期：2026-10-17
- 版本：0.1.0
- 工程名：hunger-desolate；应用标识：app.hungerdesolate.hub

## 已落地

| 工作包 | 本次落地 | 仍需完成 |
| --- | --- | --- |
| S1-01 工程 | Vue/Vite/TypeScript/Tauri/Rust、独立标识、SQLite 迁移、锁文件、检查命令、SSH/keyring 接口 | 系统凭据库实机权限验收、正式发行元数据、更多持久化领域 |
| S1-02 外壳 | 236px 侧栏、56px 顶栏、单底栏、亮暗主题、主机/实例导航、Reka Select/Tooltip | Inter/JetBrains Mono 本地资产、任务详情 Dialog、完整视觉证据 |
| S1-03 隔离 | 完整 target、列表选择授权、上下文 epoch、query sequence、任务 sequence/目标保护 | 全领域 revisionFloor、dirty 草稿、真实断线/事件对账 |

实际命令包含本地连接 CRUD，以及下文列出的 SSH 查询、信任、凭据和只读登录验证；不插入演示主机。元数据保存不自动连接 SSH，普通 DTO 不包含凭据。

界面预览必须显式开启，使用内存 fixture，地址为文档专用示例网段；切换主机清除选中实例，冷启动或直接详情地址回主机/列表。所有远端操作按钮禁用；真实连接信息的新增、编辑、删除可用。演示数据不写数据库。

## 后续依赖

先完成 S0 Docker 实验与契约核验，再接入 S2 SSH 身份发现/注册/端口/任务。远端请求 DTO 与 schema 仍以 `api-contract.md` 为实施基线；当前内部 fixture 类型只实现骨架实际使用的字段，不宣称全量契约已完成。

S0 与 S2–S4 尚未全部完成；S2 已实现连接、只读身份检查和 B2 创建接纳内核，但尚未接入持久远端任务与真实部署。迁移、定时备份、健康历史和导出为后续 P1。

## 2026-10-16：本地连接管理

- 实现添加主机 Dialog、连接编辑表单、删除确认；表单共享 Reka 行为与字段错误提示。
- recordVersion 隔离本地记录修改，更新/删除使用 CAS；冲突保留输入并重新读取以比较。
- schema v2 只追加 0002_local_operations.sql；操作结果与记录修改在同一即时事务提交，operationId 在 90 天窗口内重试关联原结果。
- 修改地址、端口、用户名或认证方式时清除旧 hostId 绑定；仅改名保留身份。
- ServerInput 只接受连接元数据，不收集密码或私钥；未知字段拒绝反序列化。凭据/keyring 管理尚未实现。
- 浏览器只预览表单，提交有效输入提示使用桌面环境；真实管理使用 npm run desktop:dev。
- 冷启动直接打开连接页时先加载记录；本地写入完成使在途旧列表查询失效。

测试记录见[本地连接验收记录](evidence/local-connections-2026-10-16.md)。本工作属于 S1-01 本地数据闭环，尚未放行 S2 远端部署。下一步接 SSH 指纹扫描/确认与独立 keyring 认证，再做远端主机身份发现。

## 2026-10-16：SSH 信任、凭据与只读登录验证

- 已实现 get_ssh_connection_state、scan_server_fingerprint、confirm_server_fingerprint、save_server_credentials、connect_server、get_credential_cleanup_status、cleanup_credentials。
- 指纹确认有五分钟挑战、连接版本绑定、第二次 SSH 握手与提交前复核。认证前再次验证公钥，没有跳过校验开关。
- 密码、私钥和口令只进入系统凭据库；SQLite 只存随机引用、操作收据和待清理记录。页面切换/关闭清空敏感输入。
- schema v3 追加信任、挑战、凭据清理与收据表。文件锁协调凭据库与连接记录；失败/崩溃候选可清理，删除错误保留稳定错误码。
- 登录后以 SFTP 读取固定 host.json；不存在、无权限、格式异常、身份不一致分别处理，不创建目录。
- 只验证登录，不维持后台会话；hostId 变化不覆盖旧绑定。Docker 环境和注册表只读/创建接纳基础已实现，持久任务与真实部署尚未实现。

测试与限制见[SSH 接入验收记录](evidence/ssh-connections-2026-10-16.md)。下一步为已信任主机的环境检查、注册表只读发现与阶段 0 Docker 实验。

## 2026-10-17：添加主机认证输入修复

- 添加表单按认证方式展示密码，或本机私钥文件路径、原生文件选择和可选口令；连接设置替换凭据复用同一组件，并保留粘贴方式。
- 私钥由 Rust 限定大小读取并检查 PEM/OpenSSH 格式；只将内容送入系统凭据库，路径不落 SQLite，也不把文件内容回传前端。
- 本地保存凭据不依赖远端在线或已确认指纹；登录认证仍要求验证主机公钥。首次确认指纹保留预存凭据，替换已信任公钥继续使旧凭据失效。
- 新增主机分元数据/凭据两步；保留非敏感进度与 operationId，凭据失败后重试沿用原记录。关闭/切换清空敏感字段，关闭后不继续下一阶段的秘密保存。
- 本轮未新增远端部署能力。后续功能、依赖和验收条件已整理到[后续功能清单](next-features.md)。

## 2026-10-17：主机环境检查

- 已实现 `get_host_environment` 和固定只读探测脚本；通过已验证 SSH 会话检查 Linux/amd64、用户 UID、内存、磁盘、管理目录权限、Docker Engine、Compose、Docker 数据盘和必要工具。
- 结果使用版本化 Base64 行协议解析，区分未安装/权限不足/不可访问；响应绑定连接记录版本和 requestId，查询失败不会覆盖旧对象。
- `HostEnvironmentPage` 已接入真实 command/演示 fixture，显示采集时间、阻断数和可展开恢复步骤；页面保持极简，不显示实现说明。
- 当前仍不创建管理目录、不安装 Docker、不执行远端变更；`deploymentVerified` 永远等待阶段 0 Docker 验证。

验收记录见[主机环境检查记录](evidence/host-environment-2026-10-17.md)。下一步进入 A3 注册表/manifest 只读发现，再进入阶段 0 Docker 三专服实机验证。

## 2026-10-17：注册表与专服列表只读发现

- 已实现 `list_instances`：通过已验证 SSH/SFTP 读取固定 `/var/lib/famine-hub/registry.json`，随后读取每个活动实例的 `manifest.json`。
- 校验 registry/manifest schema、hostId、instanceId、Compose 项目名、注册路径、生命周期、重复 ID、版本和文件类型；读取前后复查 registry，变化时返回 `REGISTRY_CHANGED`。
- `retired` 实例保留在远端历史但不进入当前专服列表；`createFailed` 可展示但没有 manifest 时不能进入详情。注册表中的 runtime/players/build 不作为实时证据，列表显示未知。
- 主机实例缓存绑定 serverId、hostId、recordVersion 和 registryRevision；旧响应、旧注册表版本和其他主机结果不能提交。真实列表选择后才建立 instance selection grant。
- InstanceOverview 已改为真实读取实例 manifest；运行验证仍标记 unverified，世界/MOD 等真实页面不显示演示数据。

测试记录见[注册表接入验收记录](evidence/registry-instances-2026-10-17.md)。主机基础初始化已接入：固定管理路径预览、plan/operation 回执、并发锁、hostId 与空 registry 原子发布、断线后对账。Docker 自动安装开发进度更新于本节。

## 2026-10-18：Docker 自动安装

- 首发候选锁定为 Ubuntu 26.04 LTS / amd64 / SSH root；安装包来自 Docker 官方 Ubuntu APT 仓库，软件包包含 Engine、CLI、containerd、Buildx 与 Compose plugin。
- 主机环境响应新增 `distributionId`、`distributionVersion`、`distributionCodename`，从 `/etc/os-release` 作为数据解析，不执行 shell。
- 新增 `preview_docker_installation`，只读检查发行版/架构/root/systemd/apt、冲突包、dpkg 未完成状态、并发 APT、固定 APT 源与签名密钥路径、主机身份、`/var` 空间及官方 Resolute InRelease TLS 连通性；预检本身不写配置。
- 新增 `start_docker_installation` 与 `get_docker_installation_status`，任务 target 绑定 serverId/hostId/recordVersion/operationId，通过 root-owned bootstrap 目录和 transient systemd unit 持久运行。APT 安装 Docker Engine/CLI、containerd、Buildx 与 Compose plugin；不覆盖非预期源、不移除冲突包，APT simulation 发现删除动作会停止。
- 环境页显示安装条件、风险、启动确认和远端任务阶段；断线后可查询 task 状态。任务成功后回读 daemon、Compose v2 和安装版本。
- 自动测试覆盖支持/阻断预检、固定包与源校验、请求 target/plan 绑定、APT 删除保护、worker 验证和任务状态文件安全。Ubuntu 26.04 实机 APT/dpkg、systemd、SSH 断线与三专服阶段 0 尚未验收；本地测试不代表真实服务器已安装或安装链路已放行。

自动测试：`scripts/test-docker-install-preview.py`、`scripts/test-docker-install-worker.py`、`scripts/test-docker-install-task-state.py`。

## 2026-10-18：创建参数与端口预览

- Docker 安装主路径已获用户实机确认；重启、断线和游戏部署实验继续待验证。
- 新增已注册的只读 `preview_instance_create` 和持久 `create_instance` command；列表页“新建专服”表单只包含实例设置和容器拓扑，世界设置/Token 不进入创建窗口或预览请求。创建路线固定公网；创建任务完成容器 runtime 验证后结束，不写世界配置。
- 预览核验 HostTarget、连接/注册表版本、远端身份和受控文件；结合保留端口、IPv4/IPv6 socket、Docker HostConfig.PortBindings 生成 Master/Caves 的 game/steamAuth/steamMaster 同号端口表。
- 既有实例端口不完整、证据不可读或范围耗尽时阻断，不用空集合推断安全。预览始终明确端口未预留，游戏镜像/部署验证未通过时禁止创建。
- 所有请求提交含 error/finally 校验上下文、输入代次与最新序号，编辑、切主机、关窗和注册表变化使旧预览失效。
- 测试入口：`python3 scripts/test-instance-create-preview.py`、Rust `instance_creation` 测试、前端 `instanceCreation.test.ts` 和 `scripts/test-instance-create-ui.mjs`。不替代真实 Linux 预检与阶段 0 验收。

## 2026-10-19：创建接纳内核

- 新增 `remote-scripts/actions/instance-create-control.py`：在远端 `registry.lock` 内重查预览端口和实际占用，分配不可变 instanceId，原子写入 `creating` 注册项、完整端口预留和 operation 幂等回执，并保留 owner token/generation/租约审计。
- 该内核不创建实例目录、不写 Token、不生成 Compose、不启动 Docker，也未注册 Tauri command；必须由后续持久 controller/worker 消费接纳结果。
- 隔离测试覆盖同 operation 重放、输入变更、registry/预览端口变化、已有预留、锁竞争和发布失败：`python3 scripts/test-instance-create-control.py`。

## 2026-10-19：持久创建任务基础设施

- 新增 `remote-scripts/actions/instance-create-task.py` 和 `remote-scripts/actions/instance-create-worker.py`：持久化创建 request、admission、status、journal，使用 systemd transient unit 脱离 SSH，按 sequence 更新状态，并在 worker 消失后对账为 `interrupted`。
- 当前 worker 在 `download` 阶段没有 release 时返回 `CREATE_RELEASE_REQUIRED`；runtime 未验证时在 `publish` 返回 `CREATE_DOCKER_RUNTIME_REQUIRED`，保留实例/端口现场和恢复动作。创建 command 已注册，但不写世界配置、不写 Token。
- 隔离测试覆盖任务接纳、同 operation 重放、输入复用冲突、部署门禁失败和进程消失对账：`python3 scripts/test-instance-create-task.py`。

## 2026-10-19：实例目录与初始 manifest

- 创建 worker 已完成固定实例目录树和初始 `manifest.json` 原子发布；目录包含配置、desired、Master/Caves 数据、release、staging、任务和锁路径，manifest 不包含 Token。
- 目录阶段成功后没有 release 时在 `download` 返回 `CREATE_RELEASE_REQUIRED`；有合格 release 时只写 staging Compose，仍不创建 Docker 网络/卷或启动容器。

## 2026-10-19：release 校验与 Compose staging

- 新增 `remote-scripts/actions/instance-release.py`：校验固定 `linux/amd64` release、镜像/产物摘要和启动命令，并生成 Master/Caves digest pin Compose。
- worker 只把合格 release 的 Compose 写入 `staging/<taskId>`，通过 config 后生成资源计划并在 `publish` 返回 `CREATE_DOCKER_RUNTIME_REQUIRED`；没有 release 时返回 `CREATE_RELEASE_REQUIRED`，不会覆盖活动 Compose 或启动 Docker。
- staging Compose 通过受控 `docker compose config --quiet` 后写入资源计划；config 失败保留 staging 并返回 `CREATE_COMPOSE_CONFIG_FAILED`，config 成功后在 `publish` 以 `CREATE_DOCKER_RUNTIME_REQUIRED` 阻断，不创建容器。
- 测试入口：`python3 scripts/test-instance-release.py`；真实 release 获取、Compose config、网络/卷、容器和联网仍待阶段 0。

## 2026-10-19：Docker 资源创建内核

- 新增 `remote-scripts/actions/instance-docker.py`：runtimeVerified release 才能创建带 host/instance/schema 标签的独立 bridge network，并执行 `docker compose create --no-start`。
- 默认任务因 runtime 尚未经过阶段 0 验证继续返回 `CREATE_DOCKER_RUNTIME_REQUIRED`；runtimeVerified 路径已覆盖 network/容器创建、labels/挂载/端口验证和 runtime/active-release 发布，不会自动启动游戏。隔离测试入口：`python3 scripts/test-instance-docker.py`。

## 2026-10-19：容器创建与 runtime 验证

- runtimeVerified release 会创建或复用带身份标签的 network，执行 `docker compose create --no-start`，验证容器 labels、挂载源和端口发布。
- 验证通过后发布实例 `compose.yaml`、`active-release.json`、`runtime.json`，创建任务进入 succeeded；不写世界配置、不写 Token、不启动游戏。
- 下一阶段是容器环境就绪后的世界配置、Token 和启动任务。

## 2026-10-19：DST release catalog 只读发现

- 新增 `list_instance_releases` command 和 `probes/instance-release-catalog.py`：只读验证远端 hostId、releaseId、Linux amd64、镜像/产物摘要、启动命令和 runtimeVerified 标记。
- 缺少 catalog 或没有 verified/runtimeVerified release 时返回 `CREATE_RELEASE_REQUIRED`；不会下载镜像、覆盖 catalog 或伪造可用 release。
- 测试入口：`python3 scripts/test-instance-release-catalog.py`。

## 2026-10-19：release 准备前置校验

- 新增 `probes/instance-release-prepare.py`：按 catalogDigest 和 releaseId 检查远端 catalog 是否变化、release 是否 verified、固定 staging 产物目录是否存在。
- 缺少 catalog、catalog 变化、release 未验证或产物未 staging 时返回明确阻断；该阶段不下载、不覆盖活动 release。
- 测试入口：`python3 scripts/test-instance-release-prepare.py`。

## 2026-10-19：release 导入任务与前端入口

- 新增 `preview_instance_release` 和 `import_instance_release` command；导入任务校验 catalogDigest、release metadata、staging artifact 和 runtime evidence，成功后原子更新 catalog 的 `runtimeVerified`。
- 创建窗口显示 release 状态；存在 verified 但未 runtimeVerified 的 release 时可点击“验证 release”，成功后刷新 catalog，再允许创建容器。
- 导入流程不下载任意远程资源，不覆盖活动 release；没有 staging artifact 或 runtime evidence 时保留失败状态。

## 2026-10-19：前端创建任务集成

- `InstanceCreateDialog` 在预览后提供”创建容器”，提交 `create_instance` 并轮询任务状态；任务完成后显示容器 runtime 验证状态，世界配置仍未在创建流程中出现。
- 前端补齐创建任务阶段、release/Compose/Docker 错误码、恢复动作和可选任务结果模型；专服列表按完整 hostId/instanceId target 展示创建阶段或失败原因。
- 新增 `instanceCreationTask` 映射与测试；`npm run check` 通过。
- ✅ 创建提交与 operationId 绑定已实现
- ✅ 任务状态轮询（1.5秒间隔）已实现
- ✅ 成功后关闭对话框、刷新列表已实现
- ✅ 恢复动作按钮已添加（具体实现待补充）
- ⚠️ Release 验证进度反馈不足
- ⚠️ 断线重试按钮未实现
