import { matchesConnection, type ConnectionTicket } from "./connectionGuard.ts";
import type { HostEnvironment } from "../types/contracts";
export function matchesEnvironment(ticket: ConnectionTicket, expectedHostId: string | null, result: HostEnvironment): boolean {
  return matchesConnection(ticket,result) && (expectedHostId === null || result.hostId === expectedHostId)
    && typeof result.environmentCheckId === "string" && result.environmentCheckId.length > 0
    && Number.isSafeInteger(result.checkedAtUnixSeconds) && result.checkedAtUnixSeconds > 0
    && Array.isArray(result.checks) && result.checks.every(check => ["passed","warning","blocker"].includes(check.level));
}
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isSafeInteger(bytes) || bytes < 0) return "未知";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB","MiB","GiB","TiB"];
  let value = bytes / 1024; let index = 0;
  while (value >= 1024 && index < units.length - 1) {value /= 1024;index++;}
  return `${value.toLocaleString("zh-CN",{maximumFractionDigits:1})} ${units[index]}`;
}
export function canInitializeHost(environment: HostEnvironment | null): boolean {
  if (!environment || environment.hostId) return false;
  return !environment.checks.some(check => check.level === "blocker" && (
    check.code === "MANAGEMENT_PATH_PERMISSION" || check.code === "MANAGEMENT_ROOT_INVALID"
    || (check.code === "REQUIRED_COMMAND_MISSING" && check.label === "python3")
  ));
}
