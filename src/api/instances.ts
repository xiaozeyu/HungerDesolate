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
export interface InstanceRuntimeControlResponse {
  schemaVersion: 1; requestId: string; operationId: string; target: InstanceTarget;
  recordVersion: number; revision: number; status: "running" | "stopped"; verificationLevel: "localVerified" | "unverified";
  startedShards: Array<"master"|"caves">; runningShards: Array<"master"|"caves">;
  listeners: Partial<Record<"master"|"caves", number[]>>; publicConnectivityVerified: false;
}
export function controlInstance(request: {
  action: "start" | "stop" | "restart";
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; operationId: string;
}): Promise<InstanceRuntimeControlResponse> { return call("start_instance", request); }

export type BackupCaptureMode = "cold" | "quasiHot";
export interface BackupSummary {
  schemaVersion: 1; backupId: string; hostId: string; instanceId: string; createdAt: string;
  reason: string; captureMode: BackupCaptureMode; captureSemantics: "stoppedBeforeRead" | "onDiskBestEffort";
  runtimeStatusAtCapture: "running" | "stopped" | "degraded" | "unknown";
  sizeBytes: number; checksum: string | null; verification: "pending" | "verified" | "failed";
  restorability: "restorable" | "missingArtifacts" | "incompatible" | "unknown";
  dstBuild: string | null; dstReleaseId: string | null; modsReleaseId: string | null;
  mods?: { workshopId: string; enabled: boolean; artifactDigest?: string | null }[];
  configRevision: number; containsSecrets: boolean;
}
export interface BackupListResponse {
  schemaVersion: 1; requestId: string; target: InstanceTarget; configRevision: number;
  restoreAcceptancePassed: boolean; items: BackupSummary[];
}
export interface BackupPreviewResponse {
  schemaVersion: 1; requestId: string; target: InstanceTarget; configRevision: number;
  captureMode: BackupCaptureMode; runtimeStatus: "running" | "stopped" | "degraded" | "unknown";
  runtimeVerified: boolean; estimatedArchiveBytes: number; availableBytes: number; fileCount: number;
  worldDataPresent: boolean; allowed: boolean; warnings: string[];
}
export interface InstanceBackupTaskResponse {
  schemaVersion: 1; requestId: string; target: InstanceTarget; task: TaskSnapshot;
}
export function listInstanceBackups(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; expectedRevision: number;
}): Promise<BackupListResponse> { return call("list_instance_backups", request); }
export function previewInstanceBackup(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; captureMode: BackupCaptureMode;
}): Promise<BackupPreviewResponse> { return call("preview_instance_backup", request); }
export function createInstanceBackup(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
  expectedRevision: number; captureMode: BackupCaptureMode; operationId: string; backupId: string; reason: string;
}): Promise<InstanceBackupTaskResponse> { return call("create_instance_backup", request); }
export function cancelInstanceBackup(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; operationId: string;
}): Promise<InstanceBackupTaskResponse> { return call("cancel_instance_backup", request); }
export function getInstanceBackupTask(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; operationId: string;
}): Promise<InstanceBackupTaskResponse> { return call("get_instance_backup_task", request); }
export function listInstanceBackupTasks(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number;
}): Promise<InstanceBackupTaskResponse[]> { return call("list_instance_backup_tasks", request); }
export interface BackupDownloadResponse { schemaVersion:1; requestId?:string; target:InstanceTarget; operationId:string; backupId:string; status:"queued"|"running"|"interrupted"|"failed"|"succeeded"; stage:string; destination?:string; temporaryPath?:string; checksum?:string; sizeBytes?:number; bytesTransferred?:number; errorCode?:string; attempt?:number; }
export function pickBackupDownloadPath(): Promise<string|null> { return call("pick_backup_download_path"); }
export function downloadInstanceBackup(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;backupId:string;operationId:string;destination:string}):Promise<BackupDownloadResponse>{return call("download_instance_backup",request);}
export function listBackupDownloadTasks(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number}):Promise<BackupDownloadResponse[]>{return call("list_backup_download_tasks",request);}
export function deleteInstanceBackup(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;expectedRevision:number;backupId:string;expectedChecksum:string}):Promise<{schemaVersion:1;requestId:string;target:InstanceTarget;backupId:string;deleted:boolean}>{return call("delete_instance_backup",request);}
export function restoreInstanceBackup(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;expectedRevision:number;backupId:string;operationId:string;createProtectionBackup:boolean}):Promise<InstanceBackupTaskResponse>{return call("restore_instance_backup",request);}
export function cancelInstanceRestore(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;operationId:string}):Promise<InstanceBackupTaskResponse>{return call("cancel_instance_restore",request);}
export function getInstanceRestoreTask(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;operationId:string}):Promise<InstanceBackupTaskResponse>{return call("get_instance_restore_task",request);}
export function listInstanceRestoreTasks(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number}):Promise<InstanceBackupTaskResponse[]>{return call("list_instance_restore_tasks",request);}

export interface InstanceCreateInput {
  displayName: string;
  autostart: boolean;
  startAfterCreate: boolean;
  portPolicy: "auto";
  cavesEnabled: boolean;
}
export interface InstanceCreateDraft extends InstanceCreateInput {}
export interface InstanceCreateTaskInput extends InstanceCreateInput { releaseId:string;releaseCatalogDigest:string; }
export interface InstanceCreateTaskRequest {
  schemaVersion: 1; requestId: string; target: {serverId:string;hostId:string}; expectedRecordVersion: number;
  expectedRegistryRevision: number; operationId: string; input: InstanceCreateTaskInput;
}
export interface InstanceCreateTaskResponse {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number; task: TaskSnapshot | null;
}
export function createInstance(request: InstanceCreateTaskRequest): Promise<InstanceCreateTaskResponse> { return call("create_instance",request); }
export function getInstanceCreateStatus(request: {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;operationId:string}): Promise<InstanceCreateTaskResponse> {
  return call("get_instance_create_status",request);
}
export interface OrphanInstanceCleanupRequest {schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;expectedRegistryRevision:number;typedName:string;}
export interface OrphanInstanceCleanupResponse {schemaVersion:1;requestId:string;target:InstanceTarget;registryRevision:number;releasedReservations:number;removed:boolean;retired:boolean;}
export function cleanupOrphanInstance(request:OrphanInstanceCleanupRequest):Promise<OrphanInstanceCleanupResponse>{return call("cleanup_orphan_instance",request);}
export interface InstanceDeletePreview {
  schemaVersion:1;requestId:string;target:InstanceTarget;recordVersion:number;registryRevision:number;
  displayName:string;lifecycle:string;containers:{id:string;role:string;status:string}[];
  networks:{id:string;name:string;endpointIds:string[]}[];dataPresent:boolean;worldData:boolean;archived:boolean;
  blockers:string[];allowed:boolean;planId:string;
}
export function previewInstanceDelete(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;expectedRegistryRevision:number}):Promise<InstanceDeletePreview>{return call("preview_instance_delete",request);}
export interface InstanceDeleteResponse {schemaVersion:1;requestId:string;target:InstanceTarget;recordVersion:number;registryRevision:number;releasedReservations:number;removed:boolean;retired:boolean;removedContainers:number;removedNetworks:number;}
export function deleteInstance(request:{schemaVersion:1;requestId:string;target:InstanceTarget;expectedRecordVersion:number;expectedRegistryRevision:number;planId:string;operationId:string;typedName:string}):Promise<InstanceDeleteResponse>{return call("delete_instance",request);}
export interface InstanceReleaseSummary { releaseId:string; platform:string; image:string; imageDigest:string; dstBuild:string; artifactDigest:string; scriptVersion:string; createdAt?: string | null; verified:boolean; runtimeVerified:boolean; commands:Record<string,string[]>; }
export interface InstanceReleaseCatalogResponse { schemaVersion:1; requestId:string; serverId:string; hostId:string; recordVersion:number; catalogDigest:string|null; releases:InstanceReleaseSummary[]; blockers:string[]; }
export function listInstanceReleases(request: ConnectionRequest): Promise<InstanceReleaseCatalogResponse> { return call("list_instance_releases",request); }
export function verifyInstanceReleaseBase(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string}):Promise<{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};recordVersion:number;releaseId:string;verified:boolean}>{return call("verify_instance_release_base",request);}
export interface InstanceReleaseDeletePreview {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};releaseId:string;dstBuild:string;imageDigest:string;instanceReferences:string[];taskReferences:string[];containerReferences:string[];sharedImage:boolean;allowed:boolean;catalogDigest:string;}
export function previewInstanceReleaseDelete(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string}):Promise<InstanceReleaseDeletePreview>{return call("preview_instance_release_delete",request);}
export interface InstanceReleaseDeleteResponse {schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};releaseId:string;imageRemoved:boolean;imageShared:boolean;stagingRemoved:boolean;}
export function deleteInstanceRelease(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string;catalogDigest:string;typedName:string}):Promise<InstanceReleaseDeleteResponse>{return call("delete_instance_release",request);}
export interface BuildImageResponse { schemaVersion:1; requestId:string; serverId:string; recordVersion:number; releaseId:string; status:string; message:string; }
export function buildDstImage(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId?:string}):Promise<BuildImageResponse>{return call("build_dst_image",request);}
export interface BuildStatusResponse { schemaVersion:1; requestId:string; releaseId:string; status:"building"|"completed"|"failed"|"notFound"; progress?:{currentStep:string;percent?:number;stepPercent?:number|null}; logTail:string[]; }
export function getBuildStatus(request:{schemaVersion:1;requestId:string;target:{serverId:string;hostId:string};expectedRecordVersion:number;releaseId:string}):Promise<BuildStatusResponse>{return call("get_build_status",request);}
