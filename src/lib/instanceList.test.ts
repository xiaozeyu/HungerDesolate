import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { validateInstanceList, instanceState, canInspectInstance } from "./instanceList.ts";
import type { InstanceListResponse } from "../api/instances.ts";
import type { InstanceSummary } from "../types/contracts.ts";
const hostId="11111111-1111-4111-8111-111111111111";
const ticket={serverId:"server",recordVersion:2,epoch:1,sequence:1,requestId:"request"};
const item:InstanceSummary={hostId,instanceId:"22222222-2222-4222-8222-222222222222",displayName:"同名专服",lifecycle:"ready",runtimeStatus:"unknown",players:null,maxPlayers:null,gameMode:"survival",cavesEnabled:true,installedBuild:null,lastSyncedAt:null,activeTaskId:null,manifestAvailable:true};
const report:InstanceListResponse={schemaVersion:1,requestId:"request",serverId:"server",recordVersion:2,hostId,registryRevision:3,capturedAtUnixSeconds:1700000000,instances:[item]};
describe("instance list boundary",()=>{
  it("checks server, host, connection version, request and unique instance identities",()=>{
    assert.ok(validateInstanceList(ticket,hostId,report));assert.ok(validateInstanceList(ticket,null,report));
    for(const response of [{...report,serverId:"other"},{...report,recordVersion:1},{...report,requestId:"old"},{...report,hostId:"33333333-3333-4333-8333-333333333333"},{...report,instances:[item,item]},{...report,instances:[{...item,instanceId:"../default"}]}])assert.equal(validateInstanceList(ticket,hostId,response),false);
  });
  it("supports distinct IDs with the same name",()=>{
    assert.ok(validateInstanceList(ticket,hostId,{...report,instances:[item,{...item,instanceId:"33333333-3333-4333-8333-333333333333"}]}));
  });
  it("unknown is not stopped and lifecycle errors remain visible",()=>{
    assert.equal(instanceState(item).label,"未知");
    assert.equal(instanceState({...item,lifecycle:"createFailed"}).label,"创建失败");
    assert.equal(instanceState({...item,runtimeStatus:"degraded"}).label,"部分异常");
  });
  it("only permits inspection with a usable manifest",()=>{
    assert.ok(canInspectInstance(item));assert.equal(canInspectInstance({...item,manifestAvailable:false}),false);
    assert.equal(canInspectInstance({...item,lifecycle:"retired"}),false);assert.equal(canInspectInstance({...item,lifecycle:"deleting"}),false);
  });
  it("rejects invalid revision and player numbers while accepting unknowns",()=>{
    assert.ok(validateInstanceList(ticket,hostId,report));
    assert.equal(validateInstanceList(ticket,hostId,{...report,registryRevision:Number.MAX_SAFE_INTEGER+1}),false);
    assert.equal(validateInstanceList(ticket,hostId,{...report,instances:[{...item,players:8,maxPlayers:6}]}),false);
  });
});
