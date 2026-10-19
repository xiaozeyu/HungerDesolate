import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from "vue";
import { getHostEnvironment, getHostInitialization, previewHostInitialization, initializeHost, previewDockerInstallation, startDockerInstallation, getDockerInstallationStatus, type DockerInstallTask, type DockerInstallationPreviewResponse, type HostInitializationPlan } from "../api/hosts";
import { demoEnvironment } from "../fixtures/environment";
import { currentConnection, type ConnectionTicket } from "../lib/connectionGuard";
import { matchesEnvironment } from "../lib/environmentView";
import { matchesInitialization } from "../lib/hostInitialization";
import { matchesDockerInstallationPreview, matchesDockerInstallStatus } from "../lib/dockerInstallation";
import { describeError, type UserError } from "../lib/commandError";
import type { HostEnvironment, ServerSummary, TaskSnapshot } from "../types/contracts";
import { useTasks } from "./useTasks";

export function useHostEnvironment(server: Ref<ServerSummary | null>, preview: Readonly<Ref<boolean>>, onHostBound?: (hostId: string, recordVersion: number) => void) {
  const data = shallowRef<HostEnvironment | null>(null);
  const loading = ref(false), stale = ref(false);
  const error = shallowRef<UserError | null>(null);
  const plan = shallowRef<HostInitializationPlan | null>(null);
  const planOpen = ref(false), initializing = ref(false), outcomeUnknown = ref(false);
  const dockerPreview = shallowRef<DockerInstallationPreviewResponse | null>(null);
  const dockerPreviewOpen = ref(false), dockerPreviewLoading = ref(false);
  const dockerPreviewError = shallowRef<UserError | null>(null);
  const dockerTask = shallowRef<DockerInstallTask | null>(null);
  const dockerStarting = ref(false);
  const dockerTaskLoading = ref(false), dockerTaskError = shallowRef<UserError | null>(null);
  const taskStore=useTasks();
  let epoch = 0, sequence = 0, requestId = "";
  let dockerEpoch=0,dockerSequence=0,dockerRequestId="";
  let taskEpoch=0,taskSequence=0,taskRequestId="";
  let taskPollTimer: ReturnType<typeof setTimeout> | undefined;
  function toTaskSnapshot(task:DockerInstallTask, target:ServerSummary):TaskSnapshot | null {
    if(!target.hostId) return null;
    const startedAt=new Date(task.startedAtUnixSeconds*1000).toISOString();
    const updatedAt=new Date(task.updatedAtUnixSeconds*1000).toISOString();
    const terminal=["succeeded","failed","interrupted"].includes(task.status);
    return {schemaVersion:1,taskId:task.taskId,operationId:task.taskId,target:{serverId:target.serverId,hostId:target.hostId},operation:"dockerInstall",status:task.status,stage:task.phase,lastCompletedStage:null,progress:{kind:"indeterminate"},sequence:task.sequence,createdAt:startedAt,startedAt,finishedAt:terminal?updatedAt:null,cancelAllowed:false,observation:"live"};
  }
  function current(ticket: ConnectionTicket) {
    return !!server.value && currentConnection(ticket,{serverId:server.value.serverId,recordVersion:server.value.recordVersion,epoch,sequence,requestId});
  }
  async function refresh() {
    const target = server.value;
    if (!target || initializing.value) return;
    requestId = crypto.randomUUID();
    const ticket = {serverId:target.serverId,recordVersion:target.recordVersion,epoch,sequence:++sequence,requestId};
    const expectedHostId = target.hostId;
    loading.value = true; error.value = null; plan.value = null; outcomeUnknown.value = false;
    try {
      const request = {schemaVersion:1 as const,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion};
      const result = preview.value ? await Promise.resolve(demoEnvironment(request,expectedHostId)) : await getHostEnvironment(request);
      if (!current(ticket)) return;
      if (!matchesEnvironment(ticket,expectedHostId,result)) throw new Error("环境检查响应身份或版本不匹配");
      data.value = result; stale.value = false;
      if (result.hostId) onHostBound?.(result.hostId, target.recordVersion);
      if (!preview.value) {
        const operation = await getHostInitialization(request);
        if (!current(ticket)) return;
        if (!matchesInitialization(ticket,operation,result.hostId)) throw new Error("初始化响应身份或版本不匹配");
        plan.value = operation.plan;
        if (operation.plan?.status === "succeeded") {
          const checked = await getHostEnvironment(request);
          if (!current(ticket)) return;
          if (!matchesEnvironment(ticket,expectedHostId,checked) || checked.hostId!==operation.plan.hostId) throw new Error("初始化后主机身份不匹配");
          data.value = checked;
          onHostBound?.(checked.hostId, target.recordVersion);
        }
      }
    } catch (cause) {
      if (current(ticket)) {error.value = describeError(cause);stale.value = data.value !== null;}
    } finally { if (current(ticket)) loading.value = false; }
  }
  async function prepareInitialization() {
    const target = server.value;
    if (!target || preview.value || loading.value || initializing.value) return;
    if (plan.value?.status === "incomplete") { planOpen.value = true; return; }
    requestId = crypto.randomUUID();
    const ticket = {serverId:target.serverId,recordVersion:target.recordVersion,epoch,sequence:++sequence,requestId};
    initializing.value = true; error.value = null;
    try {
      const result = await previewHostInitialization({schemaVersion:1,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion});
      if (!current(ticket)) return;
      if (!matchesInitialization(ticket,result,target.hostId) || result.plan?.status!=="preview") throw new Error("初始化预览响应无效");
      plan.value = result.plan; outcomeUnknown.value = false; planOpen.value = true;
    } catch (cause) { if (current(ticket)) error.value = describeError(cause); }
    finally { if (current(ticket)) initializing.value = false; }
  }
  async function confirmInitialization() {
    const target = server.value, selected = plan.value;
    if (!target || !selected || preview.value || initializing.value || loading.value || outcomeUnknown.value || selected.status==="succeeded") return;
    requestId = crypto.randomUUID();
    const ticket = {serverId:target.serverId,recordVersion:target.recordVersion,epoch,sequence:++sequence,requestId};
    initializing.value = true; error.value = null;
    try {
      const result = await initializeHost({schemaVersion:1,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion,planId:selected.planId});
      if (!current(ticket)) return;
      if (!matchesInitialization(ticket,result,target.hostId) || result.plan?.status!=="succeeded" || result.plan.operationId!==selected.operationId || result.plan.planId!==selected.planId || result.plan.hostId!==selected.hostId) throw new Error("初始化结果身份不匹配，请重查原操作");
      planOpen.value = false; initializing.value = false;
      await refresh();
    } catch (cause) {
      if (current(ticket)) { error.value = describeError(cause); outcomeUnknown.value = true; }
    } finally { if (current(ticket)) initializing.value = false; }
  }
  async function previewDockerInstall() {
    const target=server.value;
    if (!target || preview.value || loading.value || initializing.value || dockerPreviewLoading.value || dockerTaskLoading.value || dockerTask.value && ["queued","running"].includes(dockerTask.value.status)) return;
    dockerRequestId=crypto.randomUUID();
    const ticket={serverId:target.serverId,recordVersion:target.recordVersion,epoch:dockerEpoch,sequence:++dockerSequence,requestId:dockerRequestId};
    const currentDocker=()=>!!server.value && currentConnection(ticket,{serverId:server.value.serverId,recordVersion:server.value.recordVersion,epoch:dockerEpoch,sequence:dockerSequence,requestId:dockerRequestId});
    dockerPreviewLoading.value=true; dockerPreviewError.value=null;
    try {
      const result=await previewDockerInstallation({schemaVersion:1,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion});
      if (!currentDocker()) return;
      if (!matchesDockerInstallationPreview(ticket,target.hostId,result)) throw new Error("Docker 安装预检响应身份或内容无效");
      dockerTask.value=null; dockerTaskError.value=null; dockerPreview.value=result; dockerPreviewOpen.value=true;
    } catch (cause) {if(currentDocker()) dockerPreviewError.value=describeError(cause);}
    finally {if(currentDocker()) dockerPreviewLoading.value=false;}
  }
  function stopDockerTaskPolling() {
    if (taskPollTimer) clearTimeout(taskPollTimer);
    taskPollTimer=undefined;
  }
  function scheduleDockerTaskPolling() {
    stopDockerTaskPolling();
    if (!dockerTask.value || !["queued","running"].includes(dockerTask.value.status)) return;
    taskPollTimer=setTimeout(async()=>{
      await refreshDockerTask();
      scheduleDockerTaskPolling();
    },5000);
  }
  async function refreshDockerTask(preserveError = false, operationId = dockerTask.value?.taskId) {
    const target=server.value;
    if (!target || preview.value || dockerStarting.value) return;
    taskRequestId=crypto.randomUUID();
    const ticket={serverId:target.serverId,recordVersion:target.recordVersion,epoch:taskEpoch,sequence:++taskSequence,requestId:taskRequestId};
    const currentTask=()=>!!server.value && currentConnection(ticket,{serverId:server.value.serverId,recordVersion:server.value.recordVersion,epoch:taskEpoch,sequence:taskSequence,requestId:taskRequestId});
    dockerTaskLoading.value=true;
    if (!preserveError) dockerTaskError.value=null;
    try {
      const result=await getDockerInstallationStatus({schemaVersion:1,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion,...(operationId ? {operationId} : {})});
      if(!currentTask()) return;
      if(!matchesDockerInstallStatus(ticket,target.hostId,result) || (operationId && result.task && result.task.taskId!==operationId)) throw new Error("Docker 安装任务响应身份或状态无效");
      const previousTask=dockerTask.value, nextTask=result.task;
      const previous=previousTask?.status;
      if(previousTask && nextTask && previousTask.taskId===nextTask.taskId && nextTask.sequence<previousTask.sequence) return;
      if(previousTask && nextTask && previousTask.taskId===nextTask.taskId && ["succeeded","failed","interrupted"].includes(previousTask.status) && nextTask.status!==previousTask.status) return;
      dockerTask.value=nextTask;
      if(preserveError && operationId && nextTask?.taskId===operationId) dockerTaskError.value=null;
      if(nextTask) {const snapshot=toTaskSnapshot(nextTask,target);if(snapshot) taskStore.receive(snapshot);}
      scheduleDockerTaskPolling();
      if(result.task?.status==="succeeded" && previous!=="succeeded") void refresh();
    } catch(cause) {if(currentTask() && (!preserveError || !dockerTaskError.value)) dockerTaskError.value=describeError(cause);}
    finally {if(currentTask()) dockerTaskLoading.value=false;}
  }
  async function startDockerInstall() {
    const target=server.value, plan=dockerPreview.value;
    if(!target || !plan || plan.preview.status!=="ready" || !plan.planId || !plan.operationId || dockerStarting.value || dockerPreviewLoading.value || dockerTaskLoading.value || initializing.value || loading.value) return;
    taskRequestId=crypto.randomUUID();
    const ticket={serverId:target.serverId,recordVersion:target.recordVersion,epoch:taskEpoch,sequence:++taskSequence,requestId:taskRequestId};
    const currentTask=()=>!!server.value && currentConnection(ticket,{serverId:server.value.serverId,recordVersion:server.value.recordVersion,epoch:taskEpoch,sequence:taskSequence,requestId:taskRequestId});
    stopDockerTaskPolling();
    dockerStarting.value=true; dockerTaskLoading.value=true; dockerTaskError.value=null;
    try {
      const result=await startDockerInstallation({schemaVersion:1,requestId:ticket.requestId,serverId:target.serverId,expectedRecordVersion:target.recordVersion,planId:plan.planId,operationId:plan.operationId,snapshotDigest:plan.preview.snapshotDigest,expiresAtUnixSeconds:plan.preview.expiresAtUnixSeconds});
      if(!currentTask()) return;
      if(!matchesDockerInstallStatus(ticket,target.hostId,result) || result.task?.taskId!==plan.operationId) throw new Error("Docker 安装任务响应身份不匹配");
      dockerTask.value=result.task;
      if(result.task) {const snapshot=toTaskSnapshot(result.task,target);if(snapshot) taskStore.receive(snapshot);}
      scheduleDockerTaskPolling();
    } catch(cause) {
      if(currentTask()) dockerTaskError.value=describeError(cause);
    } finally {if(currentTask()) {dockerStarting.value=false;dockerTaskLoading.value=false;}}
    if(currentTask() && dockerTaskError.value) await refreshDockerTask(true, plan.operationId);
  }
  watch([() => server.value?.serverId, () => server.value?.recordVersion, () => server.value?.hostId, () => preview.value], () => {
    epoch++;sequence++; data.value = null;error.value = null;stale.value = false;loading.value = false;
    plan.value = null; planOpen.value = false; initializing.value = false; outcomeUnknown.value = false;
    dockerEpoch++;dockerSequence++;dockerPreview.value=null;dockerPreviewOpen.value=false;dockerPreviewLoading.value=false;dockerPreviewError.value=null;
    taskEpoch++;taskSequence++;dockerTask.value=null;dockerStarting.value=false;dockerTaskLoading.value=false;dockerTaskError.value=null;stopDockerTaskPolling();
    void refresh().then(()=>{if(!preview.value) return refreshDockerTask();});
  },{immediate:true});
  onBeforeUnmount(() => {epoch++;sequence++;dockerEpoch++;dockerSequence++;taskEpoch++;taskSequence++;stopDockerTaskPolling();});
  return {data,loading,error,stale,refresh,plan,planOpen,initializing,outcomeUnknown,prepareInitialization,confirmInitialization,dockerPreview,dockerPreviewOpen,dockerPreviewLoading,dockerPreviewError,previewDockerInstall,dockerTask,dockerStarting,dockerTaskLoading,dockerTaskError,startDockerInstall,refreshDockerTask};
}
