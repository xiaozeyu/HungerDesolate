import { call } from "./invoke";
import type { InstanceTarget, QueryResponse, MutationResult } from "../types/contracts";
import type { WorldConfig, WorldSettings, TokenChange, ServerPasswordChange } from "../types/world";

export interface GetWorldConfigRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
}

export interface SaveWorldConfigRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
  operationId: string;
  settings: WorldSettings;
  tokenChange: TokenChange | null;
  serverPasswordChange: ServerPasswordChange | null;
}

export interface ApplyWorldConfigRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
  operationId: string;
}

export interface ApplyWorldConfigResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  revision: number;
  activeReleaseId: string;
  desiredDigest: string;
  runtimeVerified: false;
  runningShards: Array<"master" | "caves">;
  warnings: string[];
}

export type SaveWorldConfigResponse = MutationResult<WorldConfig>;

export function getWorldConfig(request: GetWorldConfigRequest): Promise<QueryResponse<WorldConfig, InstanceTarget>> {
  return call("get_world_config", request);
}

export function saveWorldConfig(request: SaveWorldConfigRequest): Promise<SaveWorldConfigResponse> {
  return call("save_world_config", request);
}

export function applyWorldConfig(request: ApplyWorldConfigRequest): Promise<ApplyWorldConfigResponse> {
  return call("apply_world_config", request);
}
