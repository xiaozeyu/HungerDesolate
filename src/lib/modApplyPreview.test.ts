import assert from "node:assert/strict";
import test from "node:test";
import type { InstanceModSummary } from "../types/mods.ts";
import { buildModApplyPreview, mergeModApplyBlockers } from "./modApplyPreview.ts";

function mod(overrides: Partial<InstanceModSummary> = {}): InstanceModSummary {
  return {
    workshopId: "123", desiredEnabled: true, deployedEnabled: false, desiredConfiguration: { mode: 1 },
    deployedConfiguration: null, schema: { status: "available", folderName: "workshop-123", locale: "zh",
      metadata: { name: null, author: null, description: null, version: null, apiVersion: null }, fields: [], diagnostics: [] },
    artifactDigest: "a".repeat(64), desiredArtifactDigest: null, deployedArtifactDigest: null,
    preparedDigest: "a".repeat(64), preparedAt: "2026-10-01T00:00:00Z",
    modinfoSha256: "b".repeat(64), preparedSourceHandle: null, deployedVersion: null,
    candidateDigest: null, candidateVersion: null, candidateSourceHandle: null, candidatePreparedAt: null,
    ...overrides,
  };
}

test("builds a read-only preview for add/configure/disable/remove changes", () => {
  const preview = buildModApplyPreview({
    mods: [
      mod({ workshopId: "add", deployedEnabled: null }),
      mod({ workshopId: "config", deployedEnabled: true, deployedConfiguration: { mode: 0 },
        desiredConfiguration: { mode: 1 }, deployedArtifactDigest: "a".repeat(64) }),
      mod({ workshopId: "disable", desiredEnabled: false, deployedEnabled: true }),
      mod({ workshopId: "remove", desiredEnabled: null, deployedEnabled: true }),
    ],
    runtimeStatus: "running", cavesEnabled: true,
  });

  assert.deepEqual(preview.changes.map(change => [change.workshopId, change.action]), [
    ["add", "add"], ["config", "configure"], ["disable", "disable"], ["remove", "remove"],
  ]);
  assert.deepEqual(preview.shards, ["master", "caves"]);
  assert.equal(preview.canApply, true);
  assert.deepEqual(preview.blockers, []);
});

test("blocks an enabled MOD without its pinned prepared content", () => {
  const preview = buildModApplyPreview({ mods: [mod({ preparedDigest: null })],
    runtimeStatus: "stopped", cavesEnabled: false });
  assert.equal(preview.changes.length, 1);
  assert.match(preview.blockers[0] ?? "", /固定 MOD 内容/);
  assert.equal(preview.canApply, false);
});

test("rejects unknown runtime state without requiring a pre-existing backup", () => {
  const preview = buildModApplyPreview({ mods: [mod({ deployedEnabled: null })],
    runtimeStatus: "unknown", cavesEnabled: false });
  assert.equal(preview.canApply, false);
  assert.match(preview.blockers[0] ?? "", /无法确认专服运行状态/);
  assert.deepEqual(preview.warnings, []);
});

test("does not report changes for a matching deployed snapshot", () => {
  const preview = buildModApplyPreview({ mods: [mod({ deployedEnabled: true,
    desiredConfiguration: { nested: { a: 1, b: 2 } }, deployedConfiguration: { nested: { b: 2, a: 1 } },
    deployedArtifactDigest: "a".repeat(64) })], runtimeStatus: "running",
    cavesEnabled: false });
  assert.deepEqual(preview.changes, []);
  assert.equal(preview.canApply, false);
});

test("merges server blockers with a safe fallback label", () => {
  const preview = buildModApplyPreview({ mods: [mod({ deployedEnabled: null })],
    runtimeStatus: "running", cavesEnabled: false });
  const blockers = mergeModApplyBlockers(preview, ["MOD_ARTIFACT_REQUIRED", "MOD_UNKNOWN_REASON"]);

  assert.deepEqual(blockers, ["启用的 MOD 缺少主机库内容版本", "服务端阻断发布：MOD_UNKNOWN_REASON"]);
});
