import { computed, shallowRef } from "vue";
import type { DockerInstallTask } from "../api/hosts";

export interface DockerTaskEntry {
  serverId: string;
  hostId: string;
  task: DockerInstallTask;
  updatedAt: number;
}

const entries = shallowRef<ReadonlyMap<string, DockerTaskEntry>>(new Map());

export function useDockerTasks() {
  const all = computed(() => [...entries.value.values()].sort((left, right) => right.updatedAt - left.updatedAt));

  function receive(serverId: string, hostId: string, task: DockerInstallTask) {
    const current = entries.value.get(hostId);
    if (current && current.task.taskId === task.taskId && task.sequence < current.task.sequence) return;
    const next = new Map(entries.value);
    next.set(hostId, { serverId, hostId, task, updatedAt: Date.now() });
    entries.value = next;
  }

  function forHost(hostId: string | null | undefined): DockerTaskEntry | null {
    return hostId ? entries.value.get(hostId) ?? null : null;
  }

  function clear(hostId: string) {
    if (!entries.value.has(hostId)) return;
    const next = new Map(entries.value);
    next.delete(hostId);
    entries.value = next;
  }

  return { all, receive, forHost, clear };
}
