<script setup lang="ts">
import { computed, h, ref, nextTick } from "vue";
import { Server, Plus, ArrowRight, Pencil, Trash2 } from "@lucide/vue";
import { useRouter } from "vue-router";
import { isTauri } from "@tauri-apps/api/core";
import { NButton, NDataTable, type DataTableColumns } from "naive-ui";
import { notifySuccess } from "../naive/notify";
import { describeError } from "../lib/commandError";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import IconButton from "../components/IconButton.vue";
import AppDialog from "../components/AppDialog.vue";
import ServerForm from "../components/ServerForm.vue";
import CredentialCleanupNotice from "../components/CredentialCleanupNotice.vue";
import FingerprintTrustDialog from "../components/FingerprintTrustDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useConnectionDialog } from "../composables/useConnectionDialog";
import { useHostConnection } from "../composables/useHostConnection";
import type { DeleteServerRequest, HostConnection, ServerSummary } from "../types/contracts";

const router = useRouter();
const workspace = useWorkspace();
const { openConnection } = useConnectionDialog();
const connection = useHostConnection();
const editorOpen = ref(false);
const saving = ref(false);
const addButton = ref<HTMLSpanElement | null>(null);
const serverForm = ref<{testConnection: () => void} | null>(null);
const deleteOpen = ref(false);
const deleteBusy = ref(false);
const deleteError = ref("");
const deleteTarget = ref<ServerSummary | null>(null);
let deleteRequest: DeleteServerRequest | null = null;
const servers = computed(() => workspace.servers.value);
const rowKey = (host: ServerSummary) => host.serverId;

function isVerified(host: ServerSummary) {
  return connection.verifiedServerVersions.value[host.serverId] === host.recordVersion;
}
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
  if (!open) { await nextTick(); addButton.value?.querySelector("button")?.focus(); }
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
function openDelete(host: ServerSummary) {
  if (workspace.preview.value || deleteBusy.value) return;
  deleteTarget.value = { ...host };
  deleteError.value = "";
  deleteRequest = null;
  deleteOpen.value = true;
}
function closeDelete() {
  if (deleteBusy.value) return;
  deleteOpen.value = false;
  deleteTarget.value = null;
  deleteError.value = "";
  deleteRequest = null;
}
async function confirmDelete() {
  const target = deleteTarget.value;
  if (!target || deleteBusy.value) return;
  if (!isTauri()) { deleteError.value = "请在桌面环境管理连接记录"; return; }
  deleteBusy.value = true;
  deleteError.value = "";
  deleteRequest ??= { schemaVersion: 1, requestId: crypto.randomUUID(), operationId: crypto.randomUUID(), serverId: target.serverId, expectedRecordVersion: target.recordVersion };
  try {
    await workspace.remove(deleteRequest);
    deleteOpen.value = false;
    deleteTarget.value = null;
    deleteRequest = null;
    notifySuccess("连接记录已删除", target.displayName);
  } catch (cause) {
    const error = describeError(cause);
    deleteError.value = [error.message, ...error.recoverySteps].join("；");
  } finally {
    deleteBusy.value = false;
  }
}

const columns = computed<DataTableColumns<ServerSummary>>(() => {
  void connection.busyServerId.value;
  void connection.verifiedServerVersions.value;
  void workspace.preview.value;
  return [
  {
    title: "主机",
    key: "displayName",
    render: host => h("div", null, [
      h("div", { class: "table-primary" }, [h(Server, { size: 17 }), h("strong", host.displayName)]),
      h("small", { class: "table-sub" }, host.username),
    ]),
  },
  { title: "地址", key: "address", render: host => h("span", { class: "mono" }, `${host.host}:${host.sshPort}`) },
  { title: "认证", key: "auth", render: host => authLabel(host) },
  {
    title: "状态",
    key: "state",
    render: host => {
      const state = hostState(host);
      return h(StatusLabel, { tone: state.tone, label: state.label });
    },
  },
  {
    title: "",
    key: "actions",
    align: "center",
    render: host => {
      const busy = connection.busyServerId.value === host.serverId;
      const label = busy ? "正在连接" : workspace.preview.value || host.hostId || isVerified(host) ? "进入主机" : "连接主机";
      const actions = [
        h(IconButton, {
          label,
          disabled: busy,
          onClick: () => connectHost(host),
        }, () => h(ArrowRight, { size: 15 })),
      ];
      if (!workspace.preview.value) {
        actions.push(h(IconButton, {
          label: `编辑${host.displayName}连接`,
          onClick: () => openConnection(host.serverId),
        }, () => h(Pencil, { size: 16 })));
        actions.push(h(IconButton, {
          label: `删除${host.displayName}连接记录`,
          disabled: busy,
          tone: "danger",
          onClick: () => openDelete(host),
        }, () => h(Trash2, { size: 16 })));
      }
      return h("div", { class: "row-actions" }, actions);
    },
  },
  ];
});
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="主机管理" title="主机列表">
      <template #actions>
        <span ref="addButton" class="add-host-anchor">
          <NButton type="primary" :disabled="workspace.preview.value" @click="setEditorOpen(true)"><template #icon><Plus :size="16"/></template>添加主机</NButton>
        </span>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <CredentialCleanupNotice/>
    <div v-if="workspace.error.value" class="error-strip" role="alert">{{workspace.error.value}}</div>
    <div v-if="connection.error.value" class="error-strip" role="alert">
      <strong>{{ connection.error.value.message }}</strong>
      <span v-if="connection.error.value.recoverySteps.length"> {{ connection.error.value.recoverySteps.join("；") }}</span>
      <NButton v-if="connection.errorServerId.value" text size="small" @click="openConnection(connection.errorServerId.value)">连接设置</NButton>
    </div>
    <template v-if="workspace.loading.value || servers.length">
      <div class="table-wrap">
        <NDataTable :columns="columns" :data="servers" :bordered="false" :single-line="true" size="medium" :row-key="rowKey" :loading="workspace.loading.value"/>
      </div>
    </template>
    <div v-else class="empty-state"><Server :size="28"/><h2>还没有主机</h2><NButton @click="setEditorOpen(true)">添加第一台主机</NButton></div>
    <AppDialog :open="editorOpen" title="添加 Linux 主机" @update:open="setEditorOpen">
      <ServerForm v-if="editorOpen" ref="serverForm" form-id="new-server" :server="null" @saved="saved" @tested="tested" @busy="saving = $event"/>
      <template #footer>
        <NButton @click="setEditorOpen(false)">关闭</NButton>
        <NButton :disabled="saving" @click="testConnection">测试连接</NButton>
        <NButton attr-type="submit" form="new-server" type="primary" :disabled="saving">{{saving ? '保存中…' : '保存主机'}}</NButton>
      </template>
    </AppDialog>
    <AppDialog :open="deleteOpen" title="删除连接记录" :description="`确认从本机移除“${deleteTarget?.displayName ?? ''}”？不会删除远端专服。`" @update:open="value => { if (!value) closeDelete() }">
      <p v-if="deleteTarget" class="mono">{{ deleteTarget.host }}:{{ deleteTarget.sshPort }} · {{ deleteTarget.username }}</p>
      <p v-if="deleteError" class="error-strip" role="alert">{{ deleteError }}</p>
      <template #footer>
        <NButton :disabled="deleteBusy" @click="closeDelete">取消</NButton>
        <NButton type="error" ghost :disabled="deleteBusy" @click="confirmDelete">{{ deleteBusy ? "删除中…" : "确认删除连接" }}</NButton>
      </template>
    </AppDialog>
    <FingerprintTrustDialog :open="connection.trustOpen.value" :display-name="connection.challengeServer.value?.displayName ?? ''" :host="connection.challengeServer.value?.host ?? ''" :ssh-port="connection.challengeServer.value?.sshPort ?? 22" :challenge="connection.challenge.value" :busy="connection.busyServerId.value === connection.challengeServer.value?.serverId" :error="connection.trustError.value" @update:open="value => { if (!value) connection.closeTrust() }" @confirm="connection.confirmTrust"/>
  </div>
</template>
