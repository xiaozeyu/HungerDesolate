import type { OperationEntry, OperationStatus } from "../composables/useOperations";
import type { UserError } from "./commandError";

const KEY = "hunger-desolate.operations.v1";
const LIMIT = 50;

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function isEntry(value: unknown): value is OperationEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<OperationEntry>;
  return typeof entry.id === "string" && typeof entry.kind === "string"
    && typeof entry.target === "object" && typeof entry.targetKey === "string"
    && typeof entry.title === "string" && typeof entry.stage === "string"
    && typeof entry.status === "string" && typeof entry.startedAt === "number";
}

function restartError(): UserError {
  return {
    code: "APP_RESTARTED",
    message: "应用已重启，无法确认该操作是否完成；请刷新专服状态后重新判断。",
    recoverySteps: ["进入对应专服，刷新概览或世界配置", "确认运行状态和配置版本", "如操作未完成，重新发起"],
    field: null,
  };
}

export function loadOperations(): OperationEntry[] {
  const raw = storage()?.getItem(KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isEntry).slice(-LIMIT).map(entry => entry.status === "running"
      ? { ...entry, status: "interrupted" as OperationStatus, stage: "应用已重启，结果待核实", finishedAt: Date.now(), error: restartError() }
      : entry);
  } catch {
    return [];
  }
}

export function saveOperations(entries: OperationEntry[]): void {
  try {
    storage()?.setItem(KEY, JSON.stringify(entries.slice(-LIMIT)));
  } catch {
    // 配额不足或存储被禁用时保留内存状态。
  }
}
