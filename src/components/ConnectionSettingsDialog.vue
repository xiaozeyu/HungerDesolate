<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Trash2 } from "@lucide/vue";
import { isTauri } from "@tauri-apps/api/core";
import AppDialog from "./AppDialog.vue";
import ServerForm from "./ServerForm.vue";
import SshConnectionPanel from "./SshConnectionPanel.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useConnectionDialog } from "../composables/useConnectionDialog";
import { describeError } from "../lib/commandError";
import type { DeleteServerRequest, ServerSummary } from "../types/contracts";

const router = useRouter();
const workspace = useWorkspace();
const dialog = useConnectionDialog();
const server = computed(() => workspace.servers.value.find(item => item.serverId === dialog.serverId.value) ?? null);
const saving = ref(false);
const savedMessage = ref("");
const sshBusy = ref(false);
const connectedServerId = ref("");
const deleteOpen = ref(false);
const deleting = ref(false);
const deleteError = ref("");
const deleteButton = ref<HTMLButtonElement | null>(null);
const deleteTarget = ref<ServerSummary | null>(null);
let deleteRequest: DeleteServerRequest | null = null;
let epoch = 0;

watch(() => dialog.serverId.value, () => {
  epoch++;
  savedMessage.value = "";
  connectedServerId.value = "";
  deleteOpen.value = false;
  deleting.value = false;
  deleteError.value = "";
  deleteTarget.value = null;
  deleteRequest = null;
});
onBeforeUnmount(() => { epoch++; });

function close() {
  dialog.closeConnection();
}

function openDelete() {
  if (!server.value || workspace.preview.value) return;
  deleteTarget.value = { ...server.value };
  deleteError.value = "";
  deleteRequest = null;
  deleteOpen.value = true;
}

async function closeDelete(open: boolean) {
  deleteOpen.value = open;
  if (!open) { await nextTick(); deleteButton.value?.focus(); }
}

function saved(value: ServerSummary) {
  savedMessage.value = `已保存“${value.displayName}”`;
}

function connected() {
  if (server.value) connectedServerId.value = server.value.serverId;
}

function goToEnvironment() {
  if (!connectedServerId.value) return;
  close();
  void router.push(`/hosts/${connectedServerId.value}/environment`);
}

async function remove() {
  if (!deleteTarget.value || deleting.value || workspace.preview.value) return;
  if (!isTauri()) { deleteError.value = "请在桌面环境管理连接记录"; return; }
  const capturedEpoch = epoch;
  const target = deleteTarget.value;
  deleteRequest ??= { schemaVersion: 1, requestId: crypto.randomUUID(), operationId: crypto.randomUUID(), serverId: target.serverId, expectedRecordVersion: target.recordVersion };
  deleting.value = true;
  deleteError.value = "";
  try {
    await workspace.remove(deleteRequest);
    if (epoch === capturedEpoch) {
      deleteOpen.value = false;
      close();
      await router.push("/hosts");
    }
  } catch (cause) {
    if (epoch === capturedEpoch) {
      const error = describeError(cause);
      deleteError.value = [error.message, ...error.recoverySteps].join("；");
    }
  } finally {
    if (epoch === capturedEpoch) deleting.value = false;
  }
}
</script>

<template>
  <AppDialog :open="!!dialog.serverId.value" title="连接设置" size="lg" @update:open="value => { if (!value) close() }">
    <div v-if="server" class="connection-dialog">
      <p v-if="savedMessage" class="success-message" role="status">{{ savedMessage }}</p>
      <p v-if="connectedServerId" class="success-message" role="status">登录验证通过<button class="text-button" type="button" @click="goToEnvironment">进入主机环境</button></p>
      <p v-if="workspace.error.value" class="error-strip" role="alert">{{ workspace.error.value }}</p>
      <div class="connection-layout">
        <section class="connection-block">
          <div class="section-heading"><div><h2>连接信息</h2></div></div>
          <ServerForm :server="server" :readonly="workspace.preview.value || sshBusy" form-id="edit-server" @saved="saved" @busy="saving = $event">
            <template #actions><button class="button primary" type="submit" :disabled="saving || sshBusy || workspace.preview.value">{{ saving ? "保存中…" : "保存连接信息" }}</button></template>
          </ServerForm>
        </section>
        <SshConnectionPanel :server="server" :preview="workspace.preview.value" :disabled="saving || deleting" @busy="sshBusy = $event" @connected="connected"/>
      </div>
      <section v-if="!workspace.preview.value" class="connection-section">
        <div class="section-heading"><div><h2>删除连接记录</h2></div><button ref="deleteButton" class="button danger" :disabled="saving || deleting || sshBusy" @click="openDelete"><Trash2 :size="16"/>删除连接记录</button></div>
      </section>
    </div>
    <div v-else class="empty-state"><h2>连接记录不存在</h2><button class="button" type="button" @click="close">关闭</button></div>
    <template #footer><button class="button" type="button" @click="close">关闭</button></template>
  </AppDialog>
  <AppDialog :open="deleteOpen" title="删除连接记录" :description="`确认从本机移除“${deleteTarget?.displayName ?? ''}”？不会删除远端专服。`" @update:open="closeDelete">
    <p class="mono">{{ deleteTarget?.host }}:{{ deleteTarget?.sshPort }} · {{ deleteTarget?.username }}</p>
    <p v-if="deleteError" class="error-strip" role="alert">{{ deleteError }}</p>
    <template #footer>
      <button class="button" type="button" @click="closeDelete(false)">关闭</button>
      <button class="button danger" type="button" :disabled="deleting" @click="remove">{{ deleting ? "删除中…" : "确认删除连接" }}</button>
    </template>
  </AppDialog>
</template>

<style scoped>
.connection-dialog { min-width: 0; }
.connection-layout { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 36px; align-items: start; }
.connection-block { min-width: 0; }
.connection-layout > .ssh-panel { margin-top: 0; }
.connection-section { margin-top: 8px; }
@media (max-width: 860px) { .connection-layout { grid-template-columns: 1fr; gap: 28px; } }
</style>
