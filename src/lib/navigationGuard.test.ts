import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { isUuid, sameTarget, sameTaskTarget } from "./navigationGuard.ts";
describe("selection target", () => {
    it("does not reuse another connection selection", () => assert.equal(sameTarget({ serverId: "a", hostId: "h", instanceId: "i" }, { serverId: "b", hostId: "h", instanceId: "i" }), false));
    it("matches host-scoped tasks separately from instance tasks", () => {
        assert.equal(sameTaskTarget({ serverId: "s", hostId: "h" }, { serverId: "s", hostId: "h" }), true);
        assert.equal(sameTaskTarget({ serverId: "s", hostId: "h" }, { serverId: "s", hostId: "h", instanceId: "i" }), false);
    });
    it("rejects unsafe path identifiers", () => assert.equal(isUuid("../default"), false));
    it("validates an immutable UUID", () => assert.equal(isUuid("22222222-2222-4222-8222-222222222222"), true));
});
