<script setup lang="ts">
import { computed, nextTick, ref, watch, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Trash2 } from "@lucide/vue";
import { isTauri } from "@tauri-apps/api/core";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError } from "../lib/commandError";
import PageHeader from "../components/PageHeader.vue";
import SummaryBar from "../components/SummaryBar.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import AppDialog from "../components/AppDialog.vue";
import ServerForm from "../components/ServerForm.vue";
import SshConnectionPanel from "../components/SshConnectionPanel.vue";
import type { DeleteServerRequest, ServerSummary } from "../types/contracts";
const route = useRoute(); const router = useRouter(); const workspace = useWorkspace();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const authLabel = computed(() => server.value?.authKind === "key" ? "SSH 密钥" : "密码");
const stateLabel = computed(() => server.value?.connectionState === "connected" ? "已连接"
  : server.value?.connectionState === "connecting" ? "连接中"
  : server.value?.connectionState === "unreachable" ? "不可达"
  : server.value?.connectionState === "trustRequired" ? "待确认指纹" : "未连接");
const connectionSummary = computed(() => [
  { label: "SSH 地址", value: server.value ? `${server.value.host}:${server.value.sshPort}` : "—" },
  { label: "认证", value: authLabel.value },
  { label: "连接", value: stateLabel.value, tone: stateTone.value === "danger" ? "danger" as const : stateTone.value === "warning" ? "warning" as const : stateTone.value === "success" ? "success" as const : "default" as const },
]);
const stateTone = computed<"success"|"warning"|"danger"|"muted">(() => server.value?.connectionState === "connected" ? "success"
  : server.value?.connectionState === "unreachable" ? "danger"
  : server.value?.connectionState === "trustRequired" || server.value?.connectionState === "connecting" ? "warning" : "muted");
const saving = ref(false); const savedMessage = ref("");
const sshBusy = ref(false);
const connectedServerId = ref("");
const deleteOpen = ref(false); const deleting = ref(false); const deleteError = ref("");
const deleteButton = ref<HTMLButtonElement | null>(null);
const deleteTarget = ref<ServerSummary | null>(null);
let deleteRequest: DeleteServerRequest | null = null;
let epoch = 0;
watch(() => route.params.serverId, () => { epoch++; savedMessage.value = ""; connectedServerId.value = ""; deleteOpen.value = false; deleting.value = false; deleteError.value = ""; deleteRequest = null; });
onBeforeUnmount(() => { epoch++; });
function openDelete() { if (!server.value) return; deleteTarget.value = {...server.value}; deleteError.value = ""; deleteOpen.value = true; deleteRequest = null; }
async function closeDelete(open: boolean) { deleteOpen.value = open; if (!open) { await nextTick(); deleteButton.value?.focus(); } }
function saved(value: ServerSummary) { savedMessage.value = `已保存“${value.displayName}”`; }
function connected() { if (server.value) connectedServerId.value = server.value.serverId; }
async function remove() {
  if (!deleteTarget.value || deleting.value || workspace.preview.value) return;
  if (!isTauri()) { deleteError.value = "请在桌面环境管理连接记录"; return; }
  const capturedEpoch = epoch; const target = deleteTarget.value;
  deleteRequest ??= {schemaVersion:1, requestId:crypto.randomUUID(), operationId:crypto.randomUUID(), serverId:target.serverId, expectedRecordVersion:target.recordVersion};
  deleting.value = true; deleteError.value = "";
  try { await workspace.remove(deleteRequest); if (epoch === capturedEpoch) { deleteOpen.value = false; await router.push("/hosts"); } }
  catch (cause) { if (epoch === capturedEpoch) { const error = describeError(cause); deleteError.value = [error.message, ...error.recoverySteps].join("；"); } }
  finally { if (epoch === capturedEpoch) deleting.value = false; }
}
</script>
<template>
  <div class="page">
    <PageHeader :eyebrow="server?.displayName ?? '主机连接'" title="连接设置"/>
    <PreviewNotice/>
    <SummaryBar v-if="server" :items="connectionSummary" label="连接摘要"/>
    <p v-if="savedMessage" class="success-message" role="status">{{savedMessage}}</p>
    <p v-if="connectedServerId" class="success-message" role="status">登录验证通过<button class="text-button" @click="router.push(`/hosts/${connectedServerId}/environment`)">进入主机环境</button></p>
    <p v-if="workspace.error.value" class="error-strip" role="alert">{{workspace.error.value}}</p>
    <div v-if="server" class="connection-layout">
      <section class="connection-block">
        <div class="section-heading"><div><h2>连接信息</h2><p class="muted-text">显示名称、地址、端口与登录用户</p></div></div>
        <ServerForm :server="server" :readonly="workspace.preview.value || sshBusy" form-id="edit-server" @saved="saved" @busy="saving = $event">
          <template #actions><button class="button primary" type="submit" :disabled="saving || sshBusy || workspace.preview.value">{{saving ? '保存中…' : '保存连接信息'}}</button></template>
        </ServerForm>
      </section>
      <SshConnectionPanel :server="server" :preview="workspace.preview.value" :disabled="saving || deleting" @busy="sshBusy = $event" @connected="connected"/>
    </div>
    <div v-else class="empty-state"><h2>连接记录不存在</h2><button class="button" @click="router.push('/hosts')">返回主机列表</button></div>
    <section v-if="server && !workspace.preview.value" class="connection-section"><div class="section-heading"><div><h2>删除连接记录</h2></div><button ref="deleteButton" class="button danger" :disabled="saving || deleting || sshBusy" @click="openDelete"><Trash2 :size="16"/>删除连接记录</button></div></section>
    <AppDialog :open="deleteOpen" title="删除连接记录" :description="`确认从本机移除“${deleteTarget?.displayName ?? ''}”？不会删除远端专服。`" @update:open="closeDelete">
      <p class="mono">{{deleteTarget?.host}}:{{deleteTarget?.sshPort}} · {{deleteTarget?.username}}</p><p v-if="deleteError" class="error-strip" role="alert">{{deleteError}}</p>
      <template #footer><button class="button" @click="closeDelete(false)">关闭</button><button class="button danger" :disabled="deleting" @click="remove">{{deleting ? '删除中…' : '确认删除连接'}}</button></template>
    </AppDialog>
  </div>
</template>

<style scoped>
.connection-layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, .85fr); gap: 48px; align-items: start; }
.connection-block { min-width: 0; }
@media (max-width: 1120px) { .connection-layout { grid-template-columns: 1fr; gap: 32px; } }
</style>
