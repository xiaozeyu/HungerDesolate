import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { isCurrentQuery, type QueryTicket } from "./queryGuard.ts";
const target = { serverId: "server-a", hostId: "host-a", instanceId: "instance-a" };
const ticket = (epoch: number, seq: number): QueryTicket => ({ target, contextEpoch: epoch, requestSeq: seq, requestId: `${epoch}-${seq}` });
describe("query guard used by instance queries", () => {
    it("accepts the current request", () => assert.equal(isCurrentQuery(ticket(3, 2), ticket(3, 2), 3), true));
    it("rejects A → B → A late results", () => assert.equal(isCurrentQuery(ticket(1, 1), ticket(3, 1), 3), false));
    it("rejects stale response and finally from a refresh", () => assert.equal(isCurrentQuery(ticket(3, 1), ticket(3, 2), 3), false));
    it("rejects another server or instance", () => assert.equal(isCurrentQuery({ ...ticket(3, 2), target: { ...target, instanceId: "b" } }, ticket(3, 2), 3), false));
    it("rejects an invalidated request even if its ticket survived", () => assert.equal(isCurrentQuery(ticket(1, 1), ticket(1, 1), 3), false));
    it("rejects disposed queries", () => assert.equal(isCurrentQuery(ticket(3, 2), null, 3), false));
});
