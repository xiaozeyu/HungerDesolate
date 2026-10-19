import { call } from "./invoke";
import type { ConnectionRequest, HostEnvironment } from "../types/contracts";
export function getHostEnvironment(request: ConnectionRequest): Promise<HostEnvironment> {
  return call("get_host_environment",request);
}
export interface DockerInstallationPreview {
  status: "ready" | "blocked";
  blockers: string[];
  snapshotDigest: string;
  expiresAtUnixSeconds: number;
  distribution: {id: string | null; version: string | null; codename: string | null; architecture: string | null};
  hostId: string;
  uid: number;
  rootAvailableBytes: number;
  sourceState: "missing" | "matching";
  repositoryReachable: boolean;
  installedDockerPackages: Record<string,string>;
  aptRunning: boolean;
  dpkgClean: boolean;
  packages: string[];
  repository: string;
  sourceFile: string;
  keyFile: string;
}
export interface DockerInstallationPreviewResponse {
  schemaVersion: 1;
  requestId: string;
  serverId: string;
  recordVersion: number;
  planId: string | null;
  operationId: string | null;
  preview: DockerInstallationPreview;
}
export interface DockerInstallTask {
  schemaVersion: 1;
  taskId: string;
  hostId: string;
  sequence: number;
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  phase: string;
  messageCode: string | null;
  startedAtUnixSeconds: number;
  updatedAtUnixSeconds: number;
  installedVersions: Record<string,string>;
}
export interface DockerInstallStatusResponse {
  schemaVersion: 1;
  requestId: string;
  serverId: string;
  hostId: string;
  recordVersion: number;
  task: DockerInstallTask | null;
}
export function previewDockerInstallation(request: ConnectionRequest): Promise<DockerInstallationPreviewResponse> {
  return call("preview_docker_installation",request);
}
export function startDockerInstallation(request: ConnectionRequest & {planId:string;operationId:string;snapshotDigest:string;expiresAtUnixSeconds:number}): Promise<DockerInstallStatusResponse> {
  return call("start_docker_installation",request);
}
export function getDockerInstallationStatus(request: ConnectionRequest & {operationId?: string}): Promise<DockerInstallStatusResponse> {
  return call("get_docker_installation_status",request);
}
export interface HostInitializationPlan {
  planId: string;
  operationId: string;
  hostId: string;
  expiresAtUnixSeconds: number;
  status: "preview" | "incomplete" | "succeeded";
  managementRoot: string;
  paths: string[];
}
export interface HostInitializationResponse {
  schemaVersion: 1;
  requestId: string;
  serverId: string;
  recordVersion: number;
  plan: HostInitializationPlan | null;
}
export function getHostInitialization(request: ConnectionRequest): Promise<HostInitializationResponse> {
  return call("get_host_initialization",request);
}
export function previewHostInitialization(request: ConnectionRequest): Promise<HostInitializationResponse> {
  return call("preview_host_initialization",request);
}
export function initializeHost(request: ConnectionRequest & {planId: string}): Promise<HostInitializationResponse> {
  return call("initialize_host",request);
}
