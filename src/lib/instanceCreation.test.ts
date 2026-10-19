import {describe,it} from 'node:test';
import assert from 'node:assert/strict';
import {matchesCreationPreview,validateCreationInput,creationBlockerLabel} from './instanceCreation.ts';
import type {InstanceCreateInput,InstanceCreatePreview,InstanceCreatePreviewRequest} from '../api/instances.ts';
const hostId='11111111-1111-4111-8111-111111111111';
const candidate='22222222-2222-4222-8222-222222222222';
const input:InstanceCreateInput={displayName:'生存一服',autostart:true,startAfterCreate:true,portPolicy:'auto',cavesEnabled:true};
const request:InstanceCreatePreviewRequest={schemaVersion:1,requestId:'request-a',target:{serverId:'server-a',hostId},expectedRecordVersion:3,expectedRegistryRevision:4,input};
function result():InstanceCreatePreview {
  return {schemaVersion:1,requestId:request.requestId,target:request.target,recordVersion:3,registryRevision:4,previewId:'33333333-3333-4333-8333-333333333333',capturedAtUnixSeconds:1700000000,expiresAtUnixSeconds:1700000600,input:{...input},proposedInstanceId:candidate,composeProject:'fh_'+candidate.replaceAll('-',''),storagePath:`/var/lib/famine-hub/instances/${candidate}`,ports:[
    {shard:'master',purpose:'game',hostPort:11000,containerPort:11000},
    {shard:'master',purpose:'steamAuth',hostPort:8766,containerPort:8766},
    {shard:'master',purpose:'steamMaster',hostPort:27016,containerPort:27016},
    {shard:'caves',purpose:'game',hostPort:11001,containerPort:11001},
    {shard:'caves',purpose:'steamAuth',hostPort:8767,containerPort:8767},
    {shard:'caves',purpose:'steamMaster',hostPort:27017,containerPort:27017},
  ].map(port=>({...port,protocol:'udp',bindAddress:'0.0.0.0'})) as InstanceCreatePreview['ports'],blockers:[],availableBytes:32*1024**3,dockerVersion:'29.0.0',composeVersion:'2.40.0',allowed:false,portReservation:'unreserved'};
}
describe('creation preview boundary',()=>{
  it('validates names, integer player counts and only supported inputs',()=>{
    assert.equal(validateCreationInput(input),null);
    for (const change of [{displayName:' '},{displayName:'a'.repeat(65)},{displayName:'a\nb'},{cavesEnabled:'yes'}]) assert.notEqual(validateCreationInput({...input,...change} as InstanceCreateInput),null);
  });
  it('binds the preview to the current host, revision, request and exact input',()=>{
    const base=result();assert.equal(matchesCreationPreview(request,base),true);
    for (const change of [{requestId:'old'},{target:{...base.target,hostId:candidate}},{recordVersion:2},{registryRevision:3},{input:{...input,displayName:'other'}},{storagePath:'/tmp/other'},{ports:base.ports.slice(0,3)}]) assert.equal(matchesCreationPreview(request,{...base,...change}),false);
  });
  it('does not accept executable success or unsafe port plans',()=>{
    const base=result();
    assert.equal(matchesCreationPreview(request,{...base,allowed:true} as unknown as InstanceCreatePreview),false);
    assert.equal(matchesCreationPreview(request,{...base,blockers:[]}),true);
    for (const change of [{containerPort:11002},{hostPort:0,containerPort:0},{bindAddress:'127.0.0.1'},{protocol:'tcp'}]) {
      const broken=structuredClone(base);Object.assign(broken.ports[0]!,change);assert.equal(matchesCreationPreview(request,broken),false);
    }
    const duplicate=structuredClone(base);duplicate.ports[3]={...duplicate.ports[0]!};assert.equal(matchesCreationPreview(request,duplicate),false);
    const outside=structuredClone(base);outside.ports[0]!.hostPort=10000;outside.ports[0]!.containerPort=10000;
    assert.equal(matchesCreationPreview(request,outside),false);
  });
  it('maps blockers without echoing arbitrary remote strings',()=>{
    assert.equal(creationBlockerLabel('PORT_PLAN_EXHAUSTED'),'可用端口范围不足');
    assert.equal(creationBlockerLabel('CREATE_RELEASE_REQUIRED'),'没有已验证的 DST release，暂不可创建');
    assert.equal(creationBlockerLabel('CREATE_DOCKER_RUNTIME_REQUIRED'),'Docker runtime 和容器验证尚未完成');
    assert.equal(creationBlockerLabel('unknown secret'),'创建条件未满足');
    assert.equal(creationBlockerLabel('__proto__'),'创建条件未满足');
  });
});
