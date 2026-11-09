/** Exercise the production instance-list read path with mocked IPC. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {createRenderer,defineComponent,nextTick,ref} from 'vue';
let vite,useInstanceList,queryClient,api;
const virtual='\0instance-list-test-api';
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null},plugins:[{
    name:'instance-list-ipc',enforce:'pre',
    resolveId(id,importer){
      if(id==='virtual:instance-list-test')return virtual;
      if(importer?.endsWith('/useInstanceList.ts') && /\/api\/instances$|\.\/useWorkspace$/.test(id))return virtual;
    },
    configureServer(server){server.ws.listen=()=>{};},
    load(id){if(id===virtual)return `
      export const state={handler:null,commits:[]};
      export const listInstances=request=>state.handler(request);
      export const getInstanceCreateStatus=()=>Promise.resolve({task:null});
      export const useWorkspace=()=>({commitInstances:value=>state.commits.push(value),invalidateSelection(){}});
    `;},
  }]});
  api=(await vite.ssrLoadModule('virtual:instance-list-test')).state;
  ({useInstanceList}=await vite.ssrLoadModule('/src/composables/useInstanceList.ts'));
  ({queryClient}=await vite.ssrLoadModule('/src/lib/queryClient.ts'));
});
after(async()=>{await vite?.close();});
const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createElement:()=>({}),createText:()=>({}),setText(){},setElementText(){},patchProp(){}});
const flush=async()=>{await nextTick();await new Promise(resolve=>setImmediate(resolve));await nextTick();};
const serverA={serverId:'11111111-1111-4111-8111-111111111111',hostId:'22222222-2222-4222-8222-222222222222',recordVersion:1};
function response(request,players=null){return {schemaVersion:1,requestId:request.requestId,serverId:request.serverId,recordVersion:request.expectedRecordVersion,hostId:serverA.hostId,registryRevision:1,capturedAtUnixSeconds:1,instances:[{
  hostId:serverA.hostId,instanceId:'33333333-3333-4333-8333-333333333333',displayName:'测试专服',lifecycle:'ready',runtimeStatus:'running',players,maxPlayers:6,gameMode:'survival',cavesEnabled:true,manifestAvailable:true,configRevision:1,
}]};}
function deferred(){let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};}
function mount(previewValue=false){
  queryClient.clear();api.commits.length=0;
  const saved={setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
  const timers=new Map();let id=0,state;
  globalThis.setTimeout=(callback,delay)=>{timers.set(++id,{callback,delay});return id;};
  globalThis.clearTimeout=timer=>timers.delete(timer);
  const server=ref({...serverA}),preview=ref(previewValue);
  const app=renderer.createApp(defineComponent({setup(){state=useInstanceList(server,preview);return()=>null;}}));
  app.mount({});
  return {state,server,preview,timers,
    unmount(){app.unmount();Object.assign(globalThis,saved);},
  };
}

test('the list reads once and does not schedule background polling',async()=>{
  let calls=0;api.handler=async request=>{calls++;return response(request,3);};
  const view=mount();
  try{
    await flush();
    assert.equal(calls,1);
    assert.equal(view.state.data.value.instances[0].players,3);
    assert.equal(view.timers.size,0);
  }finally{view.unmount();}
  assert.equal(view.timers.size,0);
});

test('manual refresh reads the latest list without starting a loop',async()=>{
  let calls=0;api.handler=async request=>response(request,++calls);
  const view=mount();
  try{
    await flush();assert.equal(view.state.data.value.instances[0].players,1);
    await view.state.refresh();await flush();
    assert.equal(view.state.data.value.instances[0].players,2);
    assert.equal(calls,2);assert.equal(view.timers.size,0);
  }finally{view.unmount();}
});

test('an older read cannot commit after a newer manual read',async()=>{
  const pending=deferred();let oldRequest;
  api.handler=request=>{oldRequest=request;return pending.promise;};const view=mount();
  try{
    await flush();api.handler=async request=>response(request,4);
    await view.state.refresh();await flush();
    pending.resolve(response(oldRequest,1));await flush();
    assert.equal(view.state.data.value.instances[0].players,4);
    assert.deepEqual(api.commits.map(value=>value.instances[0].players),[4]);
  }finally{view.unmount();}
});

test('A to B to A navigation rejects the original A response and its workspace commit',async()=>{
  const pending=deferred();let oldRequest;
  api.handler=request=>{oldRequest=request;return pending.promise;};const view=mount();
  try{
    await flush();api.handler=async request=>response(request,2);
    view.server.value={...serverA,serverId:'44444444-4444-4444-8444-444444444444'};await flush();
    view.server.value={...serverA};await flush();
    await view.state.refresh();await flush();api.commits.length=0;
    pending.resolve(response(oldRequest,5));await flush();
    assert.equal(view.state.data.value.instances[0].players,2);assert.equal(api.commits.length,0);
  }finally{view.unmount();}
});

test('preview mode does not call the remote list',async()=>{
  let calls=0;api.handler=async request=>{calls++;return response(request);};const view=mount(true);
  try{await flush();assert.equal(calls,0);assert.equal(view.timers.size,0);}finally{view.unmount();}
});
