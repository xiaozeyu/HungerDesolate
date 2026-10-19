import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { currentConnection, matchesConnection, type ConnectionTicket } from "./connectionGuard.ts";
const ticket: ConnectionTicket = {requestId: "request", serverId: "a", recordVersion: 4, epoch: 3, sequence: 2};
describe("SSH response ownership", () => {
  it("accepts only the current connection and request", () => {
    assert.ok(currentConnection(ticket,ticket));
    for (const next of [{...ticket, serverId: "b"},{...ticket, recordVersion: 5},{...ticket, epoch: 5},{...ticket, sequence: 3}]) assert.equal(currentConnection(ticket,next),false);
  });
  it("distinguishes query snapshots from trust/credential writes", () => {
    assert.ok(matchesConnection(ticket,{schemaVersion: 1, requestId: "request", serverId: "a", recordVersion: 4}));
    assert.ok(matchesConnection(ticket,{schemaVersion: 1, requestId: "request", serverId: "a", recordVersion: 5},true));
    assert.equal(matchesConnection(ticket,{schemaVersion: 1, requestId: "request", serverId: "b", recordVersion: 5},true),false);
    assert.equal(matchesConnection(ticket,{schemaVersion: 1, requestId: "request", serverId: "a", recordVersion: 8},true),false);
    assert.equal(matchesConnection(ticket,{schemaVersion: 2, requestId: "request", serverId: "a", recordVersion: 4}),false);
    assert.equal(matchesConnection(ticket,{schemaVersion: 1, requestId: "older", serverId: "a", recordVersion: 4}),false);
  });
});
