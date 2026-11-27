<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import { RefreshCw, ChevronDown, ChevronUp, AlertCircle } from "@lucide/vue";
import {
  NButton, NCollapseTransition, NDataTable, NLog, NRadioButton, NRadioGroup,
  type DataTableColumns,
} from "naive-ui";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import IconButton from "../components/IconButton.vue";
import LoadingState from "../components/LoadingState.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { getInstanceDiagnostics, type DiagnosticsResponse } from "../api/diagnostics";
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
const logCollapsed = ref(false);
const shardLabel = (shard: "master" | "caves") => shard === "master" ? "主世界" : "洞穴";
function shardStatusLabel(status: string | null | undefined) {
  if (!status) return "未找到容器";
  if (status === "Up less than a second") return "已运行 不到 1 秒";
  if (status.startsWith("Up ")) {
    const value = status.slice(3)
      .replace(/^about an hour$/i, "约 1 小时")
      .replace(/^about a minute$/i, "约 1 分钟")
      .replace(/^an hour$/i, "1 小时")
      .replace(/^a minute$/i, "1 分钟")
      .replace(/\(healthy\)$/i, "（健康）")
      .replace(/\bweeks?\b/gi, "周")
      .replace(/\bdays?\b/gi, "天")
      .replace(/\bhours?\b/gi, "小时")
      .replace(/\bminutes?\b/gi, "分钟")
      .replace(/\bseconds?\b/gi, "秒");
    return `已运行 ${value}`;
  }
  if (status.startsWith("Exited")) return "已停止";
  if (status.startsWith("Restarting")) return "重启中";
  if (status.startsWith("Created")) return "已创建";
  if (status.startsWith("Paused")) return "已暂停";
  return status;
}
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
    <ErrorNotice :key="key ?? undefined" title="读取专服诊断失败" :error="error" :pending="loading"/>
    <StatusLabel v-if="stale && data" tone="warning" label="结果已过期"/>
    <LoadingState v-if="loading && !data" description="读取专服诊断…"/>
    <div v-else-if="!data" class="empty-state">
      <h2>{{ workspace.preview.value ? "诊断需要桌面连接" : "暂无诊断结果" }}</h2>
    </div>
    <template v-else>
      <section v-if="health?.warnings.length" class="environment-error" role="status">
        <strong>诊断发现</strong>
        <ul><li v-for="warning in health.warnings" :key="warning">{{ warningLabel(warning) }}</li></ul>
      </section>

      <section class="section-block">
        <div class="section-heading"><div><h2>分片</h2><p class="muted-text">容器状态</p></div></div>
        <div v-if="!health?.shards.length" class="muted-text">未采集到分片</div>
        <div v-else class="diagnostic-shard-grid">
          <div v-for="shard in health.shards" :key="shard.shard" class="diagnostic-shard">
            <strong>{{ shardLabel(shard.shard) }}</strong>
            <StatusLabel :tone="shard.status?.startsWith('Up') ? 'success' : 'muted'" :label="shardStatusLabel(shard.status)"/>
          </div>
        </div>
      </section>

      <section class="section-block">
        <div class="section-heading">
          <div><h2>分片日志</h2><p class="muted-text">最近 200 行，已脱敏</p></div>
        </div>
        <div class="diagnostic-log-toolbar">
          <NRadioGroup v-model:value="selectedShard" size="small">
            <NRadioButton v-for="log in data.logs" :key="log.shard" :value="log.shard">{{ shardLabel(log.shard) }}</NRadioButton>
          </NRadioGroup>
          <IconButton :label="logCollapsed ? '展开日志' : '收起日志'" @click="logCollapsed = !logCollapsed">
            <ChevronDown v-if="logCollapsed" :size="16"/>
            <ChevronUp v-else :size="16"/>
          </IconButton>
        </div>
        <NCollapseTransition :show="!logCollapsed">
          <div v-if="!shardLog?.available" class="muted-text">容器不可用，无法读取日志</div>
          <div v-else-if="!shardLog.lines.length" class="muted-text">暂无日志</div>
          <NLog v-else :log="shardLog.lines.join('\n')" :rows="12" class="diagnostic-log"/>
          <p v-if="shardLog?.truncated" class="muted-text log-truncated">日志已截断</p>
        </NCollapseTransition>
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
.diagnostic-shard-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.diagnostic-shard { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-width: 0; padding: 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.diagnostic-shard strong { font-size: 14px; }
.diagnostic-log-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.diagnostic-log { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: var(--surface-muted); }
.diagnostic-log :deep(.n-code) { background: transparent; }
.log-truncated { margin-top: 8px; }
@media (max-width: 720px) { .diagnostic-shard-grid { grid-template-columns: 1fr; } }
</style>
