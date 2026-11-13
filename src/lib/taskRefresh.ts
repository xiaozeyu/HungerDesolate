import { getDockerInstallationStatus, type DockerInstallTask } from "../api/hosts";
import { useDockerTasks } from "../composables/useDockerTasks";
import { getHostModTask } from "../api/hostMods";
import { getInstanceBackupTask, getInstanceCreateStatus, getInstanceRestoreTask } from "../api/instances";
import { getInstanceModApplyTask } from "../api/mods";
import { getInstanceUpdateTask } from "../api/updates";
import type { HostTarget, InstanceTarget, ServerSummary, TaskSnapshot } from "../types/contracts";

const inFlight = new Set<string>();

function hostTargetOf(task: TaskSnapshot): HostTarget | null {
  return "hostId" in task.target
    ? { serverId: task.target.serverId, hostId: task.target.hostId }
    : null;
}

function instanceTargetOf(task: TaskSnapshot): InstanceTarget | null {
  const host = hostTargetOf(task);
  return host && "instanceId" in task.target ? { ...host, instanceId: task.target.instanceId } : null;
}

function dockerTaskSnapshot(task: DockerInstallTask, target: HostTarget): TaskSnapshot {
  const startedAt = new Date(task.startedAtUnixSeconds * 1000).toISOString();
  const updatedAt = new Date(task.updatedAtUnixSeconds * 1000).toISOString();
  const terminal = ["succeeded", "failed", "interrupted"].includes(task.status);
  return {
    schemaVersion: 1, taskId: task.taskId, operationId: task.taskId, target,
    operation: "dockerInstall", status: task.status, stage: task.phase,
    lastCompletedStage: null, progress: { kind: "indeterminate" }, sequence: task.sequence,
    createdAt: startedAt, startedAt, finishedAt: terminal ? updatedAt : null,
    cancelAllowed: false, observation: "live",
  };
}

export async function refreshTask(task: TaskSnapshot, servers: ServerSummary[]): Promise<TaskSnapshot | null> {
  const server = servers.find(item => item.serverId === task.target.serverId);
  if (!server) return null;
  const requestId = crypto.randomUUID();
  switch (task.operation) {
    case "instanceCreate": {
      const target = hostTargetOf(task);
      if (!target) return null;
      const response = await getInstanceCreateStatus({
        schemaVersion: 1, requestId, target,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      return response.task;
    }
    case "modApply": {
      const target = instanceTargetOf(task);
      if (!target) return null;
      const response = await getInstanceModApplyTask({
        schemaVersion: 1, requestId, target,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      return response.task;
    }
    case "backupCreate": {
      const target = instanceTargetOf(task);
      if (!target) return null;
      const response = await getInstanceBackupTask({
        schemaVersion: 1, requestId, target,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      return response.task;
    }
    case "backupRestore": {
      const target = instanceTargetOf(task);
      if (!target) return null;
      const response = await getInstanceRestoreTask({
        schemaVersion: 1, requestId, target,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      return response.task;
    }
    case "instanceUpdate": {
      const target = instanceTargetOf(task);
      if (!target) return null;
      const response = await getInstanceUpdateTask({
        schemaVersion: 1, requestId, target,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      return response.task;
    }
    case "hostModPrepare": {
      const target = hostTargetOf(task);
      if (!target) return null;
      const response = await getHostModTask({
        schemaVersion: 1, requestId, target,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      return response.task;
    }
    case "dockerInstall": {
      const target = hostTargetOf(task);
      if (!target) return null;
      const response = await getDockerInstallationStatus({
        schemaVersion: 1, requestId, serverId: target.serverId,
        expectedRecordVersion: server.recordVersion, operationId: task.operationId,
      });
      if (!response.task || response.hostId !== target.hostId) return null;
      useDockerTasks().receive(target.serverId, target.hostId, response.task);
      return dockerTaskSnapshot(response.task, target);
    }
    default:
      return null;
  }
}

export async function refreshActiveTasks(
  tasks: TaskSnapshot[],
  servers: ServerSummary[],
  receive: (task: TaskSnapshot) => void,
): Promise<void> {
  await Promise.all(tasks
    .filter(task => ["queued", "running"].includes(task.status) && !inFlight.has(task.taskId))
    .map(async task => {
      inFlight.add(task.taskId);
      try {
        const next = await refreshTask(task, servers);
        if (next) receive(next);
      } catch {
        // 任务查询失败保留上一份快照，由任务记录/页面错误状态呈现。
      } finally {
        inFlight.delete(task.taskId);
      }
    }));
}

export async function reconcileTasks(
  tasks: TaskSnapshot[],
  servers: ServerSummary[],
  receive: (task: TaskSnapshot) => void,
  discard: (taskId: string) => void,
): Promise<void> {
  await Promise.all(tasks
    .filter(task => ["queued", "running"].includes(task.status) && !inFlight.has(task.taskId))
    .map(async task => {
      inFlight.add(task.taskId);
      try {
        const next = await refreshTask(task, servers);
        if (next) receive(next);
        else discard(task.taskId);
      } catch {
        discard(task.taskId);
      } finally {
        inFlight.delete(task.taskId);
      }
    }));
}
