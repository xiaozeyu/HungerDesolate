import { call } from "./invoke";
import type { InstanceTarget, QueryResponse, MutationResult } from "../types/contracts";
import type { WorldConfig, WorldSettings, TokenChange } from "../types/world";

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
}

export function getWorldConfig(
  target: InstanceTarget,
  requestId: string,
  expectedRecordVersion: number
): Promise<QueryResponse<WorldConfig, InstanceTarget>> {
  return call("get_world_config", {
    schemaVersion: 1,
    requestId,
    target,
    expectedRecordVersion,
  });
}

export function saveWorldConfig(
  target: InstanceTarget,
  settings: WorldSettings,
  tokenChange: TokenChange | null,
  expectedRevision: number,
  operationId: string,
  requestId: string,
  expectedRecordVersion: number
): Promise<MutationResult<WorldConfig>> {
  return call("save_world_config", {
    schemaVersion: 1,
    requestId,
    target,
    expectedRecordVersion,
    expectedRevision,
    operationId,
    settings,
    tokenChange,
  });
}
