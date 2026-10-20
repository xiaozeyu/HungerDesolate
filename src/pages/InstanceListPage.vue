<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Plus, RefreshCw, ArrowRight, ServerCog, Trash2 } from "@lucide/vue";
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
import { cleanupOrphanInstance } from "../api/instances";
import { describeError } from "../lib/commandError";
import type { UserError } from "../lib/commandError";
import type { InstanceSummary } from "../types/contracts";
const route=useRoute(), router=useRouter(), workspace=useWorkspace();
const server=computed(()=>workspace.servers.value.find(item=>item.serverId===route.params.serverId) ?? null);
const {data,error,loading,stale,refresh}=useInstanceList(server,workspace.preview);
const taskStore=useTasks();
const createOpen=ref(false);
const cleanupTarget=ref<InstanceSummary|null>(null), cleanupName=ref(""), cleanupBusy=ref(false), cleanupError=ref("");
const cleanupDiagnostics=ref<UserError|null>(null);
watch([()=>server.value?.serverId,()=>server.value?.hostId,()=>server.value?.recordVersion,()=>data.value?.registryRevision,()=>workspace.preview.value],()=>{createOpen.value=false;cleanupTarget.value=null;cleanupName.value="";cleanupBusy.value=false;cleanupError.value="";cleanupDiagnostics.value=null;});
const instances=computed(()=>data.value?.instances ?? []);
const running=computed(()=>instances.value.filter(item=>item.runtimeStatus==='running').length);
const unknown=computed(()=>instances.value.filter(item=>item.runtimeStatus==='unknown').length);
function enter(instance:InstanceSummary){
  if (!server.value || stale.value || !canInspectInstance(instance)) return;
  workspace.select({serverId:server.value.serverId,hostId:instance.hostId,instanceId:instance.instanceId});
  void router.push(`/hosts/${server.value.serverId}/instances/${instance.instanceId}/overview`);
}
function taskFor(instance:InstanceSummary){
  return [...taskStore.tasks.value.values()].find(task=>isCreationTask(task)
    && "hostId" in task.target
    && task.target.hostId===instance.hostId
    && "instanceId" in task.target && task.target.instanceId===instance.instanceId);
}
function canCleanupOrphan(instance:InstanceSummary){return !workspace.preview.value && !stale.value && !loading.value
  && !["deleting","deleteFailed","retired"].includes(instance.lifecycle);}
async function cleanupOrphan(){
  const instance=cleanupTarget.value, target=server.value, snapshot=data.value;
  if(!instance||!target?.hostId||!snapshot||cleanupBusy.value||cleanupName.value!==instance.displayName)return;
  cleanupBusy.value=true;cleanupError.value="";
  try{
    await cleanupOrphanInstance({schemaVersion:1,requestId:crypto.randomUUID(),
      target:{serverId:target.serverId,hostId:instance.hostId,instanceId:instance.instanceId},
      expectedRecordVersion:target.recordVersion,expectedRegistryRevision:snapshot.registryRevision,typedName:cleanupName.value});
    cleanupTarget.value=null;cleanupName.value="";await refresh();
  }catch(cause){cleanupDiagnostics.value=describeError(cause);cleanupError.value=cleanupDiagnostics.value.message;}
  finally{cleanupBusy.value=false;}
}
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
      <div v-else class="table-wrap"><table><thead><tr><th>名称</th><th>模式</th><th>洞穴</th><th>状态</th><th>玩家</th><th>版本</th><th>最近运行采集</th><th/></tr></thead><tbody><tr v-for="item in instances" :key="item.instanceId"><td><div class="table-primary"><ServerCog :size="17"/><strong>{{item.displayName}}</strong></div><small v-if="!item.manifestAvailable" class="table-sub">配置未完成</small></td><td>{{item.gameMode ?? '未知'}}</td><td>{{item.cavesEnabled === null ? '未知' : item.cavesEnabled ? '启用' : '关闭'}}</td><td><StatusLabel :tone="instanceState(item).tone" :label="instanceState(item).label"/><small v-if="taskFor(item)" class="table-sub">{{taskFor(item)!.status==='failed' ? creationTaskErrorLabel(taskFor(item)!.error?.code) : creationTaskStageLabel(taskFor(item)!.stage)}}</small></td><td>{{item.runtimeStatus==='stopped'?'—':item.players===null?(item.maxPlayers===null?'未知':`未知 / ${item.maxPlayers}`):`${item.players} / ${item.maxPlayers ?? '未知'}`}}</td><td class="mono">{{item.installedBuild ?? '未知'}}</td><td class="muted-text">{{item.lastSyncedAt ?? '未采集'}}</td><td class="table-action"><button v-if="canCleanupOrphan(item)" class="icon-button" type="button" :aria-label="`移出专服列表 ${item.displayName}`" title="清理无容器专服记录" @click="cleanupTarget=item;cleanupName='';cleanupError=''" ><Trash2 :size="15"/></button><button class="text-button" :disabled="stale || !canInspectInstance(item)" @click="enter(item)">进入 <ArrowRight :size="15"/></button></td></tr></tbody></table></div>
    </template>
    <div v-else-if="!error" class="empty-state"><ServerCog :size="28"/><h2>尚未读取专服</h2></div>
    <AppDialog :open="!!cleanupTarget" title="清理无容器专服" description="远端会再次核对任务、Docker 容器和网络。纯空目录会直接清理；目录内有文件时会移入归档，避免误删配置或世界数据。发现运行资源或活动任务时操作会被拒绝。" @update:open="value=>{if(!value&&!cleanupBusy)cleanupTarget=null}">
      <div v-if="cleanupTarget" class="form-stack"><p>专服：<strong>{{cleanupTarget.displayName}}</strong></p><label>输入专服名称确认<input v-model="cleanupName" autocomplete="off" :disabled="cleanupBusy"/></label><div v-if="cleanupError" class="environment-error" role="alert"><strong>{{cleanupError}}</strong><small v-if="cleanupDiagnostics?.details?.pythonException">Python {{cleanupDiagnostics?.details?.pythonException}}<template v-if="cleanupDiagnostics?.details?.pythonLine"> · 行 {{cleanupDiagnostics?.details?.pythonLine}}</template></small><ul v-if="cleanupDiagnostics?.recoverySteps.length"><li v-for="step in cleanupDiagnostics.recoverySteps" :key="step">{{step}}</li></ul></div></div>
      <template #footer><button class="button" :disabled="cleanupBusy" @click="cleanupTarget=null">取消</button><button class="button danger" :disabled="cleanupBusy || !cleanupTarget || cleanupName!==cleanupTarget.displayName" @click="cleanupOrphan">{{cleanupBusy?'正在核查…':'确认移出列表'}}</button></template>
    </AppDialog>
  </div>
</template>
