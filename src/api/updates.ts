import { call } from "./invoke";
import type { InstanceTarget, TaskSnapshot } from "../types/contracts";

export interface CandidateRelease {
  releaseId: string;
  build: string;
  imageDigest: string;
  runtimeVerified: boolean;
  createdAt: string | null;
}

export interface HostDstUpdateStatus {
  schemaVersion: 1;
  requestId: string;
  target: { serverId: string; hostId: string };
  latest: { build: string; checkedAt: string; source: string } | null;
  candidateReleases: CandidateRelease[];
  lastCheckError: { code: string; message: string; detail: string | null; at: string } | null;
  checking: boolean;
  stage: "image" | "steamcmd" | "finalize" | null;
  stageDetail: string | null;
  startedAt: string | null;
}

export interface UpdatePreview {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  planId: string;
  operation: "instanceUpdate";
  baseRevision: number;
  current: {
    releaseId: string | null;
    build: string | null;
    imageDigest: string | null;
    runtimeStatus: string;
    runningShards: string[];
  };
  targetRelease: {
    releaseId: string;
    build: string | null;
    imageDigest: string | null;
    runtimeVerified: boolean;
    createdAt: string | null;
  };
  shards: string[];
  stopRequired: boolean;
  backup: { required: boolean; captureMode: string; scope: string; estimatedBytes: number; availableBytes: number };
  rollback: { available: boolean; reason: string | null };
  warnings: string[];
  blockers: string[];
  allowed: boolean;
  expiresAt: string;
}

export interface UpdateTaskResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  task: TaskSnapshot;
}

export interface HostUpdateRequest {
  schemaVersion: 1;
  requestId: string;
  serverId: string;
  hostId: string;
  expectedRecordVersion: number;
}

export function getDstUpdateStatus(request: HostUpdateRequest): Promise<HostDstUpdateStatus> {
  return call("get_dst_update_status", request);
}

export function checkDstUpdates(request: HostUpdateRequest): Promise<HostDstUpdateStatus> {
  return call("check_dst_updates", request);
}

export function previewInstanceUpdate(request: {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
  targetReleaseId: string;
}): Promise<UpdatePreview> {
  return call("preview_instance_update", request);
}

export function runInstanceUpdate(request: {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  expectedRevision: number;
  planId: string;
  operationId: string;
}): Promise<UpdateTaskResponse> {
  return call("run_instance_update", request);
}

export function getInstanceUpdateTask(request: {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  operationId: string;
}): Promise<UpdateTaskResponse> {
  return call("get_instance_update_task", request);
}

export function cancelInstanceUpdate(request: {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
  operationId: string;
}): Promise<UpdateTaskResponse> {
  return call("cancel_instance_update", request);
}

export function listInstanceUpdateTasks(request: {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  expectedRecordVersion: number;
}): Promise<UpdateTaskResponse[]> {
  return call("list_instance_update_tasks", request);
}
