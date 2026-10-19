<script setup lang="ts">
import { computed, nextTick, ref, watch, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Trash2 } from "@lucide/vue";
import { isTauri } from "@tauri-apps/api/core";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError } from "../lib/commandError";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import AppDialog from "../components/AppDialog.vue";
import ServerForm from "../components/ServerForm.vue";
import SshConnectionPanel from "../components/SshConnectionPanel.vue";
import type { DeleteServerRequest, ServerSummary } from "../types/contracts";
const route = useRoute(); const router = useRouter(); const workspace = useWorkspace();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const saving = ref(false); const savedMessage = ref("");
const sshBusy = ref(false);
const deleteOpen = ref(false); const deleting = ref(false); const deleteError = ref("");
const deleteButton = ref<HTMLButtonElement | null>(null);
const deleteTarget = ref<ServerSummary | null>(null);
let deleteRequest: DeleteServerRequest | null = null;
let epoch = 0;
watch(() => route.params.serverId, () => { epoch++; savedMessage.value = ""; deleteOpen.value = false; deleting.value = false; deleteError.value = ""; deleteRequest = null; });
onBeforeUnmount(() => { epoch++; });
function openDelete() { if (!server.value) return; deleteTarget.value = {...server.value}; deleteError.value = ""; deleteOpen.value = true; deleteRequest = null; }
async function closeDelete(open: boolean) { deleteOpen.value = open; if (!open) { await nextTick(); deleteButton.value?.focus(); } }
function saved(value: ServerSummary) { savedMessage.value = `已保存“${value.displayName}”`; }
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
    <p v-if="savedMessage" class="success-message" role="status">{{savedMessage}}</p>
    <p v-if="workspace.error.value" class="error-strip" role="alert">{{workspace.error.value}}</p>
    <ServerForm v-if="server" :server="server" :readonly="workspace.preview.value || sshBusy" form-id="edit-server" @saved="saved" @busy="saving = $event">
      <template #actions><button class="button primary" type="submit" :disabled="saving || sshBusy || workspace.preview.value">{{saving ? '保存中…' : '保存连接信息'}}</button></template>
    </ServerForm>
    <div v-else class="empty-state"><h2>连接记录不存在</h2><button class="button" @click="router.push('/hosts')">返回主机列表</button></div>
    <SshConnectionPanel v-if="server" :server="server" :preview="workspace.preview.value" :disabled="saving || deleting" @busy="sshBusy = $event"/>
    <section v-if="server && !workspace.preview.value" class="connection-section"><div class="section-heading"><div><h2>删除连接记录</h2></div><button ref="deleteButton" class="button danger" :disabled="saving || deleting || sshBusy" @click="openDelete"><Trash2 :size="16"/>删除连接记录</button></div></section>
    <AppDialog :open="deleteOpen" title="删除连接记录" :description="`确认从本机移除“${deleteTarget?.displayName ?? ''}”？不会删除远端专服。`" @update:open="closeDelete">
      <p class="mono">{{deleteTarget?.host}}:{{deleteTarget?.sshPort}} · {{deleteTarget?.username}}</p><p v-if="deleteError" class="error-strip" role="alert">{{deleteError}}</p>
      <template #footer><button class="button" @click="closeDelete(false)">关闭</button><button class="button danger" :disabled="deleting" @click="remove">{{deleting ? '删除中…' : '确认删除连接'}}</button></template>
    </AppDialog>
  </div>
</template>
