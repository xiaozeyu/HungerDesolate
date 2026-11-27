<script setup lang="ts">
import { computed, h, onBeforeUnmount, ref, watch } from "vue";
import { ArrowUpCircle, Play, Square, RotateCw } from "@lucide/vue";
import { NButton, NDataTable, NSelect, type DataTableColumns } from "naive-ui";
import PageHeader from "../components/PageHeader.vue";
import AppDialog from "../components/AppDialog.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import LoadingState from "../components/LoadingState.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import TaskPanel from "../components/TaskPanel.vue";
import { useInstanceOverview } from "../composables/useInstanceOverview";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { useOperations } from "../composables/useOperations";
import { useTasks } from "../composables/useTasks";
import { useTaskDrawer } from "../composables/useTaskDrawer";
import { controlInstance, listInstanceReleases, type InstanceReleaseSummary } from "../api/instances";
import { previewInstanceUpdate, runInstanceUpdate, type UpdatePreview } from "../api/updates";
import { describeError } from "../lib/commandError";
import ErrorNotice from "../components/ErrorNotice.vue";
import { notifyError } from "../naive/notify";
import { sameTarget } from "../lib/navigationGuard";
import { formatBytes } from "../lib/environmentView";
import { inSession } from "../lib/session";
import { hasReadyShardListeners } from "../lib/instanceRuntime";
import { activeContext } from "../context/activeContext";
import { operationTargetKey, stageLabel, taskOperationLabel } from "../lib/operationView";
const { data, error, loading, stale, refresh, updateInProgress } = useInstanceOverview();
const { server, target } = usePageTarget();
const workspace = useWorkspace();
const operations = useOperations();
const taskStore = useTasks();
const { openDrawer: openTaskDrawer } = useTaskDrawer();
const targetKey = computed(() => target.value ? operationTargetKey(target.value) : "");
const instanceTasks = computed(() => {
  const selected = target.value;
  if (!selected) return [];
  return [...taskStore.tasks.value]
    .filter(task => "instanceId" in task.target && sameTarget(task.target, selected))
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
});
const visibleTask = computed(() => instanceTasks.value.find(task => ["queued", "running"].includes(task.status))
  ?? instanceTasks.value.find(task => ["failed", "interrupted"].includes(task.status) && inSession(task.createdAt))
  ?? null);
const taskTone = computed(() => visibleTask.value?.status === "failed" ? "danger"
  : visibleTask.value?.status === "interrupted" ? "warning" : "warning");
const taskStatusText = computed(() => visibleTask.value?.status === "failed" ? "失败"
  : visibleTask.value?.status === "interrupted" ? "待核实" : visibleTask.value?.status === "queued" ? "排队中" : "进行中");
const taskPercent = computed(() => {
  const progress = visibleTask.value?.progress;
  return progress?.kind === "determinate" && progress.total > 0
    ? Math.round(progress.completed * 100 / progress.total) : null;
});
const dismissedTaskId = ref<string | null>(null);
const dockTask = computed(() => visibleTask.value && visibleTask.value.taskId !== dismissedTaskId.value ? visibleTask.value : null);
const taskDismissible = computed(() => !!visibleTask.value
  && ["succeeded", "failed", "cancelled", "interrupted"].includes(visibleTask.value.status));
function dismissTask() {
  if (visibleTask.value) dismissedTaskId.value = visibleTask.value.taskId;
}
const runtimeAction = computed<"start"|"stop"|"restart"|null>(() => {
  const operation = target.value ? operations.runningFor(targetKey.value, ["start", "stop", "restart"]) : null;
  return operation && (operation.kind === "start" || operation.kind === "stop" || operation.kind === "restart")
    ? operation.kind : null;
});
let mounted = true;
onBeforeUnmount(() => { mounted = false; });
const name = computed(() => data.value?.identity.displayName ?? "专服");
const runtimeStatus = computed(() => data.value?.runtime?.status ?? data.value?.identity.runtimeStatus ?? "unknown");
const runtime = computed(() => data.value?.runtime);
const shards = computed(() => [...(runtime.value?.shards ?? [])].sort((left, right) =>
  Number(left.shard !== "master") - Number(right.shard !== "master")));
const ports = computed(() => runtime.value?.ports.filter(port => port.hostPort !== null) ?? []);
const readyShards = computed(() => shards.value.filter(shard => shard.ready).length);
const installedBuild = computed(() => runtime.value?.installedBuild ?? null);
const revision = computed(() => data.value?.manifestSummary?.configRevision ?? null);
const updateOpen = ref(false);
const updateLoading = ref(false);
let updateSequence = 0;
const updateReleases = ref<InstanceReleaseSummary[]>([]);
const updateSelected = ref("");
const updatePreview = ref<UpdatePreview | null>(null);
const updateBusy = ref(false);
const updateCandidates = computed(() => {
  const current = Number(installedBuild.value);
  if (!Number.isFinite(current)) return [];
  return updateReleases.value
    .filter(release => release.verified && Number(release.dstBuild) > current)
    .sort((left, right) => Number(right.dstBuild) - Number(left.dstBuild));
});
const selectedRelease = computed(() => updateCandidates.value.find(release => release.releaseId === updateSelected.value) ?? null);
const updateWarningLabel = (value: string) => ({
  UPDATE_ROLLBACK_UNAVAILABLE: "旧 Release 产物不可用，失败后需人工恢复",
  DST_RELEASE_RUNTIME_UNVERIFIED: "目标 Release 未做运行时验证",
}[value] ?? value);

function captureContext() {
  const selected = target.value;
  const version = server.value?.recordVersion;
  const epoch = activeContext.value.epoch;
  const preview = workspace.preview.value;
  return () => mounted && sameTarget(selected, target.value) && server.value?.recordVersion === version
    && activeContext.value.epoch === epoch && workspace.preview.value === preview;
}

function updateTicket() {
  const current = captureContext();
  const sequence = ++updateSequence;
  return () => current() && sequence === updateSequence;
}

watch([() => activeContext.value.epoch, targetKey, () => server.value?.recordVersion, workspace.preview], () => {
  updateSequence++;
  updateOpen.value = false;
  updateLoading.value = false;
  updateBusy.value = false;
  updatePreview.value = null;
  updateReleases.value = [];
  updateSelected.value = "";
}, { flush: "sync" });

function closeUpdateDialog() {
  if (updateBusy.value) return;
  updateSequence++;
  updateOpen.value = false;
  updatePreview.value = null;
}

async function openUpdateDialog() {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection?.hostId || !instanceTarget || updateLoading.value || updateInProgress.value) return;
  const current = updateTicket();
  const requestId = crypto.randomUUID();
  updateLoading.value = true;
  updatePreview.value = null;
  updateSelected.value = "";
  updateReleases.value = [];
  try {
    const response = await listInstanceReleases({ schemaVersion: 1, requestId,
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion });
    if (!current()) return;
    if (response.requestId !== requestId || response.serverId !== instanceTarget.serverId
        || response.hostId !== instanceTarget.hostId || response.recordVersion !== connection.recordVersion) {
      throw new Error("Release 列表响应身份或版本不匹配");
    }
    updateReleases.value = response.releases;
    updateSelected.value = updateCandidates.value[0]?.releaseId ?? "";
    updateOpen.value = true;
  } catch (cause) {
    if (current()) notifyError("读取 Release 失败", describeError(cause));
  } finally {
    if (current()) updateLoading.value = false;
  }
}

async function previewUpdate() {
  const connection = server.value;
  const instanceTarget = target.value;
  const release = selectedRelease.value;
  const expectedRevision = revision.value;
  if (!connection?.hostId || !instanceTarget || !release || expectedRevision === null || updateBusy.value || updateInProgress.value) return;
  const current = updateTicket();
  const requestId = crypto.randomUUID();
  updateBusy.value = true;
  updatePreview.value = null;
  try {
    const preview = await previewInstanceUpdate({ schemaVersion: 1, requestId,
      target: instanceTarget, expectedRecordVersion: connection.recordVersion,
      expectedRevision, targetReleaseId: release.releaseId });
    if (!current()) return;
    if (preview.requestId !== requestId || !sameTarget(preview.target, instanceTarget)
        || preview.baseRevision !== expectedRevision || preview.targetRelease.releaseId !== release.releaseId) {
      throw new Error("切换预览响应身份或版本不匹配");
    }
    updatePreview.value = preview;
  } catch (cause) {
    if (!current()) return;
    const failure = describeError(cause);
    const blockers = Array.isArray(failure.details?.blockers)
      ? failure.details.blockers.filter((item): item is string => typeof item === "string") : [];
    notifyError("预览切换失败", { ...failure, recoverySteps: [...blockers, ...failure.recoverySteps] });
  } finally {
    if (current()) updateBusy.value = false;
  }
}

async function confirmUpdate() {
  const connection = server.value;
  const instanceTarget = target.value;
  const preview = updatePreview.value;
  if (!connection?.hostId || !instanceTarget || !preview?.allowed || revision.value !== preview.baseRevision
      || !sameTarget(preview.target, instanceTarget) || updateBusy.value || updateInProgress.value) return;
  const current = updateTicket();
  const requestId = crypto.randomUUID();
  const operationId = crypto.randomUUID();
  updateBusy.value = true;
  try {
    const response = await runInstanceUpdate({ schemaVersion: 1, requestId,
      target: instanceTarget, expectedRecordVersion: connection.recordVersion,
      expectedRevision: preview.baseRevision, planId: preview.planId, operationId });
    const { task } = response;
    if (response.requestId !== requestId || !sameTarget(response.target, instanceTarget)
        || task.taskId !== operationId || task.operationId !== operationId || task.operation !== "instanceUpdate"
        || !("instanceId" in task.target) || !sameTarget(task.target, instanceTarget)) {
      throw new Error("切换任务回执身份不匹配");
    }
    taskStore.receive(task);
    if (current()) updateOpen.value = false;
  } catch (cause) {
    if (current()) notifyError("未能提交 DST 切换", describeError(cause));
  } finally {
    if (current()) updateBusy.value = false;
  }
}
const shardName = (shard: "master" | "caves") => shard === "master" ? "主世界 · Master" : "洞穴 · Caves";
const purposeName = (purpose: "game" | "steamMaster" | "steamAuth" | "shard") => ({
  game: "游戏", steamMaster: "Steam 查询", steamAuth: "Steam 认证", shard: "分片通信",
}[purpose]);
const containerLabel = (status: string) => ({
  created: "已创建",
  running: "运行中",
  paused: "已暂停",
  restarting: "重启中",
  removing: "删除中",
  exited: "已退出",
  dead: "已终止",
}[status] ?? "未知");
const processLabel = (value: boolean | null) => value === true ? "运行中" : value === false ? "未运行" : "未采集";
const shardTone = (shard: (typeof shards.value)[number]): "success" | "warning" | "danger" | "muted" =>
  shard.ready ? "success" : shard.containerStatus === "running" && shard.processRunning !== false
    ? "warning" : ["created", "exited", "dead"].includes(shard.containerStatus) ? "muted" : "danger";
const shardStateLabel = (shard: (typeof shards.value)[number]) =>
  shard.ready ? "就绪" : shard.processRunning === false ? "进程未运行" : shard.processRunning === true ? "端口未就绪" : containerLabel(shard.containerStatus);
const shardPortLabel = (shard: (typeof shards.value)[number]) =>
  shard.ready ? "端口就绪" : shard.missingPorts.length ? `缺少 ${shard.missingPorts.length} 个端口` : "未就绪";
const canStart = computed(() => !!target.value && !!server.value && !!data.value?.manifestSummary
  && !workspace.preview.value && !loading.value && !runtimeAction.value && !updateInProgress.value && !updateBusy.value
  && runtimeStatus.value !== "running");
const canStop = computed(() => !!target.value && !!server.value && !!data.value?.manifestSummary
  && !workspace.preview.value && !loading.value && !runtimeAction.value && !updateInProgress.value && !updateBusy.value && shards.value.length > 0
  && runtimeStatus.value !== "stopped" && runtimeStatus.value !== "unknown");
const canRestart = computed(() => canStop.value && runtimeStatus.value === "running");

async function operateRuntime(action: "start"|"stop"|"restart") {
  const selectedTarget = target.value, connection = server.value, overview = data.value;
  const allowed = action === "start" ? canStart.value : action === "stop" ? canStop.value : canRestart.value;
  if (!selectedTarget || !connection || !overview?.manifestSummary || !allowed) return;
  const cavesEnabled = overview.manifestSummary.cavesEnabled;
  const revision = overview.manifestSummary.configRevision;
  const current = captureContext();
  const outcome = await operations.run({
    kind: action,
    target: selectedTarget,
    title: action === "start" ? "启动专服" : action === "stop" ? "停止专服" : "重启专服",
    stage: "提交运行操作",
    work: async setStage => {
      const requestId = crypto.randomUUID(), operationId = crypto.randomUUID();
      const response = await controlInstance({
        action,
        schemaVersion: 1, requestId, operationId, target: selectedTarget,
        expectedRecordVersion: connection.recordVersion,
        expectedRevision: revision,
      });
      setStage("验证运行状态");
      if (response.schemaVersion !== 1 || response.requestId !== requestId || response.operationId !== operationId
        || response.recordVersion !== connection.recordVersion || response.revision !== revision
        || response.target.serverId !== selectedTarget.serverId || response.target.hostId !== selectedTarget.hostId
        || response.target.instanceId !== selectedTarget.instanceId || response.publicConnectivityVerified !== false) {
        throw new Error("专服运行操作结果身份无效");
      }
      if (action === "stop") {
        if (response.status !== "stopped" || response.runningShards.length || Object.keys(response.listeners).length) {
          throw new Error("专服停止结果仍报告运行中的分片");
        }
      } else if (response.status !== "running" || response.verificationLevel !== "localVerified"
        || !hasReadyShardListeners(response, cavesEnabled)) {
        throw new Error("专服进程或 UDP 监听未就绪");
      }
      return response;
    },
  });
  if (!current()) return;
  if (outcome.error) {
    notifyError(action === "start" ? "启动专服失败" : action === "stop" ? "停止专服失败" : "重启专服失败", outcome.error);
    return;
  }
  await refresh();
}

type RuntimePort = (typeof ports.value)[number];
const portColumns = computed<DataTableColumns<RuntimePort>>(() => [
  { title: "分片", key: "shard", render: port => shardName(port.shard) },
  { title: "用途", key: "purpose", render: port => purposeName(port.purpose) },
  { title: "宿主机端口", key: "hostPort", render: port => h("span", { class: "mono" }, `UDP ${port.hostPort}`) },
  { title: "容器监听", key: "listening", render: port => port.listening === true ? "已监听" : port.listening === false ? "未监听" : "未知" },
  { title: "Docker 发布", key: "published", render: port => port.published === true ? "已发布" : port.published === false ? "未发布" : "未知" },
]);
const portRowKey = (port: RuntimePort) => `${port.shard}:${port.purpose}`;
const updateOptions = computed(() => updateCandidates.value.map(release => ({
  label: `build ${release.dstBuild} · ${release.releaseId.slice(0, 8)}`,
  value: release.releaseId,
})));
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="name" title="专服概览">
      <template #status><span v-if="installedBuild" class="mono muted-text">DST {{ installedBuild }}</span></template>
      <template #actions>
        <NButton size="small" :disabled="loading || !!runtimeAction || updateLoading || updateInProgress" @click="openUpdateDialog"><template #icon><ArrowUpCircle :size="16"/></template>切换 DST 版本</NButton>
        <NButton size="small" :disabled="loading || !!runtimeAction || updateBusy || updateInProgress" @click="refresh"><template #icon><RotateCw :size="16"/></template>刷新</NButton>
        <NButton v-if="runtimeStatus==='running'" size="small" :disabled="!canRestart" :loading="runtimeAction==='restart'" @click="operateRuntime('restart')">
          <template #icon><RotateCw :size="16"/></template>{{runtimeAction==='restart'?'重启并验证中…':'重启'}}
        </NButton>
        <NButton v-if="canStop || runtimeAction==='stop'" size="small" :disabled="!canStop" :loading="runtimeAction==='stop'" @click="operateRuntime('stop')">
          <template #icon><Square :size="16"/></template>{{runtimeAction==='stop'?'停止中…':'停止'}}
        </NButton>
        <NButton size="small" type="primary" :disabled="!canStart" :loading="runtimeAction==='start'" @click="operateRuntime('start')">
          <template #icon><Play :size="16"/></template>{{runtimeAction==='start' ? '启动并验证中…' : '启动专服'}}
        </NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <ErrorNotice :key="targetKey" :title="stale ? '运行状态刷新失败' : '读取运行状态失败'" :pending="loading"
      :error="error && stale ? { ...error, recoverySteps: ['当前显示上次采集结果', ...error.recoverySteps] } : error"/>
    <LoadingState v-if="loading && !data" description="读取当前专服…"/>
    <template v-if="data">
      <section class="section-block shard-section">
        <div class="section-heading">
          <div><h2>分片状态</h2></div>
          <StatusLabel :tone="stale ? 'warning' : shards.length && readyShards === shards.length ? 'success' : 'warning'" :label="stale ? '状态可能已过期' : `${readyShards}/${shards.length} 就绪`"/>
        </div>
        <div v-if="shards.length" class="shard-grid">
          <article v-for="shard in shards" :key="shard.shard" class="shard-card" :class="`tone-${shardTone(shard)}`">
            <header>
              <div><span class="label">{{ shardName(shard.shard) }}</span><strong>{{ shard.ready ? "已就绪" : containerLabel(shard.containerStatus) }}</strong></div>
              <StatusLabel :tone="shardTone(shard)" :label="shardStateLabel(shard)"/>
            </header>
            <dl class="shard-facts">
              <div><dt>容器</dt><dd>{{ containerLabel(shard.containerStatus) }}</dd></div>
              <div><dt>游戏进程</dt><dd>{{ processLabel(shard.processRunning) }}</dd></div>
              <div><dt>端口</dt><dd>{{ shardPortLabel(shard) }}</dd></div>
              <div><dt>DST 版本</dt><dd class="mono">{{ shard.installedBuild ?? "未采集" }}</dd></div>
            </dl>
            <p v-if="shard.missingPorts.length" class="shard-warning">缺少 UDP {{ shard.missingPorts.join("、") }}</p>
          </article>
        </div>
        <p v-else class="muted-text">尚未采集分片运行状态</p>
      </section>
      <section class="section-block">
        <div class="section-heading">
          <div><h2>UDP 端口</h2></div>
        </div>
        <div v-if="ports.length" class="table-wrap">
          <NDataTable :columns="portColumns" :data="ports" :bordered="false" :single-line="true" size="medium" :row-key="portRowKey"/>
        </div>
        <p v-else class="muted-text">端口信息尚未采集</p>
      </section>
    </template>
<FloatingTask v-if="dockTask" label="专服任务" :tone="taskTone" :summary="taskStatusText" :progress="taskPercent" :dismissible="taskDismissible" @dismiss="dismissTask">
    <TaskPanel v-if="dockTask" dock :title="taskOperationLabel(dockTask.operation)" :status-label="taskStatusText"
      :stage-label="stageLabel(dockTask.stage)" :tone="taskTone" :queue-position="dockTask.queuePosition"
      :error="dockTask.error">
      <template #actions><NButton text size="small" @click="openTaskDrawer">任务记录</NButton></template>
    </TaskPanel>
</FloatingTask>
<AppDialog :open="updateOpen" title="切换 DST 版本" @update:open="value => { if (!value) closeUpdateDialog(); }">
  <div class="update-dialog">
    <p v-if="!updateCandidates.length" class="muted-text">没有更高 build 的已验证 Release。请先在 Release 管理页检查并构建。</p>
    <label v-else>目标版本
      <NSelect v-model:value="updateSelected" :options="updateOptions" :disabled="updateBusy" @update:value="updatePreview = null"/>
    </label>
    <div v-if="updatePreview" class="update-preview">
      <p>当前 build {{ updatePreview.current.build ?? "未知" }} → 目标 build {{ updatePreview.targetRelease.build ?? "未知" }}</p>
      <p>将停止 {{ updatePreview.shards.join(" / ") }}；世界存档保护点约 {{ formatBytes(updatePreview.backup.estimatedBytes) }}；可用 {{ formatBytes(updatePreview.backup.availableBytes) }}</p>
      <p v-if="!updatePreview.rollback.available" class="backup-warning">失败后需人工恢复：{{ updatePreview.rollback.reason }}</p>
      <p v-for="warning in updatePreview.warnings" :key="warning" class="backup-warning">{{ updateWarningLabel(warning) }}</p>
    </div>
  </div>
  <template #footer>
    <NButton :disabled="updateBusy" @click="closeUpdateDialog">取消</NButton>
    <NButton v-if="!updatePreview" type="primary" :disabled="updateBusy || !updateSelected || !updateCandidates.length || revision === null" @click="previewUpdate">
      {{ updateBusy ? "检测中…" : "预览切换" }}
    </NButton>
    <NButton v-else type="primary" :disabled="updateBusy || updateInProgress || !updatePreview.allowed || revision !== updatePreview.baseRevision" @click="confirmUpdate">
      {{ updateBusy ? "创建备份…" : "创建备份并切换" }}
    </NButton>
  </template>
</AppDialog>
  </div>
</template>


<style scoped>
.update-dialog { display: flex; flex-direction: column; gap: 12px; }
.update-preview { display: flex; flex-direction: column; gap: 6px; }
.update-preview p { margin: 0; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }
.backup-warning { color: var(--warning); }
.shard-section { margin-bottom: 38px; }
.shard-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.shard-card { border: 1px solid var(--border); border-top: 2px solid var(--text-tertiary); padding: 16px 18px 14px; background: var(--surface); }
.shard-card.tone-success { border-top-color: var(--success); }
.shard-card.tone-warning { border-top-color: var(--warning); }
.shard-card.tone-danger { border-top-color: var(--danger); }
.shard-card header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.shard-card header > div { display: flex; flex-direction: column; gap: 4px; }
.shard-card header strong { font-size: 15px; font-weight: 500; }
.shard-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 18px; margin: 16px 0 0; padding-top: 14px; border-top: 1px solid var(--border); }
.shard-facts div { min-width: 0; }
.shard-facts dt { color: var(--text-tertiary); font-size: 10px; letter-spacing: .04em; }
.shard-facts dd { margin: 3px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.shard-warning { margin: 12px 0 0; color: var(--warning); font-size: 11px; }
@media (max-width: 760px) { .shard-grid { grid-template-columns: 1fr; } }
</style>
