<script setup lang="ts">
import { computed, toRef, watch } from "vue";
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
watch(()=>props.open,(open)=>{if(open){editPreview();} });
function nextStep(){
  if(!fields.displayName.trim()){error.value={code:"INPUT_INVALID",message:"名称不能为空",recoverySteps:[],field:"displayName"};return;}
  if(!fields.autostart && fields.startAfterCreate){error.value={code:"INPUT_INVALID",message:"未自动启动时不能设置创建后启动",recoverySteps:[],field:"startAfterCreate"};return;}
  error.value=null;preview();
}
function edit(){editPreview();}
const recoveryActions=computed(()=>task.value?creationTaskRecoveryActions(task.value):[]);
function handleRecoveryAction(action:string){
  console.warn("恢复动作尚未实现",action);
  // TODO: 实现恢复动作：resumeCreate / cleanupCreate / inspectLogs
}
const {fields,result,task,releaseCatalog,releaseError,releaseReady,releasePreparing,prepareRelease,error,loading,creating,preview,create,refreshReleaseCatalog,edit:editPreview}=useInstanceCreation(computed(()=>props.server),computed(()=>props.registryRevision),toRef(props,"open"),(event,target)=>{if(event==="creationSucceeded"){emit("update:open",false);emit("creationSucceeded",target);}});
</script>
<template>
  <AppDialog :open="open" :title="result ? '创建预览' : '新建专服（预览）'" @update:open="emit('update:open',$event)">
    <div v-if="!result" class="creation-execution-gate"><StatusLabel tone="warning" label="仅预览"/><span>创建执行未开放，当前不会创建远端目录或容器</span></div>
    <div v-if="!result" class="creation-release-state"><StatusLabel :tone="releaseReady()?'success':'warning'" :label="releaseReady()?'release 已准备':'release 未准备'"/><span>{{releaseReady()?`${releaseCatalog?.releases.filter(item=>item.verified&&item.runtimeVerified).length} 个可用 release`:'需先准备并验证 DST release'}}</span><button class="text-button" type="button" @click="refreshReleaseCatalog">重新检查</button><button v-if="!releaseReady() && releaseCatalog?.releases.some(item=>item.verified)" class="text-button" type="button" :disabled="releasePreparing" @click="prepareRelease">{{releasePreparing?'验证中…':'验证 release'}}</button></div>
    <form v-if="!result" id="instance-create-form" class="creation-form" @submit.prevent="nextStep">
      <h3 class="creation-step-title">专服设置</h3>
      <div class="form-grid">
        <label class="creation-name">专服名称<input v-model="fields.displayName" autocomplete="off" placeholder="生存一服" required maxlength="128"/></label>
        <div class="form-field"><span>启动策略</span><label class="creation-switch"><input v-model="fields.autostart" type="checkbox"/>主机启动时启动</label><label class="creation-switch"><input v-model="fields.startAfterCreate" type="checkbox" :disabled="!fields.autostart"/>创建后启动</label></div>
        <div class="form-field"><span>容器拓扑</span><label class="creation-switch"><input v-model="fields.cavesEnabled" type="checkbox"/>创建洞穴容器</label></div>
        <div class="form-field"><span>端口策略</span><div class="creation-static-value">自动分配（固定公网）</div></div>
      </div>
    </form>
    <template v-else>
      <div class="creation-preview-state"><StatusLabel tone="warning" label="仅预览"/><StatusLabel tone="warning" label="端口未预留"/><span>创建执行未开放 · {{new Date(result.capturedAtUnixSeconds*1000).toLocaleString()}}</span></div>
      <div v-if="result.blockers.length" class="environment-error" role="alert"><ul><li v-for="code in result.blockers" :key="code">{{creationBlockerLabel(code)}}</li></ul></div>
      <dl class="environment-facts creation-facts">
        <div><dt>主机</dt><dd>{{server?.displayName}}</dd></div>
        <div><dt>专服名称</dt><dd>{{result.input.displayName}}</dd></div>
        <div><dt>启动策略</dt><dd>{{result.input.autostart?'自动启动':'手动启动'}} / {{result.input.startAfterCreate?'创建后启动':'创建后停止'}}</dd></div>
        <div><dt>容器拓扑</dt><dd>{{result.input.cavesEnabled?'Master + Caves':'Master'}}</dd></div>
        <div><dt>可用磁盘</dt><dd>{{formatBytes(result.availableBytes)}}</dd></div>
        <div><dt>Docker / Compose</dt><dd>{{result.dockerVersion ?? '未知'}} / {{result.composeVersion ?? '未知'}}</dd></div>
        <div><dt>建议目录</dt><dd class="mono">{{result.storagePath}}</dd></div>
        <div><dt>Compose 项目</dt><dd class="mono">{{result.composeProject}}</dd></div>
      </dl>
      <div v-if="result.ports.length" class="table-wrap creation-ports"><table><thead><tr><th>分片</th><th>用途</th><th>主机端口</th><th>容器端口</th></tr></thead><tbody><tr v-for="port in result.ports" :key="`${port.shard}:${port.purpose}`"><td>{{port.shard==='master'?'主世界':'洞穴'}}</td><td>{{purposes[port.purpose]}}</td><td class="mono">{{port.hostPort}}/udp</td><td class="mono">{{port.containerPort}}/udp</td></tr></tbody></table></div>
      <div v-if="task" class="creation-task-state" :class="task.status"><StatusLabel :tone="task.status==='failed'?'danger':task.status==='succeeded'?'success':'warning'" :label="task.status==='failed'?'创建失败':task.status==='succeeded'?'容器已验证':'创建容器中'"/><span>{{task.status==='failed'?creationTaskErrorLabel(task.error?.code):task.status==='succeeded'?'容器环境已完成验证，可进入世界配置':creationTaskStageLabel(task.stage)}}</span></div>
      <div v-if="task && task.status==='failed' && recoveryActions.length" class="creation-recovery-actions"><strong>恢复选项：</strong><button v-for="action in recoveryActions" :key="action.action" class="text-button" @click="handleRecoveryAction(action.action)">{{action.label}}</button></div>
      <div v-if="releaseError" class="environment-error" role="alert">{{releaseError.message}}</div>
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
