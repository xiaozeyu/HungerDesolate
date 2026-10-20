<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import AppDialog from "./AppDialog.vue";
import StatusLabel from "./StatusLabel.vue";
import { useInstanceCreation } from "../composables/useInstanceCreation";
import { creationBlockerLabel } from "../lib/instanceCreation";
import { creationTaskErrorLabel, creationTaskStageLabel, creationTaskRecoveryActions } from "../lib/instanceCreationTask";
import { formatBytes } from "../lib/environmentView";
import type { ServerSummary, InstanceTarget } from "../types/contracts";
const props=defineProps<{open:boolean;server:ServerSummary|null;registryRevision:number}>();
const emit=defineEmits<{"update:open":[value:boolean];"creationSucceeded":[target:InstanceTarget]}>();
const purposes={game:"游戏",steamAuth:"Steam 认证",steamMaster:"Steam 查询"};
const recoveryFeedback=ref("");
function nextStep(){
  if(!fields.displayName.trim()){error.value={code:"INPUT_INVALID",message:"名称不能为空",recoverySteps:[],field:"displayName"};return;}
  if(!fields.autostart && fields.startAfterCreate){error.value={code:"INPUT_INVALID",message:"未自动启动时不能设置创建后启动",recoverySteps:[],field:"startAfterCreate"};return;}
  error.value=null;preview();
}
function edit(){editPreview();}
const recoveryActions=computed(()=>task.value?creationTaskRecoveryActions(task.value):[]);
function handleRecoveryAction(action:string){
  const feedback:Record<string,string>={
    resumeCreate:"继续创建尚未接入，远端 staging 现场保持不变。",
    cleanupCreate:"创建现场清理尚未接入，未删除或移动任何数据。",
    inspectLogs:"以下为持久化阶段记录和错误详情；容器标准输出日志当前不采集。",
  };
  recoveryFeedback.value=feedback[action]??"此恢复操作暂不可用，远端现场未更改。";
}
const {fields,result,task,releaseReady,selectedReleaseId,releaseOptions,error,loading,creating,preview,create,refreshReleaseCatalog,edit:editPreview}=useInstanceCreation(computed(()=>props.server),computed(()=>props.registryRevision),toRef(props,"open"),(event,target)=>{if(event==="creationSucceeded"){emit("update:open",false);emit("creationSucceeded",target);}});

watch(()=>props.open,(open)=>{
  if(open){recoveryFeedback.value="";editPreview();return;}
});
</script>
<template>
  <AppDialog :open="open" :title="result ? '创建预览' : '新建专服'" @update:open="emit('update:open',$event)">
    <div v-if="!result && releaseReady()" class="creation-release-state"><StatusLabel tone="success" label="基础验证通过"/><span class="mono">DST {{releaseOptions().find(item=>item.releaseId===selectedReleaseId)?.dstBuild}}</span><button class="text-button" type="button" @click="refreshReleaseCatalog">重新检查</button></div>
    <form v-if="!result" id="instance-create-form" class="creation-form" @submit.prevent="nextStep">
      <div class="creation-form-heading"><span class="eyebrow">专服设置</span><h3>配置基础运行方式</h3></div>
      <div class="creation-settings-grid">
        <label class="creation-field creation-field-wide">专服名称<input v-model="fields.displayName" autocomplete="off" placeholder="生存一服" required maxlength="128"/></label>
        <fieldset class="creation-option-group"><legend>启动策略</legend><label class="creation-switch"><input v-model="fields.autostart" type="checkbox"/>主机启动时启动</label><label class="creation-switch"><input v-model="fields.startAfterCreate" type="checkbox" :disabled="!fields.autostart"/>创建后启动</label></fieldset>
        <fieldset class="creation-option-group"><legend>是否启用洞穴</legend><label class="creation-switch"><input v-model="fields.cavesEnabled" type="checkbox"/>启用 Caves 分片</label></fieldset>
      </div>
    </form>
    <template v-else>
      <div class="creation-preview-heading"><div><span class="eyebrow">创建预览</span><h3>{{result.input.displayName}}</h3></div><div class="creation-preview-badges"><StatusLabel tone="warning" label="端口未预留"/><span class="muted-text">{{new Date(result.capturedAtUnixSeconds*1000).toLocaleString()}}</span></div></div>
      <div v-if="result.blockers.length" class="environment-error" role="alert"><ul><li v-for="code in result.blockers" :key="code">{{creationBlockerLabel(code)}}</li></ul></div>
      <div class="creation-preview-grid">
        <section class="creation-panel creation-panel-wide"><h4>目标摘要</h4><dl class="creation-facts"><div><dt>主机</dt><dd>{{server?.displayName ?? '—'}}</dd></div><div><dt>DST Release</dt><dd class="mono">{{releaseOptions().find(item=>item.releaseId===selectedReleaseId)?.dstBuild ?? '—'}}</dd></div><div><dt>启动策略</dt><dd>{{result.input.autostart?'主机启动时启动':'手动启动'}} · {{result.input.startAfterCreate?'创建后启动':'创建后停止'}}</dd></div><div><dt>洞穴分片</dt><dd>{{result.input.cavesEnabled?'启用':'关闭'}}</dd></div></dl></section>
        <section class="creation-panel"><h4>资源计划</h4><dl class="creation-facts"><div><dt>可用磁盘</dt><dd>{{formatBytes(result.availableBytes)}}</dd></div><div><dt>Docker / Compose</dt><dd>{{result.dockerVersion ?? '未知'}} / {{result.composeVersion ?? '未知'}}</dd></div><div><dt>存储目录</dt><dd class="mono">{{result.storagePath}}</dd></div><div><dt>Compose 项目</dt><dd class="mono">{{result.composeProject}}</dd></div></dl></section>
        <section v-if="result.ports.length" class="creation-panel creation-panel-wide"><div class="creation-panel-heading"><h4>端口计划</h4><span class="muted-text">自动分配 · 同号发布</span></div><div class="table-wrap creation-ports"><table><thead><tr><th>分片</th><th>用途</th><th>UDP 端口</th></tr></thead><tbody><tr v-for="port in result.ports" :key="`${port.shard}:${port.purpose}`"><td>{{port.shard==='master'?'主世界':'洞穴'}}</td><td>{{purposes[port.purpose]}}</td><td class="mono">{{port.hostPort}}</td></tr></tbody></table></div></section>
      </div>
      <div v-if="task" class="creation-task-state" :class="task.status"><StatusLabel :tone="task.status==='failed'?'danger':task.status==='succeeded'?'success':'warning'" :label="task.status==='failed'?'创建失败':task.status==='succeeded'?'容器已验证':'创建容器中'"/><span>{{task.status==='failed'?creationTaskErrorLabel(task.error?.code):task.status==='succeeded'?'容器环境已完成验证，可进入世界配置':creationTaskStageLabel(task.stage)}}</span></div>
      <div v-if="task && task.status==='failed' && recoveryActions.length" class="creation-recovery-actions"><strong>恢复选项：</strong><button v-for="action in recoveryActions" :key="action.action" class="text-button" @click="handleRecoveryAction(action.action)">{{action.label}}</button></div>
      <div v-if="recoveryFeedback" class="environment-error" role="status" aria-live="polite">
        <p>{{recoveryFeedback}}</p>
        <template v-if="task?.error">
          <p v-if="task.error.message">{{task.error.message}}</p>
          <pre v-if="typeof task.error.details.composeDiagnostic==='string'">{{task.error.details.composeDiagnostic}}</pre>
          <pre v-if="typeof task.error.details.dockerDiagnostic==='string'">{{task.error.details.dockerDiagnostic}}</pre>
          <ul v-if="task.error.recoverySteps?.length"><li v-for="step in task.error.recoverySteps" :key="step">{{step}}</li></ul>
          <ol v-if="task.journal?.length" class="task-journal"><li v-for="entry in task.journal" :key="entry.sequence"><time>{{new Date(entry.at).toLocaleString()}}</time><span>{{entry.stage}}</span><StatusLabel :tone="entry.status==='failed'?'danger':entry.status==='succeeded'?'success':'muted'" :label="entry.status"/></li></ol>
        </template>
      </div>
    </template>
    <p v-if="error" class="environment-error" role="alert">{{error.message}}</p>
    <template #footer>
      <button class="button" @click="emit('update:open',false)">关闭</button>
      <button v-if="result" class="button" @click="edit">返回编辑</button>
      <button v-if="result && !task" class="button primary" :disabled="loading || creating" @click="preview">重新检查</button>
      <button v-if="result && !task" class="button primary" :disabled="loading || creating || !releaseReady()" @click="create">{{creating?'提交中…':releaseReady()?'创建容器':'等待 release'}}</button>
      <button v-if="!result" class="button primary" type="submit" form="instance-create-form" :disabled="loading" :aria-busy="loading">{{loading?'检查中…':'预览'}}</button>
    </template>
  </AppDialog>
</template>
