import assert from "node:assert/strict";
import test from "node:test";
import type { BackupSummary } from "../api/instances.ts";
import type { InstanceModSummary } from "../types/mods.ts";
import { buildModApplyPreview, mergeModApplyBlockers } from "./modApplyPreview.ts";

const backup: BackupSummary = {
  schemaVersion: 1, backupId: "11111111-1111-4111-8111-111111111111", hostId: "22222222-2222-4222-8222-222222222222",
  instanceId: "33333333-3333-4333-8333-333333333333", createdAt: "2026-10-22T10:00:00Z", reason: "protect",
  captureMode: "cold", captureSemantics: "stoppedBeforeRead", runtimeStatusAtCapture: "stopped", sizeBytes: 1,
  checksum: "a".repeat(64), verification: "verified", restorability: "restorable", dstBuild: "756039",
  dstReleaseId: null, modsReleaseId: null, configRevision: 2, containsSecrets: false,
};

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
    backups: [backup], runtimeStatus: "running", cavesEnabled: true, restoreAcceptancePassed: false,
  });

  assert.deepEqual(preview.changes.map(change => [change.workshopId, change.action]), [
    ["add", "add"], ["config", "configure"], ["disable", "disable"], ["remove", "remove"],
  ]);
  assert.deepEqual(preview.shards, ["master", "caves"]);
  assert.equal(preview.protectionBackup?.backupId, backup.backupId);
  assert.equal(preview.canApply, false);
  assert.match(preview.blockers[0] ?? "", /尚未通过/);
});

test("blocks an enabled MOD without its pinned prepared content", () => {
  const preview = buildModApplyPreview({ mods: [mod({ preparedDigest: null })], backups: [],
    runtimeStatus: "stopped", cavesEnabled: false, restoreAcceptancePassed: true });
  assert.equal(preview.changes.length, 1);
  assert.match(preview.blockers[0] ?? "", /固定 MOD 内容/);
});

test("requires a compatible verified cold backup and rejects unknown runtime state", () => {
  const bad = { ...backup, restorability: "missingArtifacts" as const };
  const preview = buildModApplyPreview({ mods: [mod({ deployedEnabled: null })], backups: [bad],
    runtimeStatus: "unknown", cavesEnabled: false, restoreAcceptancePassed: true });
  assert.equal(preview.protectionBackup, null);
  assert.equal(preview.canApply, false);
  assert.match(preview.blockers[0] ?? "", /无法确认专服运行状态/);
  assert.match(preview.warnings[0] ?? "", /冷保护备份/);
});

test("does not report changes for a matching deployed snapshot", () => {
  const preview = buildModApplyPreview({ mods: [mod({ deployedEnabled: true,
    desiredConfiguration: { nested: { a: 1, b: 2 } }, deployedConfiguration: { nested: { b: 2, a: 1 } },
    deployedArtifactDigest: "a".repeat(64) })], backups: [backup], runtimeStatus: "running",
    cavesEnabled: false, restoreAcceptancePassed: true });
  assert.deepEqual(preview.changes, []);
  assert.equal(preview.canApply, false);
});

test("merges local and authoritative restore acceptance blockers into one message", () => {
  const preview = buildModApplyPreview({ mods: [mod({ deployedEnabled: null })], backups: [backup],
    runtimeStatus: "running", cavesEnabled: false, restoreAcceptancePassed: false });
  const blockers = mergeModApplyBlockers(preview, ["MOD_RESTORE_ACCEPTANCE_REQUIRED"]);

  assert.deepEqual(blockers, ["MOD 发布恢复验收尚未通过此 Linux 主机验收"]);
  assert.equal(preview.canApply, false);
});
