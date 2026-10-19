<script setup lang="ts">
import { ref, nextTick } from "vue";
import { Server, Plus, ArrowRight, LayoutTemplate, Pencil } from "@lucide/vue";
import { useRouter } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
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
const addButton = ref<HTMLButtonElement | null>(null);
async function setEditorOpen(open: boolean) {
  editorOpen.value = open;
  if (!open) { await nextTick(); addButton.value?.focus(); }
}
function saved(server: ServerSummary) {
  savedMessage.value = `已添加“${server.displayName}”`;
  void setEditorOpen(false);
}
</script>
<template>
  <div class="page">
    <PageHeader eyebrow="主机管理" title="Linux 主机">
      <template #actions>
        <button class="button" @click="workspace.setPreview(!workspace.preview.value); savedMessage = ''"><LayoutTemplate :size="16"/>{{workspace.preview.value ? '退出预览' : '查看界面预览'}}</button>
        <button ref="addButton" class="button primary" :disabled="workspace.preview.value" @click="setEditorOpen(true)"><Plus :size="16"/>添加主机</button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <CredentialCleanupNotice/>
    <p v-if="savedMessage" role="status" class="success-message">{{savedMessage}}</p>
    <div v-if="workspace.error.value" class="error-strip" role="alert">{{workspace.error.value}}</div>
    <div v-if="workspace.loading.value && !workspace.servers.value.length" class="empty-state" role="status"><p>读取连接记录…</p></div>
    <div v-else-if="!workspace.servers.value.length" class="empty-state"><Server :size="28"/><h2>还没有主机</h2><button class="button" @click="setEditorOpen(true)">添加第一台主机</button></div>
    <div v-else class="host-list">
      <article v-for="host in workspace.servers.value" :key="host.serverId" class="host-row">
        <div class="host-symbol"><Server :size="19"/></div>
        <div class="host-main"><strong>{{host.displayName}}</strong><span class="mono">{{host.host}}:{{host.sshPort}}</span><small>{{host.username}}</small></div>
        <StatusLabel tone="muted" :label="workspace.preview.value ? '示例' : '未连接'"/>
        <IconButton v-if="!workspace.preview.value" :label="`编辑${host.displayName}连接`" @click="router.push(`/hosts/${host.serverId}/connection`)"><Pencil :size="17"/></IconButton>
        <IconButton :label="`进入${host.displayName}`" @click="router.push(`/hosts/${host.serverId}/${workspace.preview.value ? 'instances' : 'connection'}`)"><ArrowRight :size="17"/></IconButton>
      </article>
    </div>
    <AppDialog :open="editorOpen" title="添加 Linux 主机" @update:open="setEditorOpen">
      <ServerForm v-if="editorOpen" form-id="new-server" :server="null" @saved="saved" @busy="saving = $event"/>
      <template #footer><button class="button" @click="setEditorOpen(false)">关闭</button><button class="button primary" form="new-server" type="submit" :disabled="saving">{{saving ? '保存中…' : '保存主机'}}</button></template>
    </AppDialog>
  </div>
</template>
