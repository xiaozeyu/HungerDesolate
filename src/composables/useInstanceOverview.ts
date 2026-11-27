import { computed, watch } from "vue";
import { activeContext } from "../context/activeContext";
import { getInstanceOverview } from "../api/instances";
import { demoOverview } from "../fixtures/workspace";
import { useWorkspace } from "./useWorkspace";
import { matchesRuntimeSnapshot } from "../lib/instanceRuntime";
import { useQuery } from "../lib/queryClient";
import { sameTarget } from "../lib/navigationGuard";
import { useTasks } from "./useTasks";

export function useInstanceOverview() {
  const workspace = useWorkspace();
  const { tasks } = useTasks();

  const key = computed(() => {
    const target = activeContext.value.target;
    if (!target) return null;
    const version = workspace.servers.value.find(item => item.serverId === target.serverId && item.hostId === target.hostId)?.recordVersion;
    if (!version) return null;
    return `overview:${target.serverId}:${target.hostId}:${target.instanceId}:${version}:${workspace.preview.value ? "preview" : "live"}`;
  });

  const updateTasks = computed(() => {
    const target = activeContext.value.target;
    return target && !workspace.preview.value ? tasks.value.filter(task => task.operation === "instanceUpdate"
      && "instanceId" in task.target && sameTarget(task.target, target)) : [];
  });
  const isActive = (status: string) => status === "queued" || status === "running";
  const updateInProgress = computed(() => updateTasks.value.some(task => isActive(task.status)));

  const query = useQuery(key, async () => {
    const target = activeContext.value.target;
    if (!target) throw new Error("缺少专服上下文");
    const version = workspace.servers.value.find(item => item.serverId === target.serverId && item.hostId === target.hostId)?.recordVersion;
    if (!version) throw new Error("缺少主机连接版本");
    const epoch = activeContext.value.epoch;
    if (workspace.preview.value) {
      const instance = workspace.instances(target.hostId).find(item => item.instanceId === target.instanceId);
      if (!instance) throw new Error("示例专服不存在");
      return demoOverview(instance);
    }
    const requestId = crypto.randomUUID();
    const response = await getInstanceOverview(target, requestId, version);
    if (activeContext.value.epoch !== epoch
        || activeContext.value.target?.serverId !== target.serverId
        || activeContext.value.target?.hostId !== target.hostId
        || activeContext.value.target?.instanceId !== target.instanceId
        || workspace.servers.value.find(item => item.serverId === target.serverId)?.recordVersion !== version) {
      throw new Error("专服概览请求上下文已失效");
    }
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.recordVersion !== version
      || response.target.serverId !== target.serverId || response.target.hostId !== target.hostId
      || response.target.instanceId !== target.instanceId || response.data.identity.hostId !== target.hostId
      || response.data.identity.instanceId !== target.instanceId) {
      throw new Error("专服响应身份或版本不匹配");
    }
    const overview = response.data;
    if (!overview.manifestSummary || !matchesRuntimeSnapshot(overview.runtime, target, overview.manifestSummary.cavesEnabled)
      || overview.runtime.configRevision !== response.configRevision
      || overview.runtime.status !== overview.identity.runtimeStatus) {
      throw new Error("专服运行证据或配置版本不匹配");
    }
    return overview;
  }, { epoch: () => activeContext.value.epoch, paused: () => updateInProgress.value });

  watch(query.data, overview => {
    const target = activeContext.value.target;
    const connection = workspace.servers.value.find(item => item.serverId === target?.serverId);
    if (!workspace.preview.value && !query.loading.value && !query.stale.value && overview && connection
        && overview.identity.hostId === target?.hostId && overview.identity.instanceId === target.instanceId) {
      workspace.commitInstanceOverview(connection.serverId, connection.recordVersion, overview.identity);
    }
  }, { flush: "sync" });

  watch([key, () => activeContext.value.epoch, updateTasks], ([nextKey, epoch, next], [previousKey, previousEpoch, previous]) => {
    if (!nextKey || nextKey !== previousKey || epoch !== previousEpoch || updateInProgress.value
        || previous.some(task => isActive(task.status))) return;
    // The query resumes automatically after queued/running; only direct terminal receipts need a refresh here.
    if (next.some(task => !isActive(task.status)
      && !previous.some(old => old.taskId === task.taskId && old.status === task.status))) void query.refresh();
  });

  return {
    data: query.data,
    error: query.error,
    loading: query.loading,
    stale: query.stale,
    updateInProgress,
    refresh: query.refresh,
  };
}
