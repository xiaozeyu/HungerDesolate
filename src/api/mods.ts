import { call } from "./invoke";
import type { InstanceTarget } from "../types/contracts";
import type { InstanceModsResponse } from "../types/mods";
import type { TaskSnapshot } from "../types/contracts";

export function listInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
}): Promise<InstanceModsResponse> {
  return call("list_instance_mods", request);
}

export function saveInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; operationId: string;
  mods: { workshopId: string; enabled: boolean; configurationOptions: Record<string, unknown>; artifactDigest: string | null }[];
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; operationId: string; verifiedRevision: number; data: { revision: number; mods: unknown[] }; invalidate: unknown[] }> {
  return call("save_instance_mods", request);
}

export function prepareInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; operationId: string; workshopIds: string[];
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; task: TaskSnapshot }> {
  return call("prepare_instance_mods", request);
}

export function getInstanceModTask(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; operationId: string;
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; task: TaskSnapshot }> {
  return call("get_instance_mod_task", request);
}

export function listInstanceModTasks(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; items: TaskSnapshot[] }> {
  return call("list_instance_mod_tasks", request);
}
