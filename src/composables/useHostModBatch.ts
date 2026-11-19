import { reactive } from "vue";
import { prepareHostMods } from "../api/hostMods";
import type { WorkshopItem } from "../types/workshop";
import { describeError } from "../lib/commandError";
import { useTasks } from "./useTasks";
import { useOperations } from "./useOperations";

export interface HostModBatchTarget {
  serverId: string;
  hostId: string;
  recordVersion: number;
}

interface BatchState {
  running: boolean;
  total: number;
  accepted: number;
  failed: number;
  current: string;
  error: string;
  startedAt: number | null;
  finishedAt: number | null;
}

const state = reactive<BatchState>({
  running: false,
  total: 0,
  accepted: 0,
  failed: 0,
  current: "",
  error: "",
  startedAt: null,
  finishedAt: null,
});

export function useHostModBatch() {
  const taskStore = useTasks();
  const operations = useOperations();

  async function start(target: HostModBatchTarget, items: WorkshopItem[], action: "update" | "add" = "update") {
    if (state.running || !items.length) return;
    state.running = true;
    state.total = items.length;
    state.accepted = 0;
    state.failed = 0;
    state.current = "";
    state.error = "";
    state.startedAt = Date.now();
    state.finishedAt = null;
    try {
      for (let offset = 0; offset < items.length; offset += 50) {
        const chunk = items.slice(offset, offset + 50);
        state.current = `${Math.min(offset + chunk.length, items.length)}/${items.length}`;
        try {
          const operationId = crypto.randomUUID();
          const requestId = crypto.randomUUID();
          const metadata = Object.fromEntries(chunk.map(item => [item.workshopId, {
            name: item.title,
            author: item.author,
            previewUrl: item.previewUrl,
          }]));
          const sourceHandles = Object.fromEntries(chunk
            .filter(item => item.contentHandle)
            .map(item => [item.workshopId, item.contentHandle!]));
          const response = await prepareHostMods({
            schemaVersion: 1,
            requestId,
            target: { serverId: target.serverId, hostId: target.hostId },
            expectedRecordVersion: target.recordVersion,
            operationId,
            workshopIds: chunk.map(item => item.workshopId),
            sourceHandles,
            metadata,
          });
          if (response.requestId !== requestId || response.task.operationId !== operationId) {
            throw new Error("主机 MOD 批量更新响应身份不匹配");
          }
          taskStore.receive(response.task);
          state.accepted += chunk.length;
        } catch (cause) {
          const failure = describeError(cause);
          state.failed += chunk.length;
          if (!state.error) state.error = failure.message;
          operations.recordFailure({
            kind: "hostModUpdate",
            target: { serverId: target.serverId, hostId: target.hostId },
            title: action === "add" ? "批量添加主机 MOD" : "批量更新主机 MOD",
            stage: `提交第 ${Math.floor(offset / 50) + 1} 批`,
            error: failure,
          });
        }
      }
    } finally {
      state.running = false;
      state.current = "";
      state.finishedAt = Date.now();
    }
  }

  return { state, start };
}
