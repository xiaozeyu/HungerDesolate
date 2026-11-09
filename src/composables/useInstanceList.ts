import { computed, onBeforeUnmount, watch, type Ref } from "vue";
import { getInstanceCreateStatus, listInstances, type InstanceListResponse } from "../api/instances";
import { demoInstances } from "../fixtures/workspace";
import { validateInstanceList } from "../lib/instanceList";
import { queryClient, useQuery } from "../lib/queryClient";
import { useWorkspace } from "./useWorkspace";
import { useTasks } from "./useTasks";
import type { ServerSummary } from "../types/contracts";

export function useInstanceList(server: Ref<ServerSummary | null>, preview: Readonly<Ref<boolean>>) {
  const workspace = useWorkspace();
  const taskStore = useTasks();
  const key = computed(() => {
    const target = server.value;
    return target
      ? `instances:${target.serverId}:${target.recordVersion}:${preview.value ? "preview" : "live"}`
      : null;
  });

  let disposed = false;
  let generation = 0, requestSequence = 0;
  // The list call reads the remote registry and runtime evidence; keep it manual-refresh only.
  watch(key, () => { generation++; }, { flush: "sync" });

  const query = useQuery(key, async queryKey => {
    const currentGeneration = generation, sequence = ++requestSequence;
    const target = server.value;
    if (!target) throw new Error("缺少主机连接");
    const expectedHostId = target.hostId;
    const requestId = crypto.randomUUID();
    const request = {
      schemaVersion: 1 as const,
      requestId,
      serverId: target.serverId,
      expectedRecordVersion: target.recordVersion,
    };
    const result = preview.value
      ? {
          schemaVersion: 1 as const, requestId, serverId: target.serverId, recordVersion: target.recordVersion,
          hostId: expectedHostId ?? "", registryRevision: 1,
          capturedAtUnixSeconds: Math.floor(Date.now() / 1000),
          instances: demoInstances(expectedHostId ?? ""),
        }
      : await listInstances(request);
    if (disposed || generation !== currentGeneration || requestSequence !== sequence
        || server.value?.serverId !== target.serverId
        || server.value.recordVersion !== target.recordVersion) {
      throw new Error("专服列表请求上下文已失效");
    }
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch: 0, sequence: 0, requestId };
    if (!validateInstanceList(ticket, expectedHostId, result)) throw new Error("专服列表响应身份或格式不匹配");
    const prior = queryClient.get(queryKey)?.data as InstanceListResponse | undefined;
    if (prior && prior.hostId === result.hostId && result.registryRevision < prior.registryRevision) {
      throw new Error("注册表版本早于已读取结果，请核对远端数据");
    }
    workspace.commitInstances(result);
    return result;
  });

  const { data, error, loading, stale, refresh } = query;

  onBeforeUnmount(() => {
    disposed = true;
    generation++;
  });

  async function reconcileCreationTasks(target: ServerSummary, snapshot: InstanceListResponse) {
    const terminalStatuses = ["succeeded", "failed", "cancelled", "interrupted"];
    const pending = snapshot.instances.filter(item => (item.lifecycle === "creating" || item.lifecycle === "createFailed")
      && item.activeTaskId && !taskStore.tasks.value.some(task => task.taskId === item.activeTaskId
        && task.target.serverId === target.serverId && "hostId" in task.target && task.target.hostId === item.hostId
        && "instanceId" in task.target && task.target.instanceId === item.instanceId && terminalStatuses.includes(task.status)));
    if (!pending.length) return;
    const results = await Promise.all(pending.map(async item => {
      try {
        return await getInstanceCreateStatus({
          schemaVersion: 1, requestId: crypto.randomUUID(),
          target: { serverId: target.serverId, hostId: item.hostId },
          expectedRecordVersion: target.recordVersion,
          operationId: item.activeTaskId!,
        });
      } catch {
        return null;
      }
    }));
    if (server.value?.serverId !== target.serverId || server.value.recordVersion !== target.recordVersion) return;
    let terminal = false;
    for (let index = 0; index < pending.length; index++) {
      const response = results[index], item = pending[index], task = response?.task;
      if (!response || !item || response.serverId !== target.serverId || response.recordVersion !== target.recordVersion
        || !task || !("hostId" in task.target) || !("instanceId" in task.target)
        || task.operationId !== item.activeTaskId || task.target.serverId !== target.serverId
        || task.target.hostId !== item.hostId || task.target.instanceId !== item.instanceId) continue;
      taskStore.receive(task);
      if (terminalStatuses.includes(task.status)) terminal = true;
    }
    if (terminal) await refresh();
  }

  watch(data, snapshot => {
    if (!snapshot || !server.value || preview.value) return;
    void reconcileCreationTasks(server.value, snapshot);
  }, { flush: "post" });

  watch(error, next => {
    if (next) workspace.invalidateSelection();
  });

  return { data, error, loading, stale, refresh };
}
