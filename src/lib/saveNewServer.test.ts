import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { saveNewServer, type NewServerProgress } from "./saveNewServer.ts";
import type { ServerSummary, SshConnectionState } from "../types/contracts.ts";
const server: ServerSummary = {serverId:"server",displayName:"主机",host:"localhost",sshPort:22,username:"test",authKind:"password",hostId:null,connectionState:"disconnected",recordVersion:1};
const credential = {kind:"password" as const,password:"fixture"};
function progress(): NewServerProgress {
  return {metadataRequest:{schemaVersion:1,requestId:"request",operationId:"metadata-op",serverId:null,expectedRecordVersion:null,input:{displayName:"主机",host:"localhost",sshPort:22,username:"test",authKind:"password"}},server:null,credentialOperationId:"credential-op"};
}
function success(requestId: string): SshConnectionState {
  return {schemaVersion:1,requestId,serverId:"server",recordVersion:2,fingerprint:null,algorithm:null,credentialConfigured:true,cleanupPending:0};
}
describe("new host and credential save workflow", () => {
  it("does not create a second host after a keyring failure", async () => {
    const pending = progress(); let creates = 0;
    const create = async () => {creates++;return server;};
    await assert.rejects(saveNewServer(pending,credential,create,async()=>{throw new Error("keyring denied");},()=>true));
    assert.equal(pending.server?.serverId,"server");
    const result = await saveNewServer(pending,credential,create,async request=>{
      assert.equal(request.serverId,"server"); assert.equal(request.operationId,"credential-op");
      assert.equal(request.expectedRecordVersion,1);return success(request.requestId);
    },()=>true);
    assert.equal(creates,1);assert.equal(result?.recordVersion,2);
    assert.equal(JSON.stringify(pending).includes("fixture"),false);
  });
  it("preserves the metadata operation ID when the first response is lost", async () => {
    const pending = progress();const seen:string[]=[];
    await assert.rejects(saveNewServer(pending,credential,async request=>{seen.push(request.operationId);throw new Error("lost");},async request=>success(request.requestId),()=>true));
    await saveNewServer(pending,credential,async request=>{seen.push(request.operationId);return server;},async request=>success(request.requestId),()=>true);
    assert.deepEqual(seen,["metadata-op","metadata-op"]);
  });
  it("stops before saving credentials if the form closed while metadata was being saved", async () => {
    let writes = 0;
    const result = await saveNewServer(progress(),credential,async()=>server,async request=>{writes++;return success(request.requestId);},()=>false);
    assert.equal(result,null);assert.equal(writes,0);
  });
  it("rejects a credential response belonging to another target", async () => {
    await assert.rejects(saveNewServer(progress(),credential,async()=>server,async request=>({...success(request.requestId),serverId:"other"}),()=>true));
  });
});
