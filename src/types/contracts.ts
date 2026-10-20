export type ServerId = string;
export type HostId = string;
export type InstanceId = string;
export type TaskId = string;
export type ShardKind = "master" | "caves";
export type RuntimeStatus = "unknown" | "stopped" | "starting" | "running" | "degraded" | "stopping" | "failed";
export interface HostTarget {
    serverId: ServerId;
    hostId: HostId;
}
export interface InstanceTarget extends HostTarget {
    instanceId: InstanceId;
}
export interface ConnectionTaskTarget {
    serverId: ServerId;
    connectionVersion: number;
    verifiedFingerprint: string;
}
export type TaskTarget = HostTarget | InstanceTarget | ConnectionTaskTarget;
export interface ReadRequest<T> {
    schemaVersion: 1;
    requestId: string;
    target: T;
}
export interface QueryResponse<T, TTarget = InstanceTarget> {
    schemaVersion: 1;
    requestId: string;
    target: TTarget;
    snapshotId: string;
    capturedAt: string;
    source: "remote" | "cache";
    freshness: "fresh" | "stale";
    configRevision: number | null;
    data: T;
}
export interface ServerSummary {
    serverId: ServerId;
    displayName: string;
    host: string;
    sshPort: number;
    username: string;
    authKind: "password" | "key";
    connectionState: "disconnected" | "connecting" | "connected" | "unreachable" | "trustRequired";
    hostId: HostId | null;
    recordVersion: number;
}
export interface ServerInput {
    displayName: string;
    host: string;
    sshPort: number;
    username: string;
    authKind: "password" | "key";
}
export interface SaveServerRequest {
    schemaVersion: 1; requestId: string; operationId: string;
    serverId: string | null; expectedRecordVersion: number | null; input: ServerInput;
}
export interface DeleteServerRequest {
    schemaVersion: 1; requestId: string; operationId: string;
    serverId: string; expectedRecordVersion: number;
}
export interface InstanceSummary {
    hostId: HostId;
    instanceId: InstanceId;
    displayName: string;
    lifecycle: "creating" | "ready" | "createFailed" | "deleting" | "deleteFailed" | "retired";
    runtimeStatus: RuntimeStatus;
    players: number | null;
    maxPlayers: number | null;
    gameMode: string | null;
    cavesEnabled: boolean | null;
    installedBuild: string | null;
    lastSyncedAt: string | null;
    activeTaskId: TaskId | null;
    manifestAvailable: boolean;
}
export interface InstanceOverview {
    identity: InstanceSummary;
    manifestSummary?: {configRevision: number; composeProject: string; cavesEnabled: boolean; autostart: boolean; gameMode: string | null; pvp: boolean | null};
    verificationLevel: "unverified" | "processOnly" | "localVerified" | "networkVerified";
    blockers: string[];
    desiredVsDeployed: "inSync" | "pending" | "drifted" | "unknown";
}
export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
export interface TaskSnapshot {
    schemaVersion: 1;
    taskId: TaskId;
    operationId: string;
    target: TaskTarget;
    operation: string;
    status: TaskStatus;
    stage: string;
    lastCompletedStage: string | null;
    progress: {
        kind: "determinate";
        completed: number;
        total: number;
    } | {
        kind: "indeterminate";
    };
    sequence: number;
    createdAt: string;
    startedAt: string | null;
    finishedAt: string | null;
    cancelAllowed: boolean;
    observation: "live" | "stale" | "reconciling";
    journal?: {sequence:number;status:TaskStatus;stage:string;at:string}[];
    result?: TaskResult | null;
    error?: CommandError | null;
}
export interface TaskRecoveryAction {
    action: "reconcile" | "retryPreparation" | "restoreBackup" | "resumeCreate" | "cleanupCreate" | "inspectLogs";
    label: string;
    requiresPreview: boolean;
}
export interface TaskResult {
    preparedHost: HostTarget | null;
    verifiedRevision: number | null;
    verification: "passed" | "notVerified";
    remoteChanged: boolean;
    backupId: string | null;
    deploymentId: string | null;
    invalidate: {hostId: HostId; instanceId: InstanceId | null; domains: string[]}[];
    recoveryActions: TaskRecoveryAction[];
}

export interface MutationResult<T> {
    schemaVersion: 1;
    requestId: string;
    target: InstanceTarget;
    operationId: string;
    verifiedRevision: number;
    data: T;
    invalidate: {hostId: HostId; instanceId: InstanceId | null; domains: string[]}[];
}

export interface CommandError {
    schemaVersion: 1;
    code: string;
    category: string;
    operation: string;
    requestId: string | null;
    target: InstanceTarget | HostTarget | null;
    message: string;
    retryable: boolean;
    remoteChanged: boolean | null;
    taskId: string | null;
    recoverySteps: string[];
    details: Record<string, unknown>;
}
export interface BootstrapSnapshot {
    schemaVersion: 1;
    productName: string;
    productVersion: string;
    applicationId: string;
    remoteOperationsAvailable: boolean;
}

export interface ConnectionRequest {
  schemaVersion: 1; requestId: string; serverId: string; expectedRecordVersion: number;
}
export interface SshConnectionState {
  schemaVersion: 1; requestId: string;
  serverId: string; recordVersion: number; fingerprint: string | null; algorithm: string | null;
  credentialConfigured: boolean; cleanupPending: number;
}
export interface FingerprintChallenge {
  schemaVersion: 1; requestId: string;
  challengeId: string; serverId: string; recordVersion: number; algorithm: string;
  fingerprint: string; previousFingerprint: string | null; expiresAtUnixSeconds: number;
}
export type CredentialInput = {kind: "password"; password: string}
  | {kind: "privateKey"; privateKey: string; passphrase: string | null}
  | {kind: "privateKeyFile"; path: string; passphrase: string | null};
export interface HostConnection {
  schemaVersion: 1; requestId: string;
  serverId: string; recordVersion: number; hostId: string | null;
  managementState: "uninitialized" | "ready"; verifiedAtUnixSeconds: number;
}
export interface CleanupStatus { pendingCount: number; failedCount: number }

export interface EnvironmentCheck {
  code: string; level: "passed" | "warning" | "blocker";
  label: string; message: string; recoverySteps: string[];
}
export interface DiskSnapshot {path: string; totalBytes: number; availableBytes: number}
export interface MemorySnapshot {totalBytes: number; availableBytes: number | null}
export interface HostEnvironment {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number;
  hostId: string | null; environmentCheckId: string; checkedAtUnixSeconds: number;
  distributionId: string | null; distributionVersion: string | null; distributionCodename: string | null;
  os: string | null; kernel: string | null; architecture: string | null; uid: number | null;
  disk: DiskSnapshot | null; memory: MemorySnapshot | null;
  docker: {clientVersion: string | null; serverVersion: string | null; composeVersion: string | null;
    engineState: "notInstalled" | "permissionDenied" | "unavailable" | "available";
    dataRoot: string | null; disk: DiskSnapshot | null};
  checks: EnvironmentCheck[]; deploymentVerified: boolean;
}
