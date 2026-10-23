import type { InstanceTarget } from "./contracts";

export type ModSchemaStatus = "available" | "partial" | "noOptions" | "unavailable" | "failed";
export interface ModDiagnostic { code: string; message: string; }
export interface ModMetadata { name: string | null; author: string | null; description: string | null; version: string | null; apiVersion: number | null; }
export interface ModOption { label: string; value: string | number | boolean | null | Record<string, unknown> | unknown[]; hover: string | null; }
export interface ModField {
  name: string; label: string; hover: string | null;
  defaultValue: string | number | boolean | null | Record<string, unknown> | unknown[] | null;
  options: ModOption[]; editable: boolean; reason: string | null;
}
export interface ModSchema { status: ModSchemaStatus; folderName: string; locale: string; metadata: ModMetadata; fields: ModField[]; diagnostics: ModDiagnostic[]; }
export interface InstanceModSummary {
  workshopId: string; desiredEnabled: boolean | null; deployedEnabled: boolean | null;
  desiredConfiguration: Record<string, unknown> | null; deployedConfiguration: Record<string, unknown> | null;
  schema: ModSchema; artifactDigest: string | null; desiredArtifactDigest: string | null;
  deployedArtifactDigest: string | null; preparedDigest: string | null; preparedAt: string | null;
  modinfoSha256: string | null; preparedSourceHandle: string | null;
  deployedVersion: string | null; candidateDigest: string | null; candidateVersion: string | null;
  candidateSourceHandle: string | null; candidatePreparedAt: string | null;
}
export interface InstanceModsResponse {
  schemaVersion: 1; requestId: string; target: InstanceTarget; snapshotId: string;
  capturedAt: string; source: "remote" | "cache"; freshness: "fresh" | "stale";
  configRevision: number; items: InstanceModSummary[];
}
