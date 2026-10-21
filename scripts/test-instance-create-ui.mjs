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
  const releases=await readFile(resolve(process.cwd(),'src/pages/ReleaseManagementPage.vue'),'utf8');
  const instanceList=await readFile(resolve(process.cwd(),'src/pages/InstanceListPage.vue'),'utf8');
  const hosts=await readFile(resolve(process.cwd(),'src/pages/HostsPage.vue'),'utf8');
  assert.match(source,/id="instance-create-form" class="creation-form"/);
  assert.match(source,/form="instance-create-form"[^>]*>.*预览/s);
  assert.doesNotMatch(source,/Klei 专服配置 ZIP|验证 Release|验证镜像/);
  assert.doesNotMatch(source,/worldSettings|instance-create-world-form|Klei Token/);
  assert.match(releases,/verifyInstanceReleaseBase/);
  assert.match(releases,/verifyInstanceReleaseRuntime/);
  assert.match(releases,/可用于创建/);
  assert.match(releases,/previewInstanceReleaseDelete/);
  assert.match(releases,/deleteInstanceRelease/);
  assert.match(releases,/输入 DST 版本确认/);
  assert.match(instanceList,/previewInstanceDelete/);
  assert.match(instanceList,/deleteInstance/);
  assert.match(instanceList,/planId:plan\.planId/);
  assert.match(instanceList,/manifestAvailable/);
  assert.match(instanceList,/确认并归档删除/);
  assert.match(instanceList,/selectedTask\.journal/);
  assert.match(instanceList,/输入专服名称确认/);
  assert.doesNotMatch(hosts,/查看界面预览|退出预览|setPreview/);
  assert.doesNotMatch(source,/端口策略|容器拓扑/);
  assert.match(source,/是否启用洞穴/);
  assert.doesNotMatch(source,/console\.warn\("恢复动作尚未实现"/);
  assert.match(source,/role="status" aria-live="polite"/);
  assert.match(source,/继续创建尚未接入，远端 staging 现场保持不变/);
  assert.match(source,/创建现场清理尚未接入，未删除或移动任何数据/);
  assert.match(source,/持久化阶段记录和错误详情/);
  assert.match(source,/task\.error\.details\.composeDiagnostic/);
  assert.match(source,/v-if="task\?\.status==='failed' && task\.error"[^>]*role="alert"/);
  assert.match(source,/creationTaskStageLabel\(task\.stage\)}} · {{task\.error\.code/);
  assert.match(source,/<AppSelect v-model="selectedReleaseId"/);
  assert.match(source,/releaseError\.message/);
  assert.match(source,/selectedRelease\?\.imageDigest/);
});
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null},plugins:[{
    name:'creation-ipc',enforce:'pre',resolveId(id,importer){if(id==='virtual:creation-api'||/\/api\/instances(?:\.ts)?$/.test(id)&&importer?.endsWith('useInstanceCreation.ts'))return virtual;},
    configureServer(server){server.ws.listen=()=>{};},load(id){if(id===virtual)return 'export const handlers={}; export const previewInstanceCreate=request=>handlers.preview(request); export const listInstanceReleases=request=>handlers.listReleases(request); export const createInstance=request=>handlers.create(request); export const verifyInstanceReleaseBase=request=>handlers.verifyBase(request); export const verifyInstanceReleaseRuntime=request=>handlers.verifyRuntime(request); export const previewInstanceRelease=request=>handlers.previewRelease(request); export const importInstanceRelease=request=>handlers.importRelease(request); export const getInstanceCreateStatus=request=>handlers.status(request);';},
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
test('create request accepts a base-verified release and pins its catalog digest',async()=>{
  let createRequest;
  handlers.listReleases=async request=>({schemaVersion:1,requestId:request.requestId,serverId:'server-a',hostId,recordVersion:3,catalogDigest:'sha256:'+('a'.repeat(64)),releases:[{releaseId:candidate,platform:'linux/amd64',image:'hungerdesolate/dst-runtime',imageDigest:'sha256:'+('b'.repeat(64)),dstBuild:'build-1',artifactDigest:'sha256:'+('c'.repeat(64)),scriptVersion:'V2-test',verified:true,runtimeVerified:false,commands:{master:[],caves:[]}}],blockers:[]});
  handlers.preview=async request=>response(request);
  handlers.create=async request=>{createRequest=request;return {task:null};};
  const view=mount();
  try{
    await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(view.flow.releaseReady(),true);
    await view.flow.preview();
    await view.flow.create();
    assert.equal(createRequest.input.releaseId,candidate);
    assert.equal(createRequest.input.releaseCatalogDigest,'sha256:'+('a'.repeat(64)));
  }finally{view.unmount();}
});

const otherRelease='66666666-6666-4666-8666-666666666666';
function releaseItem(releaseId,verified=true){return {releaseId,platform:'linux/amd64',image:'hungerdesolate/dst-runtime',imageDigest:'sha256:'+((releaseId===candidate?'b':'d').repeat(64)),dstBuild:'756039',artifactDigest:'sha256:'+('c'.repeat(64)),scriptVersion:'V2-test',verified,runtimeVerified:false,commands:{master:[],caves:[]}};}
function releaseCatalog(request,releases){return {schemaVersion:1,requestId:request.requestId,serverId:request.serverId,hostId,recordVersion:request.expectedRecordVersion,catalogDigest:'sha256:'+('a'.repeat(64)),releases,blockers:[]};}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));

test('multiple releases with the same DST build require selection and submit the chosen release',async()=>{
  handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate),releaseItem(otherRelease)]);
  handlers.preview=async request=>response(request);
  let created;
  handlers.create=async request=>{created=request;return {task:null};};
  const view=mount();
  try{
    await settle();
    assert.equal(view.flow.releaseLoading.value,false);
    assert.equal(view.flow.selectedReleaseId.value,'');
    assert.equal(view.flow.releaseReady(),false);
    view.flow.selectedReleaseId.value=otherRelease;
    assert.equal(view.flow.releaseReady(),true);
    await view.flow.preview();
    await view.flow.create();
    assert.equal(created.input.releaseId,otherRelease);
    assert.equal(created.input.releaseCatalogDigest,'sha256:'+('a'.repeat(64)));
  }finally{view.unmount();}
});

test('changing the selected release discards the previous preview and its pending response',async()=>{
  handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate),releaseItem(otherRelease)]);
  const view=mount(),pending=deferred();let request;
  handlers.preview=input=>{request=input;return pending.promise;};
  try{
    await settle();
    view.flow.selectedReleaseId.value=candidate;
    const query=view.flow.preview();
    view.flow.selectedReleaseId.value=otherRelease;
    pending.resolve(response(request));await query;
    assert.equal(view.flow.result.value,null);
    assert.equal(view.flow.loading.value,false);
    assert.equal(view.flow.error.value,null);
  }finally{view.unmount();}
});

test('refreshing the catalog preserves selection but invalidates the previous creation preview',async()=>{
  handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate),releaseItem(otherRelease)]);
  handlers.preview=async request=>response(request);
  const view=mount();
  try{
    await settle();view.flow.selectedReleaseId.value=otherRelease;
    await view.flow.preview();assert.ok(view.flow.result.value);
    await view.flow.refreshReleaseCatalog();
    assert.equal(view.flow.selectedReleaseId.value,otherRelease);
    assert.equal(view.flow.result.value,null);
    assert.equal(view.flow.releaseReady(),true);
  }finally{view.unmount();}
});

test('unverified releases cannot be chosen automatically or used for creation',async()=>{
  handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate,false)]);
  const view=mount();let creates=0;
  handlers.create=async()=>{creates++;return {task:null};};
  try{
    await settle();
    assert.equal(view.flow.selectedReleaseId.value,'');
    view.flow.selectedReleaseId.value=candidate;
    assert.equal(view.flow.releaseReady(),false);
    await view.flow.create();assert.equal(creates,0);
  }finally{view.unmount();}
});

test('older release errors and finally cannot override the latest catalog request',async()=>{
  const older=deferred(),latest=deferred();let count=0,latestRequest;
  handlers.listReleases=request=>{count++;if(count===1)return older.promise;latestRequest=request;return latest.promise;};
  const view=mount();
  try{
    const refresh=view.flow.refreshReleaseCatalog();
    older.reject(new Error('old catalog failure'));await settle();
    assert.equal(view.flow.releaseError.value,null);
    assert.equal(view.flow.releaseLoading.value,true);
    latest.resolve(releaseCatalog(latestRequest,[releaseItem(candidate)]));await refresh;
    assert.equal(view.flow.releaseLoading.value,false);
    assert.equal(view.flow.releaseReady(),true);
  }finally{view.unmount();}
});

test('catalog errors are visible, invalidate readiness and clear after retry',async()=>{
  handlers.listReleases=async()=>{throw new Error('catalog unavailable');};
  const view=mount();
  try{
    await settle();assert.equal(view.flow.releaseError.value.message,'catalog unavailable');
    assert.equal(view.flow.releaseReady(),false);
    handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate)]);
    await view.flow.refreshReleaseCatalog();
    assert.equal(view.flow.releaseError.value,null);
    assert.equal(view.flow.releaseReady(),true);
  }finally{view.unmount();}
});

test('catalog responses must match request id and connection version',async()=>{
  handlers.listReleases=async request=>({...releaseCatalog(request,[releaseItem(candidate)]),requestId:'different-request'});
  const view=mount();
  try{
    await settle();assert.match(view.flow.releaseError.value.message,/身份或版本不匹配/);
    assert.equal(view.flow.releaseReady(),false);
    assert.equal(view.flow.releaseCatalog.value,null);
  }finally{view.unmount();}
});
