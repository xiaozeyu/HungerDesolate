<script setup lang="ts">
import { computed, ref, nextTick } from "vue";
import { Server, Plus, ArrowRight, Pencil } from "@lucide/vue";
import { useRouter } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
import IconButton from "../components/IconButton.vue";
import AppDialog from "../components/AppDialog.vue";
import ServerForm from "../components/ServerForm.vue";
import CredentialCleanupNotice from "../components/CredentialCleanupNotice.vue";
import { useWorkspace } from "../composables/useWorkspace";
import type { ServerSummary } from "../types/contracts";

const router = useRouter();
const workspace = useWorkspace();
const editorOpen = ref(false);
const saving = ref(false);
const savedMessage = ref("");
const savedServerId = ref("");
const addButton = ref<HTMLButtonElement | null>(null);
const servers = computed(() => workspace.servers.value);
const connectedCount = computed(() => servers.value.filter(host => host.connectionState === "connected").length);
const pendingCount = computed(() => servers.value.filter(host => !host.hostId).length);
const summaryItems = computed(() => [
  { label: "主机", value: servers.value.length },
  { label: "已连接", value: connectedCount.value, tone: "success" as const },
  { label: "待初始化", value: pendingCount.value, tone: "warning" as const },
]);

function hostState(host: ServerSummary): { label: string; tone: "success" | "warning" | "danger" | "muted" } {
  if (!host.hostId) return { label: "待初始化", tone: "warning" };
  if (host.connectionState === "connected") return { label: "已连接", tone: "success" };
  if (host.connectionState === "connecting") return { label: "连接中", tone: "warning" };
  if (host.connectionState === "unreachable") return { label: "不可达", tone: "danger" };
  if (host.connectionState === "trustRequired") return { label: "待确认指纹", tone: "warning" };
  return { label: "未连接", tone: "muted" };
}
function authLabel(host: ServerSummary) {
  return host.authKind === "key" ? "SSH 密钥" : "密码";
}
function openHost(host: ServerSummary) {
  const page = workspace.preview.value || host.hostId ? "instances" : "connection";
  void router.push(`/hosts/${host.serverId}/${page}`);
}
async function setEditorOpen(open: boolean) {
  editorOpen.value = open;
  if (!open) { await nextTick(); addButton.value?.focus(); }
}
function saved(server: ServerSummary) {
  savedMessage.value = `已添加“${server.displayName}”`;
  savedServerId.value = server.serverId;
  void setEditorOpen(false);
}
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="主机管理" title="主机列表">
      <template #actions>
        <button ref="addButton" class="button primary" :disabled="workspace.preview.value" @click="setEditorOpen(true)"><Plus :size="16"/>添加主机</button>
      </template>
    </PageHeader>
    <CredentialCleanupNotice/>
    <p v-if="savedMessage" role="status" class="success-message">{{savedMessage}}<button v-if="savedServerId" class="text-button" @click="router.push(`/hosts/${savedServerId}/connection`)">配置连接</button></p>
    <div v-if="workspace.error.value" class="error-strip" role="alert">{{workspace.error.value}}</div>
    <div v-if="workspace.loading.value && !servers.length" class="empty-state" role="status"><p>读取连接记录…</p></div>
    <div v-else-if="!servers.length" class="empty-state"><Server :size="28"/><h2>还没有主机</h2><button class="button" @click="setEditorOpen(true)">添加第一台主机</button></div>
    <template v-else>
      <SummaryBar :items="summaryItems" label="主机摘要"/>
      <div class="table-wrap">
        <table>
          <thead><tr><th>主机</th><th>地址</th><th>认证</th><th>状态</th><th>记录版本</th><th class="table-action"/></tr></thead>
          <tbody>
            <tr v-for="host in servers" :key="host.serverId">
              <td><div class="table-primary"><Server :size="17"/><strong>{{host.displayName}}</strong></div><small class="table-sub">{{host.username}}</small></td>
              <td class="mono">{{host.host}}:{{host.sshPort}}</td>
              <td>{{authLabel(host)}}</td>
              <td><StatusLabel :tone="hostState(host).tone" :label="hostState(host).label"/></td>
              <td class="mono">v{{host.recordVersion}}</td>
              <td class="table-action">
                <div class="host-actions">
                  <button class="text-button" type="button" @click="openHost(host)">
                    {{workspace.preview.value || host.hostId ? "进入" : "配置连接"}} <ArrowRight :size="15"/>
                  </button>
                  <IconButton v-if="!workspace.preview.value" :label="`编辑${host.displayName}连接`" @click="router.push(`/hosts/${host.serverId}/connection`)"><Pencil :size="16"/></IconButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <AppDialog :open="editorOpen" title="添加 Linux 主机" @update:open="setEditorOpen">
      <ServerForm v-if="editorOpen" form-id="new-server" :server="null" @saved="saved" @busy="saving = $event"/>
      <template #footer><button class="button" @click="setEditorOpen(false)">关闭</button><button class="button primary" form="new-server" type="submit" :disabled="saving">{{saving ? '保存中…' : '保存主机'}}</button></template>
    </AppDialog>
  </div>
</template>


<style scoped>
.host-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-height: 28px; }
</style>
