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

export interface WorldApplyPreview {
  schemaVersion:1;requestId:string;target:InstanceTarget;recordVersion:number;revision:number;
  desiredDigest:string;worldDataPresent:boolean;worldDataFileCount:number;
  newWorldOptions:{shard:"master"|"caves";key:string;label:string}[];
  tokenStatus:"configured"|"missing";serverPasswordStatus:"configured"|"missing";
  backupRequirement:"required"|"none";warnings:string[];blockers:string[];allowed:boolean;
}

export interface PreviewWorldApplyRequest {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
}

export type SaveWorldConfigResponse = MutationResult<WorldConfig>;

export function getWorldConfig(request: GetWorldConfigRequest): Promise<QueryResponse<WorldConfig, InstanceTarget>> {
  return call("get_world_config", request);
}

export function saveWorldConfig(request: SaveWorldConfigRequest): Promise<SaveWorldConfigResponse> {
  return call("save_world_config", request);
}

export function previewWorldApply(request: PreviewWorldApplyRequest): Promise<WorldApplyPreview> {
  return call("preview_world_apply", request);
}
