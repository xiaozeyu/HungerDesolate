import type { HostTarget, InstanceTarget, ServerSummary, TaskSnapshot } from "../types/contracts";

export type OperationKind =
  | "start" | "stop" | "restart" | "worldApply"
  | "instanceDelete" | "releaseVerify" | "hostInitialize" | "buildCacheCleanup"
  | "hostModUpdate" | "hostModCheck";

export type OperationTarget = HostTarget | InstanceTarget;

const operationLabels: Record<OperationKind, string> = {
  start: "启动专服",
  stop: "停止专服",
  restart: "重启专服",
  worldApply: "应用世界配置",
  instanceDelete: "删除专服",
  releaseVerify: "验证 Release",
  hostInitialize: "初始化主机",
  buildCacheCleanup: "清理构建缓存",
  hostModUpdate: "更新主机 MOD",
  hostModCheck: "检查主机 MOD 更新",
};

const taskLabels: Record<string, string> = {
  instanceCreate: "创建专服",
  modApply: "应用 MOD",
  backupCreate: "创建备份",
  backupRestore: "恢复备份",
  backupDownload: "下载备份",
  hostModPrepare: "准备主机 MOD",
  dockerInstall: "安装 Docker",
  instanceUpdate: "更新专服",
};

const stageLabels: Record<string, string> = {
  queued: "等待任务",
  preflight: "复核条件",
  reserve: "预留实例与端口",
  prepareDirectories: "准备目录",
  download: "准备产物",
  stagingVerify: "校验 staging",
  prepare: "准备发布",
  stop: "停止分片",
  backup: "创建备份",
  publish: "发布部署",
  start: "启动分片",
  verify: "验证结果",
  rollback: "恢复现场",
  finalize: "完成",
  accepted: "已接纳",
  rechecking: "复核主机状态",
  configuringRepository: "配置 Docker 软件源",
  updatingPackageIndex: "更新软件包索引",
  checkingPackageChanges: "核对软件包变更",
  installingPackages: "安装软件包",
  startingDocker: "启动 Docker",
  verifyingRuntime: "验证 Docker 与 Compose",
  verified: "安装并验证完成",
};

export function operationKindLabel(kind: OperationKind): string {
  return operationLabels[kind];
}

export function taskOperationLabel(operation: string): string {
  return taskLabels[operation] ?? "维护任务";
}

export function stageLabel(stage: string): string {
  return stageLabels[stage] ?? stage;
}

export function taskStatusLabel(status: string): string {
  return {
    queued: "排队中",
    running: "进行中",
    succeeded: "已完成",
    failed: "失败",
    interrupted: "待核实",
    cancelled: "已取消",
  }[status] ?? status;
}

export function operationTargetKey(target: OperationTarget): string {
  return "instanceId" in target
    ? `${target.serverId}:${target.hostId}:${target.instanceId}`
    : `${target.serverId}:${target.hostId}`;
}

export function operationTargetLabel(target: OperationTarget, servers: ServerSummary[]): string {
  const server = servers.find(item => item.serverId === target.serverId);
  const label = server?.displayName ?? target.hostId.slice(0, 8);
  return "instanceId" in target ? `${label} / ${target.instanceId.slice(0, 8)}` : label;
}

export function taskTargetLabel(task: TaskSnapshot, servers: ServerSummary[]): string {
  if (!("hostId" in task.target)) return "本机任务";
  const server = servers.find(item => item.serverId === task.target.serverId);
  const label = server?.displayName ?? task.target.hostId.slice(0, 8);
  return "instanceId" in task.target ? `${label} / ${task.target.instanceId.slice(0, 8)}` : label;
}

export function targetRoute(target: OperationTarget): string {
  return "instanceId" in target
    ? `/hosts/${target.serverId}/instances/${target.instanceId}/overview`
    : `/hosts/${target.serverId}/instances`;
}

export function taskRoute(task: TaskSnapshot): string | null {
  if (!("hostId" in task.target)) return null;
  return "instanceId" in task.target
    ? `/hosts/${task.target.serverId}/instances/${task.target.instanceId}/overview`
    : `/hosts/${task.target.serverId}/instances`;
}

export function elapsedLabel(startedAt: number, finishedAt: number | null, now = Date.now()): string {
  const seconds = Math.max(0, Math.floor(((finishedAt ?? now) - startedAt) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
