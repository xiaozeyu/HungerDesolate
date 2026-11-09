/** Production creation composable tests, with IPC mocked and no host connections. */
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {createRenderer,defineComponent,ref} from 'vue';
let vite,handlers,useInstanceCreation,taskStore,taskCounter=0,useTasks;
const virtual='\0creation-test-api';
test('creation form submits check-and-create in one step', async()=>{
  const source=await readFile(resolve(process.cwd(),'src/components/InstanceCreateDialog.vue'),'utf8');
  const api=await readFile(resolve(process.cwd(),'src/api/instances.ts'),'utf8');
  const releases=await readFile(resolve(process.cwd(),'src/pages/ReleaseManagementPage.vue'),'utf8');
  const instanceList=await readFile(resolve(process.cwd(),'src/pages/InstanceListPage.vue'),'utf8');
  const hosts=await readFile(resolve(process.cwd(),'src/pages/HostsPage.vue'),'utf8');
  assert.match(source,/id="instance-create-form" class="creation-form"/);
  assert.match(source,/form="instance-create-form"[^>]*>.*创建专服/s);
  assert.doesNotMatch(source,/Klei 专服配置 ZIP|验证 Release|验证镜像/);
  assert.doesNotMatch(source,/worldSettings|instance-create-world-form|Klei Token/);
  assert.doesNotMatch(source,/重新检查并创建|创建摘要|端口未预留|返回编辑|创建预览/);
  assert.match(source,/creationBlockerLabel\(code\)/);
  assert.match(source,/task\.error\.details\.composeDiagnostic/);
  assert.match(source,/task\.error\.recoverySteps/);
  assert.match(source,/task\.journal\?\.length/);
  assert.match(source,/<AppSelect v-model="selectedReleaseId"/);
  assert.match(source,/releaseError\.message/);
  assert.match(source,/selectedRelease\?\.imageDigest|selectedRelease\.releaseId/);
  assert.doesNotMatch(api,/previewInstanceCreate|preview_instance_create/);
  assert.match(api,/create_instance/);
  assert.match(releases,/verifyInstanceReleaseBase/);
  assert.match(releases,/已通过/);
  assert.match(releases,/previewInstanceReleaseDelete/);
  assert.match(releases,/deleteInstanceRelease/);
  assert.match(releases,/输入 DST 版本确认/);
  assert.match(instanceList,/previewInstanceDelete/);
  assert.match(instanceList,/deleteInstance/);
  assert.match(instanceList,/planId:plan\.planId/);
  assert.match(instanceList,/manifestAvailable/);
  assert.match(instanceList,/确认并归档删除/);
  assert.doesNotMatch(instanceList,/selectedTask\.journal/);
  assert.match(instanceList,/输入专服名称确认/);
  assert.doesNotMatch(hosts,/查看界面预览|退出预览|setPreview/);
  assert.doesNotMatch(source,/端口策略|容器拓扑/);
  assert.match(source,/是否启用洞穴/);
});
before(async()=>{
  vite=await createServer({configFile:false,logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,watch:null},plugins:[{
    name:'creation-ipc',enforce:'pre',resolveId(id,importer){if(id==='virtual:creation-api'||/\/api\/instances(?:\.ts)?$/.test(id)&&importer?.endsWith('useInstanceCreation.ts'))return virtual;},
    configureServer(server){server.ws.listen=()=>{};},load(id){if(id===virtual)return 'export const handlers={}; export const listInstanceReleases=request=>handlers.listReleases(request); export const createInstance=request=>handlers.create(request); export const verifyInstanceReleaseBase=request=>handlers.verifyBase(request);  export const previewInstanceRelease=request=>handlers.previewRelease(request); export const importInstanceRelease=request=>handlers.importRelease(request);';},
  }]});
  handlers=(await vite.ssrLoadModule('virtual:creation-api')).handlers;
  ({useInstanceCreation}=await vite.ssrLoadModule('/src/composables/useInstanceCreation.ts'));
  ({useTasks}=await vite.ssrLoadModule('/src/composables/useTasks.ts'));
  taskStore=useTasks();
});
after(async()=>{await vite?.close();});
const hostId='11111111-1111-4111-8111-111111111111',candidate='22222222-2222-4222-8222-222222222222';
const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createElement:()=>({}),createText:()=>({}),setText(){},setElementText(){},patchProp(){}});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function task(status,extra={}){
  taskCounter++;
  const id=`${String(taskCounter).padStart(8,'0')}-3333-4333-8333-333333333333`;
  return {schemaVersion:1,taskId:id,operationId:id,target:{serverId:'server-a',hostId,instanceId:candidate},operation:'instanceCreate',status,stage:'reserve',lastCompletedStage:null,progress:{kind:'indeterminate'},sequence:1,createdAt:'2026-10-31T00:00:00Z',startedAt:null,finishedAt:null,cancelAllowed:false,observation:'live',result:status==='succeeded'?{verification:'passed'}:null,error:null,journal:[],...extra};
}
function succeeded(previous){return {...previous,status:'succeeded',stage:'finalize',sequence:previous.sequence+1,result:{verification:'passed'},finishedAt:'2026-10-31T00:01:00Z'};}
function createResponse(request,value){return {schemaVersion:1,requestId:request.requestId,serverId:request.target.serverId,recordVersion:request.expectedRecordVersion,task:value};}
function releaseItem(releaseId,verified=true){return {releaseId,platform:'linux/amd64',image:'hungerdesolate/dst-runtime',imageDigest:'sha256:'+((releaseId===candidate?'b':'d').repeat(64)),dstBuild:'756039',artifactDigest:'sha256:'+('c'.repeat(64)),scriptVersion:'V2-test',verified,runtimeVerified:false,commands:{master:[],caves:[]}};}
function releaseCatalog(request,releases){return {schemaVersion:1,requestId:request.requestId,serverId:request.serverId,hostId,recordVersion:request.expectedRecordVersion,catalogDigest:'sha256:'+('a'.repeat(64)),releases,blockers:[]};}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function mount(onCreated){
  const server=ref({serverId:'server-a',hostId,recordVersion:3}),revision=ref(4),open=ref(false);let flow;
  const app=renderer.createApp(defineComponent({setup(){flow=useInstanceCreation(server,revision,open,onCreated);return()=>null;}}));app.mount({});open.value=true;flow.fields.displayName='生存一服';
  return {flow,server,revision,open,unmount:()=>app.unmount()};
}
async function readyView(onCreated){
  handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate)]);
  const view=mount(onCreated);
  await settle();
  return view;
}
test('create sends the full target, input and pinned release catalog digest',async()=>{
  const view=await readyView();let captured;
  handlers.create=async request=>{captured=request;return createResponse(request,task('queued'));};
  try{
    assert.equal(view.flow.releaseReady(),true);
    await view.flow.create();
    assert.equal(captured.target.serverId,'server-a');
    assert.equal(captured.target.hostId,hostId);
    assert.equal(captured.expectedRecordVersion,3);
    assert.equal(captured.expectedRegistryRevision,4);
    assert.match(captured.operationId,/^[0-9a-f-]{36}$/);
    assert.deepEqual(captured.input,{displayName:'生存一服',autostart:true,startAfterCreate:false,portPolicy:'auto',cavesEnabled:true,releaseId:candidate,releaseCatalogDigest:'sha256:'+('a'.repeat(64))});
    assert.equal(view.flow.loading.value,false);
    assert.equal(view.flow.creating.value,true);
    assert.equal(view.flow.task.value.status,'queued');
  }finally{view.unmount();}
});
test('a create response arriving after close and reopen is discarded',async()=>{
  const view=await readyView(),pending=deferred();let request;
  handlers.create=input=>{request=input;return pending.promise;};
  try{
    const query=view.flow.create();
    view.open.value=false;view.open.value=true;
    pending.resolve(createResponse(request,task('queued')));await query;
    assert.equal(view.flow.task.value,null);
    assert.equal(view.flow.loading.value,false);
    assert.equal(view.flow.creating.value,false);
  }finally{view.unmount();}
});
test('invalid fields never invoke the backend',async()=>{
  const view=await readyView();let calls=0;
  handlers.create=async()=>{calls++;return {task:null};};
  try{
    view.flow.fields.cavesEnabled='yes';
    await view.flow.create();
    assert.equal(calls,0);
    assert.ok(view.flow.error.value);
  }finally{view.unmount();}
});
test('backend blockers stay on the error for the dialog to label',async()=>{
  const view=await readyView();
  const failure={code:'CREATE_BLOCKED',message:'创建条件未满足',recoverySteps:['刷新专服列表与主机环境后重试'],details:{blockers:['PORT_PLAN_EXHAUSTED']}};
  handlers.create=async()=>{throw failure;};
  try{
    await view.flow.create();
    assert.equal(view.flow.loading.value,false);
    assert.equal(view.flow.creating.value,false);
    assert.equal(view.flow.error.value.code,'CREATE_BLOCKED');
    assert.deepEqual(view.flow.error.value.details.blockers,['PORT_PLAN_EXHAUSTED']);
  }finally{view.unmount();}
});
test('a failed creation task surfaces its error and stops creating',async()=>{
  const view=await readyView();
  const failure=task('failed',{error:{code:'CREATE_CONTAINERS_FAILED',message:'容器创建失败',recoverySteps:['核对 Docker 状态'],details:{}}});
  handlers.create=async request=>createResponse(request,failure);
  try{
    await view.flow.create();
    assert.equal(view.flow.loading.value,false);
    assert.equal(view.flow.creating.value,false);
    assert.equal(view.flow.error.value,null);
    assert.equal(view.flow.task.value.status,'failed');
    assert.equal(view.flow.task.value.error.code,'CREATE_CONTAINERS_FAILED');
  }finally{view.unmount();}
});
test('a queued task completes from the global task store and reports success',async()=>{
  const created=[];
  const view=await readyView((_event,target)=>created.push(target));
  const queued=task('queued');
  handlers.create=async request=>createResponse(request,queued);
  try{
    await view.flow.create();
    assert.equal(view.flow.creating.value,true);
    taskStore.receive(succeeded(queued));
    await new Promise(resolve=>setTimeout(resolve,1100));
    assert.equal(view.flow.task.value.status,'succeeded');
    assert.equal(view.flow.creating.value,false);
    assert.equal(created.length,1);
    assert.equal(created[0].instanceId,candidate);
  }finally{view.unmount();}
});
test('multiple releases with the same DST build require selection and submit the chosen release',async()=>{
  const otherRelease='66666666-6666-4666-8666-666666666666';
  handlers.listReleases=async request=>releaseCatalog(request,[releaseItem(candidate),releaseItem(otherRelease)]);
  let created;
  handlers.create=async request=>{created=request;return createResponse(request,task('failed'));};
  const view=mount();
  try{
    await settle();
    assert.equal(view.flow.releaseLoading.value,false);
    assert.equal(view.flow.selectedReleaseId.value,'');
    assert.equal(view.flow.releaseReady(),false);
    view.flow.selectedReleaseId.value=otherRelease;
    assert.equal(view.flow.releaseReady(),true);
    await view.flow.create();
    assert.equal(created.input.releaseId,otherRelease);
    assert.equal(created.input.releaseCatalogDigest,'sha256:'+('a'.repeat(64)));
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
