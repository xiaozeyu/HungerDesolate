<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Play, Square, RotateCw } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import TaskPanel from "../components/TaskPanel.vue";
import { useInstanceOverview } from "../composables/useInstanceOverview";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { useOperations } from "../composables/useOperations";
import { useTasks } from "../composables/useTasks";
import { controlInstance } from "../api/instances";
import type { UserError } from "../lib/commandError";
import { firewallUdpPorts, hasReadyShardListeners } from "../lib/instanceRuntime";
import { activeContext } from "../context/activeContext";
import { operationTargetKey, stageLabel, taskOperationLabel } from "../lib/operationView";
const { data, error, loading, stale, refresh, pausePolling, resumePolling } = useInstanceOverview();
const { server, target } = usePageTarget();
const workspace = useWorkspace();
const operations = useOperations();
const taskStore = useTasks();
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
const installedBuild = computed(() => runtime.value?.installedBuild ?? data.value?.identity.installedBuild ?? null);
const shards = computed(() => runtime.value?.shards ?? []);
const ports = computed(() => runtime.value?.ports.filter(port => port.hostPort !== null) ?? []);
const firewallPorts = computed(() => firewallUdpPorts(runtime.value));
const shardName = (shard: "master" | "caves") => shard === "master" ? "主世界 · Master" : "洞穴 · Caves";
const purposeName = (purpose: "game" | "steamMaster" | "steamAuth" | "shard") => ({
  game: "游戏", steamMaster: "Steam 查询", steamAuth: "Steam 认证", shard: "分片通信",
}[purpose]);
const status = computed(() => runtimeStatus.value === "running" ? "运行中"
  : runtimeStatus.value === "stopped" ? "已停止" : runtimeStatus.value === "degraded" ? "部分异常"
  : runtimeStatus.value === "starting" ? "启动中" : "未知");
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
      <section class="identity-strip">
        <div>
          <span class="label">
            实例身份
          </span>
          <strong class="mono identity-value">
            {{data.identity.instanceId}}
          </strong>
        </div>
        <div>
          <span class="label">
            实际版本
          </span>
          <strong class="mono">
            {{installedBuild ?? '未知'}}
          </strong>
        </div>
        <StatusLabel :tone="stale?'warning':runtimeStatus==='running'?'success':runtimeStatus==='degraded'?'danger':'muted'"
          :label="stale?'状态可能已过期':status"/>
      </section>
      <div class="overview-grid">
        <section class="section-block">
          <div class="section-heading">
            <div>
              <h2>
                分片状态
              </h2>
            </div>
          </div>
          <div class="shard-list">
            <div v-for="shard in shards" :key="shard.shard" class="shard-row">
              <div>
                <strong>{{shardName(shard.shard)}}</strong>
                <small class="mono">DST {{shard.installedBuild ?? '版本未采集'}}</small>
              </div>
              <StatusLabel :tone="shard.ready?'success':shard.processRunning?'warning':'muted'"
                :label="shard.ready?'进程与端口就绪':shard.processRunning?'进程运行中，端口未就绪':shard.containerStatus"/>
            </div>
            <p v-if="!shards.length" class="muted-text">尚未采集分片运行状态</p>
          </div>
        </section>
        <section class="section-block">
          <div class="section-heading">
            <div>
              <h2>
                部署状态
              </h2>
            </div>
          </div>
          <div class="fact-list">
            <div>
              <span>
                DST 版本
              </span>
              <strong class="mono">
                {{installedBuild ?? '未知'}}
              </strong>
            </div>
            <div>
              <span>版本来源</span>
              <strong>{{runtime?.buildSource==='containerFile'?'容器实际版本':runtime?.buildSource==='releaseMetadata'?'Release 元数据':'未采集'}}</strong>
            </div>
            <div>
              <span>
                MOD 发布
              </span>
              <StatusLabel tone="muted" label="未核实"/>
            </div>
            <div>
              <span>
                最近备份
              </span>
              <strong>
                未采集
              </strong>
            </div>
            <div>
              <span>
                配置版本
              </span>
              <strong>
                {{data.manifestSummary?.configRevision ?? '未采集'}}
              </strong>
            </div>
            <div>
              <span>防火墙 UDP</span>
              <strong class="mono">{{firewallPorts.length ? firewallPorts.join('、') : '未采集'}}</strong>
            </div>
          </div>
        </section>
      </div>
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
      <template #actions><RouterLink class="text-button" to="/tasks">任务记录</RouterLink></template>
    </TaskPanel>
</FloatingTask>
    </template>
  </div>
</template>
