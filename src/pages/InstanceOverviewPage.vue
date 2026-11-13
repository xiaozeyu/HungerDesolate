<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ArrowUpCircle, Play, Square, RotateCw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import AppDialog from "../components/AppDialog.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
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
import { describeError, type UserError } from "../lib/commandError";
import { formatBytes } from "../lib/environmentView";
import { hasReadyShardListeners } from "../lib/instanceRuntime";
import { activeContext } from "../context/activeContext";
import { operationTargetKey, stageLabel, taskOperationLabel } from "../lib/operationView";
const { data, error, loading, stale, refresh, pausePolling, resumePolling } = useInstanceOverview({ poll: false });
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
    .filter(task => "hostId" in task.target && task.target.hostId === selected.hostId
      && "instanceId" in task.target && task.target.instanceId === selected.instanceId)
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
});
const visibleTask = computed(() => instanceTasks.value.find(task => ["queued", "running"].includes(task.status))
  ?? instanceTasks.value.find(task => ["failed", "interrupted"].includes(task.status))
  ?? null);
const taskTone = computed(() => visibleTask.value?.status === "failed" ? "danger"
  : visibleTask.value?.status === "interrupted" ? "warning" : "warning");
const taskStatusText = computed(() => visibleTask.value?.status === "failed" ? "失败"
  : visibleTask.value?.status === "interrupted" ? "待核实" : visibleTask.value?.status === "queued" ? "排队中" : "进行中");
const runtimeAction = computed<"start"|"stop"|"restart"|null>(() => {
  const operation = target.value ? operations.runningFor(targetKey.value, ["start", "stop", "restart"]) : null;
  return operation && (operation.kind === "start" || operation.kind === "stop" || operation.kind === "restart")
    ? operation.kind : null;
});
const startError = ref<UserError|null>(null);
let mounted = true;
watch([() => activeContext.value.epoch, () => server.value?.recordVersion,
  () => target.value?.serverId, () => target.value?.hostId, () => target.value?.instanceId], () => {
  startError.value = null;
}, { flush: "sync" });
onBeforeUnmount(() => { mounted = false; });
const name = computed(() => data.value?.identity.displayName ?? "专服");
const runtimeStatus = computed(() => data.value?.runtime?.status ?? data.value?.identity.runtimeStatus ?? "unknown");
const runtime = computed(() => data.value?.runtime);
const shards = computed(() => runtime.value?.shards ?? []);
const ports = computed(() => runtime.value?.ports.filter(port => port.hostPort !== null) ?? []);
const readyShards = computed(() => shards.value.filter(shard => shard.ready).length);
const installedBuild = computed(() => runtime.value?.installedBuild ?? null);
const revision = computed(() => data.value?.manifestSummary?.configRevision ?? null);
const updateOpen = ref(false);
const updateLoading = ref(false);
const updateError = ref("");
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

async function openUpdateDialog() {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection?.hostId || !instanceTarget || updateLoading.value) return;
  updateLoading.value = true;
  updateError.value = "";
  updatePreview.value = null;
  updateSelected.value = "";
  try {
    const response = await listInstanceReleases({ schemaVersion: 1, requestId: crypto.randomUUID(),
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion });
    if (response.serverId !== connection.serverId || response.hostId !== connection.hostId) return;
    updateReleases.value = response.releases;
    updateSelected.value = updateCandidates.value[0]?.releaseId ?? "";
    updateOpen.value = true;
  } catch (cause) {
    updateError.value = describeError(cause).message;
    updateOpen.value = true;
  } finally {
    updateLoading.value = false;
  }
}

async function previewUpdate() {
  const connection = server.value;
  const instanceTarget = target.value;
  const release = selectedRelease.value;
  if (!connection?.hostId || !instanceTarget || !release || revision.value === null || updateBusy.value) return;
  updateBusy.value = true;
  updateError.value = "";
  updatePreview.value = null;
  try {
    const preview = await previewInstanceUpdate({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: instanceTarget, expectedRecordVersion: connection.recordVersion,
      expectedRevision: revision.value, targetReleaseId: release.releaseId });
    updatePreview.value = preview;
  } catch (cause) {
    const failure = describeError(cause);
    const blockers = Array.isArray(failure.details?.blockers)
      ? failure.details.blockers.filter((item): item is string => typeof item === "string") : [];
    updateError.value = blockers.length ? `${failure.message}：${blockers.join("、")}` : failure.message;
  } finally {
    updateBusy.value = false;
  }
}

async function confirmUpdate() {
  const connection = server.value;
  const instanceTarget = target.value;
  const preview = updatePreview.value;
  if (!connection?.hostId || !instanceTarget || !preview || revision.value === null || updateBusy.value) return;
  updateBusy.value = true;
  updateError.value = "";
  const operationId = crypto.randomUUID();
  try {
    const response = await runInstanceUpdate({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: instanceTarget, expectedRecordVersion: connection.recordVersion,
      expectedRevision: revision.value, planId: preview.planId, operationId });
    if (response.task.operationId !== operationId || response.task.operation !== "instanceUpdate") {
      throw new Error("切换任务回执身份不匹配");
    }
    taskStore.receive(response.task);
    updateOpen.value = false;
    await refresh();
  } catch (cause) {
    updateError.value = describeError(cause).message;
  } finally {
    updateBusy.value = false;
  }
}
const summaryItems = computed(() => [
  { label: "运行状态", value: status.value, tone: runtimeStatus.value === "running" ? "success" as const : runtimeStatus.value === "degraded" || runtimeStatus.value === "failed" ? "danger" as const : runtimeStatus.value === "starting" || runtimeStatus.value === "stopping" ? "warning" as const : "default" as const },
  { label: "分片就绪", value: `${readyShards.value}/${shards.value.length}`, tone: shards.value.length && readyShards.value === shards.value.length ? "success" as const : readyShards.value ? "warning" as const : "default" as const },
  { label: "UDP 端口", value: ports.value.length },
]);
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
const status = computed(() => runtimeStatus.value === "running" ? "运行中"
  : runtimeStatus.value === "stopped" ? "已停止" : runtimeStatus.value === "degraded" ? "部分异常"
  : runtimeStatus.value === "starting" ? "启动中" : runtimeStatus.value === "stopping" ? "停止中"
  : runtimeStatus.value === "failed" ? "启动失败" : "未知");
const canStart = computed(() => !!target.value && !!server.value && !!data.value?.manifestSummary
  && !workspace.preview.value && !loading.value && !runtimeAction.value && runtimeStatus.value !== "running");
const canStop = computed(() => !!target.value && !!server.value && !!data.value?.manifestSummary
  && !workspace.preview.value && !loading.value && !runtimeAction.value && shards.value.length > 0
  && runtimeStatus.value !== "stopped" && runtimeStatus.value !== "unknown");
const canRestart = computed(() => canStop.value && runtimeStatus.value === "running");

async function operateRuntime(action: "start"|"stop"|"restart") {
  const selectedTarget = target.value, connection = server.value, overview = data.value;
  const allowed = action === "start" ? canStart.value : action === "stop" ? canStop.value : canRestart.value;
  if (!selectedTarget || !connection || !overview?.manifestSummary || !allowed) return;
  const cavesEnabled = overview.manifestSummary.cavesEnabled;
  const revision = overview.manifestSummary.configRevision;
  startError.value = null;
  pausePolling();
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
  resumePolling();
  if (!mounted || target.value?.instanceId !== selectedTarget.instanceId
      || server.value?.recordVersion !== connection.recordVersion) return;
  if (outcome.error) {
    startError.value = outcome.error;
    return;
  }
  await refresh();
}
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="name" title="专服概览">
      <template #actions>
        <button class="button" :disabled="loading || !!runtimeAction" @click="openUpdateDialog">
          <ArrowUpCircle :size="16"/>
          切换 DST 版本
        </button>
        <button class="button" :disabled="loading || !!runtimeAction" @click="refresh">
          <RotateCw :size="16"/>
          刷新
        </button>
        <button v-if="runtimeStatus==='running'" class="button" :disabled="!canRestart" :aria-busy="runtimeAction==='restart'" @click="operateRuntime('restart')">
          <RotateCw :size="16"/>
          {{runtimeAction==='restart'?'重启并验证中…':'重启'}}
        </button>
        <button v-if="canStop || runtimeAction==='stop'" class="button" :disabled="!canStop" :aria-busy="runtimeAction==='stop'" @click="operateRuntime('stop')">
          <Square :size="16"/>
          {{runtimeAction==='stop'?'停止中…':'停止'}}
        </button>
        <button class="button primary" :disabled="!canStart" :aria-busy="runtimeAction==='start'" @click="operateRuntime('start')">
          <Play :size="16"/>
          {{runtimeAction==='start' ? '启动并验证中…' : '启动专服'}}
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="startError" class="environment-error" role="alert">
      <strong>{{startError.message}}</strong>
      <ul v-if="startError.recoverySteps.length"><li v-for="step in startError.recoverySteps" :key="step">{{step}}</li></ul>
    </div>

    <div v-if="loading && !data" class="empty-state" aria-live="polite">
      <p>
        读取当前专服…
      </p>
    </div>
    <div v-else-if="error && !data" class="error-strip" role="alert">
      {{error}}
    </div>
    <template v-if="data">
      <div v-if="error && stale" class="error-strip" role="status">
        运行状态刷新失败，当前显示上次采集结果：{{error}}
      </div>
      <SummaryBar :items="summaryItems" label="运行摘要"/>
      <section class="section-block shard-section">
        <div class="section-heading">
          <div><h2>分片状态</h2><p class="muted-text">容器、进程与 UDP 端口就绪情况</p></div>
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
          <table>
            <thead><tr><th>分片</th><th>用途</th><th>宿主机端口</th><th>容器监听</th><th>Docker 发布</th></tr></thead>
            <tbody>
              <tr v-for="port in ports" :key="`${port.shard}:${port.purpose}`">
                <td>{{shardName(port.shard)}}</td>
                <td>{{purposeName(port.purpose)}}</td>
                <td class="mono">UDP {{port.hostPort}}</td>
                <td>{{port.listening===true?'已监听':port.listening===false?'未监听':'未知'}}</td>
                <td>{{port.published===true?'已发布':port.published===false?'未发布':'未知'}}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="muted-text">端口信息尚未采集</p>
      </section>
<FloatingTask v-if="visibleTask" label="专服任务">
    <TaskPanel v-if="visibleTask" :title="taskOperationLabel(visibleTask.operation)" :status-label="taskStatusText"
      :stage-label="stageLabel(visibleTask.stage)" :tone="taskTone" :queue-position="visibleTask.queuePosition"
      :error="visibleTask.error">
      <template #actions><button class="text-button" type="button" @click="openTaskDrawer">任务记录</button></template>
    </TaskPanel>
</FloatingTask>
<AppDialog :open="updateOpen" title="切换 DST 版本" @update:open="value => { if (!value && !updateBusy) { updateOpen = false; updatePreview = null; updateError = ''; } }">
  <div class="update-dialog">
    <p v-if="!updateCandidates.length" class="muted-text">没有更高 build 的已验证 Release。请先在 Release 管理页检查并构建。</p>
    <label v-else>目标版本
      <select v-model="updateSelected" :disabled="updateBusy" @change="updatePreview = null">
        <option v-for="release in updateCandidates" :key="release.releaseId" :value="release.releaseId">
          build {{ release.dstBuild }} · {{ release.releaseId.slice(0, 8) }}
        </option>
      </select>
    </label>
    <div v-if="updateError" class="environment-error" role="alert">{{ updateError }}</div>
    <div v-if="updatePreview" class="update-preview">
      <p>当前 build {{ updatePreview.current.build ?? "未知" }} → 目标 build {{ updatePreview.targetRelease.build ?? "未知" }}</p>
      <p>将停止 {{ updatePreview.shards.join(" / ") }}；世界存档保护点约 {{ formatBytes(updatePreview.backup.estimatedBytes) }}；可用 {{ formatBytes(updatePreview.backup.availableBytes) }}</p>
      <p v-if="!updatePreview.rollback.available" class="backup-warning">失败后需人工恢复：{{ updatePreview.rollback.reason }}</p>
      <p v-for="warning in updatePreview.warnings" :key="warning" class="backup-warning">{{ updateWarningLabel(warning) }}</p>
    </div>
  </div>
  <template #footer>
    <button class="button" :disabled="updateBusy" @click="updateOpen = false">取消</button>
    <button v-if="!updatePreview" class="button primary" :disabled="updateBusy || !updateSelected || !updateCandidates.length || revision === null" @click="previewUpdate">
      {{ updateBusy ? "检测中…" : "预览切换" }}
    </button>
    <button v-else class="button primary" :disabled="updateBusy || revision === null" @click="confirmUpdate">
      {{ updateBusy ? "创建备份…" : "创建备份并切换" }}
    </button>
  </template>
</AppDialog>
    </template>
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
