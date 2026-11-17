import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import { activeContext } from "../context/activeContext";
import { getInstancePlayers } from "../api/players";
import { demoPlayers } from "../fixtures/players";
import { useWorkspace } from "./useWorkspace";
import { useQuery } from "../lib/queryClient";
import { validatePlayersResponse } from "../lib/playerList";

const POLL_INTERVAL = 15000;

export function useInstancePlayers() {
  const workspace = useWorkspace();
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let pollingPaused = false;
  let disposed = false;

  const key = computed(() => {
    const target = activeContext.value.target;
    if (!target) return null;
    const version = workspace.servers.value.find(item => item.serverId === target.serverId)?.recordVersion;
    if (!version) return null;
    return `players:${target.serverId}:${target.hostId}:${target.instanceId}:${version}:${workspace.preview.value ? "preview" : "live"}`;
  });

  const query = useQuery(key, async () => {
    const target = activeContext.value.target;
    if (!target) throw new Error("缺少专服上下文");
    if (workspace.preview.value) return demoPlayers(target);
    const version = workspace.servers.value.find(item => item.serverId === target.serverId)?.recordVersion;
    if (!version) throw new Error("缺少主机连接版本");
    const epoch = activeContext.value.epoch;
    const requestId = crypto.randomUUID();
    const response = await getInstancePlayers({ schemaVersion: 1, requestId, target,
      expectedRecordVersion: version });
    if (activeContext.value.epoch !== epoch
        || activeContext.value.target?.serverId !== target.serverId
        || activeContext.value.target?.hostId !== target.hostId
        || activeContext.value.target?.instanceId !== target.instanceId
        || workspace.servers.value.find(item => item.serverId === target.serverId)?.recordVersion !== version) {
      throw new Error("玩家列表请求上下文已失效");
    }
    if (!validatePlayersResponse(response, requestId, target)) {
      throw new Error("玩家列表响应身份或状态不匹配");
    }
    return response;
  });

  function stopPolling() {
    if (pollTimer !== undefined) {
      clearInterval(pollTimer);
      pollTimer = undefined;
    }
  }

  function startPolling() {
    if (disposed || pollingPaused || !key.value || workspace.preview.value
        || typeof document === "undefined" || document.visibilityState === "hidden") return;
    if (pollTimer !== undefined) return;
    pollTimer = setInterval(() => { void query.refresh(); }, POLL_INTERVAL);
  }

  function onVisibilityChange() {
    if (document.visibilityState === "visible") {
      void query.refresh();
      startPolling();
    } else {
      stopPolling();
    }
  }

  watch(key, next => {
    stopPolling();
    if (next) startPolling();
  }, { flush: "post" });

  onMounted(() => {
    document.addEventListener("visibilitychange", onVisibilityChange);
    startPolling();
  });

  onBeforeUnmount(() => {
    disposed = true;
    stopPolling();
    document.removeEventListener("visibilitychange", onVisibilityChange);
  });

  return {
    data: query.data,
    error: computed(() => query.error.value?.message ?? null),
    loading: query.loading,
    stale: query.stale,
    refresh: query.refresh,
  };
}
