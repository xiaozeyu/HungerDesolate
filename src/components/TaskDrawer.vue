<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RefreshCw, Trash2, X } from "@lucide/vue";
import { NDrawer, NDrawerContent, NEmpty, NPopconfirm, NTabPane, NTabs } from "naive-ui";
import TaskRow from "./TaskRow.vue";
import IconButton from "./IconButton.vue";
import { useOperations } from "../composables/useOperations";
import { useTasks } from "../composables/useTasks";
import { useBuildTasks } from "../composables/useBuildTasks";
import { useWorkspace } from "../composables/useWorkspace";
import { useTaskDrawer } from "../composables/useTaskDrawer";
import { elapsedLabel, operationKindLabel, stageLabel, targetRoute, taskOperationLabel, taskRoute, taskStatusLabel } from "../lib/operationView";
import { reconcileTasks, refreshActiveTasks } from "../lib/taskRefresh";
import { refreshBuildTasks } from "../lib/buildRefresh";
import type { OperationEntry } from "../composables/useOperations";
import type { HostTarget, InstanceTarget, TaskSnapshot, TaskTarget } from "../types/contracts";

const sessionStartedAt = Date.now();
const drawer = useTaskDrawer();
const operations = useOperations();
const taskStore = useTasks();
const buildTasks = useBuildTasks();
const workspace = useWorkspace();
const now = ref(Date.now());
const activeTab = ref("active");
const refreshing = ref(false);
const cleaning = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;

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
}

const operationRows = computed<Row[]>(() => operations.all.value.map(operation => ({
  id: `op-${operation.id}`,
  title: operation.title || operationKindLabel(operation.kind),
  target: displayTarget(operation.target),
  stage: operation.detail ?? operation.stage,
  status: operation.status,
  startedAt: operation.startedAt,
  finishedAt: operation.finishedAt,
  error: operation.error?.message ?? null,
  route: targetRoute(operation.target),
  operation,
})));

const taskRows = computed<Row[]>(() => taskStore.tasks.value.map((task: TaskSnapshot) => ({
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
  };
}));

const sessionRows = computed(() => [...operationRows.value, ...taskRows.value, ...buildRows.value]
  .filter(row => row.startedAt >= sessionStartedAt)
  .sort((left, right) => right.startedAt - left.startedAt));
const activeRows = computed(() => sessionRows.value.filter(row => row.status === "running" || row.status === "queued"));
const finishedRows = computed(() => sessionRows.value.filter(row => row.status !== "running" && row.status !== "queued"));

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
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await refreshActiveTasks(taskStore.tasks.value, workspace.servers.value, next => taskStore.receive(next));
    await refreshBuildTasks(workspace.servers.value);
  } finally {
    refreshing.value = false;
  }
}
function close() { drawer.closeDrawer(); }
async function cleanupStaleTasks() {
  if (cleaning.value) return;
  cleaning.value = true;
  try {
    await reconcileTasks(taskStore.tasks.value, workspace.servers.value,
      task => taskStore.receive(task), taskId => taskStore.discard(taskId));
  } finally {
    cleaning.value = false;
  }
}
watch(() => drawer.open.value, open => {
  if (open) { now.value = Date.now(); void refresh(); }
});
onMounted(() => {
  timer = setInterval(() => { now.value = Date.now(); }, 1000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <NDrawer :show="drawer.open.value" placement="right" :width="440" @update:show="value => { if (!value) close(); }">
    <NDrawerContent :closable="false" title="任务记录">
      <template #header>
        <header class="task-drawer-header">
          <div><span class="eyebrow">本次会话</span><h2>任务记录</h2></div>
          <div class="task-drawer-actions">
            <IconButton label="刷新任务" :disabled="refreshing" @click="refresh"><RefreshCw :size="16" :class="{ spinning: refreshing }"/></IconButton>
            <NPopconfirm @positive-click="cleanupStaleTasks">
              <template #trigger>
                <span><IconButton label="清理失效的本地任务记录" :disabled="cleaning"><Trash2 :size="16"/></IconButton></span>
              </template>
              清理已失效的本地任务记录？远端任务不受影响。
            </NPopconfirm>
            <IconButton label="关闭任务记录" @click="close"><X :size="18"/></IconButton>
          </div>
        </header>
      </template>
      <div class="task-drawer-body">
        <NTabs v-model:value="activeTab" type="line" size="small" animated class="task-tabs">
          <NTabPane name="active" :tab="`进行中${activeRows.length ? ` · ${activeRows.length}` : ''}`">
            <TransitionGroup v-if="activeRows.length" name="task-list" tag="div" class="task-list">
              <TaskRow v-for="row in activeRows" :key="row.id" :title="row.title" :target="row.target" :stage="row.stage"
                :status-label="statusLabel(row.status)" :tone="statusTone(row.status) === 'danger' ? 'danger' : 'warning'"
                :elapsed="elapsed(row)" :error="row.error" :running="row.status === 'running' || row.status === 'queued'">
                <template #actions>
                  <RouterLink v-if="row.route" class="text-button" :to="row.route" @click="close">查看</RouterLink>
                  <button v-else-if="row.operation" class="icon-button" type="button" title="关闭记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
                </template>
              </TaskRow>
            </TransitionGroup>
            <NEmpty v-else size="small" description="没有进行中的任务"/>
          </NTabPane>
          <NTabPane name="finished" :tab="`最近完成${finishedRows.length ? ` · ${finishedRows.length}` : ''}`">
            <TransitionGroup v-if="finishedRows.length" name="task-list" tag="div" class="task-list">
              <TaskRow v-for="row in finishedRows" :key="row.id" :title="row.title" :target="row.target" :stage="row.stage"
                :status-label="statusLabel(row.status)" :tone="statusTone(row.status)" :elapsed="elapsed(row)" :error="row.error">
                <template #actions>
                  <RouterLink v-if="row.route" class="text-button" :to="row.route" @click="close">查看</RouterLink>
                  <button v-if="row.operation" class="icon-button" type="button" title="移除记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
                </template>
              </TaskRow>
            </TransitionGroup>
            <NEmpty v-else size="small" description="本次会话还没有完成的任务"/>
          </NTabPane>
        </NTabs>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.task-drawer-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.task-drawer-header h2 { margin: 0; font-size: 21px; font-weight: 500; letter-spacing: -.02em; }
.task-drawer-actions { display: flex; gap: 2px; }
.task-drawer-body { min-height: 0; }
.task-tabs { margin-top: -6px; }
.task-tabs :deep(.n-tab-pane) { padding-top: 12px; }
.task-tabs :deep(.n-empty) { padding: 28px 0; }
.task-list-enter-active, .task-list-leave-active { transition: opacity 160ms ease, transform 160ms ease; }
.task-list-enter-from, .task-list-leave-to { opacity: 0; transform: translateY(-4px); }
.task-list-move { transition: transform 180ms ease; }
.spinning { animation: spin 800ms linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 760px) { :deep(.n-drawer) { width: 100vw !important; } }
</style>
