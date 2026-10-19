import {describe, it} from "node:test";
import assert from "node:assert/strict";
import {creationTaskErrorLabel, creationTaskStageLabel, isCreationTask} from "./instanceCreationTask.ts";
import type {TaskSnapshot} from "../types/contracts.ts";

const base: TaskSnapshot = {
  schemaVersion: 1, taskId: "11111111-1111-4111-8111-111111111111", operationId: "11111111-1111-4111-8111-111111111111",
  target: {serverId: "server", hostId: "host", instanceId: "22222222-2222-4222-8222-222222222222"}, operation: "instanceCreate",
  status: "failed", stage: "publish", lastCompletedStage: "stagingVerify", progress: {kind: "indeterminate"}, sequence: 3,
  createdAt: "2026-10-19T00:00:00Z", startedAt: "2026-10-19T00:00:00Z", finishedAt: "2026-10-19T00:01:00Z",
  cancelAllowed: false, observation: "live", result: null, error: null,
};

describe("instance creation task display", () => {
  it("maps stable stages and error codes", () => {
    assert.equal(creationTaskStageLabel("prepareDirectories"), "准备实例目录");
    assert.equal(creationTaskStageLabel("unknown"), "创建任务");
    assert.equal(creationTaskErrorLabel("CREATE_COMPOSE_CONFIG_FAILED"), "Compose 配置校验失败，已保留 staging 现场");
  });
  it("recognizes only instance creation operations", () => {
    assert.equal(isCreationTask(base), true);
    assert.equal(isCreationTask({...base, operation: "dockerInstall"}), false);
  });
});
