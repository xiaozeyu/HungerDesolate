import { matchesConnection, type ConnectionTicket } from "./connectionGuard.ts";
import type { DockerInstallationPreviewResponse, DockerInstallStatusResponse } from "../api/hosts.ts";

const packages = ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"];
const repository = "https://download.docker.com/linux/ubuntu";

export function matchesDockerInstallationPreview(ticket: ConnectionTicket, expectedHostId: string | null, response: DockerInstallationPreviewResponse): boolean {
  if (!response || !response.preview || !response.preview.distribution) return false;
  const plan=response.preview;
  return matchesConnection(ticket,response)
    && ["ready","blocked"].includes(plan.status)
    && Array.isArray(plan.blockers) && plan.blockers.every(item=>typeof item==="string" && item.length<=64)
    && /^[0-9a-f]{64}$/.test(plan.snapshotDigest)
    && Number.isSafeInteger(plan.expiresAtUnixSeconds) && plan.expiresAtUnixSeconds>0
    && Number.isSafeInteger(plan.uid) && plan.uid>=0
    && Number.isSafeInteger(plan.rootAvailableBytes) && plan.rootAvailableBytes>=0
    && ["missing","matching"].includes(plan.sourceState)
    && typeof plan.repositoryReachable==="boolean"
    && typeof plan.aptRunning==="boolean" && typeof plan.dpkgClean==="boolean"
    && typeof plan.installedDockerPackages==="object" && plan.installedDockerPackages!==null
    && Object.entries(plan.installedDockerPackages).every(([name,version])=>packages.includes(name) && typeof version==="string" && version.length<=128)
    && Array.isArray(plan.packages) && plan.packages.length===packages.length && plan.packages.every((item,index)=>item===packages[index])
    && plan.repository===repository
    && plan.sourceFile==="/etc/apt/sources.list.d/docker.sources"
    && plan.keyFile==="/etc/apt/keyrings/docker.asc"
    && ((plan.status==="ready" && uuid.test(response.planId ?? "") && uuid.test(response.operationId ?? ""))
      || (plan.status==="blocked" && response.planId===null && response.operationId===null))
    && [plan.distribution.id,plan.distribution.version,plan.distribution.codename,plan.distribution.architecture]
      .every(item=>item===null || (typeof item==="string" && item.length<=64 && !/[\u0000-\u001f]/.test(item)))
    && uuid.test(plan.hostId) && (!expectedHostId || expectedHostId===plan.hostId)
}

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export function matchesDockerInstallStatus(ticket: ConnectionTicket, expectedHostId: string | null, response: DockerInstallStatusResponse): boolean {
  if (!matchesConnection(ticket,response)) return false;
  if (!uuid.test(response.hostId) || (expectedHostId && response.hostId!==expectedHostId)) return false;
  const task=response.task;
  return task===null || (task.hostId===response.hostId && uuid.test(task.taskId) && Number.isSafeInteger(task.sequence) && task.sequence>0
    && ["queued","running","succeeded","failed","interrupted"].includes(task.status)
    && typeof task.phase==="string" && task.phase.length<=64
    && (task.messageCode===null || (typeof task.messageCode==="string" && task.messageCode.length<=64))
    && Number.isSafeInteger(task.startedAtUnixSeconds) && task.startedAtUnixSeconds>0
    && Number.isSafeInteger(task.updatedAtUnixSeconds) && task.updatedAtUnixSeconds>=task.startedAtUnixSeconds
    && typeof task.installedVersions==="object" && task.installedVersions!==null
    && Object.entries(task.installedVersions).every(([key,value])=>key.length<=64 && typeof value==="string" && value.length<=128));
}

export function dockerInstallBlockerLabel(code: string): string {
  const httpStatus = /^DOCKER_REPOSITORY_HTTP_([1-5][0-9]{2})$/.exec(code)?.[1];
  if (httpStatus) return `Docker 仓库请求失败（HTTP ${httpStatus}）`;
  const labels: Record<string,string> = {
    UNSUPPORTED_DISTRIBUTION:"仅支持 Ubuntu 26.04 LTS",
    UNSUPPORTED_ARCHITECTURE:"需要 amd64 架构",
    ROOT_REQUIRED:"需要 root SSH 账户",
    SYSTEMD_REQUIRED:"需要 systemd",
    APT_REQUIRED:"需要 apt 软件包管理器",
    CONFLICTING_PACKAGES:"检测到冲突的软件包",
    DOCKER_ALREADY_INSTALLED:"已检测到非 Docker 官方包管理的 Docker 客户端",
    HOST_NOT_INITIALIZED:"请先初始化主机管理目录",
    HOST_IDENTITY_MISSING:"主机身份或注册表文件缺失",
    HOST_IDENTITY_INVALID:"主机身份或注册表格式/归属无效",
    APT_BUSY:"APT 或 dpkg 正在执行其他操作",
    DPKG_STATE_INCOMPLETE:"dpkg 有未完成的软件包配置",
    APT_SOURCE_DIRECTORY_UNSAFE:"APT 源目录类型不安全",
    APT_SOURCE_DIRECTORY_TOO_LARGE:"APT 源文件数量超过检查上限",
    APT_SOURCE_DIRECTORY_UNREADABLE:"无法安全读取现有 APT 源",
    MANAGEMENT_ROOT_UNSAFE:"管理目录类型不安全",
    BOOTSTRAP_PATH_UNSAFE:"任务目录类型不安全",
    BOOTSTRAP_OWNER_OR_MODE_INVALID:"任务目录必须由 root 持有且权限为 0700",
    DOCKER_SOURCE_UNSAFE:"Docker 软件源路径类型不安全",
    DOCKER_SOURCE_CONFLICT:"已有 Docker 软件源内容不同",
    LEGACY_DOCKER_SOURCE_PRESENT:"发现旧版 Docker 软件源配置",
    DOCKER_KEY_UNSAFE:"Docker 签名密钥路径类型不安全",
    DOCKER_KEY_CONFLICT:"发现未匹配的软件源签名密钥",
    INSUFFICIENT_DISK_SPACE:"/var 可用空间不足 4 GiB",
    DOCKER_REPOSITORY_UNREACHABLE:"无法读取 Docker 官方 Ubuntu 软件源",
    DOCKER_REPOSITORY_TIMEOUT:"Docker 仓库连接或读取超时",
    DOCKER_REPOSITORY_DNS_FAILED:"Docker 仓库域名解析失败",
    DOCKER_REPOSITORY_TLS_CERT_INVALID:"Docker 仓库 TLS 证书校验失败",
    DOCKER_REPOSITORY_TLS_FAILED:"Docker 仓库 TLS 连接失败",
    DOCKER_REPOSITORY_CONNECTION_FAILED:"Docker 仓库网络连接失败",
    DOCKER_REPOSITORY_RESPONSE_INVALID:"Docker 仓库响应格式异常",
  };
  return labels[code] ?? "安装条件未满足";
}
export function dockerInstallPhaseLabel(phase: string): string {
  const labels:Record<string,string>={accepted:"已接纳",rechecking:"复核主机状态",configuringRepository:"配置 Docker 软件源",updatingPackageIndex:"更新软件包索引",checkingPackageChanges:"核对软件包变更",installingPackages:"安装软件包",startingDocker:"启动 Docker",verifyingRuntime:"验证 Docker 与 Compose",verified:"安装并验证完成",interrupted:"任务中断",failed:"安装失败"};
  return labels[phase]??"处理中";
}
export function dockerInstallStatusLabel(status: string): string {
  const labels:Record<string,string>={queued:"排队中",running:"安装中",succeeded:"已完成",failed:"失败",interrupted:"待核实"};
  return labels[status]??"未知";
}
export function dockerInstallTaskErrorLabel(code: string | null): string {
  const httpStatus = /^INSTALL_KEY_HTTP_([1-5][0-9]{2})$/.exec(code ?? "")?.[1];
  if (httpStatus) return `Docker 签名密钥下载失败（HTTP ${httpStatus}）`;
  const labels:Record<string,string>={INSTALL_PLAN_STALE:"安装前状态发生变化，请重新检查",INSTALL_PATH_UNSAFE:"安装路径类型或权限不安全",INSTALL_SOURCE_UNSAFE:"软件源文件类型不安全",INSTALL_SOURCE_CONFLICT:"软件源配置与预期不同，未覆盖",INSTALL_SIGNING_KEY_INVALID:"Docker 官方签名密钥格式无效",INSTALL_KEY_UNSAFE:"签名密钥路径类型不安全",INSTALL_LOCK_UNSAFE:"安装锁文件类型或权限不安全",INSTALL_COMMAND_FAILED:"APT 或 systemd 命令执行失败",INSTALL_VERIFY_FAILED:"安装后 Docker 或 Compose 验证失败",INSTALL_SIMULATION_FAILED:"APT 无法计算安装变更",INSTALL_WOULD_REMOVE_PACKAGES:"安装计划会移除现有软件包，已停止",INSTALL_COMMAND_TIMEOUT:"安装命令超时",INSTALL_ALREADY_RUNNING:"已有主机维护任务正在运行",INSTALL_CONTROLLER_START_FAILED:"未能启动安装任务",INSTALL_CONTROLLER_START_TIMEOUT:"启动持久 Docker 安装任务超时",INSTALL_WORKER_INTERRUPTED:"任务进程已退出，结果待核实",INSTALL_TASK_PATH_UNSAFE:"安装任务目录权限或类型不安全",INSTALL_TASK_ID_INVALID:"远端 Docker 安装任务编号无效",INSTALL_TASK_STATE_INVALID:"远端 Docker 安装任务状态文件无效",DOCKER_INSTALL_INPUT_INVALID:"Docker 安装任务请求参数无效",DOCKER_INSTALL_ACTION_INVALID:"Docker 安装控制器动作无效",INSTALL_KEY_TIMEOUT:"Docker 签名密钥下载超时",INSTALL_KEY_DNS_FAILED:"下载签名密钥时域名解析失败",INSTALL_KEY_TLS_CERT_INVALID:"签名密钥下载的 TLS 证书校验失败",INSTALL_KEY_TLS_FAILED:"签名密钥下载的 TLS 连接失败",INSTALL_KEY_CONNECTION_FAILED:"签名密钥下载网络连接失败",INSTALL_KEY_RESPONSE_INVALID:"签名密钥下载响应异常",INSTALL_PERMISSION_DENIED:"安装文件读写权限不足",INSTALL_DISK_FULL:"安装所需磁盘空间或配额不足",INSTALL_FILESYSTEM_READ_ONLY:"安装目标文件系统只读",INSTALL_FILE_MISSING:"安装任务所需文件缺失",INSTALL_FILE_CONFLICT:"安装目标文件已存在，未覆盖",INSTALL_FILESYSTEM_ERROR:"安装文件读写失败",INSTALL_COMMAND_MISSING:"安装所需程序不存在",INSTALL_TASK_DATA_INVALID:"安装任务数据格式或字段异常",INSTALL_FAILED:"Docker 安装脚本执行异常",ROOT_REQUIRED:"需要 root SSH 账户"};
  return code ? labels[code] ?? "Docker 安装任务失败" : "Docker 安装任务失败";
}
