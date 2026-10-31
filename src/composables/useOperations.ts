import { computed, shallowRef } from "vue";
import { describeError, type UserError } from "../lib/commandError";
import { operationTargetKey, type OperationKind, type OperationTarget } from "../lib/operationView";
import { loadOperations, saveOperations } from "../lib/operationStorage";
import { queryClient } from "../lib/queryClient";

export type OperationStatus = "running" | "succeeded" | "failed" | "interrupted";

export interface OperationEntry {
  id: string;
  kind: OperationKind;
  target: OperationTarget;
  targetKey: string;
  title: string;
  stage: string;
  status: OperationStatus;
  startedAt: number;
  finishedAt: number | null;
  error: UserError | null;
}

export interface OperationOutcome<T> {
  result: T | null;
  error: UserError | null;
}

const initial = loadOperations();
const entries = shallowRef<ReadonlyMap<string, OperationEntry>>(new Map(initial.map(entry => [entry.id, entry])));
saveOperations(initial);

function invalidateForOperation(target: OperationTarget) {
  queryClient.invalidate(`instances:${target.serverId}:`);
  queryClient.invalidate(`overview:${target.serverId}:${target.hostId}:`);
  if ("instanceId" in target) queryClient.invalidate(`overview:${target.serverId}:${target.hostId}:${target.instanceId}:`);
}

function commit(entry: OperationEntry) {
  const next = new Map(entries.value);
  next.set(entry.id, entry);
  entries.value = next;
  saveOperations([...next.values()]);
}

function patch(id: string, value: Partial<OperationEntry>) {
  const current = entries.value.get(id);
  if (!current) return;
  commit({ ...current, ...value });
}

export function useOperations() {
  const all = computed(() => [...entries.value.values()].sort((left, right) => right.startedAt - left.startedAt));
  const running = computed(() => all.value.filter(entry => entry.status === "running"));

  function latestFor(targetKey: string, kinds?: OperationKind[]): OperationEntry | null {
    return all.value.find(entry => entry.targetKey === targetKey && (!kinds || kinds.includes(entry.kind))) ?? null;
  }

  function runningFor(targetKey: string, kinds?: OperationKind[]): OperationEntry | null {
    return running.value.find(entry => entry.targetKey === targetKey && (!kinds || kinds.includes(entry.kind))) ?? null;
  }

  function isRunningFor(targetKey: string, kinds?: OperationKind[]): boolean {
    return runningFor(targetKey, kinds) !== null;
  }

  function dismiss(id: string) {
    const current = entries.value.get(id);
    if (!current || current.status === "running") return;
    const next = new Map(entries.value);
    next.delete(id);
    entries.value = next;
    saveOperations([...next.values()]);
  }

  async function run<T>(options: {
    kind: OperationKind;
    target: OperationTarget;
    title: string;
    stage: string;
    work: (setStage: (stage: string) => void) => Promise<T>;
  }): Promise<OperationOutcome<T>> {
    const id = crypto.randomUUID();
    const targetKey = operationTargetKey(options.target);
    commit({
      id,
      kind: options.kind,
      target: options.target,
      targetKey,
      title: options.title,
      stage: options.stage,
      status: "running",
      startedAt: Date.now(),
      finishedAt: null,
      error: null,
    });
    try {
      const result = await options.work(stage => patch(id, { stage }));
      patch(id, { status: "succeeded", stage: "已完成", finishedAt: Date.now() });
      invalidateForOperation(options.target);
      return { result, error: null };
    } catch (cause) {
      const error = describeError(cause);
      patch(id, { status: "failed", error, finishedAt: Date.now() });
      invalidateForOperation(options.target);
      return { result: null, error };
    }
  }

  return { all, running, latestFor, runningFor, isRunningFor, dismiss, run };
}
