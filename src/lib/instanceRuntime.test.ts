import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { firewallUdpPorts, hasReadyShardListeners } from "./instanceRuntime.ts";
import type { InstanceRuntimeSnapshot } from "../types/contracts.ts";
import type { InstanceRuntimeControlResponse } from "../api/instances.ts";

describe("DST shard readiness", () => {
  it("accepts real server evidence without requiring unused fixed authentication ports", () => {
    assert.equal(hasReadyShardListeners({runningShards:["master","caves"],
      listeners:{master:[11000,27016],caves:[11001,27017]}}, true), true);
    assert.equal(hasReadyShardListeners({runningShards:["master"],
      listeners:{master:[11000,27016]}}, false), true);
  });
  it("rejects missing shards, duplicate ports and invalid port evidence", () => {
    const cases: Pick<InstanceRuntimeControlResponse,"runningShards"|"listeners">[] = [
      {runningShards:["master"],listeners:{master:[10888,11000,27016]}},
      {runningShards:["master","master"],listeners:{master:[11000,27016],caves:[11001,27017]}},
      {runningShards:["master","caves"],listeners:{master:[11000],caves:[11001,27017]}},
      {runningShards:["master","caves"],listeners:{master:[10888,11000,27016],caves:[11001,11001]}},
      {runningShards:["master","caves"],listeners:{master:[10888,11000,27016],caves:[0,27017]}},
      {runningShards:["master","caves"],listeners:{master:[10888,11000,27016],caves:[11001,70000]}},
    ];
    for (const value of cases) assert.equal(hasReadyShardListeners(value,true),false);
  });
});

describe("published UDP firewall ports", () => {
  it("lists unique published host ports and excludes internal shard links", () => {
    const snapshot = { ports: [
      { hostPort: 11000, published: true },
      { hostPort: 8766, published: true },
      { hostPort: 27016, published: true },
      { hostPort: 11000, published: true },
      { hostPort: 10888, published: false },
      { hostPort: null, published: false },
      { hostPort: 11001, published: null },
    ] } as InstanceRuntimeSnapshot;
    assert.deepEqual(firewallUdpPorts(snapshot), [8766, 11000, 27016]);
    assert.deepEqual(firewallUdpPorts(undefined), []);
  });
});
