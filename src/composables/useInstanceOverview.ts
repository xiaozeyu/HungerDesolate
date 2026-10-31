import { computed, onBeforeUnmount, watch } from "vue";
import { activeContext } from "../context/activeContext";
import { getInstanceOverview } from "../api/instances";
import { demoOverview } from "../fixtures/workspace";
import { useWorkspace } from "./useWorkspace";
import { matchesRuntimeSnapshot } from "../lib/instanceRuntime";
import { useQuery } from "../lib/queryClient";

export function useInstanceOverview(options: { poll?: boolean } = {}) {
  const workspace = useWorkspace();
  const shouldPoll = options.poll !== false;
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let pollingPaused = false;
  let disposed = false;

  const key = computed(() => {
    const target = activeContext.value.target;
    if (!target) return null;
    const version = workspace.servers.value.find(item => item.serverId === target.serverId)?.recordVersion;
    if (!version) return null;
    return `overview:${target.serverId}:${target.hostId}:${target.instanceId}:${version}:${workspace.preview.value ? "preview" : "live"}`;
  });

  const query = useQuery(key, async () => {
    const target = activeContext.value.target;
    if (!target) throw new Error("缺少专服上下文");
    const version = workspace.servers.value.find(item => item.serverId === target.serverId)?.recordVersion;
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
    workspace.commitInstanceOverview(target.serverId, version, overview.identity);
    return overview;
  });

  function stopPolling() {
    if (pollTimer !== undefined) {
      clearInterval(pollTimer);
      pollTimer = undefined;
    }
  }

  function startPolling() {
    if (!shouldPoll || pollingPaused || disposed || !key.value || workspace.preview.value) return;
    if (pollTimer !== undefined) return;
    pollTimer = setInterval(() => { void query.refresh(); }, 10000);
  }

  function pausePolling() {
    pollingPaused = true;
    stopPolling();
  }

  function resumePolling() {
    pollingPaused = false;
    startPolling();
  }

  watch(key, next => {
    stopPolling();
    if (next) startPolling();
  }, { flush: "post" });

  onBeforeUnmount(() => {
    disposed = true;
    stopPolling();
  });

  return {
    data: query.data,
    error: computed(() => query.error.value?.message ?? null),
    loading: query.loading,
    stale: query.stale,
    refresh: query.refresh,
    pausePolling,
    resumePolling,
  };
}
