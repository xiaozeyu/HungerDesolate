<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Plus, RefreshCw, ArrowRight, ServerCog, Trash2, FileText } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";
import InstanceCreateDialog from "../components/InstanceCreateDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useInstanceList } from "../composables/useInstanceList";
import { useTasks } from "../composables/useTasks";
import { instanceState, canInspectInstance } from "../lib/instanceList";
import { creationTaskErrorLabel, creationTaskStageLabel, isCreationTask } from "../lib/instanceCreationTask";
import { deleteInstance, previewInstanceDelete, type InstanceDeletePreview } from "../api/instances";
import { describeError } from "../lib/commandError";
import type { UserError } from "../lib/commandError";
import type { InstanceSummary } from "../types/contracts";
import type { TaskSnapshot } from "../types/contracts";
const route=useRoute(), router=useRouter(), workspace=useWorkspace();
const server=computed(()=>workspace.servers.value.find(item=>item.serverId===route.params.serverId) ?? null);
const {data,error,loading,stale,refresh}=useInstanceList(server,workspace.preview);
const taskStore=useTasks();
const selectedTask=ref<TaskSnapshot|null>(null);
const createOpen=ref(false);
const deleteTarget=ref<InstanceSummary|null>(null), deletePlan=ref<InstanceDeletePreview|null>(null), deleteName=ref(""), deleteOperationId=ref(""), deleteBusy=ref(false), deletePreviewBusy=ref(false), deleteError=ref("");
const deleteDiagnostics=ref<UserError|null>(null);
watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>data.value?.registryRevision,()=>workspace.preview.value],()=>{createOpen.value=false;deleteTarget.value=null;deletePlan.value=null;deleteName.value="";deleteOperationId.value="";deleteBusy.value=false;deletePreviewBusy.value=false;deleteError.value="";deleteDiagnostics.value=null;selectedTask.value=null;});
const instances=computed(()=>data.value?.instances ?? []);
const running=computed(()=>instances.value.filter(item=>item.runtimeStatus==='running').length);
const unknown=computed(()=>instances.value.filter(item=>item.runtimeStatus==='unknown').length);
function enter(instance:InstanceSummary){
  if (!server.value || stale.value || !canInspectInstance(instance)) return;
  workspace.select({serverId:server.value.serverId,hostId:instance.hostId,instanceId:instance.instanceId});
  void router.push(`/hosts/${server.value.serverId}/instances/${instance.instanceId}/overview`);
}
function taskFor(instance:InstanceSummary){
  const task=[...taskStore.tasks.value.values()].find(task=>isCreationTask(task)
    && "hostId" in task.target
    && task.target.hostId===instance.hostId
    && "instanceId" in task.target && task.target.instanceId===instance.instanceId);
  return task?.status==="succeeded"?undefined:task;
}
function canDeleteInstance(instance:InstanceSummary){return !workspace.preview.value && !stale.value && !loading.value
  && !["deleting","deleteFailed","retired"].includes(instance.lifecycle);}
async function openDelete(instance:InstanceSummary){
  const target=server.value,snapshot=data.value;
  if(!canDeleteInstance(instance)||!target?.hostId||!snapshot)return;
  deleteTarget.value=instance;deletePlan.value=null;deleteName.value="";deleteOperationId.value="";deleteError.value="";deleteDiagnostics.value=null;deletePreviewBusy.value=true;
  try{
    const requestId=crypto.randomUUID();
    const response=await previewInstanceDelete({schemaVersion:1,requestId,
      target:{serverId:target.serverId,hostId:instance.hostId,instanceId:instance.instanceId},
      expectedRecordVersion:target.recordVersion,expectedRegistryRevision:snapshot.registryRevision});
    if(deleteTarget.value?.instanceId!==instance.instanceId||server.value?.serverId!==target.serverId
      ||server.value.recordVersion!==target.recordVersion||data.value?.registryRevision!==snapshot.registryRevision)return;
    if(response.requestId!==requestId||response.recordVersion!==target.recordVersion
      ||response.target.serverId!==target.serverId||response.target.hostId!==instance.hostId
      ||response.target.instanceId!==instance.instanceId||response.registryRevision!==snapshot.registryRevision)
      throw new Error("删除预览目标或注册版本不匹配");
    deletePlan.value=response;
  }catch(cause){if(deleteTarget.value?.instanceId===instance.instanceId){deleteDiagnostics.value=describeError(cause);deleteError.value=deleteDiagnostics.value.message;}}
  finally{deletePreviewBusy.value=false;}
}
async function deleteSelectedInstance(){
  const instance=deleteTarget.value,target=server.value,snapshot=data.value,plan=deletePlan.value;
  if(!instance||!target?.hostId||!snapshot||!plan||!plan.allowed||deleteBusy.value||deleteName.value!==instance.displayName)return;
  deleteBusy.value=true;deleteError.value="";
  try{
    if(!deleteOperationId.value)deleteOperationId.value=crypto.randomUUID();
    const requestId=crypto.randomUUID();
    const response=await deleteInstance({schemaVersion:1,requestId,
      target:{serverId:target.serverId,hostId:instance.hostId,instanceId:instance.instanceId},
      expectedRecordVersion:target.recordVersion,expectedRegistryRevision:snapshot.registryRevision,
      planId:plan.planId,operationId:deleteOperationId.value,typedName:deleteName.value});
    if(response.requestId!==requestId||response.recordVersion!==target.recordVersion
      ||response.target.serverId!==target.serverId||response.target.hostId!==instance.hostId||response.target.instanceId!==instance.instanceId
      ||!response.removed||response.registryRevision<=snapshot.registryRevision)
      throw new Error("删除结果目标不匹配");
    if(server.value?.serverId!==target.serverId||server.value.recordVersion!==target.recordVersion)return;
    deleteTarget.value=null;deletePlan.value=null;deleteName.value="";await refresh();
  }catch(cause){deleteDiagnostics.value=describeError(cause);deleteError.value=deleteDiagnostics.value.message;}
  finally{deleteBusy.value=false;}
}
function deleteBlockerLabel(code:string){const labels:Record<string,string>={ACTIVE_TASK:"实例仍有未结束任务",TASK_STATUS_MISSING:"任务状态缺失，结果待核实",CONTAINER_RUNNING:"实例仍有运行中的容器",CONTAINER_REMOVING:"实例容器正在删除，请等待 Docker 状态稳定",BACKUP_REQUIRED:"发现世界数据，需先完成保护备份",TASK_STATUS_UNKNOWN:"无法核对任务状态",RESOURCE_IDENTITY_INVALID:"Docker 资源归属不匹配",DATA_UNSAFE:"实例目录权限或文件类型不安全",DATA_CONFLICT:"归档目标已存在"};return labels[code]??"删除条件未满足";}
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="server?.displayName" title="专服列表"><template #actions><button class="button" :disabled="loading || !server" @click="refresh"><RefreshCw :size="16"/>{{loading?'读取中…':'刷新'}}</button><button class="button primary" :disabled="workspace.preview.value || !server?.hostId || !data || loading || stale || !!error" @click="createOpen=true"><Plus :size="16"/>新建专服</button></template></PageHeader>
    <PreviewNotice/>
    <InstanceCreateDialog v-model:open="createOpen" :server="server" :registry-revision="data?.registryRevision ?? 0" @creation-succeeded="refresh"/>
    <div v-if="error" class="environment-error" role="alert"><strong>{{error.message}}</strong><ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul><button v-if="server" class="text-button" @click="router.push(`/hosts/${server.serverId}/connection`)">连接设置</button><button v-if="server" class="text-button" @click="router.push(`/hosts/${server.serverId}/environment`)">主机环境</button></div>
    <div v-if="loading && !data" class="empty-state" role="status">读取专服列表…</div>
    <template v-else-if="data">
      <section class="summary-line"><div><span class="metric-number">{{instances.length}}</span><span>个专服</span></div><span class="summary-divider"/><div><span>{{running}} 个运行中</span><span v-if="unknown">{{unknown}} 个未核实</span></div><StatusLabel v-if="stale" tone="warning" label="结果已过期"/></section>
      <div class="list-sync-meta"><span>注册版本 {{data.registryRevision}}</span><span>{{new Date(data.capturedAtUnixSeconds*1000).toLocaleString()}}</span></div>
      <div v-if="!instances.length" class="empty-state"><ServerCog :size="28"/><h2>暂无专服</h2></div>
      <div v-else class="table-wrap"><table><thead><tr><th>名称</th><th>模式</th><th>洞穴</th><th>状态</th><th>玩家</th><th>版本</th><th>最近运行采集</th><th/></tr></thead><tbody><tr v-for="item in instances" :key="item.instanceId"><td><div class="table-primary"><ServerCog :size="17"/><strong>{{item.displayName}}</strong></div><small v-if="!item.manifestAvailable" class="table-sub">配置未完成</small></td><td>{{item.gameMode ?? '未知'}}</td><td>{{item.cavesEnabled === null ? '未知' : item.cavesEnabled ? '启用' : '关闭'}}</td><td><StatusLabel :tone="instanceState(item).tone" :label="instanceState(item).label"/><small v-if="taskFor(item)" class="table-sub">{{taskFor(item)!.status==='failed' ? creationTaskErrorLabel(taskFor(item)!.error?.code) : creationTaskStageLabel(taskFor(item)!.stage)}}</small><button v-if="taskFor(item)" class="text-button" type="button" @click="selectedTask=taskFor(item)??null"><FileText :size="14"/>任务记录</button></td><td>{{item.runtimeStatus==='stopped'?'—':item.players===null?(item.maxPlayers===null?'未知':`未知 / ${item.maxPlayers}`):`${item.players} / ${item.maxPlayers ?? '未知'}`}}</td><td class="mono">{{item.installedBuild ?? '未知'}}</td><td class="muted-text">{{item.lastSyncedAt ?? '未采集'}}</td><td class="table-action"><button v-if="canDeleteInstance(item)" class="icon-button" type="button" :aria-label="`预览删除专服 ${item.displayName}`" title="预览删除专服" @click="openDelete(item)" ><Trash2 :size="15"/></button><button class="text-button" :disabled="stale || !canInspectInstance(item)" @click="enter(item)">进入 <ArrowRight :size="15"/></button></td></tr></tbody></table></div>
    </template>
    <div v-else-if="!error" class="empty-state"><ServerCog :size="28"/><h2>尚未读取专服</h2></div>
    <AppDialog :open="!!deleteTarget" title="删除专服" description="只移除已核实归属且未运行的 Docker 容器与网络；启动失败且反复重启的受管容器会先强制停止。实例目录会完整移入 retired 归档；不删除 DST 镜像和其他实例资源。" @update:open="value=>{if(!value&&!deleteBusy){deleteTarget=null;deletePlan=null}}">
      <div v-if="deleteTarget" class="form-stack">
        <p>专服：<strong>{{deleteTarget.displayName}}</strong></p>
        <p v-if="deletePreviewBusy" role="status">正在检查任务、容器、网络和归档目录…</p>
        <template v-if="deletePlan">
          <dl class="creation-facts"><div><dt>容器</dt><dd>{{deletePlan.containers.length}} 个</dd></div><div><dt>网络</dt><dd>{{deletePlan.networks.map(item=>item.name).join('、') || '无'}}</dd></div><div><dt>数据</dt><dd>{{deletePlan.dataPresent?'将保留到 retired 归档':'无实例文件'}}</dd></div></dl>
          <ul v-if="deletePlan.containers.length" class="delete-resource-list"><li v-for="container in deletePlan.containers" :key="container.id">{{container.role==='master'?'Master':'Caves'}} · {{container.status}}</li></ul>
          <ul v-if="deletePlan.blockers.length" class="environment-error"><li v-for="blocker in deletePlan.blockers" :key="blocker">{{deleteBlockerLabel(blocker)}}</li></ul>
          <label v-if="deletePlan.allowed">输入专服名称确认<input v-model="deleteName" autocomplete="off" :disabled="deleteBusy"/></label>
        </template>
        <div v-if="deleteError" class="environment-error" role="alert"><strong>{{deleteError}}</strong><small v-if="deleteDiagnostics?.details?.remoteCode">远端代码 {{deleteDiagnostics.details.remoteCode}}</small><small v-if="deleteDiagnostics?.details?.pythonException">{{deleteDiagnostics.details.pythonException}}<template v-if="deleteDiagnostics.details.pythonLine"> · 脚本行 {{deleteDiagnostics.details.pythonLine}}</template><template v-if="deleteDiagnostics.details.errno"> · errno {{deleteDiagnostics.details.errno}}</template></small><ul v-if="deleteDiagnostics?.recoverySteps.length"><li v-for="step in deleteDiagnostics.recoverySteps" :key="step">{{step}}</li></ul></div>
      </div>
      <template #footer><button class="button" :disabled="deleteBusy" @click="deleteTarget=null;deletePlan=null">取消</button><button v-if="deletePlan?.allowed" class="button danger" :disabled="deleteBusy || deleteName!==deleteTarget?.displayName" @click="deleteSelectedInstance">{{deleteBusy?'正在删除…':'确认并归档删除'}}</button></template>
    </AppDialog>
    <AppDialog :open="!!selectedTask" title="创建任务记录" @update:open="value=>{if(!value)selectedTask=null}">
      <div v-if="selectedTask" class="form-stack">
        <p><StatusLabel :tone="selectedTask.status==='failed'?'danger':selectedTask.status==='succeeded'?'success':'warning'" :label="selectedTask.status==='failed'?'失败':selectedTask.status==='succeeded'?'成功':selectedTask.status==='interrupted'?'待核实':'进行中'"/> {{creationTaskStageLabel(selectedTask.stage)}}</p>
        <p v-if="selectedTask.error?.message" class="environment-error">{{selectedTask.error.message}}</p>
        <pre v-if="typeof selectedTask.error?.details.composeDiagnostic==='string'">{{selectedTask.error.details.composeDiagnostic}}</pre>
        <pre v-if="typeof selectedTask.error?.details.dockerDiagnostic==='string'">{{selectedTask.error.details.dockerDiagnostic}}</pre>
        <ol v-if="selectedTask.journal?.length" class="task-journal"><li v-for="entry in selectedTask.journal" :key="entry.sequence"><time>{{new Date(entry.at).toLocaleString()}}</time><span>{{entry.stage}}</span><StatusLabel :tone="entry.status==='failed'?'danger':entry.status==='succeeded'?'success':'muted'" :label="entry.status"/></li></ol>
        <p v-else class="muted-text">暂无阶段记录</p>
      </div>
      <template #footer><button class="button" @click="selectedTask=null">关闭</button></template>
    </AppDialog>
  </div>
</template>
