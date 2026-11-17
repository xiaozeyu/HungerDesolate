import { call } from "./invoke";
import type { InstanceTarget } from "../types/contracts";

export interface PlayerEntry {
  userId: string;
  name: string;
  character: string;
  shard: "Master" | "Caves";
  capturedAtUnixSeconds: number;
}
export interface PlayerBridgeInfo {
  enabled: boolean;
  version: string;
  digest: string;
  installedAt: string | null;
}
export interface PlayersResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  snapshotId: string;
  capturedAt: string;
  source: "remote";
  freshness: "fresh" | "stale" | "unavailable" | "disabled";
  configRevision: number;
  data: {
    bridge: PlayerBridgeInfo | null;
    runtimeStatus: "running" | "stopped" | "degraded" | "unknown";
    onlineCount: number;
    players: PlayerEntry[];
    warnings: string[];
  };
}
export interface PlayerBridgeStatusResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  configRevision: number;
  bridge: PlayerBridgeInfo | null;
  blockers: string[];
  allowed: boolean;
  restartRequired: boolean;
  runtimeStatus: "running" | "stopped" | "degraded" | "unknown";
  shards: Array<"master" | "caves">;
}

export function getInstancePlayers(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
}): Promise<PlayersResponse> {
  return call("get_instance_players", request);
}
export function previewInstancePlayerBridge(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget;
  expectedRecordVersion: number; expectedRevision: number;
}): Promise<PlayerBridgeStatusResponse> {
  return call("preview_instance_player_bridge", request);
}
export function installInstancePlayerBridge(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget;
  expectedRecordVersion: number; expectedRevision: number;
}): Promise<PlayerBridgeStatusResponse> {
  return call("install_instance_player_bridge", request);
}
