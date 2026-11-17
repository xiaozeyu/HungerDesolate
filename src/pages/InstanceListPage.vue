<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import { Plus, RefreshCw, ArrowRight, ServerCog, Trash2, AlertTriangle } from "@lucide/vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NDataTable, NInput, NModal, type DataTableColumns } from "naive-ui";
import InstanceCreateDialog from "../components/InstanceCreateDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import IconButton from "../components/IconButton.vue";
import { notifySuccess } from "../naive/notify";
import { useWorkspace } from "../composables/useWorkspace";
import { useOperations } from "../composables/useOperations";
import { useInstanceList } from "../composables/useInstanceList";
import { instanceState, canInspectInstance, gameModeLabel, formatBeijingDateTime } from "../lib/instanceList";
import { deleteInstance, previewInstanceDelete, type InstanceDeletePreview } from "../api/instances";
import { describeError } from "../lib/commandError";
import type { UserError } from "../lib/commandError";
import type { InstanceSummary } from "../types/contracts";
const route=useRoute(), router=useRouter(), workspace=useWorkspace(), operations=useOperations();
const server=computed(()=>workspace.servers.value.find(item=>item.serverId===route.params.serverId) ?? null);
const {data,error,loading,stale,refresh}=useInstanceList(server,workspace.preview);
const createOpen=ref(false);
const deleteTarget=ref<InstanceSummary|null>(null), deletePlan=ref<InstanceDeletePreview|null>(null), deleteName=ref(""), deleteOperationId=ref(""), deleteBusy=ref(false), deletePreviewBusy=ref(false), deleteError=ref("");
const deleteDiagnostics=ref<UserError|null>(null);
watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>data.value?.registryRevision,()=>workspace.preview.value],()=>{createOpen.value=false;deleteTarget.value=null;deletePlan.value=null;deleteName.value="";deleteOperationId.value="";deleteBusy.value=false;deletePreviewBusy.value=false;deleteError.value="";deleteDiagnostics.value=null;});
const instances=computed(()=>data.value?.instances ?? []);
const rowKey = (item: InstanceSummary) => item.instanceId;
function playersLabel(item: InstanceSummary) {
  return item.runtimeStatus === "stopped" ? "—"
    : item.players === null ? (item.maxPlayers === null ? "未知" : `未知 / ${item.maxPlayers}`)
    : `${item.players} / ${item.maxPlayers ?? "未知"}`;
}
async function creationSucceeded(created:{serverId:string;hostId:string;instanceId:string}){
  await refresh();
  const instance=data.value?.instances.find(item=>item.instanceId===created.instanceId);
  notifySuccess("专服已创建", instance?.displayName);
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
function openDeletePreviewForPilot() {
  const item = instances.value[0];
  if (!item) return;
  deleteTarget.value = item;
  deletePlan.value = {
    schemaVersion: 1, requestId: crypto.randomUUID(),
    target: { serverId: server.value?.serverId ?? "", hostId: item.hostId, instanceId: item.instanceId },
    recordVersion: server.value?.recordVersion ?? 0, registryRevision: data.value?.registryRevision ?? 0,
    displayName: item.displayName, lifecycle: item.lifecycle,
    containers: [], networks: [], dataPresent: true, worldData: true, archived: false,
    blockers: ["CONTAINER_RUNNING"], allowed: true, planId: "pilot-plan",
  };
  deleteName.value = "";
  deleteDiagnostics.value = null;
  deleteError.value = "";
  deletePreviewBusy.value = false;
}
const columns = computed<DataTableColumns<InstanceSummary>>(() => {
  void stale.value;
  void loading.value;
  void workspace.preview.value;
  return [
  {
    title: "名称",
    key: "displayName",
    render: item => {
      const children = [h("div", { class: "table-primary" }, [h(ServerCog, { size: 17 }), h("strong", item.displayName)])];
      if (!item.manifestAvailable) children.push(h("small", { class: "table-sub" }, "配置未完成"));
      return h("div", null, children);
    },
  },
  { title: "模式", key: "gameMode", render: item => gameModeLabel(item.gameMode) },
  { title: "洞穴", key: "caves", render: item => item.cavesEnabled === null ? "未知" : item.cavesEnabled ? "启用" : "关闭" },
  {
    title: "状态",
    key: "status",
    render: item => {
      const state = instanceState(item);
      return h(StatusLabel, { tone: state.tone, label: state.label });
    },
  },
  { title: "玩家", key: "players", render: item => playersLabel(item) },
  { title: "版本", key: "build", render: item => h("span", { class: "mono" }, item.installedBuild ?? "未知") },
  { title: "最近运行采集", key: "lastSyncedAt", render: item => h("span", { class: "muted-text" }, formatBeijingDateTime(item.lastSyncedAt)) },
  {
    title: "",
    key: "actions",
    align: "center",
    render: item => h("div", { class: "row-actions" }, [
      h(IconButton, {
        label: canDeleteInstance(item) ? "预览删除专服" : "当前状态不可删除",
        disabled: !canDeleteInstance(item),
        tone: "danger",
        onClick: () => void openDelete(item),
      }, () => h(Trash2, { size: 15 })),
      h(IconButton, {
        label: "进入专服",
        disabled: stale.value || !canInspectInstance(item),
        onClick: () => enter(item),
      }, () => h(ArrowRight, { size: 15 })),
    ]),
  },
  ];
});
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="server?.displayName" title="专服列表">
      <template #actions>
        <NButton v-if="workspace.preview.value" size="small" @click="openDeletePreviewForPilot">预览删除弹窗</NButton>
        <NButton size="small" :disabled="loading || !server" @click="refresh"><template #icon><RefreshCw :size="16"/></template>{{loading?'读取中…':'刷新'}}</NButton>
        <NButton size="small" type="primary" :disabled="workspace.preview.value || !server?.hostId || !data || loading || stale || !!error" @click="createOpen=true"><template #icon><Plus :size="16"/></template>新建专服</NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <InstanceCreateDialog v-model:open="createOpen" :server="server" :registry-revision="data?.registryRevision ?? 0" @creation-succeeded="creationSucceeded"/>
    <div v-if="error" class="environment-error" role="alert"><strong>{{error.message}}</strong><ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul></div>
    <template v-if="loading || data">
      <div v-if="stale" class="stale-note"><StatusLabel tone="warning" label="结果已过期"/></div>
      <div class="table-wrap"><NDataTable :columns="columns" :data="instances" :bordered="false" :single-line="true" size="medium" :row-key="rowKey" :loading="loading">
        <template #empty><div class="table-empty"><ServerCog :size="20"/><strong>暂无专服</strong></div></template>
      </NDataTable></div>
    </template>
    <div v-else-if="!error" class="empty-state"><ServerCog :size="28"/><h2>尚未读取专服</h2></div>
    <NModal :show="!!deleteTarget" preset="card" :bordered="false" :closable="!deleteBusy" :mask-closable="!deleteBusy" :close-on-esc="!deleteBusy" title="删除专服" aria-label="删除专服" :style="{ width: 'min(680px, calc(100vw - 48px))' }" @update:show="value=>{if(!value&&!deleteBusy){deleteTarget=null;deletePlan=null}}">
      <div v-if="deleteTarget" class="form-stack">
        <p>专服：<strong>{{deleteTarget.displayName}}</strong></p>
        <p v-if="deletePreviewBusy" role="status">正在检查删除条件…</p>
        <template v-if="deletePlan">
          <dl class="creation-facts"><div><dt>容器</dt><dd>{{deletePlan.containers.length}} 个</dd></div><div><dt>数据</dt><dd>{{deletePlan.dataPresent?'将永久删除':'无实例文件'}}</dd></div></dl>
          <div v-if="deletePlan.blockers.length" class="delete-blockers" role="alert">
            <div class="delete-error-head"><AlertTriangle :size="16"/><strong>暂不能删除</strong></div>
            <ul><li v-for="blocker in deletePlan.blockers" :key="blocker">{{deleteBlockerLabel(blocker)}}</li></ul>
          </div>
          <label v-if="deletePlan.allowed">输入专服名称确认(该专服的备份也会一并删除)<NInput v-model:value="deleteName" placeholder="" :input-props="{ autocomplete: 'off' }" :disabled="deleteBusy"/></label>
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
      <template #footer><div class="dialog-footer-actions"><NButton :disabled="deleteBusy" @click="deleteTarget=null;deletePlan=null">取消</NButton><NButton v-if="deletePlan?.allowed" type="error" ghost :disabled="deleteBusy || deleteName!==deleteTarget?.displayName" @click="deleteSelectedInstance">{{deleteBusy?'正在删除…':'确认永久删除'}}</NButton></div></template>
    </NModal>
  </div>
</template>


<style scoped>
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
