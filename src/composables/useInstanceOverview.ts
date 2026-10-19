import { ref, shallowRef, watch, onBeforeUnmount } from "vue";
import { activeContext } from "../context/activeContext";
import { isCurrentQuery, type QueryTicket } from "../lib/queryGuard";
import { getInstanceOverview } from "../api/instances";
import { demoOverview } from "../fixtures/workspace";
import { useWorkspace } from "./useWorkspace";
import { describeError } from "../lib/commandError";
import type { InstanceOverview } from "../types/contracts";
export function useInstanceOverview() {
  const workspace=useWorkspace(),data=shallowRef<InstanceOverview|null>(null),error=ref<string|null>(null),loading=ref(false);
  let sequence=0,current:QueryTicket|null=null;
  function valid(ticket:QueryTicket,version:number) {
    return isCurrentQuery(ticket,current,activeContext.value.epoch)
      && workspace.servers.value.find(item=>item.serverId===ticket.target.serverId)?.recordVersion===version;
  }
  async function refresh(){
    const {target,epoch}=activeContext.value;if(!target)return;
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
      }
      if(valid(ticket,version))data.value=overview;
    }catch(cause){if(valid(ticket,version))error.value=describeError(cause).message;}
    finally{if(valid(ticket,version))loading.value=false;}
  }
  watch([activeContext,()=>workspace.servers.value.find(item=>item.serverId===activeContext.value.target?.serverId)?.recordVersion],()=>{
    current=null;data.value=null;error.value=null;loading.value=false;void refresh();
  },{immediate:true});
  onBeforeUnmount(()=>{current=null;sequence++;});
  return{data,error,loading,refresh};
}
