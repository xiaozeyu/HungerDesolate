import type { BuildTaskEntry } from "../composables/useBuildTasks";

const KEY = "hunger-desolate.builds.v1";
const LIMIT = 20;

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function isEntry(value: unknown): value is BuildTaskEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<BuildTaskEntry>;
  return typeof entry.serverId === "string" && typeof entry.hostId === "string"
    && typeof entry.releaseId === "string" && typeof entry.startedAt === "number"
    && typeof entry.updatedAt === "number" && !!entry.status
    && typeof entry.status === "object" && typeof entry.status.status === "string";
}

export function loadBuildTasks(): BuildTaskEntry[] {
  const raw = storage()?.getItem(KEY);
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(-LIMIT) : [];
  } catch {
    return [];
  }
}

export function saveBuildTasks(entries: BuildTaskEntry[]): void {
  try {
    storage()?.setItem(KEY, JSON.stringify(entries.slice(-LIMIT)));
  } catch {
    // 配额不足或存储被禁用时保留内存状态。
  }
}
