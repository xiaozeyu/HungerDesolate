import type { InstanceModSummary } from "../types/mods";

export interface PreparedModReference {
  workshopId: string;
  contentDigest: string;
  updateCandidate: boolean;
  previousArtifactDigest?: string | null;
}

export interface PreparedModSave {
  workshopId: string;
  enabled: boolean;
  configurationOptions: Record<string, unknown>;
  artifactDigest: string | null;
}

export function buildPreparedModSave(items: InstanceModSummary[], prepared: PreparedModReference[]) {
  const bindings = new Map<string, string>();
  const conflicts: string[] = [];

  for (const artifact of prepared) {
    if (artifact.updateCandidate) continue;
    if (!/^[0-9a-f]{64}$/.test(artifact.contentDigest)) {
      conflicts.push(artifact.workshopId);
      continue;
    }
    const current = items.find(item => item.workshopId === artifact.workshopId);
    if (!current || current.desiredEnabled !== true) {
      conflicts.push(artifact.workshopId);
      continue;
    }
    if (current.desiredArtifactDigest === artifact.contentDigest) continue;
    if (current.desiredArtifactDigest !== (artifact.previousArtifactDigest ?? null)) {
      conflicts.push(artifact.workshopId);
      continue;
    }
    bindings.set(artifact.workshopId, artifact.contentDigest);
  }

  const changedWorkshopIds = [...bindings.keys()];
  const mods: PreparedModSave[] = items
    .filter(item => item.desiredEnabled !== null)
    .map(item => ({
      workshopId: item.workshopId,
      enabled: item.desiredEnabled === true,
      configurationOptions: item.desiredConfiguration ?? {},
      artifactDigest: bindings.get(item.workshopId) ?? item.desiredArtifactDigest,
    }));
  return { mods, changedWorkshopIds, conflicts };
}
