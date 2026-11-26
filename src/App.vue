<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterView, useRoute, useRouter } from "vue-router";
import { isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { ArrowLeft, Cpu, FileText, Gauge, Globe2, HardDrive, List, Moon, Package, Server, Settings, Sun, TerminalSquare, Users, Wrench } from "@lucide/vue";
import { NButton, NConfigProvider, NLayout, NLayoutContent, NLayoutFooter, NLayoutHeader, NLayoutSider, darkTheme, lightTheme, zhCN } from "naive-ui";
import { useWorkspace } from "./composables/useWorkspace";
import { buildThemeOverrides } from "./naive/theme";
import { useTheme } from "./composables/useTheme";
import { useTasks } from "./composables/useTasks";
import { useOperations } from "./composables/useOperations";
import { useBuildTasks } from "./composables/useBuildTasks";
import { useTaskDrawer } from "./composables/useTaskDrawer";
import { getBootstrapSnapshot } from "./api/bootstrap";
import IconButton from "./components/IconButton.vue";
import TaskDrawer from "./components/TaskDrawer.vue";
import ConnectionSettingsDialog from "./components/ConnectionSettingsDialog.vue";
import { operationKindLabel, operationTargetLabel, taskOperationLabel, taskTargetLabel } from "./lib/operationView";
import { refreshActiveTasks } from "./lib/taskRefresh";
import { refreshBuildTasks } from "./lib/buildRefresh";
import type { TaskSnapshot } from "./types/contracts";
const route = useRoute();
const router = useRouter();
const workspace = useWorkspace();
const taskStore = useTasks();
const operations = useOperations();
const buildTasks = useBuildTasks();
const { openDrawer: openTaskDrawer } = useTaskDrawer();
const { theme, toggleTheme } = useTheme();
const naiveTheme = computed(() => theme.value === "dark" ? darkTheme : lightTheme);
const themeOverrides = computed(() => buildThemeOverrides(theme.value === "dark"));
const appVersion = ref(__APP_VERSION__);
const settingsReturnPath = ref("/hosts");
watch(() => route.fullPath, (path, previous) => {
  if (path === "/settings" && previous && previous !== "/settings") settingsReturnPath.value = previous;
}, { immediate: true });
const contextRoute = computed(() => route.path === "/settings" ? router.resolve(settingsReturnPath.value) : route);
const activeServerId = computed(() => String(contextRoute.value.params.serverId ?? ""));
const activeInstanceId = computed(() => String(contextRoute.value.params.instanceId ?? ""));
const activeServer = computed(() => workspace.servers.value.find(server => server.serverId === activeServerId.value));
const activeInstance = computed(() => activeServer.value?.hostId ? workspace.instances(activeServer.value.hostId).find(instance => instance.instanceId === activeInstanceId.value) : undefined);
const instancePages = [
    { name: "overview", label: "概览", icon: Gauge }, { name: "players", label: "玩家", icon: Users },
    { name: "world", label: "世界", icon: Globe2 }, { name: "mods", label: "MOD", icon: Wrench },
    { name: "backups", label: "备份", icon: HardDrive }, { name: "diagnostics", label: "诊断", icon: TerminalSquare },
];
function hostRoute(page: string) { void router.push(activeServerId.value ? `/hosts/${activeServerId.value}/${page}` : "/hosts"); }

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
const activityTarget = computed(() => runningOperation.value
    ? operationTargetLabel(runningOperation.value.target, workspace.servers.value)
    : runningTask.value ? taskTargetLabel(runningTask.value, workspace.servers.value)
    : runningBuild.value ? operationTargetLabel({ serverId: runningBuild.value.serverId, hostId: runningBuild.value.hostId }, workspace.servers.value) : "");
let unlisten: UnlistenFn | undefined;
let disposed = false;
let taskPoller: ReturnType<typeof setInterval> | undefined;
function pollTasks() {
    void refreshActiveTasks(taskStore.tasks.value, workspace.servers.value, task => taskStore.receive(task));
    void refreshBuildTasks(workspace.servers.value);
}
onMounted(async () => {
    try {
        appVersion.value = (await getBootstrapSnapshot()).productVersion;
    } catch {
        // Browser preview has no Tauri bootstrap; keep the package version.
    }
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
});
onUnmounted(() => {
    disposed = true;
    unlisten?.();
    if (taskPoller) clearInterval(taskPoller);
});
</script>


<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="themeOverrides" :locale="zhCN">
  <NLayout class="app-shell naive-app" has-sider style="height:100dvh">
    <NLayoutSider class="app-sidebar" :width="216" :native-scrollbar="true" content-style="display:flex;flex-direction:column;height:100%;gap:30px;padding:28px 20px 18px;box-sizing:border-box;">
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
          <button class="nav-item" :class="{active:route.path==='/hosts'}" :aria-current="route.path==='/hosts' ? 'page' : undefined" @click="router.push('/hosts')">
            <Server :size="17"/>
            主机列表
          </button>
          <template v-if="activeServer">
            <button class="nav-item" :class="{active:route.path.endsWith('/instances')}" :aria-current="route.path.endsWith('/instances') ? 'page' : undefined" @click="hostRoute('instances')">
              <List :size="17"/>
              专服列表
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/mods')}" :aria-current="route.path.endsWith('/mods') ? 'page' : undefined" @click="hostRoute('mods')">
              <Wrench :size="17"/>
              MOD 库
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/releases')}" :aria-current="route.path.endsWith('/releases') ? 'page' : undefined" @click="hostRoute('releases')">
              <Package :size="17"/>
              Release 管理
            </button>
            <button class="nav-item" :class="{active:route.path.endsWith('/environment')}" :aria-current="route.path.endsWith('/environment') ? 'page' : undefined" @click="hostRoute('environment')">
              <Cpu :size="17"/>
              主机环境
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
          <button v-for="page in instancePages" :key="page.name" class="nav-item" :class="{active:route.path.endsWith('/'+page.name)}" :aria-current="route.path.endsWith('/'+page.name) ? 'page' : undefined" @click="router.push(`/hosts/${activeServerId}/instances/${activeInstanceId}/${page.name}`)">
            <component :is="page.icon" :size="17"/>
            {{page.label}}
          </button>
        </div>
      </nav>
      <div class="sidebar-spacer"/>
      <div class="sidebar-footer">
        <button class="nav-item" :class="{active:route.path==='/settings'}" :aria-current="route.path==='/settings' ? 'page' : undefined" @click="router.push('/settings')">
          <Settings :size="17"/>
          应用设置
        </button>
        <div class="version-row">
          <span>
            版本
          </span>
          <span>
            {{ appVersion }} 正式版
          </span>
        </div>
      </div>
    </NLayoutSider>
    <NLayout class="app-main">
      <NLayoutHeader class="app-topbar">
        <div class="topbar-leading">
          <IconButton v-if="route.path==='/settings'" label="返回上一页" @click="router.push(settingsReturnPath)">
            <ArrowLeft :size="17"/>
          </IconButton>
        </div>
        <IconButton :label="theme==='light'?'切换暗色主题':'切换亮色主题'" @click="toggleTheme">
          <Moon v-if="theme==='light'" :size="17"/>
          <Sun v-else :size="17"/>
        </IconButton>
      </NLayoutHeader>
      <NLayoutContent class="route-content">
        <section class="route-scroll">
          <RouterView/>
        </section>
      </NLayoutContent>
      <NLayoutFooter class="operation-bar" aria-live="polite">
        <div class="operation-leading">
          <template v-if="runningOperation || runningTask || runningBuild">
            <span class="status-dot warning"></span>
            <strong>{{activityTitle}}</strong>
            <span class="muted-text">{{activityTarget}}</span>
          </template>
        </div>
        <NButton text size="small" class="operation-task-button" @click="openTaskDrawer">
          <template #icon><FileText :size="14"/></template>
          任务记录<span v-if="activityCount"> · {{activityCount}}</span>
        </NButton>
      </NLayoutFooter>
    </NLayout>
  </NLayout>
  <TaskDrawer/>
  <ConnectionSettingsDialog/>
  </NConfigProvider>
</template>
