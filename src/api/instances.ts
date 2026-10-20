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
export interface InstanceCreateTaskInput extends InstanceCreateInput { releaseId:string;releaseCatalogDigest:string;expectedPorts:CreationPort[]; }
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
export interface OrphanInstanceCleanupRequest {schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;expectedRegistryRevision:number;typedName:string;}
export interface OrphanInstanceCleanupResponse {schemaVersion:1;requestId:string;target:InstanceTarget;registryRevision:number;releasedReservations:number;removed:boolean;retired:boolean;}
export function cleanupOrphanInstance(request:OrphanInstanceCleanupRequest):Promise<OrphanInstanceCleanupResponse>{return call("cleanup_orphan_instance",request);}
export interface InstanceReleaseSummary { releaseId:string; platform:string; image:string; imageDigest:string; dstBuild:string; artifactDigest:string; scriptVersion:string; verified:boolean; runtimeVerified:boolean; commands:Record<string,string[]>; }
export interface InstanceReleaseCatalogResponse { schemaVersion:1; requestId:string; serverId:string; hostId:string; recordVersion:number; catalogDigest:string|null; releases:InstanceReleaseSummary[]; blockers:string[]; }
export function listInstanceReleases(request: ConnectionRequest): Promise<InstanceReleaseCatalogResponse> { return call("list_instance_releases",request); }
export interface InstanceReleasePrepareResponse { schemaVersion:1; requestId:string; target:{serverId:string;hostId:string}; recordVersion:number; releaseId:string; catalogDigest:string|null; status:"ready"|"blocked"; blockers:string[]; artifactPath:string|null; }
export function previewInstanceRelease(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string}):Promise<InstanceReleasePrepareResponse>{return call("preview_instance_release",request);}
export function importInstanceRelease(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string;operationId:string}):Promise<{schemaVersion:1;requestId:string;serverId:string;recordVersion:number;task:Record<string,unknown>|null}>{return call("import_instance_release",request);}
export interface RuntimeVerifyReleaseResponse {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};recordVersion:number;releaseId:string;runtimeVerified:boolean;listeners:Record<string,number[]>;}
export function verifyInstanceReleaseRuntime(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string;operationId:string;settingsBundleBase64:string}):Promise<RuntimeVerifyReleaseResponse>{return call("verify_instance_release_runtime",request);}
export function verifyInstanceReleaseBase(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string}):Promise<{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};recordVersion:number;releaseId:string;verified:boolean}>{return call("verify_instance_release_base",request);}
export interface InstanceReleaseDeletePreview {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};releaseId:string;dstBuild:string;imageDigest:string;instanceReferences:string[];taskReferences:string[];containerReferences:string[];sharedImage:boolean;allowed:boolean;catalogDigest:string;}
export function previewInstanceReleaseDelete(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string}):Promise<InstanceReleaseDeletePreview>{return call("preview_instance_release_delete",request);}
export interface InstanceReleaseDeleteResponse {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};releaseId:string;imageRemoved:boolean;imageShared:boolean;stagingRemoved:boolean;}
export function deleteInstanceRelease(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string;typedName:string}):Promise<InstanceReleaseDeleteResponse>{return call("delete_instance_release",request);}
export interface BuildImageResponse { schemaVersion:1; requestId:string; serverId:string; recordVersion:number; releaseId:string; status:string; message:string; }
export function buildDstImage(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId?:string}):Promise<BuildImageResponse>{return call("build_dst_image",request);}
export interface BuildStatusResponse { schemaVersion:1; requestId:string; releaseId:string; status:"building"|"completed"|"failed"|"notFound"; progress?:{currentStep:string;percent?:number}; logTail:string[]; }
export function getBuildStatus(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string}):Promise<BuildStatusResponse>{return call("get_build_status",request);}
