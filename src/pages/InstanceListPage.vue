<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Plus, RefreshCw, ArrowRight, ServerCog, Trash2, AlertTriangle } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";
import InstanceCreateDialog from "../components/InstanceCreateDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useOperations } from "../composables/useOperations";
import { useInstanceList } from "../composables/useInstanceList";
import { useConnectionDialog } from "../composables/useConnectionDialog";
import { instanceState, canInspectInstance, gameModeLabel, formatBeijingDateTime } from "../lib/instanceList";
import { deleteInstance, previewInstanceDelete, type InstanceDeletePreview } from "../api/instances";
import { describeError } from "../lib/commandError";
import type { UserError } from "../lib/commandError";
import type { InstanceSummary } from "../types/contracts";
const route=useRoute(), router=useRouter(), workspace=useWorkspace(), operations=useOperations();
const { openConnection } = useConnectionDialog();
const server=computed(()=>workspace.servers.value.find(item=>item.serverId===route.params.serverId) ?? null);
const {data,error,loading,stale,refresh}=useInstanceList(server,workspace.preview);
const createOpen=ref(false);
const createdTarget=ref<{serverId:string;hostId:string;instanceId:string}|null>(null);
const deleteTarget=ref<InstanceSummary|null>(null), deletePlan=ref<InstanceDeletePreview|null>(null), deleteName=ref(""), deleteOperationId=ref(""), deleteBusy=ref(false), deletePreviewBusy=ref(false), deleteError=ref("");
const deleteDiagnostics=ref<UserError|null>(null);
watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>data.value?.registryRevision,()=>workspace.preview.value],()=>{createOpen.value=false;deleteTarget.value=null;deletePlan.value=null;deleteName.value="";deleteOperationId.value="";deleteBusy.value=false;deletePreviewBusy.value=false;deleteError.value="";deleteDiagnostics.value=null;createdTarget.value=null;});
const instances=computed(()=>data.value?.instances ?? []);
const running=computed(()=>instances.value.filter(item=>item.runtimeStatus==='running').length);
const unknown=computed(()=>instances.value.filter(item=>item.runtimeStatus==='unknown').length);
const summaryItems=computed(()=>[
  {label:"专服",value:instances.value.length},
  {label:"运行中",value:running.value,tone:"success" as const},
  {label:"未核实",value:unknown.value,tone:unknown.value?"warning" as const:"default" as const},
]);
async function creationSucceeded(created:{serverId:string;hostId:string;instanceId:string}){
  await refresh();
  createdTarget.value=created;
}
function enter(instance:InstanceSummary){
  if (!server.value || stale.value || !canInspectInstance(instance)) return;
  workspace.select({serverId:server.value.serverId,hostId:instance.hostId,instanceId:instance.instanceId});
  void router.push(`/hosts/${server.value.serverId}/instances/${instance.instanceId}/overview`);
}
function canDeleteInstance(instance:InstanceSummary){return !workspace.preview.value && !stale.value && !loading.value
  && !["deleting","retired"].includes(instance.lifecycle);}
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
  if(!deleteOperationId.value)deleteOperationId.value=crypto.randomUUID();
  const operationId=deleteOperationId.value;
  const instanceTarget={serverId:target.serverId,hostId:instance.hostId,instanceId:instance.instanceId};
  deleteBusy.value=true;deleteError.value="";
  const outcome=await operations.run({
    kind:"instanceDelete",target:instanceTarget,title:`删除专服 ${instance.displayName}`,stage:"删除专服",
    work:async setStage=>{
      const requestId=crypto.randomUUID();
      const response=await deleteInstance({schemaVersion:1,requestId,
        target:instanceTarget,
        expectedRecordVersion:target.recordVersion,expectedRegistryRevision:snapshot.registryRevision,
        planId:plan.planId,operationId,typedName:deleteName.value});
      setStage("验证删除结果");
      if(response.requestId!==requestId||response.recordVersion!==target.recordVersion
        ||response.target.serverId!==target.serverId||response.target.hostId!==instance.hostId||response.target.instanceId!==instance.instanceId
        ||!response.removed||response.registryRevision<=snapshot.registryRevision)
        throw new Error("删除结果目标不匹配");
      return response;
    },
  });
  deleteBusy.value=false;
  if(server.value?.serverId!==target.serverId||server.value.recordVersion!==target.recordVersion)return;
  if(outcome.error){deleteDiagnostics.value=outcome.error;deleteError.value=outcome.error.message;return;}
  deleteTarget.value=null;deletePlan.value=null;deleteName.value="";await refresh();
}
function deleteBlockerLabel(code:string){const labels:Record<string,string>={ACTIVE_TASK:"实例仍有未结束任务",TASK_STATUS_MISSING:"任务状态缺失，结果待核实",CONTAINER_RUNNING:"容器尚未全部停止",TASK_STATUS_UNKNOWN:"无法核对任务状态",RESOURCE_IDENTITY_INVALID:"Docker 资源归属不匹配",DATA_UNSAFE:"实例目录权限或文件类型不安全",DATA_CONFLICT:"归档目标已存在"};return labels[code]??"删除条件未满足";}
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="server?.displayName" title="专服列表"><template #actions><button class="button" :disabled="loading || !server" @click="refresh"><RefreshCw :size="16"/>{{loading?'读取中…':'刷新'}}</button><button class="button primary" :disabled="workspace.preview.value || !server?.hostId || !data || loading || stale || !!error" @click="createOpen=true"><Plus :size="16"/>新建专服</button></template></PageHeader>
    <PreviewNotice/>
    <p v-if="createdTarget" class="success-message" role="status">专服已创建<button class="text-button" @click="router.push(`/hosts/${createdTarget.serverId}/instances/${createdTarget.instanceId}/world`)">进入世界配置</button></p>
    <InstanceCreateDialog v-model:open="createOpen" :server="server" :registry-revision="data?.registryRevision ?? 0" @creation-succeeded="creationSucceeded"/>
    <div v-if="error" class="environment-error" role="alert"><strong>{{error.message}}</strong><ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul><button v-if="server" class="text-button" type="button" @click="openConnection(server.serverId)">连接设置</button><button v-if="server" class="text-button" @click="router.push(`/hosts/${server.serverId}/environment`)">主机环境</button></div>
    <div v-if="loading && !data" class="empty-state" role="status">读取专服列表…</div>
    <template v-else-if="data">
      <SummaryBar :items="summaryItems" label="专服摘要"/>
      <div v-if="stale" class="stale-note"><StatusLabel tone="warning" label="结果已过期"/></div>
      <div v-if="!instances.length" class="empty-state"><ServerCog :size="28"/><h2>暂无专服</h2></div>
      <div v-else class="table-wrap"><table><thead><tr><th>名称</th><th>模式</th><th>洞穴</th><th>状态</th><th>玩家</th><th>版本</th><th>最近运行采集</th><th/></tr></thead><tbody><tr v-for="item in instances" :key="item.instanceId"><td><div class="table-primary"><ServerCog :size="17"/><strong>{{item.displayName}}</strong></div><small v-if="!item.manifestAvailable" class="table-sub">配置未完成</small></td><td>{{gameModeLabel(item.gameMode)}}</td><td>{{item.cavesEnabled === null ? '未知' : item.cavesEnabled ? '启用' : '关闭'}}</td><td><StatusLabel :tone="instanceState(item).tone" :label="instanceState(item).label"/></td><td>{{item.runtimeStatus==='stopped'?'—':item.players===null?(item.maxPlayers===null?'未知':`未知 / ${item.maxPlayers}`):`${item.players} / ${item.maxPlayers ?? '未知'}`}}</td><td class="mono">{{item.installedBuild ?? '未知'}}</td><td class="muted-text">{{formatBeijingDateTime(item.lastSyncedAt)}}</td><td class="table-action"><div class="row-actions"><button class="icon-button" type="button" :aria-label="`预览删除专服 ${item.displayName}`" :title="canDeleteInstance(item) ? '预览删除专服' : '当前状态不可删除'" :disabled="!canDeleteInstance(item)" @click="openDelete(item)"><Trash2 :size="15"/></button><button class="text-button" :disabled="stale || !canInspectInstance(item)" @click="enter(item)">进入 <ArrowRight :size="15"/></button></div></td></tr></tbody></table></div>
    </template>
    <div v-else-if="!error" class="empty-state"><ServerCog :size="28"/><h2>尚未读取专服</h2></div>
    <AppDialog :open="!!deleteTarget" title="删除专服" @update:open="value=>{if(!value&&!deleteBusy){deleteTarget=null;deletePlan=null}}">
      <div v-if="deleteTarget" class="form-stack">
        <p>专服：<strong>{{deleteTarget.displayName}}</strong></p>
        <p v-if="deletePreviewBusy" role="status">正在检查删除条件…</p>
        <template v-if="deletePlan">
          <dl class="creation-facts"><div><dt>容器</dt><dd>{{deletePlan.containers.length}} 个</dd></div><div><dt>数据</dt><dd>{{deletePlan.dataPresent?'将永久删除':'无实例文件'}}</dd></div></dl>
          <div v-if="deletePlan.blockers.length" class="delete-blockers" role="alert">
            <div class="delete-error-head"><AlertTriangle :size="16"/><strong>暂不能删除</strong></div>
            <ul><li v-for="blocker in deletePlan.blockers" :key="blocker">{{deleteBlockerLabel(blocker)}}</li></ul>
          </div>
          <label v-if="deletePlan.allowed">输入专服名称确认<input v-model="deleteName" autocomplete="off" :disabled="deleteBusy"/></label>
        </template>
        <div v-if="deleteError" class="delete-error" role="alert">
          <div class="delete-error-head"><AlertTriangle :size="16"/><strong>删除未完成</strong></div>
          <p class="delete-error-message">{{deleteError}}</p>
          <div v-if="deleteDiagnostics?.details?.remoteCode || deleteDiagnostics?.details?.pythonException || deleteDiagnostics?.details?.pythonLine || deleteDiagnostics?.details?.errno" class="delete-error-meta">
            <span v-if="deleteDiagnostics?.details?.remoteCode">错误码 <code>{{deleteDiagnostics.details.remoteCode}}</code></span>
            <span v-if="deleteDiagnostics?.details?.pythonException">异常 <code>{{deleteDiagnostics.details.pythonException}}</code></span>
            <span v-if="deleteDiagnostics?.details?.pythonLine">脚本行 <code>{{deleteDiagnostics.details.pythonLine}}</code></span>
            <span v-if="deleteDiagnostics?.details?.errno">errno <code>{{deleteDiagnostics.details.errno}}</code></span>
          </div>
          <div v-if="deleteDiagnostics?.recoverySteps.length" class="delete-error-steps">
            <span>处理建议</span>
            <ol><li v-for="step in deleteDiagnostics.recoverySteps" :key="step">{{step}}</li></ol>
          </div>
        </div>
      </div>
      <template #footer><button class="button" :disabled="deleteBusy" @click="deleteTarget=null;deletePlan=null">取消</button><button v-if="deletePlan?.allowed" class="button danger" :disabled="deleteBusy || deleteName!==deleteTarget?.displayName" @click="deleteSelectedInstance">{{deleteBusy?'正在删除…':'确认并归档删除'}}</button></template>
    </AppDialog>
  </div>
</template>


<style scoped>
.row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-height: 28px; }
.delete-blockers, .delete-error { border: 1px solid color-mix(in srgb, var(--danger) 28%, var(--border)); background: color-mix(in srgb, var(--danger) 4%, var(--surface)); padding: 14px 16px; }
.delete-error { display: grid; gap: 10px; }
.delete-error-head { display: flex; align-items: center; gap: 8px; color: var(--danger); font-size: 12px; }
.delete-error-head svg { flex: 0 0 auto; }
.delete-blockers ul, .delete-error-steps ol { margin: 8px 0 0; padding-left: 20px; color: var(--text-secondary); font-size: 12px; }
.delete-blockers li + li, .delete-error-steps li + li { margin-top: 5px; }
.delete-error-message { margin: 0; color: var(--text); font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; }
.delete-error-meta { display: flex; flex-wrap: wrap; gap: 6px 14px; color: var(--text-tertiary); font-size: 10px; }
.delete-error-meta code { margin-left: 4px; color: var(--text-secondary); font-size: 10px; }
.delete-error-steps > span { color: var(--text-tertiary); font-size: 9px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; }
</style>
