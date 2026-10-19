import { onBeforeUnmount, reactive, ref, shallowRef, watch, type Ref } from "vue";
import { createInstance, getInstanceCreateStatus, importInstanceRelease, listInstanceReleases, previewInstanceRelease, previewInstanceCreate, type InstanceCreateDraft, type InstanceCreateInput, type InstanceCreatePreview, type InstanceCreatePreviewRequest, type InstanceReleaseCatalogResponse } from "../api/instances";
import { matchesCreationPreview, validateCreationInput } from "../lib/instanceCreation";
import { describeError, type UserError } from "../lib/commandError";
import type { ServerSummary, TaskSnapshot, InstanceTarget } from "../types/contracts";
import { useTasks } from "./useTasks";

export function useInstanceCreation(server:Readonly<Ref<ServerSummary|null>>, revision:Readonly<Ref<number>>, open:Readonly<Ref<boolean>>, emit?:(event:"creationSucceeded",target:InstanceTarget)=>void) {
  const fields=reactive<InstanceCreateDraft>({displayName:"",autostart:true,startAfterCreate:true,portPolicy:"auto",cavesEnabled:true});
  const result=shallowRef<InstanceCreatePreview|null>(null), task=shallowRef<TaskSnapshot|null>(null), releaseCatalog=shallowRef<InstanceReleaseCatalogResponse|null>(null), error=shallowRef<UserError|null>(null), releaseError=shallowRef<UserError|null>(null);
  const loading=ref(false), creating=ref(false), releasePreparing=ref(false), taskStore=useTasks();
  let epoch=0, sequence=0, pollTimer:ReturnType<typeof setTimeout>|undefined;
  function stopPolling(){if(pollTimer){clearTimeout(pollTimer);pollTimer=undefined;}}
  const releaseReady=()=>!!releaseCatalog.value?.releases.some(item=>item.verified&&item.runtimeVerified);
  function invalidate(){epoch++;sequence++;stopPolling();result.value=null;task.value=null;releaseCatalog.value=null;error.value=null;releaseError.value=null;loading.value=false;creating.value=false;}
  watch(fields,invalidate,{flush:"sync"});
  watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>revision.value,()=>open.value],()=>{
    invalidate();
    if(open.value) { Object.assign(fields,{displayName:"",autostart:true,startAfterCreate:true,portPolicy:"auto",cavesEnabled:true}); void refreshReleaseCatalog(); }
  },{flush:"sync"});
  async function refreshReleaseCatalog(){const target=server.value;if(!open.value||!target?.hostId)return;const ticket=epoch;try{const response=await listInstanceReleases({schemaVersion:1,requestId:crypto.randomUUID(),serverId:target.serverId,expectedRecordVersion:target.recordVersion});if(!open.value||epoch!==ticket||response.serverId!==target.serverId)return;releaseCatalog.value=response;}catch(cause){if(open.value&&epoch===ticket)releaseError.value=describeError(cause);}}
  async function prepareRelease(){const target=server.value;const release=releaseCatalog.value?.releases.find(item=>!item.runtimeVerified);const digest=releaseCatalog.value?.catalogDigest;if(!open.value||!target?.hostId||!release||!digest||releasePreparing.value)return;releasePreparing.value=true;releaseError.value=null;try{const preview=await previewInstanceRelease({schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId},expectedRecordVersion:target.recordVersion,releaseId:release.releaseId,catalogDigest:digest});if(preview.status!=="ready")throw new Error(preview.blockers.join("、")||"release artifact 尚未准备");await importInstanceRelease({schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId},expectedRecordVersion:target.recordVersion,releaseId:release.releaseId,catalogDigest:digest,operationId:crypto.randomUUID()});await refreshReleaseCatalog();}catch(cause){releaseError.value=describeError(cause);}finally{releasePreparing.value=false;}}
  async function preview(){
    const target=server.value;
    if(!open.value || loading.value || creating.value || !target?.hostId) return;
    const invalid=validateCreationInput(fields); if(invalid){error.value=describeError(new Error(invalid));return;}
    const capturedEpoch=epoch, requestSequence=++sequence;
    const input:InstanceCreateInput={displayName:fields.displayName.trim(),autostart:fields.autostart,startAfterCreate:fields.startAfterCreate,portPolicy:fields.portPolicy,cavesEnabled:fields.cavesEnabled};
    const request:InstanceCreatePreviewRequest={schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId},expectedRecordVersion:target.recordVersion,expectedRegistryRevision:revision.value,input};
    const current=()=>open.value&&epoch===capturedEpoch&&sequence===requestSequence&&server.value?.serverId===request.target.serverId&&server.value.hostId===request.target.hostId&&server.value.recordVersion===request.expectedRecordVersion&&revision.value===request.expectedRegistryRevision;
    loading.value=true;result.value=null;task.value=null;error.value=null;
    try{const response=await previewInstanceCreate(request);if(!current())return;if(!matchesCreationPreview(request,response))throw new Error("创建预览响应身份、参数或端口无效");result.value=response;}
    catch(cause){if(current())error.value=describeError(cause);} finally{if(current())loading.value=false;}
  }
  async function refreshTask(target:ServerSummary,operationId:string,ticket:number){
    if(!open.value||epoch!==ticket)return;
    try{const response=await getInstanceCreateStatus({schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId!},expectedRecordVersion:target.recordVersion,operationId});if(!open.value||epoch!==ticket)return;task.value=response.task;if(response.task)taskStore.receive(response.task);if(response.task&&["queued","running"].includes(response.task.status))pollTimer=setTimeout(()=>void refreshTask(target,operationId,ticket),1500);else{creating.value=false;if(response.task?.status==="succeeded"&&response.task.result?.verification==="passed"){setTimeout(()=>{if(open.value&&epoch===ticket&&emit)emit("creationSucceeded",response.task!.target as InstanceTarget);},1000);}}}
    catch(cause){if(open.value&&epoch===ticket){creating.value=false;error.value=describeError(cause);}}
  }
  async function create(){
    const target=server.value, previewResult=result.value;
    if(!open.value||creating.value||!target?.hostId||!previewResult||!releaseReady())return;
    const invalid=validateCreationInput(fields);if(invalid){error.value=describeError(new Error(invalid));return;}
    const ticket=epoch, operationId=crypto.randomUUID();creating.value=true;error.value=null;
    try{const response=await createInstance({schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId},expectedRecordVersion:target.recordVersion,expectedRegistryRevision:previewResult.registryRevision,planId:previewResult.previewId,operationId,input:{...previewResult.input,expectedPorts:previewResult.ports}});if(!open.value||epoch!==ticket)return;task.value=response.task;if(response.task)taskStore.receive(response.task);if(response.task&&["queued","running"].includes(response.task.status))pollTimer=setTimeout(()=>void refreshTask(target,operationId,ticket),500);else creating.value=false;}
    catch(cause){if(open.value&&epoch===ticket){creating.value=false;error.value=describeError(cause);}}
  }
  onBeforeUnmount(()=>{stopPolling();invalidate();});
  return {fields,result,task,releaseCatalog,releaseError,releaseReady,releasePreparing,prepareRelease,error,loading,creating,preview,create,refreshReleaseCatalog,edit:()=>{stopPolling();task.value=null;result.value=null;error.value=null;}};
}
