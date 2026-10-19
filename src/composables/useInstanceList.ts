import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from "vue";
import { listInstances, type InstanceListResponse } from "../api/instances";
import { demoInstances } from "../fixtures/workspace";
import { validateInstanceList } from "../lib/instanceList";
import { currentConnection, type ConnectionTicket } from "../lib/connectionGuard";
import { describeError, type UserError } from "../lib/commandError";
import { useWorkspace } from "./useWorkspace";
import type { ServerSummary } from "../types/contracts";
export function useInstanceList(server: Ref<ServerSummary | null>, preview: Readonly<Ref<boolean>>) {
  const workspace = useWorkspace();
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
    } catch (cause) {
      if (current(ticket)) {error.value = describeError(cause);stale.value = data.value !== null;workspace.invalidateSelection();}
    } finally {if (current(ticket)) loading.value = false;}
  }
  // hostId is bound on the first successful discovery without changing its connection version.
  watch([() => server.value?.serverId, () => server.value?.recordVersion, () => preview.value], () => {
    epoch++;sequence++;data.value = null;error.value = null;loading.value = false;stale.value = false;void refresh();
  },{immediate:true});
  onBeforeUnmount(() => {epoch++;sequence++;});
  return {data,error,loading,stale,refresh};
}
