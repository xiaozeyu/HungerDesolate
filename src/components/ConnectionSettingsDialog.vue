<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { NButton } from "naive-ui";
import { notifySuccess, notifySuccessAction } from "../naive/notify";
import AppDialog from "./AppDialog.vue";
import ServerForm from "./ServerForm.vue";
import SshConnectionPanel from "./SshConnectionPanel.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useConnectionDialog } from "../composables/useConnectionDialog";
import { useHostConnection } from "../composables/useHostConnection";
import type { ServerSummary } from "../types/contracts";

const router = useRouter();
const workspace = useWorkspace();
const dialog = useConnectionDialog();
const hostConnection = useHostConnection();
const server = computed(() => workspace.servers.value.find(item => item.serverId === dialog.serverId.value) ?? null);
const saving = ref(false);
const sshBusy = ref(false);

function close() {
  dialog.closeConnection();
}

function saved(value: ServerSummary) {
  notifySuccess("连接已保存", value.displayName);
}

function connected() {
  const current = server.value;
  if (!current) return;
  hostConnection.markVerified(current);
  notifySuccessAction("登录验证通过", current.displayName, "进入主机环境", () => {
    close();
    void router.push(`/hosts/${current.serverId}/environment`);
  });
}
</script>

<template>
  <AppDialog :open="!!dialog.serverId.value" title="连接设置" size="md" @update:open="value => { if (!value) close() }">
    <div v-if="server" class="connection-dialog">
      <p v-if="workspace.error.value" class="error-strip" role="alert">{{ workspace.error.value }}</p>
      <section class="connection-block">
        <div class="section-heading"><div><h2>连接信息</h2></div></div>
        <ServerForm :server="server" :readonly="workspace.preview.value || sshBusy" form-id="edit-server" @saved="saved" @busy="saving = $event">
          <template #actions><NButton attr-type="submit" type="primary" :disabled="saving || sshBusy || workspace.preview.value">{{ saving ? "保存中…" : "保存连接信息" }}</NButton></template>
        </ServerForm>
      </section>
      <SshConnectionPanel :server="server" :preview="workspace.preview.value" :disabled="saving" @busy="sshBusy = $event" @connected="connected"/>
    </div>
    <div v-else class="empty-state"><h2>连接记录不存在</h2><NButton @click="close">关闭</NButton></div>
    <template #footer><NButton @click="close">关闭</NButton></template>
  </AppDialog>
</template>

<style scoped>
.connection-dialog { min-width: 0; }
.connection-block { min-width: 0; }
</style>
