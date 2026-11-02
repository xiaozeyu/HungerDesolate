/** Exercise the production list refresh loop with mocked IPC and a local clock. */
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
  hostId:serverA.hostId,instanceId:'33333333-3333-4333-8333-333333333333',displayName:'测试专服',lifecycle:'ready',runtimeStatus:'running',players,maxPlayers:6,gameMode:'survival',cavesEnabled:true,manifestAvailable:true,
}]};}
function deferred(){let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};}
function mount(previewValue=false){
  queryClient.clear();api.commits.length=0;
  const saved={setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout,document:globalThis.document};
  const timers=new Map(),listeners=new Map();let id=0,state;
  const doc={visibilityState:'visible',addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
  globalThis.document=doc;
  globalThis.setTimeout=(callback,delay)=>{timers.set(++id,{callback,delay});return id;};
  globalThis.clearTimeout=timer=>timers.delete(timer);
  const server=ref({...serverA}),preview=ref(previewValue);
  const app=renderer.createApp(defineComponent({setup(){state=useInstanceList(server,preview);return()=>null;}}));
  app.mount({});
  return {state,server,preview,timers,listeners,
    async tick(){assert.equal(timers.size,1);const [timer,entry]=[...timers][0];timers.delete(timer);entry.callback();await flush();},
    async visibility(value){doc.visibilityState=value;listeners.get('visibilitychange')?.();await flush();},
    unmount(){app.unmount();Object.assign(globalThis,saved);},
  };
}

test('the list replaces unknown player counts on its next scheduled read',async()=>{
  let calls=0;api.handler=async request=>response(request,++calls===1?null:3);
  const view=mount();
  try{
    await flush();assert.equal(view.state.data.value.instances[0].players,null);
    assert.equal([...view.timers.values()][0].delay,15000);
    await view.tick();assert.equal(view.state.data.value.instances[0].players,3);assert.equal(calls,2);
  }finally{view.unmount();}
});

test('polling skips an in-flight read and stops when the page is hidden or unmounted',async()=>{
  const pending=deferred();let calls=0,request;
  api.handler=input=>{calls++;request=input;return pending.promise;};
  const view=mount();
  try{
    await flush();await view.tick();assert.equal(calls,1);
    await view.visibility('hidden');assert.equal(view.timers.size,0);
    pending.resolve(response(request,0));await flush();assert.equal(view.state.data.value.instances[0].players,0);
    api.handler=async request=>{calls++;return response(request,2);};
    await view.visibility('visible');assert.equal(calls,2);assert.equal(view.state.data.value.instances[0].players,2);
  }finally{view.unmount();}
  assert.equal(view.timers.size,0);assert.equal(view.listeners.size,0);
});

test('failed reads back off to 60 seconds and successful reads reset the interval',async()=>{
  api.handler=async()=>{throw new Error('offline');};const view=mount();
  try{
    await flush();await view.tick();assert.equal([...view.timers.values()][0].delay,30000);
    await view.tick();assert.equal([...view.timers.values()][0].delay,60000);
    await view.tick();assert.equal([...view.timers.values()][0].delay,60000);
    api.handler=async request=>response(request,1);
    await view.tick();assert.equal([...view.timers.values()][0].delay,15000);assert.equal(view.state.error.value,null);
  }finally{view.unmount();}
});

test('an older poll cannot commit to the workspace after a newer manual read',async()=>{
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

test('preview mode does not schedule remote polling',async()=>{
  let calls=0;api.handler=async request=>{calls++;return response(request);};const view=mount(true);
  try{await flush();assert.equal(view.timers.size,0);assert.equal(calls,0);}finally{view.unmount();}
});
