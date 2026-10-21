import { ref, shallowRef, watch, onBeforeUnmount } from "vue";
import { activeContext } from "../context/activeContext";
import { isCurrentQuery, type QueryTicket } from "../lib/queryGuard";
import { getInstanceOverview } from "../api/instances";
import { demoOverview } from "../fixtures/workspace";
import { useWorkspace } from "./useWorkspace";
import { describeError } from "../lib/commandError";
import { matchesRuntimeSnapshot } from "../lib/instanceRuntime";
import type { InstanceOverview } from "../types/contracts";
export function useInstanceOverview() {
  const workspace=useWorkspace(),data=shallowRef<InstanceOverview|null>(null),error=ref<string|null>(null),loading=ref(false),stale=ref(false);
  let sequence=0,current:QueryTicket|null=null;
  let pollTimer:ReturnType<typeof setTimeout>|undefined;
  let inFlight:{key:string;promise:Promise<void>}|null=null;
  let pollingPaused=false,disposed=false;
  function stopPolling(){if(pollTimer!==undefined){clearTimeout(pollTimer);pollTimer=undefined;}}
  function schedulePolling(){
    if(!pollingPaused&&!disposed&&!workspace.preview.value)pollTimer=setTimeout(()=>void refresh(),10000);
  }
  function pausePolling(){pollingPaused=true;stopPolling();}
  function resumePolling(){pollingPaused=false;stopPolling();schedulePolling();}
  function valid(ticket:QueryTicket,version:number) {
    return isCurrentQuery(ticket,current,activeContext.value.epoch)
      && workspace.servers.value.find(item=>item.serverId===ticket.target.serverId)?.recordVersion===version;
  }
  function queryKey(target:NonNullable<typeof activeContext.value.target>,version:number,epoch:number){
    return `${target.serverId}:${target.hostId}:${target.instanceId}:${version}:${epoch}`;
  }
  async function refresh(){
    stopPolling();
    const {target}=activeContext.value;if(!target)return;
    const version=workspace.servers.value.find(item=>item.serverId===target.serverId)?.recordVersion;if(!version)return;
    const key=queryKey(target,version,activeContext.value.epoch);
    if(inFlight?.key===key)return inFlight.promise;
    const promise=refreshTarget(target);
    inFlight={key,promise};
    try{await promise;}finally{if(inFlight?.promise===promise)inFlight=null;}
  }
  async function refreshTarget(target:NonNullable<typeof activeContext.value.target>){
    const {epoch}=activeContext.value;
    const version=workspace.servers.value.find(item=>item.serverId===target.serverId)?.recordVersion;if(!version)return;
    const ticket={target:{...target},contextEpoch:epoch,requestSeq:++sequence,requestId:crypto.randomUUID()};current=ticket;
    loading.value=true;error.value=null;
    try{
      let overview:InstanceOverview;
      if(workspace.preview.value){
        const instance=workspace.instances(target.hostId).find(item=>item.instanceId===target.instanceId);
        if(!instance)throw new Error("示例专服不存在");overview=await Promise.resolve(demoOverview(instance));
      }else{
        const response=await getInstanceOverview(target,ticket.requestId,version);
        if(!valid(ticket,version))return;
        if(response.schemaVersion!==1||response.requestId!==ticket.requestId||response.recordVersion!==version
          ||response.target.serverId!==target.serverId||response.target.hostId!==target.hostId||response.target.instanceId!==target.instanceId
          ||response.data.identity.hostId!==target.hostId||response.data.identity.instanceId!==target.instanceId)throw new Error("专服响应身份或版本不匹配");
        overview=response.data;
        if(!overview.manifestSummary || !matchesRuntimeSnapshot(overview.runtime,target,overview.manifestSummary.cavesEnabled)
          ||overview.runtime.configRevision!==response.configRevision||overview.runtime.status!==overview.identity.runtimeStatus)throw new Error("专服运行证据或配置版本不匹配");
        if(!valid(ticket,version))return;
        workspace.commitInstanceOverview(target.serverId,version,overview.identity);
      }
      if(valid(ticket,version)){data.value=overview;stale.value=false;}
    }catch(cause){if(valid(ticket,version)){error.value=describeError(cause).message;stale.value=data.value!==null;}}
    finally{if(valid(ticket,version)){loading.value=false;schedulePolling();}}
  }
  watch([activeContext,()=>workspace.servers.value.find(item=>item.serverId===activeContext.value.target?.serverId)?.recordVersion],()=>{
    const target=activeContext.value.target;
    const version=target&&workspace.servers.value.find(item=>item.serverId===target.serverId)?.recordVersion;
    if(target&&version&&inFlight?.key===queryKey(target,version,activeContext.value.epoch))return;
    pollingPaused=false;
    stopPolling();current=null;data.value=null;error.value=null;loading.value=false;stale.value=false;void refresh();
  },{immediate:true});
  onBeforeUnmount(()=>{disposed=true;stopPolling();current=null;sequence++;});
  return{data,error,loading,stale,refresh,pausePolling,resumePolling};
}
