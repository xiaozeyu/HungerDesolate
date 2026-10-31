import { computed, shallowRef } from "vue";
import type { BuildStatusResponse } from "../api/instances";
import { loadBuildTasks, saveBuildTasks } from "../lib/buildStorage";

export interface BuildTaskEntry {
  serverId: string;
  hostId: string;
  recordVersion: number;
  releaseId: string;
  message: string;
  status: BuildStatusResponse;
  startedAt: number;
  updatedAt: number;
  observationError: string;
}

const entries = shallowRef<ReadonlyMap<string, BuildTaskEntry>>(new Map(loadBuildTasks().map(entry => [entry.hostId, entry])));

export function useBuildTasks() {
  const all = computed(() => [...entries.value.values()].sort((left, right) => right.updatedAt - left.updatedAt));

  function start(serverId: string, hostId: string, recordVersion: number, releaseId: string, message: string) {
    const now = Date.now();
    const next = new Map(entries.value);
    next.set(hostId, {
      serverId, hostId, recordVersion, releaseId, message,
      status: { schemaVersion: 1, requestId: "", releaseId, status: "building", logTail: [] },
      startedAt: now, updatedAt: now, observationError: "",
    });
    entries.value = next;
    saveBuildTasks([...next.values()]);
  }

  function receive(serverId: string, hostId: string, recordVersion: number, status: BuildStatusResponse) {
    const current = entries.value.get(hostId);
    if (current && current.releaseId !== status.releaseId) return;
    const next = new Map(entries.value);
    next.set(hostId, {
      serverId, hostId, recordVersion,
      releaseId: status.releaseId,
      message: current?.releaseId === status.releaseId ? current.message : "",
      status,
      startedAt: current?.releaseId === status.releaseId ? current.startedAt : Date.now(),
      updatedAt: Date.now(),
      observationError: "",
    });
    entries.value = next;
    saveBuildTasks([...next.values()]);
  }

  function error(hostId: string, message: string) {
    const current = entries.value.get(hostId);
    if (!current) return;
    const next = new Map(entries.value);
    next.set(hostId, { ...current, observationError: message, updatedAt: Date.now() });
    entries.value = next;
    saveBuildTasks([...next.values()]);
  }

  function forHost(hostId: string | null | undefined): BuildTaskEntry | null {
    return hostId ? entries.value.get(hostId) ?? null : null;
  }

  function clear(hostId: string) {
    if (!entries.value.has(hostId)) return;
    const next = new Map(entries.value);
    next.delete(hostId);
    entries.value = next;
    saveBuildTasks([...next.values()]);
  }

  return { all, start, receive, error, forHost, clear };
}
