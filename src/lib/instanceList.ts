import { isUuid } from "./navigationGuard.ts";
import { matchesConnection, type ConnectionTicket } from "./connectionGuard.ts";
import type { InstanceSummary } from "../types/contracts";
import type { InstanceListResponse } from "../api/instances";
const lifecycles = ["creating","ready","createFailed","deleting","deleteFailed","retired"];
const runtimes = ["unknown","stopped","starting","running","degraded","stopping","failed"];
export function validateInstanceList(ticket: ConnectionTicket, expectedHostId: string | null, result: InstanceListResponse): boolean {
  if (!matchesConnection(ticket,result) || !isUuid(result.hostId) || (expectedHostId !== null && expectedHostId !== result.hostId)
    || !Number.isSafeInteger(result.registryRevision) || result.registryRevision < 0
    || !Number.isSafeInteger(result.capturedAtUnixSeconds) || result.capturedAtUnixSeconds <= 0
    || !Array.isArray(result.instances) || result.instances.length > 200) return false;
  const ids = new Set<string>();
  return result.instances.every(item => {
    if (!isUuid(item.instanceId) || item.hostId !== result.hostId || ids.has(item.instanceId)
      || typeof item.displayName !== "string" || item.displayName.trim().length === 0
      || !lifecycles.includes(item.lifecycle) || !runtimes.includes(item.runtimeStatus)
      || typeof item.manifestAvailable !== "boolean"
      || (item.gameMode !== null && (typeof item.gameMode !== "string" || item.gameMode.length > 64))
      || (item.cavesEnabled !== null && typeof item.cavesEnabled !== "boolean")
      || (item.maxPlayers !== null && (!Number.isInteger(item.maxPlayers) || item.maxPlayers < 1 || item.maxPlayers > 128))
      || (item.players !== null && (!Number.isInteger(item.players) || item.players < 0 || (item.maxPlayers !== null && item.players > item.maxPlayers)))) return false;
    ids.add(item.instanceId);return true;
  });
}
export function instanceState(item: InstanceSummary): {label:string;tone:"success"|"warning"|"danger"|"muted"} {
  if (item.lifecycle === "creating") return {label:"创建中",tone:"warning"};
  if (item.lifecycle === "createFailed") return {label:"创建失败",tone:"danger"};
  if (item.lifecycle === "deleting") return {label:"删除中",tone:"warning"};
  if (item.lifecycle === "deleteFailed") return {label:"删除失败",tone:"danger"};
  if (item.lifecycle === "retired") return {label:"已归档",tone:"muted"};
  return {
    unknown:{label:"未知",tone:"muted"},stopped:{label:"已停止",tone:"muted"},starting:{label:"启动中",tone:"warning"},
    running:{label:"运行中",tone:"success"},degraded:{label:"部分异常",tone:"warning"},stopping:{label:"停止中",tone:"warning"},failed:{label:"运行失败",tone:"danger"},
  }[item.runtimeStatus] as {label:string;tone:"success"|"warning"|"danger"|"muted"};
}
export function canInspectInstance(item: InstanceSummary): boolean {
  return item.manifestAvailable && !["deleting","deleteFailed","retired"].includes(item.lifecycle);
}
