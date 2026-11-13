<script setup lang="ts">
import { computed, toRef } from "vue";
import { useRouter } from "vue-router";
import { NButton, NCheckbox, NInput } from "naive-ui";
import AppDialog from "./AppDialog.vue";
import AppSelect from "./AppSelect.vue";
import StatusLabel from "./StatusLabel.vue";
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
          <p v-if="releaseError" class="creation-release-error" role="alert">{{releaseError.message}}</p>
          <p v-else-if="!releaseLoading && !availableReleases.length" class="muted-text" role="status">暂无通过基础验证的 Release。<button class="text-button" type="button" @click="goRelease">前往 Release 管理构建</button></p>
        </div>
        <fieldset class="creation-option-group"><legend>启动策略</legend><NCheckbox v-model:checked="fields.autostart" :disabled="loading" class="creation-switch">主机启动时自动拉起容器</NCheckbox></fieldset>
        <fieldset class="creation-option-group"><legend>是否启用洞穴</legend><NCheckbox v-model:checked="fields.cavesEnabled" :disabled="loading" class="creation-switch">启用 Caves 分片</NCheckbox></fieldset>
      </div>
    </form>
    <template v-else>
      <div class="creation-preview-heading">
        <div><span class="eyebrow">创建专服</span><h3>{{fields.displayName}}</h3></div>
        <StatusLabel :tone="task.status==='failed'?'danger':task.status==='succeeded'?'success':'warning'" :label="task.status==='failed'?'创建失败':task.status==='succeeded'?'容器已验证':'创建容器中'"/>
      </div>
      <div class="creation-task-state" :class="task.status">
        <StatusLabel :tone="task.status==='failed'?'danger':task.status==='succeeded'?'success':'warning'" :label="task.status==='failed'?'创建失败':task.status==='succeeded'?'容器已验证':'创建容器中'"/>
        <span>{{task.status==='failed'?creationTaskErrorLabel(task.error?.code):task.status==='succeeded'?'容器环境已完成验证，可进入世界配置':creationTaskStageLabel(task.stage)}}</span>
      </div>
      <div v-if="task.status==='failed' && task.error" class="environment-error" role="alert">
        <strong>{{creationTaskStageLabel(task.stage)}} · {{task.error.code}}</strong>
        <p>{{task.error.message}}</p>
        <pre v-if="typeof task.error.details.composeDiagnostic==='string'">{{task.error.details.composeDiagnostic}}</pre>
        <pre v-if="typeof task.error.details.dockerDiagnostic==='string'">{{task.error.details.dockerDiagnostic}}</pre>
        <ul v-if="task.error.recoverySteps?.length"><li v-for="step in task.error.recoverySteps" :key="step">{{step}}</li></ul>
        <ol v-if="task.journal?.length" class="task-journal"><li v-for="entry in task.journal" :key="entry.sequence"><time>{{new Date(entry.at).toLocaleString()}}</time><span>{{entry.stage}}</span><StatusLabel :tone="entry.status==='failed'?'danger':entry.status==='succeeded'?'success':'muted'" :label="entry.status"/></li></ol>
        <small class="mono">任务 {{task.taskId}}</small>
      </div>
    </template>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{error.message}}</strong>
      <ul v-if="blockers.length"><li v-for="code in blockers" :key="code">{{creationBlockerLabel(code)}}</li></ul>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul>
    </div>
    <template #footer>
      <NButton @click="emit('update:open',false)">关闭</NButton>
      <NButton v-if="!task" attr-type="submit" form="instance-create-form" type="primary" :disabled="loading || creating || !releaseReady()" :loading="loading || creating">{{loading?'检查并创建中…':'创建专服'}}</NButton>
    </template>
  </AppDialog>
</template>
