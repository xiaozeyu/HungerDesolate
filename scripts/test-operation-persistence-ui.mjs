/** Running foreground operations become 'result pending verification' after restart. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
let vite,operationStorage,useOperations;
const memory=new Map();
globalThis.localStorage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key),clear:()=>memory.clear(),key:index=>[...memory.keys()][index]??null,get length(){return memory.size;}};
const target={serverId:'server-a',hostId:'11111111-1111-4111-8111-111111111111',instanceId:'22222222-2222-4222-8222-222222222222'};
function entry(id,status='running'){return {id,kind:'worldApply',target,targetKey:`${target.serverId}:${target.hostId}:${target.instanceId}`,title:'应用世界配置',stage:'检查并应用',status,startedAt:1,finishedAt:null,error:null};}
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null}});
  operationStorage=await vite.ssrLoadModule('/src/lib/operationStorage.ts');
  operationStorage.saveOperations([entry('op-1')]);
  ({useOperations}=await vite.ssrLoadModule('/src/composables/useOperations.ts'));
});
after(async()=>{await vite?.close();globalThis.localStorage=undefined;});
test('a running operation is marked interrupted after reload',()=>{
  const loaded=operationStorage.loadOperations();
  assert.equal(loaded[0].status,'interrupted');
  assert.equal(loaded[0].error.code,'APP_RESTARTED');
  assert.match(loaded[0].stage,/待核实/);
});
test('operation store exposes the interrupted record and persists dismissal',()=>{
  const operations=useOperations();
  const interrupted=operations.all.value.find(item=>item.id==='op-1');
  assert.equal(interrupted?.status,'interrupted');
  operations.dismiss('op-1');
  assert.equal(operations.all.value.some(item=>item.id==='op-1'),false);
  const saved=JSON.parse(memory.get('hunger-desolate.operations.v1'));
  assert.equal(saved.some(item=>item.id==='op-1'),false);
});
test('operation storage keeps at most fifty records',()=>{
  const many=Array.from({length:55},(_,index)=>entry(`op-${index}`,'succeeded'));
  operationStorage.saveOperations(many);
  const loaded=operationStorage.loadOperations();
  assert.equal(loaded.length,50);
  assert.equal(loaded[0].id,'op-5');
});
