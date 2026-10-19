import type { CommandError, TaskResult, TaskSnapshot } from "../types/contracts";

const stages: Record<string, string> = {
  queued: "等待任务",
  preflight: "复核创建条件",
  reserve: "预留实例与端口",
  prepareDirectories: "准备实例目录",
  download: "准备 release",
  stagingVerify: "校验 staging Compose",
  publish: "准备 Docker 资源",
  verify: "验证容器与端口",
  finalize: "完成创建",
};

const errors: Record<string, string> = {
  CREATE_RELEASE_REQUIRED: "没有已验证的 DST release，已保留创建现场",
  CREATE_RELEASE_INVALID: "DST release 元数据或摘要无效",
  CREATE_COMPOSE_CONFIG_FAILED: "Compose 配置校验失败，已保留 staging 现场",
  CREATE_COMPOSE_CONFIG_UNAVAILABLE: "远端 Docker Compose 不可用",
  CREATE_COMPOSE_CONFIG_TIMEOUT: "Compose 配置校验超时",
  CREATE_DOCKER_RUNTIME_REQUIRED: "Docker runtime 尚未通过验证",
  CREATE_NETWORK_CONFLICT: "实例 network 标签与现有资源冲突",
  CREATE_NETWORK_FAILED: "实例 network 创建失败",
  CREATE_CONTAINERS_FAILED: "实例容器创建失败，现场已保留",
  CREATE_CONTAINER_VERIFICATION_REQUIRED: "容器已创建，仍需验证挂载、端口和进程",
  DEPLOYMENT_VALIDATION_REQUIRED: "游戏镜像和 Compose 部署兼容性尚未验证",
};

export function creationTaskStageLabel(stage: string): string {
  return stages[stage] ?? "创建任务";
}

export function creationTaskErrorLabel(code: string | null | undefined): string {
  return code ? errors[code] ?? "创建任务失败，现场已保留" : "创建任务失败，现场已保留";
}

export function creationTaskRecoveryActions(task: Pick<TaskSnapshot, "result" | "error">): TaskResult["recoveryActions"] {
  return task.result?.recoveryActions ?? task.error?.details?.recoveryActions as TaskResult["recoveryActions"] ?? [];
}

export function isCreationTask(task: TaskSnapshot): boolean {
  return task.operation === "instanceCreate" || task.operation === "instanceCreateResume";
}

export type CreationTaskError = CommandError;
