/** Global operation store: long work survives page-level ownership and reports stages. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
let vite,useOperations,operationTargetKey;
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null}});
  ({useOperations}=await vite.ssrLoadModule('/src/composables/useOperations.ts'));
  ({operationTargetKey}=await vite.ssrLoadModule('/src/lib/operationView.ts'));
});
after(async()=>{await vite?.close();});
const target={serverId:'server-a',hostId:'11111111-1111-4111-8111-111111111111',instanceId:'22222222-2222-4222-8222-222222222222'};
const key=()=>operationTargetKey(target);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
test('operation store keeps running state across consumers and publishes stage updates',async()=>{
  const first=useOperations(),second=useOperations(),pending=deferred();
  const outcome=first.run({kind:'start',target,title:'启动专服',stage:'提交运行操作',work:async setStage=>{
    setStage('验证运行状态');
    await pending.promise;
    return 42;
  }});
  assert.equal(first.running.value.length,1);
  assert.equal(first.runningFor(key())?.stage,'验证运行状态');
  assert.equal(second.runningFor(key())?.kind,'start');
  pending.resolve();
  const result=await outcome;
  assert.equal(result.result,42);
  assert.equal(result.error,null);
  assert.equal(first.running.value.length,0);
  assert.equal(first.all.value[0]?.status,'succeeded');
});
test('operation failures stay queryable and cannot be dismissed while running',async()=>{
  const operations=useOperations(),pending=deferred();
  const outcome=operations.run({kind:'worldApply',target,title:'应用世界配置',stage:'检查并应用',work:async()=>{
    await pending.promise;
    throw {code:'WORLD_APPLY_BLOCKED',message:'世界配置应用条件未满足',recoverySteps:[],field:null};
  }});
  const entry=operations.runningFor(key(),['worldApply']);
  assert.ok(entry);
  operations.dismiss(entry.id);
  assert.equal(operations.runningFor(key(),['worldApply'])?.id,entry.id);
  pending.resolve();
  const result=await outcome;
  assert.equal(result.result,null);
  assert.equal(result.error?.code,'WORLD_APPLY_BLOCKED');
  assert.equal(operations.running.value.length,0);
  assert.equal(operations.latestFor(key(),['worldApply'])?.status,'failed');
  operations.dismiss(entry.id);
  assert.equal(operations.latestFor(key(),['worldApply']),null);
});
