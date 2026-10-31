import { call } from "./invoke";
import type { InstanceTarget } from "../types/contracts";

export interface DiagnosticShard {
  shard: "master" | "caves";
  containerId: string | null;
  status: string | null;
}

export interface DiagnosticHealth {
  runtimeStatus: string;
  configRevision: number;
  worldRevision: number | null;
  modsRevision: number | null;
  dstBuild: string | null;
  dstReleaseId: string | null;
  modsReleaseId: string | null;
  dockerVersion: string | null;
  diskAvailableBytes: number | null;
  shards: DiagnosticShard[];
  warnings: string[];
}

export interface DiagnosticLog {
  shard: "master" | "caves";
  lines: string[];
  truncated: boolean;
  available: boolean;
}

export interface DiagnosticTask {
  taskId: string;
  operation: string | null;
  status: string;
  stage: string | null;
  errorCode: string | null;
  recoverySteps: string[];
  createdAt: string | null;
  finishedAt: string | null;
  journal: Array<{ sequence?: number; status?: string; stage?: string; at?: string }>;
}

export interface DiagnosticsResponse {
  schemaVersion: 1;
  requestId: string;
  target: InstanceTarget;
  recordVersion: number;
  capturedAt: string;
  health: DiagnosticHealth;
  logs: DiagnosticLog[];
  tasks: DiagnosticTask[];
}

export function getInstanceDiagnostics(request: {
  schemaVersion: 1; requestId: string; target: InstanceTarget; expectedRecordVersion: number; logLimit?: number;
}): Promise<DiagnosticsResponse> {
  return call("get_instance_diagnostics", request);
}
