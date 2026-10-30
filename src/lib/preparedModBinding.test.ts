import assert from "node:assert/strict";
import test from "node:test";
import type { InstanceModSummary } from "../types/mods.ts";
import { buildPreparedModSave } from "./preparedModBinding.ts";

function mod(overrides: Partial<InstanceModSummary> = {}): InstanceModSummary {
  return {
    workshopId: "123", desiredEnabled: true, deployedEnabled: null,
    desiredConfiguration: {}, deployedConfiguration: null,
    schema: { status: "unavailable", folderName: "workshop-123", locale: "zh",
      metadata: { name: null, author: null, description: null, version: null, apiVersion: null }, fields: [], diagnostics: [] },
    artifactDigest: null, desiredArtifactDigest: null, deployedArtifactDigest: null,
    preparedDigest: null, preparedAt: null, modinfoSha256: null, preparedSourceHandle: null,
    deployedVersion: null, candidateDigest: null, candidateVersion: null,
    candidateSourceHandle: null, candidatePreparedAt: null, ...overrides,
  };
}

test("binds a normal prepared digest while leaving update candidates for explicit adoption", () => {
  const firstDigest = "a".repeat(64);
  const candidateDigest = "b".repeat(64);
  const result = buildPreparedModSave([
    mod({ workshopId: "123" }),
    mod({ workshopId: "456", desiredArtifactDigest: firstDigest, artifactDigest: firstDigest }),
  ], [
    { workshopId: "123", contentDigest: firstDigest, updateCandidate: false, previousArtifactDigest: null },
    { workshopId: "456", contentDigest: candidateDigest, updateCandidate: true, previousArtifactDigest: firstDigest },
  ]);

  assert.deepEqual(result.changedWorkshopIds, ["123"]);
  assert.equal(result.mods.find(item => item.workshopId === "123")?.artifactDigest, firstDigest);
  assert.equal(result.mods.find(item => item.workshopId === "456")?.artifactDigest, firstDigest);
  assert.deepEqual(result.conflicts, []);
});

test("does not overwrite a changed or disabled desired MOD", () => {
  const digest = "a".repeat(64);
  const result = buildPreparedModSave([
    mod({ workshopId: "123", desiredArtifactDigest: "b".repeat(64) }),
    mod({ workshopId: "456", desiredEnabled: false }),
  ], [
    { workshopId: "123", contentDigest: digest, updateCandidate: false, previousArtifactDigest: null },
    { workshopId: "456", contentDigest: digest, updateCandidate: false, previousArtifactDigest: null },
  ]);

  assert.deepEqual(result.changedWorkshopIds, []);
  assert.deepEqual(result.conflicts, ["123", "456"]);
});
