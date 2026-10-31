import { call } from "./invoke";
import type { HostTarget, TaskSnapshot } from "../types/contracts";

export interface HostModItem {
  workshopId: string;
  contentDigest: string;
  sourceHandle: string | null;
  preparedAt: string | null;
  sizeBytes: number | null;
  fileCount: number | null;
  usedBy: { instanceId: string; displayName: string | null }[];
}

export function listHostMods(request: {
  schemaVersion: 1; requestId: string; target: HostTarget; expectedRecordVersion: number;
}): Promise<{ schemaVersion: 1; requestId: string; target: HostTarget; recordVersion: number; items: HostModItem[] }> {
  return call("list_host_mods", request);
}

export function deleteHostMod(request: {
  schemaVersion: 1; requestId: string; target: HostTarget; expectedRecordVersion: number;
  workshopId: string; contentDigest: string;
}): Promise<{ schemaVersion: 1; requestId: string; target: HostTarget; recordVersion: number; deleted: unknown }> {
  return call("delete_host_mod", request);
}

export function prepareHostMods(request: {
  schemaVersion: 1; requestId: string; target: HostTarget; expectedRecordVersion: number;
  operationId: string; workshopIds: string[]; sourceHandles?: Record<string, string>;
}): Promise<{ schemaVersion: 1; requestId: string; target: HostTarget; task: TaskSnapshot }> {
  return call("prepare_host_mods", request);
}

export function cancelHostModTask(request: {
  schemaVersion: 1; requestId: string; target: HostTarget; expectedRecordVersion: number; operationId: string;
}): Promise<{ schemaVersion: 1; requestId: string; target: HostTarget; task: TaskSnapshot }> {
  return call("cancel_host_mod_task", request);
}
export function getHostModTask(request: {
  schemaVersion: 1; requestId: string; target: HostTarget; expectedRecordVersion: number; operationId: string;
}): Promise<{ schemaVersion: 1; requestId: string; target: HostTarget; task: TaskSnapshot }> {
  return call("get_host_mod_task", request);
}

export function listHostModTasks(request: {
  schemaVersion: 1; requestId: string; target: HostTarget; expectedRecordVersion: number;
}): Promise<{ schemaVersion: 1; requestId: string; target: HostTarget; items: TaskSnapshot[] }> {
  return call("list_host_mod_tasks", request);
}
