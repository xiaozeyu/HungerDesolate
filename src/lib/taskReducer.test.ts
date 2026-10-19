import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { mergeTask } from "./taskReducer.ts";
import type { TaskSnapshot, TaskStatus } from "../types/contracts.ts";
const task = (sequence: number, status: TaskStatus): TaskSnapshot => ({
    schemaVersion: 1, taskId: "task-a", operationId: "operation-a", target: { serverId: "s", hostId: "h", instanceId: "a" },
    operation: "instanceUpdate", status, stage: "verify", lastCompletedStage: null, progress: { kind: "indeterminate" },
    sequence, createdAt: "2026-10-14T00:00:00Z", startedAt: null, finishedAt: null, cancelAllowed: false, observation: "live",
});
const hostTask = (sequence:number,status:TaskStatus):TaskSnapshot=>({...task(sequence,status),target:{serverId:"s",hostId:"h"},operation:"dockerInstall"});
describe("task event reducer", () => {
    it("ignores old events", () => assert.equal(mergeTask(task(2, "running"), task(1, "running")).sequence, 2));
    it("never rewrites a confirmed terminal state", () => assert.equal(mergeTask(task(2, "succeeded"), task(3, "running")).status, "succeeded"));
    it("accepts reconciliation after interruption", () => assert.equal(mergeTask(task(2, "interrupted"), task(3, "succeeded")).status, "succeeded"));
    it("rejects mutated task target", () => assert.equal(mergeTask(task(2, "running"), { ...task(3, "running"), target: { serverId: "s", hostId: "h", instanceId: "b" } }).sequence, 2));
    it("merges host-scoped installation tasks and rejects another host",()=>{
        assert.equal(mergeTask(hostTask(1,"queued"),hostTask(2,"running")).sequence,2);
        assert.equal(mergeTask(hostTask(1,"queued"),{...hostTask(2,"running"),target:{serverId:"s",hostId:"other"}}).sequence,1);
    });
});
