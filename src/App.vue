<script setup lang="ts">
import { computed, onMounted, onUnmounted } from "vue";
import { RouterView, useRoute, useRouter } from "vue-router";
import { isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { ArrowLeft, Cpu, Database, Gauge, Globe2, HardDrive, List, Monitor, Moon, Package, Server, Settings, Sun, TerminalSquare, Wrench } from "@lucide/vue";
import { useWorkspace } from "./composables/useWorkspace";
import { useTheme } from "./composables/useTheme";
import { useTasks } from "./composables/useTasks";
import AppSelect from "./components/AppSelect.vue";
import IconButton from "./components/IconButton.vue";
import type { TaskSnapshot } from "./types/contracts";
const route = useRoute();
const router = useRouter();
const workspace = useWorkspace();
const taskStore = useTasks();
const { theme, toggleTheme } = useTheme();
const activeServerId = computed(() => String(route.params.serverId ?? ""));
const activeInstanceId = computed(() => String(route.params.instanceId ?? ""));
const activeServer = computed(() => workspace.servers.value.find(server => server.serverId === activeServerId.value));
const activeInstance = computed(() => activeServer.value?.hostId ? workspace.instances(activeServer.value.hostId).find(instance => instance.instanceId === activeInstanceId.value) : undefined);
const hostOptions = computed(() => workspace.servers.value.map(server => ({ label: server.displayName, value: server.serverId })));
const breadcrumb = computed(() => [activeServer.value?.displayName, activeInstance.value?.displayName, String(route.meta.title ?? "主机")].filter(Boolean).join(" / "));
const instancePages = [
    { name: "overview", label: "概览", icon: Gauge }, { name: "world", label: "世界", icon: Globe2 },
    { name: "mods", label: "MOD", icon: Wrench }, { name: "backups", label: "备份", icon: HardDrive },
    { name: "updates", label: "更新", icon: Database }, { name: "diagnostics", label: "诊断", icon: TerminalSquare },
];
function goToServer(serverId: string) { void router.push(`/hosts/${serverId}/instances`); }
function hostRoute(page: string) { void router.push(activeServerId.value ? `/hosts/${activeServerId.value}/${page}` : "/hosts"); }
let unlisten: UnlistenFn | undefined;
let disposed = false;
onMounted(async () => {
    await workspace.refresh();
    if (isTauri()) {
        const off = await listen<TaskSnapshot>("famine://task-updated", event => taskStore.receive(event.payload));
        if (disposed)
            off();
        else
            unlisten = off;
    }
});
onUnmounted(() => { disposed = true; unlisten?.(); });
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
      <AppSelect :model-value="activeServerId" :options="hostOptions" label="选择主机" placeholder="选择主机" :disabled="hostOptions.length === 0" @update:model-value="goToServer">
        <template #icon>
          <Server :size="16"/>
        </template>
      </AppSelect>
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
            0.2.0
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
    </main>
  </div>
</template>
