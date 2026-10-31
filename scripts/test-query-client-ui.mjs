/** Query cache reuses Release-style data across mounts and revalidates in background. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {createRenderer,defineComponent,nextTick,ref} from 'vue';
let vite,useQuery,queryClient,useTasks;
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null}});
  ({useQuery,queryClient}=await vite.ssrLoadModule('/src/lib/queryClient.ts'));
  ({useTasks}=await vite.ssrLoadModule('/src/composables/useTasks.ts'));
});
after(async()=>{await vite?.close();});
const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createElement:()=>({}),createText:()=>({}),setText(){},setElementText(){},patchProp(){}});
const flush=async()=>{await nextTick();await new Promise(resolve=>setImmediate(resolve));await nextTick();};
function deferred(){let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};}
function mount(key,fetcher){let state;const app=renderer.createApp(defineComponent({setup(){state=useQuery(key,fetcher);return()=>null;}}));app.mount({});return {state,unmount:()=>app.unmount()};}
test('cached data is shown immediately on remount while revalidation runs',async()=>{
  queryClient.clear();
  const key=ref('releases:server-a:host-a:3');let calls=0;const pending=deferred();
  const first=mount(key,async()=>{calls++;return {value:1};});
  await flush();
  assert.equal(first.state.data.value.value,1);
  first.unmount();
  const second=mount(key,()=>{calls++;return pending.promise;});
  await flush();
  assert.equal(second.state.data.value.value,1);
  assert.equal(second.state.loading.value,true);
  pending.resolve({value:2});
  await flush();
  assert.equal(second.state.data.value.value,2);
  assert.equal(calls,2);
  second.unmount();
});
test('terminal tasks invalidate instance, overview and release caches',async()=>{
  queryClient.clear();
  const serverId='server-a',hostId='11111111-1111-4111-8111-111111111111',instanceId='22222222-2222-4222-8222-222222222222';
  queryClient.set(`instances:${serverId}:3:live`,{value:1});
  queryClient.set(`overview:${serverId}:${hostId}:${instanceId}:3:live`,{value:1});
  queryClient.set(`releases:${serverId}:${hostId}:3`,{value:1});
  const tasks=useTasks();
  tasks.receive({schemaVersion:1,taskId:'task-invalidate',operationId:'op-invalidate',target:{serverId,hostId,instanceId},operation:'modApply',status:'succeeded',stage:'finalize',lastCompletedStage:null,progress:{kind:'indeterminate'},sequence:1,createdAt:'2026-10-31T00:00:00Z',startedAt:null,finishedAt:'2026-10-31T00:01:00Z',cancelAllowed:false,observation:'live',result:null,error:null,journal:[]});
  assert.equal(queryClient.get(`instances:${serverId}:3:live`),undefined);
  assert.equal(queryClient.get(`overview:${serverId}:${hostId}:${instanceId}:3:live`),undefined);
  assert.equal(queryClient.get(`releases:${serverId}:${hostId}:3`),undefined);
});

test('invalidate drops the cached entry and a null key resets state',async()=>{
  queryClient.clear();
  const key=ref('releases:server-a:host-a:3');
  const view=mount(key,async()=>({value:1}));
  await flush();
  assert.ok(queryClient.get(key.value));
  queryClient.invalidate('releases:');
  assert.equal(queryClient.get(key.value),undefined);
  key.value=null;
  await flush();
  assert.equal(view.state.data.value,null);
  view.unmount();
});
