<script setup lang="ts">
import { computed, toRef } from "vue";
import { useRouter } from "vue-router";
import { NButton, NCheckbox, NInput } from "naive-ui";
import AppDialog from "./AppDialog.vue";
import AppSelect from "./AppSelect.vue";
import StatusLabel from "./StatusLabel.vue";
import ErrorNotice from "./ErrorNotice.vue";
import { notifyError } from "../naive/notify";
import { useInstanceCreation } from "../composables/useInstanceCreation";
import { creationBlockerLabel } from "../lib/instanceCreation";
import { creationTaskErrorLabel, creationTaskStageLabel } from "../lib/instanceCreationTask";
import type { ServerSummary, InstanceTarget } from "../types/contracts";
const props=defineProps<{open:boolean;server:ServerSummary|null;registryRevision:number}>();
const emit=defineEmits<{"update:open":[value:boolean];"creationSucceeded":[target:InstanceTarget]}>();
const router=useRouter();
const {fields,task,releaseReady,selectedReleaseId,releaseOptions,releaseLoading,releaseError,error,loading,creating,create,refreshReleaseCatalog}=useInstanceCreation(computed(()=>props.server),computed(()=>props.registryRevision),toRef(props,"open"),(event,target)=>{if(event==="creationSucceeded"){emit("update:open",false);emit("creationSucceeded",target);}});
const availableReleases=computed(()=>releaseOptions().filter(item=>item.verified).map(item=>({
  value:item.releaseId,label:`DST ${item.dstBuild} · ${item.releaseId.slice(0,8)}`,
})));
const selectedRelease=computed(()=>releaseOptions().find(item=>item.releaseId===selectedReleaseId.value));
const taskTone=computed(()=>task.value?.status==="failed"?"danger":task.value?.status==="succeeded"?"success":"warning");
const taskStatusLabel=computed(()=>{
  const labels:Record<string,string>={failed:"创建失败",succeeded:"容器已验证",interrupted:"待核实",cancelled:"已取消"};
  return labels[task.value?.status??""]??"创建容器中";
});
const taskProgressLabel=computed(()=>{
  const current=task.value;
  if(!current)return "";
  if(current.status==="succeeded")return "容器环境已完成验证，可进入世界配置";
  if(current.status==="cancelled")return "创建任务已取消";
  return creationTaskStageLabel(current.stage);
});
const taskError = computed(() => {
  const current = task.value;
  const failure = current?.error;
  if (!current || !failure || !["failed", "interrupted"].includes(current.status)) return null;
  const details = failure.details ?? {};
  return {
    message: [creationTaskErrorLabel(failure.code), failure.message, `${creationTaskStageLabel(current.stage)} · ${failure.code}`]
      .filter((line, index, lines) => line && lines.indexOf(line) === index).join("\n"),
    recoverySteps: [
      ...[details.composeDiagnostic, details.dockerDiagnostic].filter((value): value is string => typeof value === "string"),
      ...(failure.recoverySteps ?? []),
      `任务 ${current.taskId}`,
    ],
  };
});
const blockers=computed(()=>{
  const value=error.value?.details?.blockers;
  return Array.isArray(value)?value.filter((item):item is string=>typeof item==="string"):[];
});
function goRelease(){if(!props.server)return;emit("update:open",false);void router.push(`/hosts/${props.server.serverId}/releases`);}
</script>
<template>
  <AppDialog :open="open" title="新建专服" @update:open="emit('update:open',$event)">
    <form v-if="!task" id="instance-create-form" class="creation-form" @submit.prevent="create">
      <div class="creation-form-heading"><span class="eyebrow">专服设置</span><h3>配置基础运行方式</h3></div>
      <div class="creation-settings-grid">
        <label class="creation-field creation-field-wide">专服名称<NInput v-model:value="fields.displayName" placeholder="生存一服" maxlength="128" :input-props="{ autocomplete: 'off' }" :disabled="loading"/></label>
        <div class="creation-field creation-field-wide">
          <div class="creation-release-heading"><span>DST Release</span><button class="text-button" type="button" :disabled="releaseLoading || loading" @click="refreshReleaseCatalog">{{releaseLoading?'读取中…':'刷新 Release'}}</button></div>
          <AppSelect v-model="selectedReleaseId" :options="availableReleases" label="DST Release" :placeholder="releaseLoading?'读取 Release…':'选择 Release'" :disabled="releaseLoading || loading || !availableReleases.length"/>
          <div v-if="selectedRelease && releaseReady()" class="creation-release-meta"><StatusLabel tone="success" label="基础验证通过"/><span class="mono" aria-label="Release ID">{{selectedRelease.releaseId}}</span></div>
          <ErrorNotice title="读取 DST Release 失败" :error="releaseError" :pending="releaseLoading"/>
          <p v-if="!releaseError && !releaseLoading && !availableReleases.length" class="muted-text" role="status">暂无通过基础验证的 Release。<button class="text-button" type="button" @click="goRelease">前往 Release 管理构建</button></p>
        </div>
        <fieldset class="creation-option-group"><legend>启动策略</legend><NCheckbox v-model:checked="fields.autostart" :disabled="loading" class="creation-switch">主机启动时自动拉起容器</NCheckbox></fieldset>
        <fieldset class="creation-option-group"><legend>是否启用洞穴</legend><NCheckbox v-model:checked="fields.cavesEnabled" :disabled="loading" class="creation-switch">启用 Caves 分片</NCheckbox></fieldset>
      </div>
    </form>
    <template v-else>
      <div class="creation-preview-heading">
        <div><span class="eyebrow">创建专服</span><h3>{{fields.displayName}}</h3></div>
        <StatusLabel :tone="taskTone" :label="taskStatusLabel"/>
      </div>
      <div class="creation-task-state" :class="task.status">
        <StatusLabel :tone="taskTone" :label="taskStatusLabel"/>
        <span>{{taskProgressLabel}}</span>
      </div>
      <ErrorNotice :key="task.taskId" title="创建专服未完成" :error="taskError"/>
      <details v-if="taskError && task.journal?.length">
        <summary>任务日志</summary>
        <ol class="task-journal">
          <li v-for="entry in task.journal" :key="entry.sequence">
            <time>{{ new Date(entry.at).toLocaleString() }}</time><span>{{ entry.stage }}</span>
            <StatusLabel :tone="entry.status === 'failed' ? 'danger' : entry.status === 'succeeded' ? 'success' : 'muted'" :label="entry.status"/>
          </li>
        </ol>
      </details>
    </template>
    <ErrorNotice title="未能创建专服" :pending="loading"
      :error="error && { ...error, recoverySteps: [...blockers.map(creationBlockerLabel), ...error.recoverySteps] }"/>
    <template #footer>
      <NButton v-if="taskError" text @click="notifyError('创建专服未完成', taskError, 0)">错误详情</NButton>
      <NButton @click="emit('update:open',false)">关闭</NButton>
      <NButton v-if="!task" attr-type="submit" form="instance-create-form" type="primary" :disabled="loading || creating || !releaseReady()" :loading="loading || creating">{{loading?'检查并创建中…':'创建专服'}}</NButton>
    </template>
  </AppDialog>
</template>
