<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RefreshCw, X } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import SummaryBar from "../components/SummaryBar.vue";
import TaskRow from "../components/TaskRow.vue";
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
  stage: task.queuePosition ? `等待资源 · 第 ${task.queuePosition} 位` : stageLabel(task.stage),
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
const taskSummary = computed(() => [
  { label: "进行中", value: activeRows.value.length, tone: activeRows.value.length ? "warning" as const : "default" as const },
  { label: "最近完成", value: finishedRows.value.length },
  { label: "失败待处理", value: rows.value.filter(row => row.status === "failed").length, tone: rows.value.some(row => row.status === "failed") ? "danger" as const : "default" as const },
]);

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

    <SummaryBar :items="taskSummary" label="任务记录摘要"/>

    <section class="section-block">
      <div class="section-heading"><div><h2>进行中</h2></div><span class="muted-text">切换页面不会停止任务</span></div>
      <div v-if="activeRows.length" class="task-list">
        <TaskRow v-for="row in activeRows" :key="row.id" :title="row.title" :target="row.target" :stage="row.stage"
          :status-label="statusLabel(row.status)" :tone="statusTone(row.status) === 'danger' ? 'danger' : 'warning'"
          :elapsed="elapsed(row)" :error="row.error">
          <template #actions>
            <RouterLink v-if="row.route" class="text-button" :to="row.route">查看</RouterLink>
            <button v-else-if="row.operation" class="icon-button" type="button" title="关闭记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
          </template>
        </TaskRow>
      </div>
      <div v-else class="empty-state task-empty"><h2>没有进行中的任务</h2><p>部署、启动、备份等长操作会集中显示在这里。</p></div>
    </section>

    <section class="section-block">
      <div class="section-heading"><div><h2>最近完成</h2></div><span class="muted-text">失败任务保留现场与错误信息</span></div>
      <div v-if="finishedRows.length" class="task-list">
        <TaskRow v-for="row in finishedRows" :key="row.id" :title="row.title" :target="row.target" :stage="row.stage"
          :status-label="statusLabel(row.status)" :tone="statusTone(row.status)" :elapsed="elapsed(row)" :error="row.error">
          <template #actions>
            <RouterLink v-if="row.route" class="text-button" :to="row.route">查看</RouterLink>
            <button v-if="row.operation" class="icon-button" type="button" title="移除记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
          </template>
        </TaskRow>
      </div>
      <div v-else class="empty-state task-empty"><h2>暂无任务记录</h2></div>
    </section>
  </div>
</template>
