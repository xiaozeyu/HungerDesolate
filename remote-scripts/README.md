# 远端脚本边界

当前已提供只读环境检查 `probes/host-environment.sh`、Docker 安装预检 `probes/docker-install-preview.py`、管理元数据初始化 `actions/initialize-host.py`，以及 Docker 安装 admission/controller/worker。实例创建已有 `actions/instance-create-control.py` 短接纳内核和 `actions/instance-create-task.py` / `actions/instance-create-worker.py` 持久任务基础设施；它们尚未接入 Tauri command，当前 worker 只会报告部署门禁阻断。游戏部署唯一采用 Docker Compose，不添加原生专服部署实现。

后续按 `spec/backend-implementation.md` 的 §4、§7–11 实现受控脚本包：身份/路径检查、flock、原子文件、任务接纳/controller、对账、worker 操作。脚本必须经过固定版本/摘要验证，先通过 SSH 信任与目标归属验证。

只有实现生产调用时才创建具体脚本；不得添加返回成功的空脚本。安装、更新与恢复的断线持续执行须在阶段 0 实机证明。

只读探测脚本以 stdin 交给受信任 SSH 会话内的 Bash，不落远端文件。它不接收用户路径/action，不写 JSON 或安装软件，结果用固定字段与 Base64 编码的行协议传回 Rust。Docker 指向 `/var/run/docker.sock`，不沿用用户的其他 Docker context。脚本修改需同步核对 Rust 协议解析与隔离 fixture。

初始化脚本使用固定 `/usr/bin/python3 -I -`，源代码随应用编译，输入以 Base64 JSON 随 stdin 提交，不经过 shell 参数插值。Rust 只暴露 preview/initialize/query 三个固定动作；生产根目录固定。预览不写远端；初始化通过私有 bootstrap 回执和 flock 对账，逐文件完整无覆盖发布 host/registry。它是可重试短操作，不安装依赖、不使用 Docker、不提权、不脱离 SSH 启动后台任务。异常退出可能保留私有 staging，继续原操作时只处理本操作可证明归属的文件，不提供自动删除管理目录的功能。

`python3 scripts/test-host-initialization.py` 对一次性目录执行生产脚本，包含 25 个进程退出边界和双客户端竞争；macOS 测试仅模拟 Linux 平台标识，不替代 Linux 实机验收。`scripts/test-local-ssh.py` 另外验证 SSH 执行与 SFTP 空列表读取。

Docker 安装预检只读 `/etc/os-release`、管理身份、dpkg/source 配置、systemd 和磁盘，并对官方 Resolute `InRelease` 发起 TLS 读取；不修改系统。安装只允许 root SSH 与固定 Ubuntu 26.04 amd64，通过 systemd transient unit 执行；APT simulation 发现删除行为则拒绝安装。测试入口：`python3 scripts/test-docker-install-preview.py`、`python3 scripts/test-docker-install-worker.py`、`python3 scripts/test-docker-install-task-state.py`。隔离测试不替代实际 apt/dpkg/重启/SSH 断线验收。

仓库检查保留单次请求及 `urlopen(timeout=5)`，不后台重试或自动切换源。连接/读取超时、DNS、TLS 证书、TLS 连接、其他连接错误、HTTP 状态与非预期响应分别返回固定阻断码；页面仍显示最近一次预检快照。修改脚本后重新编译并启动桌面后端，下一次预检自动通过 SSH 下发新代码。不能通过本机仓库连通性推断服务器的实际结果。

安装 worker 在新软件源发布之前下载签名密钥并检查完整 PEM 包络与大小；不覆盖已有文件。下载网络超时、DNS/连接错误、HTTP 408/429/5xx 最多尝试三次，间隔 1、2 秒；证书、TLS、格式错误直接失败，仍使用系统 TLS 校验和固定官方地址。失败状态保留实际阶段及安全错误码。运行和已结束的旧任务继续使用创建时的脚本；更新桌面后重新预检并创建新任务才能应用修复，旧 `INSTALL_FAILED` 不能补推原始异常。

`probes/instance-create-preview.py` 为只读创建规划：当前固定公网路线，核验完整主机身份与注册表版本，保守合并 registry/manifest 端口、Linux socket 和 Docker 发布配置；不写文件/创建容器/分配实例。端口与目录是未预留建议，部署兼容性门禁未通过前返回固定阻断。测试入口 `python3 scripts/test-instance-create-preview.py`；实际游戏联网和容量仍需阶段 0。

`actions/instance-create-control.py` 只做创建任务接纳：在 `registry.lock` 内重新校验 hostId、registryRevision、预览端口计划和实际端口占用，原子写入 `creating` 实例、完整端口预留和受限 operation 回执。它不创建实例目录、不写 Token、不生成 Compose、不启动 Docker；这些动作必须由后续持久 controller/worker 完成。测试入口 `python3 scripts/test-instance-create-control.py`。

`actions/instance-create-task.py` 负责持久任务目录、幂等 request、status、journal、systemd transient unit 和 interrupted 对账；`actions/instance-create-worker.py` 负责阶段更新并在部署兼容性未验证时返回固定 `DEPLOYMENT_VALIDATION_REQUIRED`。测试入口 `python3 scripts/test-instance-create-task.py`。

当前 worker 已实现实例目录树和初始 manifest 的安全发布；目录/manifest 完成后在 `download` 阶段等待已验证 release，不生成未验证的 Compose，不创建网络/卷，不启动游戏容器。

`actions/instance-release.py` 负责校验固定 Linux amd64 release 和 digest pin Compose 渲染。worker 只把结果写入 `staging/<taskId>`，执行 `docker compose config --quiet` 并生成资源计划；随后在 `publish` 等待阶段 0 的真实产物和容器验证，不执行 `up` 或创建容器。测试入口为 `python3 scripts/test-instance-release.py`。

`actions/instance-docker.py` 是 gated 资源创建与验证内核：只有 `runtimeVerified=true` 的 release 才能创建带身份标签的 `<project>_instance` network 和 `docker compose create --no-start` 容器，并验证 labels、挂载源和端口发布。默认 worker 仍因 runtime 未验收而阻断；测试入口为 `python3 scripts/test-instance-docker.py`。

`probes/instance-release-catalog.py` 与 `list_instance_releases` command 只读核对远端 release catalog；没有合格 release 时返回 `CREATE_RELEASE_REQUIRED`，不下载、不覆盖、不伪造 release。
