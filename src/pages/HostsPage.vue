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
import FingerprintTrustDialog from "../components/FingerprintTrustDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useConnectionDialog } from "../composables/useConnectionDialog";
import { useHostConnection } from "../composables/useHostConnection";
import type { HostConnection, ServerSummary } from "../types/contracts";

const router = useRouter();
const workspace = useWorkspace();
const { openConnection } = useConnectionDialog();
const connection = useHostConnection();
const editorOpen = ref(false);
const saving = ref(false);
const addButton = ref<HTMLButtonElement | null>(null);
const serverForm = ref<{testConnection: () => void} | null>(null);
const servers = computed(() => workspace.servers.value);
function isVerified(host: ServerSummary) {
  return connection.verifiedServerVersions.value[host.serverId] === host.recordVersion;
}
const verifiedCount = computed(() => servers.value.filter(isVerified).length);
const pendingCount = computed(() => servers.value.filter(host => !host.hostId).length);
const summaryItems = computed(() => [
  { label: "主机", value: servers.value.length },
  { label: "已验证", value: verifiedCount.value, tone: "success" as const },
  { label: "待初始化", value: pendingCount.value, tone: "warning" as const },
]);

function hostState(host: ServerSummary): { label: string; tone: "success" | "warning" | "muted" } {
  if (isVerified(host)) return { label: "已验证", tone: "success" };
  if (!host.hostId) return { label: "待初始化", tone: "warning" };
  return { label: "未连接", tone: "muted" };
}
function authLabel(host: ServerSummary) {
  return host.authKind === "key" ? "SSH 密钥" : "密码";
}
function enterConnectedHost(connectionResult: HostConnection, host: ServerSummary) {
  if (connectionResult.managementState === "ready" && connectionResult.hostId) {
    void router.push(`/hosts/${host.serverId}/instances`);
  } else {
    void router.push(`/hosts/${host.serverId}/environment`);
  }
}
function connectHost(host: ServerSummary) {
  if (workspace.preview.value) {
    void router.push(`/hosts/${host.serverId}/instances`);
    return;
  }
  void connection.connect(host, enterConnectedHost);
}
async function setEditorOpen(open: boolean) {
  editorOpen.value = open;
  if (!open) { await nextTick(); addButton.value?.focus(); }
}
function saved() {
  void setEditorOpen(false);
}
async function tested(server: ServerSummary) {
  await setEditorOpen(false);
  const latest = workspace.servers.value.find(item => item.serverId === server.serverId) ?? server;
  connectHost(latest);
}
function testConnection() {
  serverForm.value?.testConnection();
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
    <div v-if="workspace.error.value" class="error-strip" role="alert">{{workspace.error.value}}</div>
    <div v-if="connection.error.value" class="error-strip" role="alert">
      <strong>{{ connection.error.value.message }}</strong>
      <span v-if="connection.error.value.recoverySteps.length"> {{ connection.error.value.recoverySteps.join("；") }}</span>
      <button v-if="connection.errorServerId.value" class="text-button" type="button" @click="openConnection(connection.errorServerId.value)">连接设置</button>
    </div>
    <div v-if="workspace.loading.value && !servers.length" class="empty-state" role="status"><p>读取连接记录…</p></div>
    <div v-else-if="!servers.length" class="empty-state"><Server :size="28"/><h2>还没有主机</h2><button class="button" @click="setEditorOpen(true)">添加第一台主机</button></div>
    <template v-else>
      <SummaryBar :items="summaryItems" label="主机摘要"/>
      <div class="table-wrap">
        <table>
          <thead><tr><th>主机</th><th>地址</th><th>认证</th><th>状态</th><th class="table-action"/></tr></thead>
          <tbody>
            <tr v-for="host in servers" :key="host.serverId">
              <td><div class="table-primary"><Server :size="17"/><strong>{{host.displayName}}</strong></div><small class="table-sub">{{host.username}}</small></td>
              <td class="mono">{{host.host}}:{{host.sshPort}}</td>
              <td>{{authLabel(host)}}</td>
              <td><StatusLabel :tone="hostState(host).tone" :label="hostState(host).label"/></td>
              <td class="table-action">
                <div class="host-actions">
                  <button class="text-button" type="button" :disabled="connection.busyServerId.value === host.serverId" @click="connectHost(host)">
                    {{ connection.busyServerId.value === host.serverId ? "连接中…" : workspace.preview.value || host.hostId || isVerified(host) ? "进入" : "连接" }} <ArrowRight :size="15"/>
                  </button>
                  <IconButton v-if="!workspace.preview.value" :label="`编辑${host.displayName}连接`" @click="openConnection(host.serverId)"><Pencil :size="16"/></IconButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <AppDialog :open="editorOpen" title="添加 Linux 主机" @update:open="setEditorOpen">
      <ServerForm v-if="editorOpen" ref="serverForm" form-id="new-server" :server="null" @saved="saved" @tested="tested" @busy="saving = $event"/>
      <template #footer><button class="button" @click="setEditorOpen(false)">关闭</button><button class="button" type="button" :disabled="saving" @click="testConnection">测试连接</button><button class="button primary" form="new-server" type="submit" :disabled="saving">{{saving ? '保存中…' : '保存主机'}}</button></template>
    </AppDialog>
    <FingerprintTrustDialog :open="connection.trustOpen.value" :display-name="connection.challengeServer.value?.displayName ?? ''" :host="connection.challengeServer.value?.host ?? ''" :ssh-port="connection.challengeServer.value?.sshPort ?? 22" :challenge="connection.challenge.value" :busy="connection.busyServerId.value === connection.challengeServer.value?.serverId" :error="connection.trustError.value" @update:open="value => { if (!value) connection.closeTrust() }" @confirm="connection.confirmTrust"/>
  </div>
</template>


<style scoped>
.host-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-height: 28px; }
</style>
