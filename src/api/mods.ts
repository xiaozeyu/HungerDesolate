import { call } from "./invoke";
import type { InstanceTarget } from "../types/contracts";
import type { InstanceModsResponse } from "../types/mods";
import type { TaskSnapshot } from "../types/contracts";

export interface ModApplyPreviewResponse {
  schemaVersion: 1; requestId: string; target: InstanceTarget; configRevision: number;
  desiredDigest: string; modsReleaseId: string | null; runtimeStatus: "running" | "stopped";
  shards: Array<"master" | "caves">; changes: { workshopId: string; action: "add" | "update" | "configure" | "disable" | "remove" }[];
  blockers: string[]; allowed: boolean;
}
export function previewInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; expectedRevision: number;
}): Promise<ModApplyPreviewResponse> {
  return call("preview_apply_instance_mods", request);
}
export function applyInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; desiredDigest: string; operationId: string; createProtectionBackup: boolean;
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; task: TaskSnapshot }> {
  return call("apply_instance_mods", request);
}
export function cancelInstanceModApply(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; operationId: string;
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; task: TaskSnapshot }> {
  return call("cancel_instance_mod_apply", request);
}
export function getInstanceModApplyTask(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; operationId: string;
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; task: TaskSnapshot }> {
  return call("get_instance_mod_apply_task", request);
}
export function listInstanceModApplyTasks(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; items: TaskSnapshot[] }> {
  return call("list_instance_mod_apply_tasks", request);
}

export function listInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
}): Promise<InstanceModsResponse> {
  return call("list_instance_mods", request);
}

export function saveInstanceMods(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; operationId: string;
  mods: { workshopId: string; enabled: boolean; configurationOptions: Record<string, unknown>; artifactDigest: string | null }[];
  schemaBindings?: { workshopId: string; artifactDigest: string; modinfoSha256: string; changedFields: string[] }[];
}): Promise<{ schemaVersion: 1; requestId: string; target: InstanceTarget; operationId: string; verifiedRevision: number; data: { revision: number; mods: unknown[] }; invalidate: unknown[] }> {
  return call("save_instance_mods", request);
}
