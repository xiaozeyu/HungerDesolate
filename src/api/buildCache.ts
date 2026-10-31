import { call } from "./invoke";

export interface BuildCachePreview {
  schemaVersion: 1;
  requestId: string;
  serverId: string;
  hostId: string;
  recordVersion: number | null;
  capturedAt: string;
  contextBytes: number;
  buildActive: boolean;
  dockerAvailable: boolean;
  dockerCache: { size: string | null; reclaimable: string | null };
  allowed: boolean;
}

export interface BuildCacheCleanup {
  schemaVersion: 1;
  requestId: string;
  serverId: string;
  hostId: string;
  recordVersion: number | null;
  capturedAt: string;
  contextBytes: number;
  dockerOutput: string;
}

export function previewBuildCache(request: {
  schemaVersion: 1; requestId: string; action: "preview"; serverId: string; hostId: string; expectedRecordVersion: number;
}): Promise<BuildCachePreview> {
  return call("manage_build_cache", request);
}

export function cleanupBuildCache(request: {
  schemaVersion: 1; requestId: string; action: "cleanup"; serverId: string; hostId: string; expectedRecordVersion: number;
}): Promise<BuildCacheCleanup> {
  return call("manage_build_cache", request);
}
