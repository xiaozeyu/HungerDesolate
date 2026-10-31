<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RefreshCw, X } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { useOperations } from "../composables/useOperations";
import { useTasks } from "../composables/useTasks";
import { useBuildTasks } from "../composables/useBuildTasks";
import { useWorkspace } from "../composables/useWorkspace";
import { elapsedLabel, operationKindLabel, stageLabel, targetRoute, taskOperationLabel, taskRoute, taskStatusLabel } from "../lib/operationView";
import { refreshActiveTasks } from "../lib/taskRefresh";
import { refreshBuildTasks } from "../lib/buildRefresh";
import type { OperationEntry } from "../composables/useOperations";
import type { HostTarget, InstanceTarget, TaskSnapshot, TaskTarget } from "../types/contracts";

const operations = useOperations();
const taskStore = useTasks();
const buildTasks = useBuildTasks();
const workspace = useWorkspace();
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;
let unmounted = false;

function displayTarget(target: TaskTarget | HostTarget | InstanceTarget): string {
  if (!("hostId" in target)) return "本机任务";
  const server = workspace.servers.value.find(item => item.serverId === target.serverId);
  const instance = "instanceId" in target
    ? workspace.instances(target.hostId).find(item => item.instanceId === target.instanceId)
    : null;
  return [server?.displayName ?? target.hostId.slice(0, 8), instance?.displayName].filter(Boolean).join(" / ");
}

interface Row {
  id: string;
  title: string;
  target: string;
  stage: string;
  status: string;
  startedAt: number;
  finishedAt: number | null;
  error: string | null;
  route: string | null;
  operation: OperationEntry | null;
  task: TaskSnapshot | null;
}

const operationRows = computed<Row[]>(() => operations.all.value.map(operation => ({
  id: `op-${operation.id}`,
  title: operation.title || operationKindLabel(operation.kind),
  target: displayTarget(operation.target),
  stage: operation.stage,
  status: operation.status,
  startedAt: operation.startedAt,
  finishedAt: operation.finishedAt,
  error: operation.error?.message ?? null,
  route: targetRoute(operation.target),
  operation,
  task: null,
})));

const taskRows = computed<Row[]>(() => taskStore.tasks.value.map(task => ({
  id: `task-${task.taskId}`,
  title: taskOperationLabel(task.operation),
  target: displayTarget(task.target),
  stage: task.queuePosition ? `排队中 · 第 ${task.queuePosition} 位` : stageLabel(task.stage),
  status: task.status,
  startedAt: Date.parse(task.createdAt) || Date.now(),
  finishedAt: task.finishedAt ? Date.parse(task.finishedAt) : null,
  error: task.error?.message ?? null,
  route: taskRoute(task),
  operation: null,
  task,
})));

const buildRows = computed<Row[]>(() => buildTasks.all.value.map(entry => {
  const status = entry.status.status;
  return {
    id: `build-${entry.hostId}`,
    title: "构建 DST 镜像",
    target: displayTarget({ serverId: entry.serverId, hostId: entry.hostId }),
    stage: entry.status.progress?.currentStep
      ?? (status === "building" ? "构建中" : status === "completed" ? "已完成" : status === "failed" ? "构建失败" : "构建任务不存在"),
    status: status === "building" ? "running" : status === "completed" ? "succeeded" : status === "failed" ? "failed" : "interrupted",
    startedAt: entry.startedAt,
    finishedAt: status === "building" ? null : entry.updatedAt,
    error: status === "failed" ? entry.status.logTail.at(-1) ?? null : null,
    route: `/hosts/${entry.serverId}/releases`,
    operation: null,
    task: null,
  };
}));
const rows = computed(() => [...operationRows.value, ...taskRows.value, ...buildRows.value].sort((left, right) => right.startedAt - left.startedAt));
const activeRows = computed(() => rows.value.filter(row => row.status === "running" || row.status === "queued"));
const finishedRows = computed(() => rows.value.filter(row => row.status !== "running" && row.status !== "queued"));

function statusLabel(status: string) {
  return status === "running" ? "进行中" : status === "queued" ? "排队中"
    : status === "succeeded" ? "已完成" : status === "failed" ? "失败"
    : status === "interrupted" ? "待核实" : status === "cancelled" ? "已取消" : taskStatusLabel(status);
}
function statusTone(status: string) {
  return status === "succeeded" ? "success" : status === "failed" ? "danger"
    : status === "interrupted" || status === "running" ? "warning" : "muted";
}
function elapsed(row: Row) {
  return elapsedLabel(row.startedAt, row.finishedAt, now.value);
}
async function refresh() {
  if (unmounted) return;
  await refreshActiveTasks(taskStore.tasks.value, workspace.servers.value, next => taskStore.receive(next));
  await refreshBuildTasks(workspace.servers.value);
}

onMounted(() => {
  timer = setInterval(() => { now.value = Date.now(); }, 1000);
});
onUnmounted(() => {
  unmounted = true;
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="应用" title="任务记录">
      <template #actions>
        <button class="button" type="button" @click="refresh"><RefreshCw :size="16"/>刷新</button>
      </template>
    </PageHeader>

    <div class="task-summary">
      <div><span class="metric-number">{{ activeRows.length }}</span><span>进行中</span></div>
      <span class="summary-divider"/>
      <div><span class="metric-number">{{ finishedRows.length }}</span><span>最近完成</span></div>
      <span class="summary-divider"/>
      <div><span class="metric-number warning-number">{{ rows.filter(row => row.status === "failed").length }}</span><span>失败待处理</span></div>
    </div>

    <section class="section-block">
      <div class="section-heading"><div><h2>进行中</h2></div><span class="muted-text">切换页面不会停止任务</span></div>
      <div v-if="activeRows.length" class="table-wrap">
        <table>
          <thead><tr><th>目标</th><th>操作</th><th>阶段</th><th>耗时</th><th>状态</th><th/></tr></thead>
          <tbody>
            <tr v-for="row in activeRows" :key="row.id">
              <td><strong>{{row.target}}</strong></td>
              <td>{{row.title}}</td>
              <td>{{row.stage}}</td>
              <td class="mono">{{elapsed(row)}}</td>
              <td><StatusLabel :tone="statusTone(row.status)" :label="statusLabel(row.status)"/></td>
              <td class="table-action">
                <RouterLink v-if="row.route" class="text-button" :to="row.route">查看</RouterLink>
                <button v-else-if="row.operation" class="icon-button" type="button" title="关闭记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state task-empty"><h2>没有进行中的任务</h2><p>部署、启动、备份等长操作会集中显示在这里。</p></div>
    </section>

    <section class="section-block">
      <div class="section-heading"><div><h2>最近完成</h2></div><span class="muted-text">失败任务保留现场与错误信息</span></div>
      <div v-if="finishedRows.length" class="table-wrap">
        <table>
          <thead><tr><th>目标</th><th>操作</th><th>阶段</th><th>耗时</th><th>状态</th><th/></tr></thead>
          <tbody>
            <tr v-for="row in finishedRows" :key="row.id">
              <td>
                <strong>{{row.target}}</strong>
                <small v-if="row.error" class="table-sub">{{row.error}}</small>
              </td>
              <td>{{row.title}}</td>
              <td>{{row.stage}}</td>
              <td class="mono">{{elapsed(row)}}</td>
              <td><StatusLabel :tone="statusTone(row.status)" :label="statusLabel(row.status)"/></td>
              <td class="table-action">
                <RouterLink v-if="row.route" class="text-button" :to="row.route">查看</RouterLink>
                <button v-if="row.operation" class="icon-button" type="button" title="移除记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state task-empty"><h2>暂无任务记录</h2></div>
    </section>
  </div>
</template>
