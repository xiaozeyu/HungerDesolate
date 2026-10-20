# 后端实施文档

- 日期：2026-10-19
- 状态：实施设计；B2 创建接纳内核、B3 持久任务基础设施和 B4 目录/manifest/release/Compose/资源计划/Docker 创建/runtime 验证内核已落地，真实 runtime/产物实验仍未完成，下一阶段为世界配置
- 接口依据：[共享接口契约](api-contract.md)
- 范围：Rust/Tauri 桌面核心、SSH/SFTP、远端受控脚本与 Docker Compose

## 1. 架构与模块

```text
Vue api wrappers
  → Tauri commands：校验、权限、目标解析、任务入口、DTO
    → domain：配置/端口/备份/运行规则的唯一实现
    → ssh / secrets / persistence：公共 I/O 入口
      → SSH/SFTP：已信任连接 + 受控数据文件
        → remote controller：锁、任务持久化、Docker/Compose 编排
          → Docker worker：下载/校验/备份/恢复 staging
          → Compose Master/Caves：实例游戏运行
```

桌面关闭后仍须执行的任务不能依赖 Rust 内存线程。Rust 负责提交、观察和对账，远端 controller 与 worker 负责已接受任务。Docker worker 不具备 Docker daemon 操作权限；主机 controller 执行 Docker 命令。这是同一 Docker 部署实现的编排分工，不增加其他专服运行后端。

### 1.1 建议目录

```text
src-tauri/src/
  commands/{servers,hosts,instances,world,mods,backups,updates,tasks,diagnostics}.rs
  dto/{target,query,instance,world,mods,backup,task,error}.rs
  domain/{identity,ports,world_config,mod_config,release,backup,runtime}.rs
  remote/{session,paths,protocol,script_bundle,task_client}.rs
  persistence/{db,migrations,state,task_repository,event_repository}.rs
  secrets/{credentials,redaction}.rs
  state.rs                         # 小型内存状态与连接索引
remote-scripts/
  lib/{identity,locks,atomic_files,task_state,compose}.sh
  actions/{prepare_host,admit_task,task_controller,reconcile,cleanup}.sh
  worker/{install,prepare_mods,backup,restore}.sh
docker/
  runtime/Dockerfile
  worker/Dockerfile
```

只创建有实际生产调用的模块，不先铺空壳。配置渲染、MOD Lua 解析、运行证据归一化为纯函数；不得在前端、Rust、脚本分别维护等价业务判定。Rust 渲染结构化配置与执行计划，远端脚本执行计划并复核身份、锁、权限、路径与实际资源。

保留 Tauri 2、ssh2、rusqlite、keyring、serde、uuid、sha2 等已经验证的能力。新增依赖必须证明现有能力无法合理实现；只有一个生产实现时不创建 trait、factory 或 backend registry。模块不继续增长到 1200 行以上。

### 1.2 阻塞与连接生命周期

- ssh2、SQLite、keyring、ureq、大文件 I/O 进入 `spawn_blocking` 或专用线程。
- 内存锁只复制配置/句柄索引或提交状态；释放 MutexGuard 后再进行网络、文件、线程等待。
- SSH 会话带 serverId、连接版本、已验证指纹和读到的 hostId；连接参数变更后旧会话失效。
- 不跨线程无约束复用单个 ssh2 session。采用每连接串行工作入口或独立查询会话，长任务只占短提交与观察连接。
- 配置写入对账使用固定 target，禁止在回调时读取当前 UI 选中对象。

## 2. 身份解析与权限

每次业务调用执行：参数 schema/格式 → 查本地 serverId → 验证 SSH 信任 → 读取远端 host.json → 比较 hostId → 查 registry instanceId → 校验 manifest 身份/版本 → 生成受控布局 → 操作权限/锁 → 执行。

读请求可使用有效会话缓存，但在敏感写入、重连和错误恢复前必须重读归属。任何不匹配返回稳定错误，无“默认专服”“第一个实例”或按名称寻找目标的 fallback。

实例 UUID 不携带用户文本。Compose 项目名由 UUID 去连字符后生成 `fh_<32位小写hex>`；Master/Caves 使用服务名 master/caves，不指定全局 container_name。所有受管容器带 app、schemaVersion、hostId、instanceId、role 标签；worker 额外带 taskId、operation。发现时验证标签、Compose 项目、manifest 与 mount 源一致，不能只按名称或端口接受容器。

主机权限只开放必要目录、受控脚本和 Docker 操作入口。具有 Docker daemon 操作能力的主机账户仍属于高权限边界；不把 Docker 用户组当普通低权限账号。禁止容器 privileged、host 网络、挂 Docker socket、任意宿主目录挂载。容器 UID/GID、capabilities、可写目录在实验后固定。

完整环境准备仍由后续 `prepare_host` 任务完成：预览目录和权限，经验证的权限流程安装脚本/镜像，重新读能力结果。Docker 安装方式必须在目标发行版上验证，不能执行未经固定来源与校验的远端 curl pipe shell。

2026-10-17 实施拆分：先提供独立短写入 `initialize_host`，仅创建 `/var/lib/famine-hub`、不可变 hostId 和空 registry。无需 Docker，不安装软件、不提权、不启动 controller；这不放行阶段 0 部署门禁。固定 Python 脚本从应用编译产物经受信任 SSH stdin 运行，要求 Linux 的 `/usr/bin/python3` 和当前账户可写的受控父目录。生产不接收自定义路径。目录逐级用 nofollow 打开，拒绝符号链接、不安全所有者/组或其他用户可写路径；已有目录只接纳空目录，已有完整身份只能只读发现。

初始化计划保存在独立 SQLite 表，绑定 serverId、recordVersion、已确认指纹、脚本 SHA-256、目录快照和 10 分钟有效期。确认后在发送前持久标记 started。固定兄弟目录 `/var/lib/famine-hub.bootstrap`（0700）保存 flock 和 operationId 回执，回执先于业务文件写入。host.json/registry.json 各自完整 staging、fsync、无覆盖链接发布、目录 fsync；不宣称两个文件同时出现。未完成回执可验证并复用自己的 staging/文件，拒绝覆盖或清理其他数据。完成后重新通过 SFTP 验证身份、注册表，再绑定本地 hostId。

初始化命令不自行 sudo。普通 SSH 用户没有 `/var/lib` 写权限时，环境预检阻止确认并要求管理员预建缺失目录、设为 SSH 账户所有且 mode 0700：`/var/lib/famine-hub` 与 `/var/lib/famine-hub.bootstrap`。已有目录含文件时不通过改所有者/权限绕过保护；必须先核实原数据。环境预检还需 `/usr/bin/python3` 可用。

Docker 自动安装首发目标固定为 Ubuntu 26.04 LTS (`ID=ubuntu`, `VERSION_ID=26.04`, `VERSION_CODENAME=resolute`)、amd64、SSH root。`preview_docker_installation` 只读检查管理根身份、APT 冲突包/并发/dpkg 状态、所有 APT 源中的 Docker 配置、固定源/签名密钥路径、systemd、`/var` 空间和 Docker 官方仓库连通性。确认后 `start_docker_installation` 绑定 serverId/hostId/recordVersion、预检 snapshot 和 operationId，经 root-owned bootstrap 目录发布 task worker，再用 transient systemd unit 脱离 SSH 会话执行；task status/journal 保留在主机并由 `get_docker_installation_status` 对账。使用固定 Docker APT signed-by 源及 Engine/CLI/containerd/Buildx/Compose 包；拒绝覆盖冲突源、拒绝自动移除任何包，APT simulation 发现 remove 动作则停止。安装后验证 daemon 为 linux/amd64、Compose v2、systemd active 和 package 版本。Docker daemon 提供 root 级主机能力；确认界面说明该权限及 Docker 发布端口与 UFW 的影响。当前实现尚未在 Ubuntu 26.04 实机验证；阶段 0 仍须覆盖 apt/dpkg、重启、断线、daemon/主机故障和三专服部署。

SSH/桌面中断只表示结果未知；页面重开按原连接版本和 plan 查询回执。预览有效期内无回执时保留原 operationId 重试，避免仍未接纳的旧 SSH 进程延迟执行；有效期届满且在远端锁内确认无回执后才释放本地 pending。incomplete 使用同 operationId 继续；succeeded 重新校验后完成本地对账。已有回执的重试优先于 plan 过期检查。主机断电后的文件系统持久性仍需 Linux 实机验证；不承诺后台持续执行。这里只实现初始化短操作回执，不能作为 B3 的远端任务系统。不会自动删除孤立 staging/回执或已有管理数据。

## 3. 本地持久化

新应用数据目录存 SQLite 和非敏感缓存；命名空间在建工程时确定，与旧项目不同。SQLite 是连接/缓存/任务索引，不是远端实例存在性、端口占用或部署成功的权威来源。

| 表/状态 | 主键、约束、索引 | 内容与生命周期 |
| --- | --- | --- |
| servers | server_id；版本字段 | 连接元数据、host_id/null、认证方式、凭据引用、已确认指纹；删连接不删远端 |
| host_bindings | server_id FK；索引 host_id | 管理根、远端身份观察证据、确认时间；同 host 多连接可并存 |
| instance_cache | PK(host_id,instance_id) | manifest 摘要、最后采集时间、schema；失联只读，远端发现后对账 |
| drafts | PK(host_id,instance_id,domain) | baseRevision、非敏感 fields、draftVersion、updatedAt；仅 world/mods 等显式领域 |
| tasks | task_id；UNIQUE(host_id,operation_id)；索引(host_id,instance_id,created_at) | 固定业务目标、操作、最后 sequence、状态、远端目录身份、脱敏结果 |
| task_events | PK(task_id,sequence)；索引 created_at | 脱敏状态变更证据，可分页保留 |
| metadata_cache | workshop_id | 通用元数据、采集时间、schema，不存启用状态 |
| preferences | 固定 key | 主题、最后主机、窗口；最后实例不授权自动进入详情 |

实例/分片先复用状态模型与 JSON 摘要，不引入只有 CRUD 的独立仓储层。任务与事件因分页、保留、并发使用仓储。SQL 值参数绑定，动态表列只允许固定白名单。task+event、连接+binding 等多记录变更同事务。

迁移采用追加 SQL，设置版本校验与 foreign_keys。测试空库、上一版升级、事务回滚、删除连接、并发事件合并、重复 sequence。以 CAS 更新任务 sequence，低序号不得覆盖高序号。

设计保留值：本地终态任务摘要 90 天、事件 30 天，活动/待核实任务不自动删；普通快照按重新发现覆盖；非敏感草稿直到用户丢弃/成功应用/确认清理。远端详细日志另设容量，备份不按任务保留规则删除。所有这些是首版设计值，可通过实测收敛但不得无界增长。

密码、私钥、Klei Token、cluster_key 不进入数据库、缓存、草稿或诊断。凭据引用只保留内部仓储，不直接序列化为 UI DTO。删除连接仅在无其他引用时删除其 keyring 项，失败记录事件并给出清理入口。

## 4. 远端布局与文件模型

```text
/opt/famine-hub/scripts/<scriptVersion>/   # 只读受控脚本及摘要
/var/lib/famine-hub/
  host.json
  registry.json
  locks/{registry,heavy}.lock             # flock inode，不随释放删除
  tasks/<taskId>/                         # 主机准备等尚无实例的任务
  operations/<operationId>.json           # 幂等索引，受保护，不含明文秘密
  instances/<instanceId>/
    manifest.json
    runtime.json
    compose.yaml
    desired/{world,mods}.json             # 期望配置，秘密只保留状态/引用
    desired-secrets/<secretVersion>/      # 权限受限的期望秘密文件，不覆盖活动秘密
    config/                              # 实际部署的集群/分片配置及秘密文件
    data/{Master,Caves}/                  # 世界数据
    mods/releases/<releaseId>/{Master,Caves}/
    active-release.json                  # 一份原子发布指针，记录完整发布组合
    staging/<taskId>/
    backups/<backupId>/{archive,metadata.json}
    logs/
    tasks/<taskId>/{request.json,status.json,journal.jsonl,worker-progress.json}
    locks/maintenance.lock
    locks/maintenance-owner.json
  retired/<instanceId>/                   # 数据保留区；历史查询可解析
```

远端管理根不与旧默认根混用。host.json 存 schemaVersion/hostId/createdAt。registry.json 存 schemaVersion/hostId/registryRevision/instances/portReservations/heavyQueue；instance entries 存身份、lifecycle、相对位置、pendingTaskId。创建接纳阶段允许增加有界 `operations[]` 回执项，保存 operationId、规范化 payloadDigest 和不可变接纳结果，不把秘密写入回执；registry 更新仅短时持有主机注册锁。

manifest 示例（省略无关字段）：

```json
{
  "schemaVersion": 1,
  "hostId": "11111111-1111-4111-8111-111111111111",
  "instanceId": "22222222-2222-4222-8222-222222222222",
  "displayName": "生存一服",
  "configRevision": 7,
  "composeProject": "fh_22222222222242228222222222222222",
  "cavesEnabled": true,
  "autostart": true,
  "ports": [
    { "shard": "master", "purpose": "game", "protocol": "udp", "bindAddress": "0.0.0.0", "hostPort": 11000, "containerPort": 11000 }
  ],
  "desiredDigests": { "world": "sha256:...", "mods": "sha256:..." },
  "deploymentId": "deployment-opaque-id",
  "dstReleaseId": "dst-opaque-id",
  "modsReleaseId": "mods-opaque-id",
  "worldAppliedRevision": 7,
  "modsAppliedRevision": 6,
  "scriptVersion": "scripts-opaque-id"
}
```

示例 ports 仅展示一个条目，生产必须覆盖每个启用分片的 game/steamAuth/steamMaster 全套项目。内部分片 masterPort 单独记录，不能加入宿主对外预留。

runtime.json 是最近证据与容器绑定，不能当实时事实。每次采样记录 snapshotId/capturedAt/source；镜像标签、实际 build、容器状态和目录摘要独立采集。外部手工修改文件导致 digest 不符时返回 drift/blocker，提供重新读取比较；不以本地 desired 静默覆盖。

文件发布统一：写同文件系统临时文件 → schema/归属/内容校验 → 权限设定 → fsync 文件 → rename → 必要时 fsync 父目录。JSON 使用结构化工具生成。跨 manifest/registry 多文件不能声称单次 rename 具有整体事务性；通过持久 journal 与确定性的提交顺序恢复，并在恢复完成前阻断写入。

路径由可信根 + 校验 UUID + 固定子路径生成。拒绝任意前端路径、`..`、控制字符和越界 symlink；SFTP 上传到专属 staging，执行前重新 realpath/ownership 检查。归档解压拒绝绝对路径、路径穿越、设备文件及越界 symlink/hardlink；设置展开容量上限，防止解压耗尽磁盘。

## 5. Docker 运行与产物

### 5.1 Compose 生成

阶段 0 优先验证两个服务 master/caves；Caves 未启用时不生成其游戏服务与对外端口。每实例独立默认 bridge 网络，Caves 使用当前项目内可解析的 master 服务地址。认证 cluster_key 每实例随机生成，两个分片共用，其他实例不能复用。

Compose 模板从已验证 manifest + active-release 生成：

- 项目名显式 `docker compose -p <project>`，不依赖执行目录推断。
- 对外 ports 为 hostPort:containerPort/protocol，必须相等；分片通信 UDP 不 publish。
- DST 二进制包含在 digest 固定的 runtime image 中；配置、data、每分片 UGC 使用独立 bind mount，不覆盖镜像内 `/srv/dst`。
- Release runtime 验证使用一次性 Klei 配置包，在 `/run` 下建立隔离的 LAN-only Master/Caves 测试集群；不发布宿主端口，不挂实例数据，清理后仅发布不含 token 的 digest 绑定 evidence。
- runtime 镜像固定 digest；使用经阶段 0 证实的非 root UID/GID、init、信号退出与最小权限。
- Token/cluster_key 以限制权限文件提供，实际 DST 需要的文件名与位置在实验中确认；不进入 argv 或内联环境。
- 工作目录、启动参数、UGC 路径必须通过真实产物验证，不复制社区镜像自动更新入口。
- compose.yaml/启动脚本不暴露秘密值；inspect/docker logs 也不能输出秘密。

先执行 `docker compose config` 校验，再创建服务；容器 running 后重新验证 mount、label、端口、实际 build、分片关联及有限网络证据。Docker restart policy 不是完整健康判断。

### 5.2 开机启动与停止

保存 autostart 后显式更新并验证容器 restart policy。设计目标：enabled 的实例在 daemon/主机重启后运行，disabled 的实例保持停止；用户手工“停止”应持续到再次“启动”，不能随刷新重新启动。

Docker 的 always/unless-stopped 对手动停止和 daemon 重启行为不同。阶段 0 同时测主机重启、daemon 重启、手动停止、容器重建；优先采用与持续手动停止语义一致的策略，并记录最终 product 行为。此项未实测前 UI 不承诺“手动停止后下次开机会自动启动”。

停止流程给 DST 可验证的保存/优雅退出机会，等待设定超时，再明确记录是否强制终止。只有游戏容器退出、目标监听消失且绑定无残留时视为停止。启动/重启不隐式检查并下载新版本。

### 5.3 发布模型

releaseId 是实例内不可变安全标识；记录 imageDigest、实际 DST build、产物 checksum、Workshop 项摘要、创建任务和验证结果。staging 校验成功后才成为 release。

每次部署记录完整组合 `deploymentId → configRevision/digests + dstReleaseId + modsReleaseId`。停止目标分片后，在一个 active-release.json 内原子切换组合，再按该组合重建受管服务。不要分散修改多个 symlink 后假设任意断点都是一致发布。manifest 最后记录验证结果；journal 可恢复中间态。

DST 二进制可在同实例两分片只读共享；UGC 可写区按分片独立，MOD 任务顺序准备一致版本。若实际 DST 自动写入要求与只读布局冲突，阶段 0 调整具体 mount 与启动流程，但不允许多个实例共享可写 MOD 或在线覆盖当前产物。

不主动承诺从官方重新下载任意旧 build。只有留存历史产物、MOD 摘要及完整保护备份时才提供自动回滚。release GC 计算 active、备份、任务、恢复点引用，保护引用永不清除；容量不足时阻断新操作，不能删掉唯一恢复产物凑空间。

## 6. 端口分配与资源预算

端口事实来源是远端 registry + 当前 Linux socket/Docker publish 证据，端口预留保留至实例完成删除。预留项包括 hostId/instanceId/shard/purpose/protocol/bindAddress/hostPort/containerPort/reservedAt。

创建流程：

1. 在注册锁内读取最新注册表和 revision。
2. 收集实际 UDP/TCP bind 与 Docker 发布。通配地址与具体地址按保守冲突规则合并；首期 IPv4 通配发布，检测 IPv6 双栈对同端口的冲突。
3. 分配完整端口集合，检查同协议同地址空间与预留项；停止实例仍占预留。
4. 公网模式按支持范围分配；LAN 模式将游戏端口限制在规划引用的 10998–11018 范围，实际规则待 Klei/联网核验。范围耗尽明确报错，不自动改公网模式。
5. 原子写预留与 creating 记录后立即释放注册锁。下载不占注册锁。
6. 启动前重新检查外部占用，启动后验证发布与进程实际监听；存在 TOCTOU 竞争则失败保留预留与现场，不声称一次扫描保证永无冲突。

端口由程序默认自动分配，用户显式端口也必须全套校验。云安全组与防火墙仅生成需求说明；应用不自动修改。公网登记、Steam 查询、直连、玩家进入、洞穴迁移分别验收。

空间预算至少包含当前数据、完整新发布、staging、保护备份、保留旧产物、restore 解压与安全余量，不能只检查压缩包大小。无法估算时预览显示未知并在执行前实测。每主机至少三实例的内存/CPU预算在实验记录；未定义硬件下不宣称任意主机能运行三服。

## 7. 锁、任务接纳与幂等

### 7.1 锁职责

| 锁/记录 | 作用 | 持有期间 |
| --- | --- | --- |
| registry.lock | 注册、端口、队列、幂等提交关联 | 短临界区，不下载/备份 |
| heavy.lock | 同主机重型 worker 限流 | 安装/更新；恢复/迁移同样进入该资源通道 |
| maintenance.lock | 单实例所有配置、启停、备份、恢复、更新、删除 | 实际变更与验证期间 |
| pendingTaskId | 队列阶段保留该实例变更意图 | 不占 flock；其他写入返回 INSTANCE_BUSY |
| task 状态写锁 | 任务 sequence、取消请求、对账更新 | 短持有，防止 controller 与对账竞争 |

涉及 registry 与 maintenance 时必须 registry → maintenance。重任务执行先取得 heavy 再取得 maintenance，期间不取 registry；多锁总顺序为 registry → heavy → maintenance → task 状态写锁，但接纳阶段不阻塞等待 heavy。修改注册表的尾部阶段先结束长变更、释放 heavy/maintenance，再按 registry → maintenance 重新校验任务 owner 后提交。不能从 maintenance 内反向等 registry。

flock 是排他权威，owner JSON 是审计/租约/代际校验。owner 包含 taskId、随机 ownerToken、递增 generation、进程证据、heartbeatAt、leaseUntil。续租失败停止进入新的变更阶段并记录待核实；不因 lease 超时就 unlink 锁文件或抢占仍被 flock 持有的任务。

锁文件 inode 永不通过释放操作删除。清理验证 ownerToken/generation；旧任务无权清除新 owner。进程消失后先检查 flock 可获得、任务 journal、worker、Compose 状态，才能恢复。每个原子阶段前重新校验 owner，防止过期 controller 在接管后继续写入。

### 7.2 接纳流程

1. 校验信任、target、plan、输入及 operationId；查远端幂等索引。
2. 已有相同 operationId/摘要时返回原 task/结果；不同摘要返回 OPERATION_ID_REUSED。
3. 在短注册/实例锁内复核 revision/归属，记录任务 request、queued 状态、operation 索引和 pendingTaskId，写 journal。
4. 提交记录有任何一步失败，返回明确阶段；恢复扫描能补全或撤销未完成接纳，不返回无持久记录的 taskId。
5. 启动主机 controller 并验证 PID/任务标签与状态可发现；提交应答丢失也能用 operationId 找回。
6. controller 按队列顺序取得资源与实例锁，重新验证 plan/revision/空间/端口再运行。

主机 controller 是固定版本的受控脚本，采用已验证的脱离 SSH 会话启动方式，日志重定向到任务目录；不是专服的另一种运行后端，不新增 systemd 专服模式。具体 detach/进程监督方式必须在阶段 0 证实，不能只用 `command &` 视为可靠后台执行。

初始化前的任务使用受控 bootstrap 目录保存 schema、ConnectionTaskTarget、operation 索引和状态。hostId 建立后在主机任务索引记录其来源关联，避免提交应答丢失导致再次初始化。bootstrap 目录权限/并发初始化锁及关闭 SSH 后持续执行也属于阶段 0 必验条件。

controller 启动 Docker worker：worker 只挂当前实例必要 staging/tasks/data/releases 路径，不挂全部主机根、其他实例或 socket。worker 输出进度到自己的 progress 文件，controller 是 status/journal 的单一业务写入者，统一增加 sequence。worker 文件操作服从 controller 的锁租约，controller 丢失后不独立切换发布。

任务 request 不含明文秘密；Token 通过 SFTP 写受保护临时文件后以文件引用进入计划。敏感临时文件成功消费后清理，失败则限制访问并进入恢复清理，禁止通用 JSON 序列化记录其内容。

## 8. 任务执行、取消与断线

各长任务采用持久阶段机：每阶段写 started → 执行动作 → 写证据/摘要 → 写 completed，再进入下一阶段。记录原部署、原运行状态、保护备份、目标部署、remoteChanged、取消安全边界；不能仅存百分比。

| 通用阶段 | 可取消 | 断线/崩溃后处理 |
| --- | --- | --- |
| queued / preflight | 是 | 清除有效 reservation，确认无变更后 cancelled |
| download / stagingVerify | 是，等待安全边界 | 下载到独立 staging；可继续准备或清理，不能当已部署 |
| stop / backup | 依阶段 | 完成一致性动作后处理取消；按原运行状态恢复 |
| publish / restoreCommit | 否 | 对照 journal/active-release/文件摘要对账，不盲目重复 rename |
| start / verify | 否或由具体流程声明 | 验证目标实际状态，失败给恢复动作 |
| finalize / cleanup | 否 | 保存终态后清运行资源；清理失败单独记录 |

SSH 断开不停止 controller/worker。客户端标 observation=stale，重连读取 status/journal 和真实容器；桌面退出不发送默认 cancel。Docker daemon 重启、worker 被杀、主机重启不保证任务继续执行，必须有 interrupted 证据与恢复路径；worker 禁止通过 restart policy 自动重复破坏性 action。

取消请求写入任务控制文件；controller 在安全边界处理，清理/恢复后才记 cancelled。原子发布期间拒绝取消。任务状态与原子文件证据冲突时保持待核实，按固定恢复规则形成更高 sequence 的对账记录。

后端状态判定不依赖日志文本的偶然关键词作为唯一证据。运行证据采集有 timeout、bytes/lines 上限和脱敏；成功必须满足本操作的最后验证条件。任务容器成功退出仅证明 worker 阶段，不代替游戏启动与联网验证。

## 9. 创建、配置与 MOD

### 9.1 新建专服

预览环境与端口 → 注册/预留 → 建目录/拓扑 manifest → 准备镜像与 DST 发布 → 渲染配置/Compose → 创建但不启动容器 → 验证容器环境。创建阶段不写世界配置、Token 或运行时世界参数；容器环境验证完成后，才进入世界配置保存/应用流程。

创建前初始化只包含实例身份、拓扑、端口和 autostart 等容器字段；世界配置和 Token 不写入初始 manifest。`startAfterCreate=false` 时完成镜像、容器身份、挂载和停止状态校验，不假设网络已验证；世界配置在容器环境验证完成后由独立配置流程提交。

失败写 createFailed，保留 instanceId、端口、阶段、日志和产物。继续创建使用相同身份并校验已完成阶段；清理确认后停止资源、保护数据、retired，再释放端口。不把失败记录移除来隐藏异常。

### 9.2 世界配置

save 只更新 desired 与 revision；不立即改活动 config/data。tokenChange 由 keyring/受保护远端 desired-secrets 文件处理，普通 desired JSON 不存明文。apply 才将选定 secretVersion 发布为活动配置；保护备份保留原秘密恢复材料/受保护引用。CAS 或写入失败不得提前覆盖正在运行实例的 Token。

apply 以已保存 revision 生成预览，实例锁内重查 → staging 渲染/解析校验 → 如需停服先优雅停止 → 创建一致保护备份 → 发布配置与完整 active-release 组合 → 按原运行状态或明确用户选择启动 → 实际重读 digest/状态。

禁用洞穴不能直接删除 Caves 世界数据；停止洞穴、更新拓扑并保留存档，重新开启默认复用原存档。重置世界为未来单独危险动作，不能藏在洞穴开关/普通配置保存中。端口变更须在 registry → maintenance 短阶段重新预留整套，发布验证后释放旧预留，失败恢复旧端口。

### 9.3 MOD

Workshop 通用缓存只提供元数据。实例 desired 记录 workshopId、enabled、配置；下载、应用、检查版本绑定实例。配置 schema 来自受限解析结果，不执行 modinfo.lua 任意代码；安全解析失败允许展示明确“不支持自动配置”，不退化为 eval。

应用流程：解析/去重/校验 → staging 顺序下载每分片需要的内容 → 记录可识别实际版本/摘要 → 验证一致性与权限 → 停目标分片 → 保护备份 → 切换完整部署 → 启动/重读配置与运行证据。

下载成功、配置已部署、运行验证通过分别记录阶段。任何 MOD 自动下载或写 UGC 行为必须在阶段 0 核对；不得因启动时会下载就省略版本和失败证据。手动“检查更新”是只读查询，不占维护栏；真正下载/部署进入任务。

## 10. 备份与恢复

### 10.1 备份内容与一致性

包括：完整世界数据、可复现配置、configRevision/digests、DST build/产物标识、MOD 发布摘要、schema、hostId/instanceId、必要秘密恢复材料或受保护引用。普通 keyring 条目不自动装入备份；无法恢复 Token 时明确标记需要重新提供，不宣称备份完全自足。

默认使用可验证停服备份，步骤：检查空间 → 保存原运行状态 → 优雅停止 → 打包/校验 → 发布 backup metadata → 恢复原运行状态并验证。运行中复制不构成默认一致备份；未来热备须独立证明。

备份先到临时路径，生成文件清单/checksum，校验归属、读取可用性和存档结构后才进入列表 verified。metadata 包含保护的 deployment/产物引用。若压缩、校验或重启失败，分别保留明确状态，不能把“有一个压缩包”当保护备份有效。

游戏已在其他维护任务内停止时，由同一锁 owner 调用备份子阶段，不能递归再获取自己的 flock。备份操作失败时阻断下一破坏性阶段。创建任务尚无存档、首次安装等例外要记录“无已有世界”，不是任意跳过备份。

### 10.2 恢复

1. 校验 backupId → 原实例归属 → checksum/schema/路径安全 → 产物可用与兼容性。
2. 预览目标、当前保护点、空间、停服和旧版本影响；不允许普通跨实例恢复。
3. 锁内重新检查；解压到 staging 并验证容量、权限、摘要。
4. 停分片，生成当前状态保护备份。
5. journal 记录原 data/config/active-release，将当前数据移动到受控恢复区，提交完整恢复点；每个 rename 有前置/完成证据。
6. 恢复期望配置时生成一个新的递增 configRevision，记录来源旧 revision，不将当前 revision 回退到备份数值。
7. 以一致 DST/MOD/配置/世界组合启动并验证；失败停止新世界，保留现场和恢复前保护点。

若不可取得旧 DST/MOD 产物、版本不兼容或 checksum 不通过，提交前拒绝自动恢复。恢复失败不自动在两个不同版本间循环尝试；仅执行经过验证且有完整恢复点的安全回退，否则留待用户选择。

下载由 SFTP 执行，本地目标来自用户文件选择产生的授权句柄。下载先临时文件，远端/本地摘要一致后 rename；包含秘密的备份需清楚提示并保护本地权限，不复制秘密到日志。

下载 task/status 与 operationId 在本地持久化，桌面退出不能承诺继续传输。重启后检查临时文件与摘要、恢复/重下该备份，不对远端世界执行任何写入。所有其他远端维护任务仍按 controller/worker 模型持续和对账。

## 11. 更新、回滚与删除

### 11.1 更新

check 采集可用信息；preview 固定目标 build 和 MOD 摘要；run 按预览执行，不在排队后无声升级为更晚版本。

流程：取得重任务/实例锁 → revalidate → staging 下载/校验 → 停分片 → 一致保护备份 → 切换完整部署 → 启动 → 验证实际 build、MOD、存档、容器身份与必要网络证据 → finalize。

只更新 A 的实例发布/config/data，不写 B/C 的 manifest 或可写路径。远端镜像缓存可以共用，但 runtime 镜像固定 digest 且 Docker 缓存不是实例世界数据。任务报告需包含更新前后产物证据和其他实例的不变性证据。

回滚必须恢复旧二进制、旧 MOD、旧配置和保护存档的组合。存档可能被新版本升级，只切 active-release 的二进制字段不算回滚。保护点/产物不全时禁用自动回滚；自动恢复旧组合成功仍记录原更新 failed，结果写“已恢复原版本”。

### 11.2 删除

默认有效保护备份 → 停分片 → 按 labels/项目归属移除该实例受管容器与网络 → 将数据移动到 retired → 验证无运行资源/监听 → 注册表标 retired 并释放端口。备份保留原 instanceId，历史查找通过 registry 的 retired locator。

目录移动前结束 worker，释放或重新获取管理锁时遵循统一顺序；不能移动仍持有不同 inode 的 lock 目录后继续假设互斥有效。删除 journal 在主机任务索引也保留定位，确保中途重连可发现。

删到一半失败写 deleteFailed，端口继续保留；复试按证据继续清理，不对不明资源执行宽泛 Docker prune/down -v。备份永久清理独立于删服，首期不提供列表批量删服。

## 12. P1：策略、诊断与旧部署迁移

| 功能 | 具体实施 | 接口/完成条件 |
| --- | --- | --- |
| 定时备份 | 远端受控 scheduler 容器，不挂 socket；定时产生请求，主机 controller 接纳并走同实例锁/备份流程；非桌面计时器 | get/save_backup_policy；错过执行、排队、DST 停服、重启后重复触发都有持久 operationId；桌面关闭仍执行 |
| 保留策略 | count/age/space 规则；保护备份、活动任务引用和唯一恢复点不删除，先预览再清理 | policy revision CAS；任何删除失败记录事件，不能清除索引假装成功 |
| 健康历史 | 有界实例采样，只记录证据、时间和 schema；摘要不推断未采集状态 | get_health_history 分页，主机/实例隔离 |
| 诊断包 | 有限日志、任务摘要、非敏感 manifest、能力/版本；排除 Token/密钥/存档个人信息和秘密文件 | export_diagnostics；持久化前脱敏，用户预览范围，测试敏感样本不出现 |
| 旧部署发现 | 只读扫描旧目录/service/端口/版本，返回 sourceId 与摘要，不接管旧默认目录 | discover_legacy_deployment |
| 迁移 | 预览 → 保护备份 → 停旧服务 → 复制到新独立实例 → 验证 → 启动新 Compose；失败停新并恢复旧 | preview/run_legacy_migration；旧路径/keyring 不自动授权导入，不删除旧数据 |

scheduler 只记录计划请求，由已验证的主机编排能力处理 Docker 变更；其触发与 controller 的可靠启动、主机重启、锁续租在 P1 单独验收，不能仅跑一个无监督 shell 循环就宣布完成。支持的宿主启动设施在阶段 0/P1 决策记录中冻结，不扩展为另一种专服部署模式。

旧端口复用必须证明旧服务已停止、绑定释放；选新端口则显式预览。禁止两套管理软件同时写活动世界。迁移 sourceId 绑定读取来源与摘要，不接受前端任意路径；跨主机自动迁移不在范围内。

## 13. 安全、错误与容量控制

- CommandError 在产生点构造，包含稳定 code、中文恢复步骤、是否已变更和 taskId；禁止按文本分类。
- 生产禁止 unwrap/expect/panic/todo/unimplemented，不能吞锁续租/状态保存/凭据更新/删除错误。
- 脚本使用 `set -euo pipefail`、固定 PATH、白名单 action、参数与 UUID/schema 校验；动态值 shell_quote，不拼原始 shell。
- 所有远端任意文本、下载文件、Lua、日志视为数据；下载源 HTTPS，非官方下载源 SHA-256，镜像/脚本固定来源与摘要。
- shell xtrace 关闭；错误、日志和事件持久化前统一脱敏；敏感配置 SFTP/文件发布，禁止 argv/环境/Compose 内联秘密。
- MOD/诊断/日志读取限制字节、行数、超时；task 日志轮转设计总量 20 MiB/任务，截断保留状态及证据索引，具体值经实测调整。
- 终态 worker 可清运行资源但保留 status/journal；queued/running/interrupted 与恢复引用不按普通 GC 清理。

## 14. 测试与交付要求

纯函数测试：身份/路径/端口、INI/Lua 渲染解析、版本/digest、运行状态归一化、backup 元数据、task 状态迁移。数据库测试：空库、升级、读写、事务、删除、sequence、秘密不落盘。命令测试走真实 production 入口，避免测试另一份同规则实现。

远端脚本执行 bash -n、隔离文件系统 fixture、锁并发/租约旧 owner、journal 每阶段崩溃恢复。集成在固定 Linux amd64 实机跑 A/B/C、两个客户端同时创建、更新/恢复断线、磁盘满、端口竞争、错误 target/备份、daemon/主机重启。

```bash
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo check --manifest-path src-tauri/Cargo.toml --all-targets
cargo test --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
git diff --check
```

command/DTO 变更同时跑前端类型检查、测试、构建。交付包括 script bundle 校验清单、runtime/worker image digest、DB migration、远端 schema、环境要求、恢复 runbook 和场景证据。以上是后续实现门禁，不表示本次文档编写已执行 Rust 或 Docker 测试。
