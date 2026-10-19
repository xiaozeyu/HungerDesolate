/** Production creation composable tests, with IPC mocked and no host connections. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createRenderer,defineComponent,ref} from 'vue';
let vite,handlers,useInstanceCreation;
const virtual='\0creation-test-api';
test('creation form exposes the preview action without world settings', async()=>{
  const source=await readFile(resolve(process.cwd(),'src/components/InstanceCreateDialog.vue'),'utf8');
  assert.match(source,/id="instance-create-form" class="creation-form"/);
  assert.match(source,/form="instance-create-form"[^>]*>.*预览/s);
  assert.doesNotMatch(source,/worldSettings|instance-create-world-form|Klei Token/);
});
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null},plugins:[{
    name:'creation-ipc',enforce:'pre',resolveId(id,importer){if(id==='virtual:creation-api'||/\/api\/instances(?:\.ts)?$/.test(id)&&importer?.endsWith('useInstanceCreation.ts'))return virtual;},
    configureServer(server){server.ws.listen=()=>{};},load(id){if(id===virtual)return 'export const handlers={}; export const previewInstanceCreate=request=>handlers.preview(request);';},
  }]});
  handlers=(await vite.ssrLoadModule('virtual:creation-api')).handlers;
  ({useInstanceCreation}=await vite.ssrLoadModule('/src/composables/useInstanceCreation.ts'));
});
after(async()=>{await vite?.close();});
const hostId='11111111-1111-4111-8111-111111111111',candidate='22222222-2222-4222-8222-222222222222';
const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createElement:()=>({}),createText:()=>({}),setText(){},setElementText(){},patchProp(){}});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function response(request){return {schemaVersion:1,requestId:request.requestId,target:request.target,recordVersion:request.expectedRecordVersion,registryRevision:request.expectedRegistryRevision,previewId:'33333333-3333-4333-8333-333333333333',capturedAtUnixSeconds:1700000000,expiresAtUnixSeconds:1700000600,input:request.input,proposedInstanceId:candidate,composeProject:'fh_'+candidate.replaceAll('-',''),storagePath:`/var/lib/famine-hub/instances/${candidate}`,ports:[],blockers:['DEPLOYMENT_VALIDATION_REQUIRED'],availableBytes:32*1024**3,dockerVersion:'29.0.0',composeVersion:'2.40.0',allowed:false,portReservation:'unreserved'};}
function mount(){
  const server=ref({serverId:'server-a',hostId,recordVersion:3}),revision=ref(4),open=ref(false);let flow;
  const app=renderer.createApp(defineComponent({setup(){flow=useInstanceCreation(server,revision,open);return()=>null;}}));app.mount({});open.value=true;flow.fields.displayName='生存一服';
  return {flow,server,revision,open,unmount:()=>app.unmount()};
}
test('creation preview sends an immutable full target and input snapshot',async()=>{
  const view=mount();let captured;
  handlers.preview=async request=>{captured=request;return response(request);};
  try{await view.flow.preview();assert.equal(captured.target.hostId,hostId);assert.equal(captured.expectedRegistryRevision,4);assert.equal(view.flow.result.value.input.displayName,'生存一服');view.flow.fields.displayName='新名称';assert.equal(captured.input.displayName,'生存一服');assert.equal(view.flow.result.value,null);}finally{view.unmount();}
});
test('editing during a pending query discards its success, failure and finally',async()=>{
  const view=mount(),old=deferred(),latest=deferred();let calls=0,latestRequest;
  handlers.preview=request=>{calls++;if(calls===1)return old.promise;latestRequest=request;return latest.promise;};
  try{
    const first=view.flow.preview();view.flow.fields.displayName='新名称';const second=view.flow.preview();
    old.reject(new Error('old failure'));await first;
    assert.equal(view.flow.error.value,null);assert.equal(view.flow.loading.value,true);
    latest.resolve(response(latestRequest));await second;
    assert.equal(view.flow.result.value.input.displayName,'新名称');assert.equal(view.flow.loading.value,false);
  }finally{view.unmount();}
});
test('closing and reopening cannot accept a late response from the old dialog',async()=>{
  const view=mount(),pending=deferred();let request;handlers.preview=input=>{request=input;return pending.promise;};
  try{const query=view.flow.preview();view.open.value=false;view.open.value=true;pending.resolve(response(request));await query;assert.equal(view.flow.result.value,null);assert.equal(view.flow.loading.value,false);}finally{view.unmount();}
});
test('A to B to A and registry changes invalidate pending creation previews',async()=>{
  const view=mount(),pending=deferred();let request;handlers.preview=input=>{request=input;return pending.promise;};
  try{const query=view.flow.preview();const original=view.server.value;view.server.value={...original,serverId:'server-b'};view.server.value=original;view.revision.value=5;pending.resolve(response(request));await query;assert.equal(view.flow.result.value,null);assert.equal(view.flow.error.value,null);assert.equal(view.flow.loading.value,false);}finally{view.unmount();}
});
test('invalid fields never invoke the backend',async()=>{
  const view=mount();let calls=0;handlers.preview=()=>{calls++;};
  try{view.flow.fields.cavesEnabled='yes';await view.flow.preview();assert.equal(calls,0);assert.ok(view.flow.error.value);}finally{view.unmount();}
});
