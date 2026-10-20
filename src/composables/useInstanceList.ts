import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from "vue";
import { getInstanceCreateStatus, listInstances, type InstanceListResponse } from "../api/instances";
import { demoInstances } from "../fixtures/workspace";
import { validateInstanceList } from "../lib/instanceList";
import { currentConnection, type ConnectionTicket } from "../lib/connectionGuard";
import { describeError, type UserError } from "../lib/commandError";
import { useWorkspace } from "./useWorkspace";
import { useTasks } from "./useTasks";
import type { ServerSummary } from "../types/contracts";
export function useInstanceList(server: Ref<ServerSummary | null>, preview: Readonly<Ref<boolean>>) {
  const workspace = useWorkspace();
  const taskStore = useTasks();
  const data = shallowRef<InstanceListResponse | null>(null), error = shallowRef<UserError | null>(null);
  const loading = ref(false), stale = ref(false);
  let epoch = 0, sequence = 0, requestId = "";
  function current(ticket: ConnectionTicket) {
    return !!server.value && currentConnection(ticket,{serverId:server.value.serverId,recordVersion:server.value.recordVersion,epoch,sequence,requestId});
  }
  async function refresh() {
    const target = server.value;if (!target) return;
    const expectedHostId = target.hostId;
    requestId = crypto.randomUUID();const ticket = {serverId:target.serverId,recordVersion:target.recordVersion,epoch,sequence:++sequence,requestId};
    loading.value = true;error.value = null;
    try {
      const request = {schemaVersion:1 as const,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion};
      const result = preview.value ? await Promise.resolve({schemaVersion:1 as const,requestId:ticket.requestId,serverId:target.serverId,recordVersion:target.recordVersion,hostId:expectedHostId ?? "",registryRevision:1,capturedAtUnixSeconds:Math.floor(Date.now()/1000),instances:demoInstances(expectedHostId ?? "")}) : await listInstances(request);
      if (!current(ticket)) return;
      if (!validateInstanceList(ticket,expectedHostId,result)) throw new Error("专服列表响应身份或格式不匹配");
      if (data.value && data.value.hostId === result.hostId && result.registryRevision < data.value.registryRevision) throw new Error("注册表版本早于已读取结果，请核对远端数据");
      workspace.commitInstances(result);
      data.value = result;stale.value = false;
      if (!preview.value) void reconcileCreationTasks(target, result, ticket);
    } catch (cause) {
      if (current(ticket)) {error.value = describeError(cause);stale.value = data.value !== null;workspace.invalidateSelection();}
    } finally {if (current(ticket)) loading.value = false;}
  }
  async function reconcileCreationTasks(target:ServerSummary, snapshot:InstanceListResponse, ticket:ConnectionTicket) {
    const terminalStatuses=["succeeded","failed","cancelled","interrupted"];
    const pending=snapshot.instances.filter(item=>(item.lifecycle==="creating"||item.lifecycle==="createFailed")
      &&item.activeTaskId&&!taskStore.tasks.value.some(task=>task.taskId===item.activeTaskId
        &&task.target.serverId===target.serverId&&"hostId" in task.target&&task.target.hostId===item.hostId
        &&"instanceId" in task.target&&task.target.instanceId===item.instanceId&&terminalStatuses.includes(task.status)));
    if(!pending.length)return;
    const results=await Promise.all(pending.map(async item=>{
      try{return await getInstanceCreateStatus({schemaVersion:1,requestId:crypto.randomUUID(),
        target:{serverId:target.serverId,hostId:item.hostId},expectedRecordVersion:target.recordVersion,
        operationId:item.activeTaskId!});}catch{return null;}
    }));
    if(!current(ticket))return;
    let terminal=false;
    for(let index=0;index<pending.length;index++){
      const response=results[index],item=pending[index],task=response?.task;
      if(!response||!item||response.serverId!==target.serverId||response.recordVersion!==target.recordVersion
        ||!task||!("hostId" in task.target)||!("instanceId" in task.target)
        ||task.operationId!==item.activeTaskId||task.target.serverId!==target.serverId
        ||task.target.hostId!==item.hostId||task.target.instanceId!==item.instanceId)continue;
      taskStore.receive(task);
      if(terminalStatuses.includes(task.status))terminal=true;
    }
    if(terminal&&current(ticket))await refresh();
  }
  // hostId is bound on the first successful discovery without changing its connection version.
  watch([() => server.value?.serverId, () => server.value?.recordVersion, () => preview.value], () => {
    epoch++;sequence++;data.value = null;error.value = null;loading.value = false;stale.value = false;void refresh();
  },{immediate:true});
  onBeforeUnmount(() => {epoch++;sequence++;});
  return {data,error,loading,stale,refresh};
}
