import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { canInitializeHost, formatBytes, matchesEnvironment } from "./environmentView.ts";
import type { HostEnvironment } from "../types/contracts.ts";
const ticket = {serverId:"server",recordVersion:2,epoch:3,sequence:4,requestId:"request"};
const report = {schemaVersion:1,requestId:"request",serverId:"server",recordVersion:2,hostId:"host",environmentCheckId:"check",checkedAtUnixSeconds:1700000000,checks:[]} as unknown as HostEnvironment;
describe("host environment display boundary", () => {
  it("keeps missing resources unknown instead of displaying zero", () => {
    assert.equal(formatBytes(null),"未知"); assert.equal(formatBytes(undefined),"未知");
    assert.equal(formatBytes(0),"0 B"); assert.equal(formatBytes(1024**3),"1 GiB");
    assert.equal(formatBytes(-1),"未知"); assert.equal(formatBytes(Number.MAX_SAFE_INTEGER+1),"未知");
  });
  it("matches the connection before accepting a host binding", () => {
    assert.ok(matchesEnvironment(ticket,null,report)); assert.ok(matchesEnvironment(ticket,"host",report));
    assert.equal(matchesEnvironment(ticket,"other-host",report),false);
    assert.equal(matchesEnvironment(ticket,"host",{...report,serverId:"other"}),false);
    assert.equal(matchesEnvironment(ticket,"host",{...report,requestId:"older"}),false);
    assert.equal(matchesEnvironment(ticket,"host",{...report,recordVersion:1}),false);
    assert.equal(matchesEnvironment(ticket,"host",{...report,checkedAtUnixSeconds:NaN}),false);
  });
  it("gates initialization on management path permissions and runtime, not Docker readiness", () => {
    const ready={...report,hostId:null,checks:[]} as unknown as HostEnvironment;
    assert.equal(canInitializeHost(ready), true);
    assert.equal(canInitializeHost({...ready,hostId:"bound-host"}), false);
    assert.equal(canInitializeHost({...ready,checks:[{code:"MANAGEMENT_PATH_PERMISSION",level:"blocker"}]} as unknown as HostEnvironment),false);
    assert.equal(canInitializeHost({...ready,checks:[{code:"MANAGEMENT_ROOT_INVALID",level:"blocker"}]} as unknown as HostEnvironment),false);
    assert.equal(canInitializeHost({...ready,checks:[{code:"REQUIRED_COMMAND_MISSING",label:"python3",level:"blocker"}]} as unknown as HostEnvironment),false);
    assert.equal(canInitializeHost({...ready,checks:[{code:"DOCKER_NOT_INSTALLED",level:"blocker"}]} as unknown as HostEnvironment),true);
  });
});
