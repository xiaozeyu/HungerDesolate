import type { TaskSnapshot } from "../types/contracts";

const KEY = "hunger-desolate.tasks.v1";
const LIMIT = 80;

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function isTask(value: unknown): value is TaskSnapshot {
  if (!value || typeof value !== "object") return false;
  const task = value as Partial<TaskSnapshot>;
  return task.schemaVersion === 1 && typeof task.taskId === "string"
    && typeof task.operationId === "string" && typeof task.operation === "string"
    && typeof task.status === "string" && typeof task.sequence === "number"
    && typeof task.createdAt === "string" && typeof task.target === "object";
}

export function loadTasks(): TaskSnapshot[] {
  const raw = storage()?.getItem(KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isTask).slice(-LIMIT) : [];
  } catch {
    return [];
  }
}

export function saveTasks(tasks: TaskSnapshot[]): void {
  try {
    storage()?.setItem(KEY, JSON.stringify(tasks.slice(-LIMIT)));
  } catch {
    // 配额不足或存储被禁用时保留内存状态。
  }
}
