<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RefreshCw, X } from "@lucide/vue";
import TaskRow from "./TaskRow.vue";
import { useOperations } from "../composables/useOperations";
import { useTasks } from "../composables/useTasks";
import { useBuildTasks } from "../composables/useBuildTasks";
import { useWorkspace } from "../composables/useWorkspace";
import { useTaskDrawer } from "../composables/useTaskDrawer";
import { elapsedLabel, operationKindLabel, stageLabel, targetRoute, taskOperationLabel, taskRoute, taskStatusLabel } from "../lib/operationView";
import { refreshActiveTasks } from "../lib/taskRefresh";
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
const refreshing = ref(false);
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
function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && drawer.open.value) close();
}
watch(() => drawer.open.value, open => {
  if (open) { now.value = Date.now(); void refresh(); }
});
onMounted(() => {
  timer = setInterval(() => { now.value = Date.now(); }, 1000);
  window.addEventListener("keydown", onKeydown);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  window.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="drawer.open.value" class="task-drawer-layer">
        <button class="task-drawer-backdrop" type="button" aria-label="关闭任务记录" @click="close"/>
        <aside class="task-drawer" role="dialog" aria-modal="true" aria-label="任务记录">
          <header class="task-drawer-header">
            <div><span class="eyebrow">本次会话</span><h2>任务记录</h2></div>
            <div class="task-drawer-actions">
              <button class="icon-button" type="button" title="刷新任务" :disabled="refreshing" @click="refresh"><RefreshCw :size="16" :class="{ spinning: refreshing }"/></button>
              <button class="icon-button" type="button" aria-label="关闭任务记录" @click="close"><X :size="18"/></button>
            </div>
          </header>
          <div class="task-drawer-body">
            <section class="task-drawer-section">
              <div class="task-drawer-section-head"><h3>进行中</h3><span>{{ activeRows.length }}</span></div>
              <div v-if="activeRows.length" class="task-list">
                <TaskRow v-for="row in activeRows" :key="row.id" :title="row.title" :target="row.target" :stage="row.stage"
                  :status-label="statusLabel(row.status)" :tone="statusTone(row.status) === 'danger' ? 'danger' : 'warning'"
                  :elapsed="elapsed(row)" :error="row.error">
                  <template #actions>
                    <RouterLink v-if="row.route" class="text-button" :to="row.route" @click="close">查看</RouterLink>
                    <button v-else-if="row.operation" class="icon-button" type="button" title="关闭记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
                  </template>
                </TaskRow>
              </div>
            </section>
            <section class="task-drawer-section">
              <div class="task-drawer-section-head"><h3>最近完成</h3><span>{{ finishedRows.length }}</span></div>
              <div v-if="finishedRows.length" class="task-list">
                <TaskRow v-for="row in finishedRows" :key="row.id" :title="row.title" :target="row.target" :stage="row.stage"
                  :status-label="statusLabel(row.status)" :tone="statusTone(row.status)" :elapsed="elapsed(row)" :error="row.error">
                  <template #actions>
                    <RouterLink v-if="row.route" class="text-button" :to="row.route" @click="close">查看</RouterLink>
                    <button v-if="row.operation" class="icon-button" type="button" title="移除记录" @click="operations.dismiss(row.operation.id)"><X :size="15"/></button>
                  </template>
                </TaskRow>
              </div>
            </section>
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.task-drawer-layer { position: fixed; inset: 0; z-index: 105; }
.task-drawer-backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(22, 19, 15, .28); }
.task-drawer { position: absolute; top: 0; right: 0; bottom: 0; display: flex; width: min(440px, 100vw); flex-direction: column; border-left: 1px solid var(--border-strong); background: var(--surface); box-shadow: -18px 0 50px rgba(25, 21, 15, .12); }
.task-drawer-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 24px 24px 18px; border-bottom: 1px solid var(--border); }
.task-drawer-header h2 { margin: 0; font-size: 21px; font-weight: 500; letter-spacing: -.02em; }
.task-drawer-actions { display: flex; gap: 2px; }
.task-drawer-body { flex: 1; overflow: auto; padding: 22px 24px 32px; }
.task-drawer-section + .task-drawer-section { margin-top: 32px; }
.task-drawer-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.task-drawer-section-head h3 { margin: 0; font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; }
.task-drawer-section-head span { color: var(--text-tertiary); font-size: 10px; }
.drawer-enter-active, .drawer-leave-active { transition: opacity 180ms ease; }
.drawer-enter-active .task-drawer, .drawer-leave-active .task-drawer { transition: transform 220ms ease; }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-from .task-drawer, .drawer-leave-to .task-drawer { transform: translateX(100%); }
.spinning { animation: spin 800ms linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 760px) { .task-drawer { width: 100vw; } }
</style>
