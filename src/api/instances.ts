import { call } from "./invoke";
import type { ConnectionRequest, InstanceOverview, InstanceTarget, InstanceSummary, TaskSnapshot } from "../types/contracts";
export interface InstanceListResponse {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number;
  hostId: string; registryRevision: number; capturedAtUnixSeconds: number; instances: InstanceSummary[];
}
export interface InstanceOverviewResponse {
  schemaVersion: 1; requestId: string; target: InstanceTarget; recordVersion: number;
  snapshotId: string; capturedAtUnixSeconds: number; configRevision: number; data: InstanceOverview;
}
export function listInstances(request: ConnectionRequest): Promise<InstanceListResponse> { return call("list_instances",request); }
export function getInstanceOverview(target: InstanceTarget, requestId: string, expectedRecordVersion: number): Promise<InstanceOverviewResponse> {
  return call("get_instance_overview",{schemaVersion:1,requestId,target,expectedRecordVersion});
}

export interface InstanceCreateInput {
  displayName: string;
  autostart: boolean;
  startAfterCreate: boolean;
  portPolicy: "auto";
  cavesEnabled: boolean;
}
export interface InstanceCreateDraft extends InstanceCreateInput {}
export interface CreationPort {
  shard: "master" | "caves";
  purpose: "game" | "steamAuth" | "steamMaster";
  protocol: "udp";
  bindAddress: "0.0.0.0";
  hostPort: number;
  containerPort: number;
}
export interface InstanceCreatePreview {
  schemaVersion: 1; requestId: string; target: {serverId:string;hostId:string};
  recordVersion: number; registryRevision: number; previewId: string;
  capturedAtUnixSeconds: number; expiresAtUnixSeconds: number;
  input: InstanceCreateInput; proposedInstanceId: string; composeProject: string;
  storagePath: string; ports: CreationPort[]; blockers: string[];
  availableBytes: number; dockerVersion: string | null; composeVersion: string | null;
  allowed: false; portReservation: "unreserved";
}
export interface InstanceCreatePreviewRequest {
  schemaVersion: 1; requestId: string; target: {serverId:string;hostId:string};
  expectedRecordVersion: number; expectedRegistryRevision: number; input: InstanceCreateInput;
}
export interface InstanceCreateTaskInput extends InstanceCreateInput { expectedPorts: CreationPort[]; }
export interface InstanceCreateTaskRequest {
  schemaVersion: 1; requestId: string; target: {serverId:string;hostId:string}; expectedRecordVersion: number;
  expectedRegistryRevision: number; planId: string; operationId: string; input: InstanceCreateTaskInput;
}
export interface InstanceCreateTaskResponse {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number; task: TaskSnapshot | null;
}
export function previewInstanceCreate(request: InstanceCreatePreviewRequest): Promise<InstanceCreatePreview> {
  return call("preview_instance_create",request);
}
export function createInstance(request: InstanceCreateTaskRequest): Promise<InstanceCreateTaskResponse> { return call("create_instance",request); }
export function getInstanceCreateStatus(request: {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;operationId:string}): Promise<InstanceCreateTaskResponse> {
  return call("get_instance_create_status",request);
}
export interface InstanceReleaseSummary { releaseId:string; platform:string; image:string; imageDigest:string; dstBuild:string; artifactDigest:string; scriptVersion:string; verified:boolean; runtimeVerified:boolean; commands:Record<string,string[]>; }
export interface InstanceReleaseCatalogResponse { schemaVersion:1; requestId:string; serverId:string; hostId:string; recordVersion:number; catalogDigest:string|null; releases:InstanceReleaseSummary[]; blockers:string[]; }
export function listInstanceReleases(request: ConnectionRequest): Promise<InstanceReleaseCatalogResponse> { return call("list_instance_releases",request); }
export interface InstanceReleasePrepareResponse { schemaVersion:1; requestId:string; target:{serverId:string;hostId:string}; recordVersion:number; releaseId:string; catalogDigest:string|null; status:"ready"|"blocked"; blockers:string[]; artifactPath:string|null; }
export function previewInstanceRelease(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string}):Promise<InstanceReleasePrepareResponse>{return call("preview_instance_release",request);}
export function importInstanceRelease(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string;operationId:string}):Promise<{schemaVersion:1;requestId:string;serverId:string;recordVersion:number;task:Record<string,unknown>|null}>{return call("import_instance_release",request);}
