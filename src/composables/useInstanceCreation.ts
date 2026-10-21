import { onBeforeUnmount, reactive, ref, shallowRef, watch, type Ref } from "vue";
import { createInstance, getInstanceCreateStatus, listInstanceReleases, previewInstanceCreate, type InstanceCreateDraft, type InstanceCreateInput, type InstanceCreatePreview, type InstanceCreatePreviewRequest, type InstanceReleaseCatalogResponse } from "../api/instances";
import { matchesCreationPreview, validateCreationInput } from "../lib/instanceCreation";
import { describeError, type UserError } from "../lib/commandError";
import type { ServerSummary, TaskSnapshot, InstanceTarget } from "../types/contracts";
import { useTasks } from "./useTasks";

export function useInstanceCreation(server:Readonly<Ref<ServerSummary|null>>, revision:Readonly<Ref<number>>, open:Readonly<Ref<boolean>>, emit?:(event:"creationSucceeded",target:InstanceTarget)=>void) {
  const fields=reactive<InstanceCreateDraft>({displayName:"",autostart:true,startAfterCreate:true,portPolicy:"auto",cavesEnabled:true});
  const result=shallowRef<InstanceCreatePreview|null>(null), task=shallowRef<TaskSnapshot|null>(null), releaseCatalog=shallowRef<InstanceReleaseCatalogResponse|null>(null), error=shallowRef<UserError|null>(null), releaseError=shallowRef<UserError|null>(null);
  const selectedReleaseId=ref(""), releaseLoading=ref(false), loading=ref(false), creating=ref(false), taskStore=useTasks();
  let epoch=0, sequence=0, releaseSequence=0, pollTimer:ReturnType<typeof setTimeout>|undefined;
  function stopPolling(){if(pollTimer){clearTimeout(pollTimer);pollTimer=undefined;}}
  const releaseOptions=()=>releaseCatalog.value?.releases??[];
  const selectedRelease=()=>releaseOptions().find(item=>item.releaseId===selectedReleaseId.value)??null;
  const releaseReady=()=>!releaseLoading.value&&!releaseError.value&&!!releaseCatalog.value?.catalogDigest&&!!selectedRelease()?.verified;
  function invalidateContext(){epoch++;sequence++;releaseSequence++;stopPolling();result.value=null;task.value=null;releaseCatalog.value=null;selectedReleaseId.value="";error.value=null;releaseError.value=null;releaseLoading.value=false;loading.value=false;creating.value=false;}
  function invalidatePreview(){sequence++;stopPolling();result.value=null;task.value=null;error.value=null;loading.value=false;creating.value=false;}
  watch(fields,invalidatePreview,{flush:"sync"});
  watch(selectedReleaseId,invalidatePreview,{flush:"sync"});
  watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>revision.value,()=>open.value],()=>{
    invalidateContext();
    if(open.value) { Object.assign(fields,{displayName:"",autostart:true,startAfterCreate:true,portPolicy:"auto",cavesEnabled:true}); void refreshReleaseCatalog(); }
  },{flush:"sync",immediate:true});
  async function refreshReleaseCatalog(){
    const target=server.value;
    if(!open.value||!target?.hostId||creating.value)return;
    const ticket=epoch,requestSequence=++releaseSequence,requestId=crypto.randomUUID();
    const {serverId,hostId,recordVersion}=target;
    const current=()=>open.value&&epoch===ticket&&releaseSequence===requestSequence
      &&server.value?.serverId===serverId&&server.value.hostId===hostId&&server.value.recordVersion===recordVersion;
    invalidatePreview();
    releaseLoading.value=true;releaseError.value=null;releaseCatalog.value=null;
    try{
      const response=await listInstanceReleases({schemaVersion:1,requestId,serverId,expectedRecordVersion:recordVersion});
      if(!current())return;
      if(response.schemaVersion!==1||response.requestId!==requestId||response.recordVersion!==recordVersion
        ||response.serverId!==serverId||response.hostId!==hostId||!Array.isArray(response.releases)
        ||(response.catalogDigest!==null&&!/^sha256:[0-9a-f]{64}$/.test(response.catalogDigest))){
        throw new Error("Release 列表响应身份或版本不匹配");
      }
      releaseCatalog.value=response;
      const available=response.releases.filter(item=>item.verified);
      if(!available.some(item=>item.releaseId===selectedReleaseId.value)){
        selectedReleaseId.value=available.length===1?available[0]!.releaseId:"";
      }
    }catch(cause){if(current())releaseError.value=describeError(cause);}
    finally{if(current())releaseLoading.value=false;}
  }
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
    const target=server.value, previewResult=result.value, release=selectedRelease(), catalogDigest=releaseCatalog.value?.catalogDigest;
    if(!open.value||creating.value||!target?.hostId||!previewResult||!releaseReady()||!release||!catalogDigest)return;
    const invalid=validateCreationInput(fields);if(invalid){error.value=describeError(new Error(invalid));return;}
    const ticket=epoch, operationId=crypto.randomUUID();creating.value=true;error.value=null;
    try{const response=await createInstance({schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId},expectedRecordVersion:target.recordVersion,expectedRegistryRevision:previewResult.registryRevision,planId:previewResult.previewId,operationId,input:{...previewResult.input,releaseId:release.releaseId,releaseCatalogDigest:catalogDigest,expectedPorts:previewResult.ports}});if(!open.value||epoch!==ticket)return;task.value=response.task;if(response.task)taskStore.receive(response.task);if(response.task&&["queued","running"].includes(response.task.status))pollTimer=setTimeout(()=>void refreshTask(target,operationId,ticket),500);else creating.value=false;}
    catch(cause){if(open.value&&epoch===ticket){creating.value=false;error.value=describeError(cause);}}
  }
  onBeforeUnmount(()=>{stopPolling();invalidateContext();});
  return {fields,result,task,releaseCatalog,releaseError,releaseLoading,releaseReady,selectedReleaseId,releaseOptions,error,loading,creating,preview,create,refreshReleaseCatalog,edit:()=>{stopPolling();task.value=null;result.value=null;error.value=null;}};
}
