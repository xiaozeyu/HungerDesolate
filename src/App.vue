<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterView, useRoute, useRouter } from "vue-router";
import { isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { ArrowLeft, Cpu, FileText, Gauge, Globe2, HardDrive, List, Monitor, Moon, Package, Server, Settings, Sun, TerminalSquare, Wrench } from "@lucide/vue";
import { useWorkspace } from "./composables/useWorkspace";
import { useTheme } from "./composables/useTheme";
import { useTasks } from "./composables/useTasks";
import { useOperations } from "./composables/useOperations";
import { useBuildTasks } from "./composables/useBuildTasks";
import IconButton from "./components/IconButton.vue";
import { elapsedLabel, operationKindLabel, operationTargetLabel, stageLabel, taskOperationLabel, taskTargetLabel } from "./lib/operationView";
import { refreshActiveTasks } from "./lib/taskRefresh";
import { refreshBuildTasks } from "./lib/buildRefresh";
import type { TaskSnapshot } from "./types/contracts";
const route = useRoute();
const router = useRouter();
const workspace = useWorkspace();
const taskStore = useTasks();
const operations = useOperations();
const buildTasks = useBuildTasks();
const { theme, toggleTheme } = useTheme();
const activeServerId = computed(() => String(route.params.serverId ?? ""));
const activeInstanceId = computed(() => String(route.params.instanceId ?? ""));
const activeServer = computed(() => workspace.servers.value.find(server => server.serverId === activeServerId.value));
const activeInstance = computed(() => activeServer.value?.hostId ? workspace.instances(activeServer.value.hostId).find(instance => instance.instanceId === activeInstanceId.value) : undefined);
const breadcrumb = computed(() => [activeServer.value?.displayName, activeInstance.value?.displayName, String(route.meta.title ?? "主机")].filter(Boolean).join(" / "));
const instancePages = [
    { name: "overview", label: "概览", icon: Gauge }, { name: "world", label: "世界", icon: Globe2 },
    { name: "mods", label: "MOD", icon: Wrench }, { name: "backups", label: "备份", icon: HardDrive },
    { name: "diagnostics", label: "诊断", icon: TerminalSquare },
];
function hostRoute(page: string) { void router.push(activeServerId.value ? `/hosts/${activeServerId.value}/${page}` : "/hosts"); }

const now = ref(Date.now());
const runningOperation = computed(() => operations.running.value[0] ?? null);
const runningTask = computed(() => taskStore.tasks.value
    .filter(task => ["queued", "running"].includes(task.status))
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0))[0] ?? null);
const runningBuild = computed(() => buildTasks.all.value.find(entry => entry.status.status === "building") ?? null);
const activityCount = computed(() => operations.running.value.length
    + taskStore.tasks.value.filter(task => ["queued", "running"].includes(task.status)).length
    + (runningBuild.value ? 1 : 0));
const activityTitle = computed(() => runningOperation.value
    ? operationKindLabel(runningOperation.value.kind)
    : runningTask.value ? taskOperationLabel(runningTask.value.operation)
    : runningBuild.value ? "构建 DST 镜像" : "");
const activityStage = computed(() => runningOperation.value
    ? stageLabel(runningOperation.value.stage)
    : runningTask.value ? stageLabel(runningTask.value.stage)
    : runningBuild.value ? runningBuild.value.status.progress?.currentStep ?? "构建中" : "");
const activityTarget = computed(() => runningOperation.value
    ? operationTargetLabel(runningOperation.value.target, workspace.servers.value)
    : runningTask.value ? taskTargetLabel(runningTask.value, workspace.servers.value)
    : runningBuild.value ? operationTargetLabel({ serverId: runningBuild.value.serverId, hostId: runningBuild.value.hostId }, workspace.servers.value) : "");
const activityStartedAt = computed(() => runningOperation.value?.startedAt
    ?? (runningTask.value ? Date.parse(runningTask.value.createdAt) || now.value
      : runningBuild.value ? runningBuild.value.startedAt : null));
const activityElapsed = computed(() => activityStartedAt.value
    ? elapsedLabel(activityStartedAt.value, null, now.value) : "");

let unlisten: UnlistenFn | undefined;
let disposed = false;
let taskPoller: ReturnType<typeof setInterval> | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;
function pollTasks() {
    void refreshActiveTasks(taskStore.tasks.value, workspace.servers.value, task => taskStore.receive(task));
    void refreshBuildTasks(workspace.servers.value);
}
onMounted(async () => {
    await workspace.refresh();
    if (disposed) return;
    if (isTauri()) {
        const off = await listen<TaskSnapshot>("famine://task-updated", event => taskStore.receive(event.payload));
        if (disposed)
            off();
        else
            unlisten = off;
    }
    if (disposed) return;
    pollTasks();
    taskPoller = setInterval(pollTasks, 2500);
    clockTimer = setInterval(() => { now.value = Date.now(); }, 1000);
});
onUnmounted(() => {
    disposed = true;
    unlisten?.();
    if (taskPoller) clearInterval(taskPoller);
    if (clockTimer) clearInterval(clockTimer);
});
</script>


<template>
  <div class="app-shell">
    <aside class="app-sidebar">
      <div class="brand-row">
        <div class="brand-mark">
          荒
        </div>
        <div>
          <strong>
            荒野中枢
          </strong>
          <small>
            专服控制台
          </small>
        </div>
      </div>
      <nav class="app-nav" aria-label="主导航">
        <div v-if="!activeInstanceId" class="nav-group">
          <div class="nav-group-label">
            主机
          </div>
          <button class="nav-item" :class="{active:route.path==='/hosts'}" @click="router.push('/hosts')">
            <Server :size="17"/>
            主机列表
          </button>
          <template v-if="activeServer">
            <button class="nav-item" :class="{active:route.path.endsWith('/instances')}" @click="hostRoute('instances')">
              <List :size="17"/>
              专服列表
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/mods')}" @click="hostRoute('mods')">
              <Wrench :size="17"/>
              MOD 库
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/releases')}" @click="hostRoute('releases')">
              <Package :size="17"/>
              Release 管理
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/environment')}" @click="hostRoute('environment')">
              <Cpu :size="17"/>
              主机环境
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/connection')}" @click="hostRoute('connection')">
              <Monitor :size="17"/>
              连接设置
            </button>
          </template>
        </div>
        <div v-else class="nav-group">
          <button class="nav-item" @click="hostRoute('instances')">
            <ArrowLeft :size="17"/>
            专服列表
          </button>
          <div class="nav-group-label current-instance">
            当前：{{activeInstance?.displayName ?? '未知专服'}}
          </div>
          <button v-for="page in instancePages" :key="page.name" class="nav-item" :class="{active:route.path.endsWith('/'+page.name)}" @click="router.push(`/hosts/${activeServerId}/instances/${activeInstanceId}/${page.name}`)">
            <component :is="page.icon" :size="17"/>
            {{page.label}}
          </button>
        </div>
      </nav>
      <div class="sidebar-spacer"/>
      <div class="sidebar-footer">
        <button class="nav-item" :class="{active:route.path==='/settings'}" @click="router.push('/settings')">
          <Settings :size="17"/>
          应用设置
        </button>
        <div class="version-row">
          <span>
            版本
          </span>
          <span>
            1.0.1 Bate
          </span>
        </div>
      </div>
    </aside>
    <main class="app-main">
      <header class="app-topbar">
        <span class="breadcrumb">
          {{breadcrumb}}
        </span>
        <IconButton :label="theme==='light'?'切换暗色主题':'切换亮色主题'" @click="toggleTheme">
          <Moon v-if="theme==='light'" :size="17"/>
          <Sun v-else :size="17"/>
        </IconButton>
      </header>
      <section class="route-scroll">
        <RouterView/>
      </section>
      <footer class="operation-bar" aria-live="polite">
        <div class="operation-leading">
          <span class="status-dot" :class="runningOperation || runningTask || runningBuild ? 'warning' : 'success'"></span>
          <template v-if="runningOperation || runningTask || runningBuild">
            <strong>{{activityTitle}}</strong>
            <span>{{activityStage}}</span>
            <span class="muted-text">{{activityTarget}}</span>
            <span class="mono muted-text">{{activityElapsed}}</span>
          </template>
          <template v-else>
            <strong>{{activeInstance?.displayName ?? activeServer?.displayName ?? '荒野中枢'}}</strong>
            <span class="muted-text">{{activeServer ? 'SSH 已连接' : '从主机列表进入'}}</span>
          </template>
        </div>
        <button v-if="route.path!=='/tasks'" class="text-button" type="button" @click="router.push('/tasks')">
          <FileText :size="14"/>
          任务记录<span v-if="activityCount"> · {{activityCount}}</span>
        </button>
      </footer>
    </main>
  </div>
</template>
