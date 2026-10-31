<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { RefreshCw, CheckCircle2, AlertTriangle, CircleX, ChevronDown } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useHostEnvironment } from "../composables/useHostEnvironment";
import { canInitializeHost, formatBytes } from "../lib/environmentView";
import { dockerInstallBlockerLabel, dockerInstallPhaseLabel, dockerInstallStatusLabel, dockerInstallTaskErrorLabel } from "../lib/dockerInstallation";
const route = useRoute(), router = useRouter(), workspace = useWorkspace();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const {data,loading,error,stale,refresh,plan,planOpen,initializing,outcomeUnknown,prepareInitialization,confirmInitialization,dockerPreview,dockerPreviewOpen,dockerPreviewLoading,dockerPreviewError,previewDockerInstall,dockerTask,dockerStarting,dockerTaskLoading,dockerTaskError,startDockerInstall,clearDockerTask} = useHostEnvironment(server,workspace.preview,(hostId,recordVersion)=>workspace.bindHost(server.value?.serverId ?? "",recordVersion,hostId));
const blockers = computed(() => data.value?.checks.filter(check => check.level === "blocker").length ?? 0);
const engineLabels = {notInstalled:"未安装",permissionDenied:"权限不足",unavailable:"不可访问",available:"可访问"};
const checks = computed(() => [...(data.value?.checks ?? [])].sort((a,b)=>({blocker:0,warning:1,passed:2}[a.level]-{blocker:0,warning:1,passed:2}[b.level])));
const refreshButton = ref<HTMLButtonElement | null>(null);
const dockerPlanInvalid = computed(() => ["PLAN_STALE","PLAN_EXPIRED","INSTALL_HOST_CHANGED","INSTALL_SNAPSHOT_CHANGED","INSTALL_PREFLIGHT_BLOCKED"].includes(dockerTaskError.value?.code ?? ""));
async function confirm() {
  const target = server.value;
  await confirmInitialization();
  await nextTick();
  if (!planOpen.value && target?.serverId === server.value?.serverId && target?.recordVersion === server.value?.recordVersion) refreshButton.value?.focus();
}
async function retryDockerInstall() {
  clearDockerTask();
  await previewDockerInstall();
}
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="server?.displayName" title="主机环境"><template #actions><button v-if="!workspace.preview.value && data?.hostId && (data.docker.engineState === 'notInstalled' || data.docker.clientVersion && (data.docker.engineState !== 'available' || !data.docker.composeVersion))" class="button" :disabled="loading || initializing || stale || dockerPreviewLoading || dockerTaskLoading || dockerTask?.status === 'queued' || dockerTask?.status === 'running'" @click="previewDockerInstall">{{dockerTask?.status === 'queued' || dockerTask?.status === 'running' ? '安装中…' : dockerPreviewLoading ? '检查中…' : data.docker.engineState === 'available' ? '修复 Compose' : data.docker.engineState === 'unavailable' ? '修复 Docker' : '安装 Docker'}}</button><button v-if="!workspace.preview.value && data && (!data.hostId || plan?.status === 'incomplete')" class="button" :disabled="loading || initializing || stale || outcomeUnknown || !canInitializeHost(data)" @click="prepareInitialization">{{initializing ? '处理中…' : plan?.status === 'incomplete' ? '查看初始化' : '初始化'}}</button><button ref="refreshButton" class="button primary" :disabled="!server || loading || initializing" @click="refresh"><RefreshCw :size="16"/>{{loading ? '检查中…' : outcomeUnknown ? '重查原操作' : '重新检查'}}</button></template></PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="environment-error" role="alert"><strong>{{error.message}}</strong><ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul><button v-if="server" class="text-button" @click="router.push(`/hosts/${server.serverId}/connection`)">连接设置</button></div>
    <div v-if="dockerPreviewError" class="environment-error" role="alert">{{dockerPreviewError.message}}</div>
    <div v-if="dockerTaskError" class="environment-error" role="alert">{{dockerTaskError.message}}</div>
    <div v-if="loading && !data" class="empty-state" role="status">检查中…</div>
    <template v-else-if="data">
      <section class="summary-line" aria-live="polite">
        <div><span class="metric-number">{{blockers}}</span><span>项阻断</span></div>
        <span class="summary-divider"/>
        <div><span class="metric-number">{{data.checks.length}}</span><span>项检查</span></div>
        <span class="summary-divider"/>
        <div><span>{{data.os ?? data.kernel ?? '未知系统'}}</span><span class="mono">{{data.architecture ?? '未知架构'}}</span></div>
        <StatusLabel :tone="stale ? 'warning' : blockers ? 'danger' : 'success'" :label="stale ? '结果已过期' : blockers ? `${blockers} 项阻断` : '检查通过'"/>
        <span class="muted-text">{{new Date(data.checkedAtUnixSeconds*1000).toLocaleString()}}</span>
      </section>
      <div v-if="dockerTask && ['queued','running'].includes(dockerTask.status)" class="environment-summary" role="status"><StatusLabel tone="warning" :label="dockerInstallStatusLabel(dockerTask.status)"/><span>{{dockerInstallPhaseLabel(dockerTask.phase)}}</span><span class="muted-text">{{dockerTaskLoading ? '同步中…' : ''}}</span></div>
      <div class="overview-grid">
        <section class="section-block"><div class="section-heading"><div><h2>系统与资源</h2><p class="muted-text">主机身份、内存与磁盘</p></div></div><dl class="environment-facts"><div><dt>系统</dt><dd>{{data.os ?? data.kernel ?? '未知'}}</dd></div><div><dt>架构</dt><dd class="mono">{{data.architecture ?? '未知'}}</dd></div><div><dt>用户 UID</dt><dd class="mono">{{data.uid ?? '未知'}}</dd></div><div><dt>可用 / 总内存</dt><dd>{{formatBytes(data.memory?.availableBytes)}} / {{formatBytes(data.memory?.totalBytes)}}</dd></div><div><dt>可用 / 总磁盘</dt><dd>{{formatBytes(data.disk?.availableBytes)}} / {{formatBytes(data.disk?.totalBytes)}}</dd></div><div><dt>磁盘检查路径</dt><dd class="mono">{{data.disk?.path ?? '未知'}}</dd></div><div><dt>主机身份</dt><dd class="mono">{{data.hostId ?? '未初始化'}}</dd></div></dl></section>
        <section class="section-block"><div class="section-heading"><div><h2>Docker</h2><p class="muted-text">引擎、Compose 与数据目录</p></div></div><dl class="environment-facts"><div><dt>客户端</dt><dd>{{data.docker.clientVersion ?? '未知'}}</dd></div><div><dt>引擎</dt><dd>{{engineLabels[data.docker.engineState]}}</dd></div><div><dt>服务端版本</dt><dd class="mono">{{data.docker.serverVersion ?? '未知'}}</dd></div><div><dt>Compose</dt><dd class="mono">{{data.docker.composeVersion ?? '未知'}}</dd></div><div><dt>数据目录</dt><dd class="mono">{{data.docker.dataRoot ?? '未知'}}</dd></div><div><dt>可用 / 总磁盘</dt><dd>{{formatBytes(data.docker.disk?.availableBytes)}} / {{formatBytes(data.docker.disk?.totalBytes)}}</dd></div></dl></section>
      </div>
      <section class="section-block"><div class="section-heading"><div><h2>检查结果</h2><p class="muted-text">阻断、警告与恢复建议</p></div></div><div class="environment-checks"><details v-for="check in checks" :key="`${check.code}:${check.label}`" class="environment-check"><summary><CheckCircle2 v-if="check.level === 'passed'" class="icon-success" :size="17"/><CircleX v-else-if="check.level === 'blocker'" class="icon-danger" :size="17"/><AlertTriangle v-else class="icon-warning" :size="17"/><span>{{check.label}}</span><span class="environment-check-result">{{check.message}}</span><ChevronDown v-if="check.recoverySteps.length" class="environment-check-chevron" :size="15"/></summary><ul v-if="check.recoverySteps.length"><li v-for="step in check.recoverySteps" :key="step">{{step}}</li></ul></details></div></section>
    </template>
    <div v-else-if="!error" class="empty-state"><h2>尚未检查</h2></div>
    <AppDialog v-model:open="planOpen" title="初始化管理目录">
      <template v-if="plan">
        <dl class="environment-facts initialization-facts"><div><dt>主机</dt><dd>{{server?.displayName}}</dd></div><div><dt>主机身份</dt><dd class="mono">{{plan.hostId}}</dd></div><div><dt>操作编号</dt><dd class="mono">{{plan.operationId}}</dd></div><div><dt>状态</dt><dd>{{outcomeUnknown ? '待核对' : plan.status === 'incomplete' ? '未完成' : '待初始化'}}</dd></div></dl>
        <ul class="initialization-paths"><li v-for="path in plan.paths" :key="path" class="mono">{{path}}</li></ul>
        <div v-if="error" role="alert" class="form-error">{{error.message}}</div>
      </template>
      <template #footer><button class="button" @click="planOpen = false">关闭</button><button v-if="outcomeUnknown" class="button primary" :disabled="loading || initializing" @click="planOpen = false; refresh()">重查原操作</button><button v-else class="button primary" :disabled="!plan || initializing || loading" @click="confirm">{{initializing ? '初始化中…' : plan?.status === 'incomplete' ? '继续初始化' : '确认初始化'}}</button></template>
    </AppDialog>
    <AppDialog :open="dockerPreviewOpen" title="Docker 安装" @update:open="dockerPreviewOpen=$event">
      <template v-if="dockerPreview">
        <div v-if="!dockerTask && !dockerPlanInvalid" class="environment-summary"><StatusLabel :tone="dockerPreview.preview.status==='ready' ? 'success' : 'danger'" :label="dockerPreview.preview.status==='ready' ? '符合安装条件' : `${dockerPreview.preview.blockers.length} 项阻断`"/></div>
        <div v-if="dockerTask" class="environment-error" :role="['failed','interrupted'].includes(dockerTask.status) ? 'alert' : 'status'"><StatusLabel :tone="dockerTask.status==='succeeded' ? 'success' : ['failed','interrupted'].includes(dockerTask.status) ? 'danger' : 'warning'" :label="dockerInstallStatusLabel(dockerTask.status)"/><span> · {{dockerInstallPhaseLabel(dockerTask.phase)}}</span><p v-if="['failed','interrupted'].includes(dockerTask.status)">{{dockerInstallTaskErrorLabel(dockerTask.messageCode)}}</p><dl v-if="dockerTask.status==='succeeded'" class="environment-facts"><div v-for="(version,name) in dockerTask.installedVersions" :key="name"><dt>{{name}}</dt><dd class="mono">{{version}}</dd></div></dl></div>
        <p v-if="dockerTaskError" class="environment-error" role="alert">{{dockerTaskError.message}}</p>
        <dl class="environment-facts initialization-facts"><div><dt>发行版</dt><dd>{{dockerPreview.preview.distribution.id ?? '未知'}} {{dockerPreview.preview.distribution.version ?? ''}} · {{dockerPreview.preview.distribution.codename ?? '未知代号'}}</dd></div><div><dt>架构</dt><dd class="mono">{{dockerPreview.preview.distribution.architecture ?? '未知'}}</dd></div><div><dt>权限</dt><dd>UID {{dockerPreview.preview.uid}}</dd></div><div><dt>/var 可用空间</dt><dd>{{formatBytes(dockerPreview.preview.rootAvailableBytes)}}</dd></div><div><dt>Docker 仓库</dt><dd>{{dockerPreview.preview.repositoryReachable ? '可访问' : '不可访问'}}</dd></div><div><dt>APT 源</dt><dd>{{dockerPreview.preview.sourceState==='matching' ? '已配置' : '待添加'}}</dd></div></dl>
        <div class="section-block"><h3>软件包</h3><ul class="initialization-paths"><li v-for="name in dockerPreview.preview.packages" :key="name" class="mono">{{name}}</li></ul></div>
        <div v-if="dockerPreview.preview.blockers.length" class="environment-error" role="alert"><ul><li v-for="code in dockerPreview.preview.blockers" :key="code">{{dockerInstallBlockerLabel(code)}}</li></ul></div>
        <p v-else-if="!dockerTask" class="form-hint">将配置 Docker 官方 APT 源、更新索引、安装所列软件包并启动服务。Docker daemon 拥有 root 级主机权限；容器发布端口可能绕过 UFW 规则。<span v-if="Object.keys(dockerPreview.preview.installedDockerPackages).length">已安装：{{Object.keys(dockerPreview.preview.installedDockerPackages).join('、')}}</span></p>
      </template>
      <p v-if="dockerPreviewError" class="environment-error" role="alert">{{dockerPreviewError.message}}</p>
      <template #footer><button class="button" @click="dockerPreviewOpen=false">关闭</button><button v-if="dockerPreview?.preview.status==='ready' && !dockerTask && !dockerPlanInvalid" class="button primary" :disabled="dockerStarting || dockerTaskLoading || dockerPreviewLoading || initializing || loading" :aria-busy="dockerStarting || dockerTaskLoading" @click="startDockerInstall">{{dockerStarting ? '提交中…' : dockerTaskLoading ? '核对中…' : '确认安装 Docker'}}</button><button v-if="dockerPlanInvalid || dockerPreview?.preview.status==='ready' && dockerTask && ['failed','interrupted'].includes(dockerTask.status)" class="button primary" :disabled="dockerPreviewLoading || dockerTaskLoading" @click="retryDockerInstall">重新检查</button></template>
    </AppDialog>
  </div>
</template>
