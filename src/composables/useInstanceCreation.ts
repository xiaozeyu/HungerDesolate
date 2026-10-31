import { onBeforeUnmount, reactive, ref, shallowRef, watch, type Ref } from "vue";
import { createInstance, getInstanceCreateStatus, listInstanceReleases, type InstanceCreateDraft, type InstanceCreateInput, type InstanceCreateTaskRequest, type InstanceReleaseCatalogResponse } from "../api/instances";
import { validateCreationInput } from "../lib/instanceCreation";
import { describeError, type UserError } from "../lib/commandError";
import type { ServerSummary, TaskSnapshot, InstanceTarget } from "../types/contracts";
import { useTasks } from "./useTasks";

export function useInstanceCreation(server:Readonly<Ref<ServerSummary|null>>, revision:Readonly<Ref<number>>, open:Readonly<Ref<boolean>>, emit?:(event:"creationSucceeded",target:InstanceTarget)=>void) {
  const fields=reactive<InstanceCreateDraft>({displayName:"",autostart:true,startAfterCreate:false,portPolicy:"auto",cavesEnabled:true});
  const task=shallowRef<TaskSnapshot|null>(null), releaseCatalog=shallowRef<InstanceReleaseCatalogResponse|null>(null), error=shallowRef<UserError|null>(null), releaseError=shallowRef<UserError|null>(null);
  const selectedReleaseId=ref(""), releaseLoading=ref(false), loading=ref(false), creating=ref(false), taskStore=useTasks();
  let epoch=0, releaseSequence=0, pollTimer:ReturnType<typeof setTimeout>|undefined;
  function stopPolling(){if(pollTimer){clearTimeout(pollTimer);pollTimer=undefined;}}
  const releaseOptions=()=>releaseCatalog.value?.releases??[];
  const selectedRelease=()=>releaseOptions().find(item=>item.releaseId===selectedReleaseId.value)??null;
  const releaseReady=()=>!releaseLoading.value&&!releaseError.value&&!!releaseCatalog.value?.catalogDigest&&!!selectedRelease()?.verified;
  function invalidateContext(){epoch++;releaseSequence++;stopPolling();task.value=null;releaseCatalog.value=null;selectedReleaseId.value="";error.value=null;releaseError.value=null;releaseLoading.value=false;loading.value=false;creating.value=false;}
  watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>revision.value,()=>open.value],()=>{
    invalidateContext();
    if(open.value) { Object.assign(fields,{displayName:"",autostart:true,startAfterCreate:false,portPolicy:"auto",cavesEnabled:true}); void refreshReleaseCatalog(); }
  },{flush:"sync",immediate:true});
  async function refreshReleaseCatalog(){
    const target=server.value;
    if(!open.value||!target?.hostId||loading.value||creating.value)return;
    const ticket=epoch,requestSequence=++releaseSequence,requestId=crypto.randomUUID();
    const {serverId,hostId,recordVersion}=target;
    const current=()=>open.value&&epoch===ticket&&releaseSequence===requestSequence
      &&server.value?.serverId===serverId&&server.value.hostId===hostId&&server.value.recordVersion===recordVersion;
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
  function applyTask(next:TaskSnapshot|null,target:ServerSummary,operationId:string,ticket:number,delay:number){
    task.value=next;
    if(next)taskStore.receive(next);
    if(next&&["queued","running"].includes(next.status)){
      creating.value=true;
      pollTimer=setTimeout(()=>void refreshTask(target,operationId,ticket),delay);
      return;
    }
    creating.value=false;
    if(next?.status==="succeeded"&&next.result?.verification==="passed"){
      setTimeout(()=>{if(open.value&&epoch===ticket&&emit)emit("creationSucceeded",next.target as InstanceTarget);},1000);
    }
  }
  async function refreshTask(target:ServerSummary,operationId:string,ticket:number){
    if(!open.value||epoch!==ticket)return;
    try{
      const response=await getInstanceCreateStatus({schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId!},expectedRecordVersion:target.recordVersion,operationId});
      if(!open.value||epoch!==ticket)return;
      applyTask(response.task,target,operationId,ticket,1500);
    }catch(cause){if(open.value&&epoch===ticket){creating.value=false;error.value=describeError(cause);}}
  }
  async function create(){
    const target=server.value, release=selectedRelease(), catalogDigest=releaseCatalog.value?.catalogDigest;
    if(!open.value||loading.value||creating.value||!target?.hostId||!releaseReady()||!release||!catalogDigest)return;
    const invalid=validateCreationInput(fields);if(invalid){error.value=describeError(new Error(invalid));return;}
    const ticket=epoch, operationId=crypto.randomUUID();
    const input:InstanceCreateInput={displayName:fields.displayName.trim(),autostart:fields.autostart,startAfterCreate:fields.startAfterCreate,portPolicy:fields.portPolicy,cavesEnabled:fields.cavesEnabled};
    const request:InstanceCreateTaskRequest={schemaVersion:1,requestId:crypto.randomUUID(),target:{serverId:target.serverId,hostId:target.hostId},expectedRecordVersion:target.recordVersion,expectedRegistryRevision:revision.value,operationId,input:{...input,releaseId:release.releaseId,releaseCatalogDigest:catalogDigest}};
    const current=()=>open.value&&epoch===ticket&&server.value?.serverId===request.target.serverId&&server.value.hostId===request.target.hostId&&server.value.recordVersion===request.expectedRecordVersion&&revision.value===request.expectedRegistryRevision;
    loading.value=true;task.value=null;error.value=null;
    try{const response=await createInstance(request);if(!current())return;applyTask(response.task,target,operationId,ticket,500);}
    catch(cause){if(current())error.value=describeError(cause);}
    finally{if(current())loading.value=false;}
  }
  onBeforeUnmount(()=>{stopPolling();invalidateContext();});
  return {fields,task,releaseCatalog,releaseError,releaseLoading,releaseReady,selectedReleaseId,releaseOptions,error,loading,creating,create,refreshReleaseCatalog};
}
