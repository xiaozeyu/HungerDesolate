<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import { RefreshCw, Terminal, AlertCircle } from "@lucide/vue";
import { NButton, NDataTable, type DataTableColumns } from "naive-ui";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import LoadingState from "../components/LoadingState.vue";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { getInstanceDiagnostics, type DiagnosticsResponse } from "../api/diagnostics";
import { formatBytes } from "../lib/environmentView";
import { useQuery } from "../lib/queryClient";

const { server, target, instanceName } = usePageTarget();
const workspace = useWorkspace();
const selectedShard = ref<"master" | "caves">("master");
const key = computed(() => {
  const connection = server.value, current = target.value;
  return connection && current
    ? `diagnostics:${current.serverId}:${current.hostId}:${current.instanceId}:${connection.recordVersion}`
    : null;
});
const query = useQuery<DiagnosticsResponse>(key, async () => {
  const connection = server.value, current = target.value;
  if (!connection || !current) throw new Error("缺少专服连接");
  const requestId = crypto.randomUUID();
  const response = await getInstanceDiagnostics({ schemaVersion: 1, requestId, target: current,
    expectedRecordVersion: connection.recordVersion, logLimit: 200 });
  if (server.value?.recordVersion !== connection.recordVersion
      || target.value?.instanceId !== current.instanceId) throw new Error("诊断请求上下文已失效");
  if (response.requestId !== requestId || response.recordVersion !== connection.recordVersion
      || response.target.serverId !== current.serverId || response.target.hostId !== current.hostId
      || response.target.instanceId !== current.instanceId) {
    throw new Error("诊断响应身份或版本不匹配");
  }
  return response;
});
const data = query.data;
const loading = query.loading;
const error = query.error;
const stale = query.stale;
const refresh = query.refresh;
const shardLog = computed(() => data.value?.logs.find(item => item.shard === selectedShard.value) ?? null);
const health = computed(() => data.value?.health ?? null);
const runtimeLabel = computed(() => {
  const value = health.value?.runtimeStatus;
  return value === "running" ? "运行中" : value === "stopped" ? "已停止" : value === "degraded" ? "部分异常" : "待核实";
});
const shardLabel = (shard: "master" | "caves") => shard === "master" ? "Master" : "Caves";
const taskLabel = (status: string) => status === "failed" ? "失败" : status === "interrupted" ? "中断" : status;
const formatTime = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
const warningLabel = (value: string) => value === "DOCKER_CONTAINERS_UNAVAILABLE" ? "无法读取 Docker 容器"
  : value.startsWith("SHARD_CONTAINER_MISSING:") ? `${shardLabel(value.slice(24) as "master" | "caves")} 容器缺失`
  : value;
watch(data, response => {
  if (!response) return;
  if (!response.logs.some(item => item.shard === selectedShard.value)) selectedShard.value = response.logs[0]?.shard ?? "master";
}, { flush: "post" });

type DiagnosticsTask = DiagnosticsResponse["tasks"][number];
const columns = computed<DataTableColumns<DiagnosticsTask>>(() => [
  {
    title: "任务",
    key: "taskId",
    render: task => h("div", null, [
      h("span", { class: "mono" }, task.taskId.slice(0, 8)),
      h("small", { class: "table-sub" }, task.operation ?? "任务"),
    ]),
  },
  {
    title: "状态",
    key: "status",
    render: task => h(StatusLabel, { tone: task.status === "failed" ? "danger" : "warning", label: taskLabel(task.status) }),
  },
  { title: "阶段", key: "stage", render: task => task.stage ?? "—" },
  { title: "错误", key: "errorCode", render: task => h("span", { class: "mono" }, task.errorCode ?? "—") },
  {
    title: "证据",
    key: "evidence",
    render: task => {
      if (!task.journal.length && !task.recoverySteps.length) return h("span", { class: "muted-text" }, "—");
      return h("details", null, [
        h("summary", null, `${task.journal.length} 条`),
        task.recoverySteps.length ? h("ul", null, task.recoverySteps.map(step => h("li", { key: step }, step))) : null,
        task.journal.length ? h("ul", { class: "task-journal" }, task.journal.map(entry => h("li", { key: entry.sequence }, `${entry.stage} · ${entry.status}`))) : null,
      ]);
    },
  },
  { title: "时间", key: "time", render: task => formatTime(task.finishedAt ?? task.createdAt) },
]);
const taskRowKey = (task: DiagnosticsTask) => task.taskId;
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="诊断">
      <template #actions>
        <NButton size="small" :disabled="loading || workspace.preview.value || !target" @click="refresh">
          <template #icon><RefreshCw :size="16" :class="{ spinning: loading }"/></template>
          {{ loading ? "读取中…" : "刷新证据" }}
        </NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>
    <div v-if="stale" class="error-strip" role="status">诊断刷新失败，当前显示上次结果</div>
    <LoadingState v-if="loading && !data" description="读取专服诊断…"/>
    <div v-else-if="!data" class="empty-state" role="alert">
      <h2>{{ workspace.preview.value ? "诊断需要桌面连接" : "暂无诊断结果" }}</h2>
    </div>
    <template v-else>
      <section class="identity-strip">
        <div><span class="label">运行状态</span><StatusLabel :tone="health?.runtimeStatus === 'running' ? 'success' : health?.runtimeStatus === 'degraded' ? 'danger' : 'muted'" :label="runtimeLabel"/></div>
        <div><span class="label">配置版本</span><strong class="mono">{{ health?.configRevision ?? '—' }}</strong></div>
        <div><span class="label">世界版本</span><strong class="mono">{{ health?.worldRevision ?? '—' }}</strong></div>
        <div><span class="label">MOD 版本</span><strong class="mono">{{ health?.modsRevision ?? '—' }}</strong></div>
        <div><span class="label">采集时间</span><span class="mono">{{ formatTime(data.capturedAt) }}</span></div>
      </section>

      <section v-if="health?.warnings.length" class="environment-error" role="status">
        <strong>诊断发现</strong>
        <ul><li v-for="warning in health.warnings" :key="warning">{{ warningLabel(warning) }}</li></ul>
      </section>

      <div class="diagnostic-grid">
        <section class="section-block">
          <div class="section-heading"><div><h2>运行证据</h2><p class="muted-text">容器、Release 与磁盘</p></div></div>
          <dl class="creation-facts">
            <div><dt>DST build</dt><dd class="mono">{{ health?.dstBuild ?? '未知' }}</dd></div>
            <div><dt>Release ID</dt><dd class="mono">{{ health?.dstReleaseId ?? '—' }}</dd></div>
            <div><dt>MOD Release</dt><dd class="mono">{{ health?.modsReleaseId ?? '—' }}</dd></div>
            <div><dt>Docker</dt><dd>{{ health?.dockerVersion ?? '不可用' }}</dd></div>
            <div><dt>可用磁盘</dt><dd>{{ health?.diskAvailableBytes == null ? '未知' : formatBytes(health.diskAvailableBytes) }}</dd></div>
          </dl>
        </section>
        <section class="section-block">
          <div class="section-heading"><div><h2>分片</h2><p class="muted-text">容器状态</p></div></div>
          <div v-if="!health?.shards.length" class="muted-text">未采集到分片</div>
          <div v-else class="shard-list">
            <div v-for="shard in health.shards" :key="shard.shard" class="shard-row">
              <strong>{{ shardLabel(shard.shard) }}</strong>
              <StatusLabel :tone="shard.status?.startsWith('Up') ? 'success' : 'muted'" :label="shard.status ?? '未找到容器'"/>
            </div>
          </div>
        </section>
      </div>

      <section class="section-block">
        <div class="section-heading">
          <div><h2>分片日志</h2><p class="muted-text">最近 200 行，已脱敏</p></div>
          <Terminal :size="18"/>
        </div>
        <div class="segmented" role="group" aria-label="日志分片">
          <button v-for="log in data.logs" :key="log.shard" :aria-pressed="selectedShard === log.shard" @click="selectedShard = log.shard">{{ shardLabel(log.shard) }}</button>
        </div>
        <div v-if="!shardLog?.available" class="muted-text">容器不可用，无法读取日志</div>
        <div v-else-if="!shardLog.lines.length" class="muted-text">暂无日志</div>
        <pre v-else class="log-window">{{ shardLog.lines.join("\n") }}</pre>
        <p v-if="shardLog?.truncated" class="muted-text">日志已截断</p>
      </section>

      <section class="section-block">
        <div class="section-heading">
          <div><h2>失败任务</h2><p class="muted-text">最近失败或中断的任务</p></div>
          <AlertCircle :size="18" class="icon-warning"/>
        </div>
        <div v-if="!data.tasks.length" class="empty-state"><h2>没有失败任务</h2></div>
        <div v-else class="table-wrap">
          <NDataTable :columns="columns" :data="data.tasks" :bordered="false" :single-line="true" size="medium" :row-key="taskRowKey"/>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.diagnostic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-8); }
.segmented { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-control); overflow: hidden; margin-bottom: var(--space-4); }
.segmented button { min-height: 34px; padding: 0 var(--space-4); border: 0; background: var(--surface); color: var(--text-secondary); cursor: pointer; }
.segmented button + button { border-left: 1px solid var(--border); }
.segmented button[aria-pressed="true"] { background: var(--surface-muted); color: var(--text); font-weight: 600; }
@media (max-width: 720px) { .diagnostic-grid { grid-template-columns: 1fr; } }
</style>
