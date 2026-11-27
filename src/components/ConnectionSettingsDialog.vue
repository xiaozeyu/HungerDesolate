<script setup lang="ts">
import { computed, ref } from "vue";
import { NButton } from "naive-ui";
import { notifySuccess } from "../naive/notify";
import AppDialog from "./AppDialog.vue";
import ServerForm from "./ServerForm.vue";
import SshConnectionPanel from "./SshConnectionPanel.vue";
import FingerprintTrustDialog from "./FingerprintTrustDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useConnectionDialog } from "../composables/useConnectionDialog";
import { useHostConnection } from "../composables/useHostConnection";
import type { HostConnection, ServerSummary, SshConnectionState } from "../types/contracts";

const workspace = useWorkspace();
const dialog = useConnectionDialog();
const hostConnection = useHostConnection();
const server = computed(() => workspace.servers.value.find(item => item.serverId === dialog.serverId.value) ?? null);
const serverForm = ref<{testConnection: () => void} | null>(null);
const saving = ref(false);
const sshState = ref<SshConnectionState | null>(null);
const sshStateLoaded = ref(false);
const hostBusy = computed(() => !!server.value && hostConnection.busyServerId.value === server.value.serverId);
const busy = computed(() => saving.value || hostBusy.value);

function close() {
  dialog.closeConnection();
}
function sshStateChanged(value: SshConnectionState | null) {
  sshState.value = value;
  sshStateLoaded.value = true;
}
function saved(value: ServerSummary) {
  notifySuccess("连接已保存", value.displayName);
  close();
}
async function tested(value: ServerSummary) {
  await hostConnection.connect(value, connected);
}
function connected(_connection: HostConnection, value: ServerSummary) {
  notifySuccess("连接测试通过", value.displayName);
}
function testConnection() {
  if (busy.value || workspace.preview.value || !server.value) return;
  serverForm.value?.testConnection();
}
</script>

<template>
  <AppDialog :open="!!dialog.serverId.value" title="连接设置" size="md" @update:open="value => { if (!value) close() }">
    <div v-if="server" class="connection-dialog">
      <section class="connection-block">
        <div class="section-heading"><div><h2>连接信息</h2></div></div>
        <ServerForm ref="serverForm" :server="server" :readonly="workspace.preview.value" form-id="edit-server"
          :credential-configured="!!sshState?.credentialConfigured"
          @saved="saved" @tested="tested" @busy="saving = $event"/>
      </section>
      <SshConnectionPanel :server="server" :preview="workspace.preview.value" @state="sshStateChanged"/>
      <FingerprintTrustDialog :open="hostConnection.trustOpen.value"
        :display-name="hostConnection.challengeServer.value?.displayName ?? ''"
        :host="hostConnection.challengeServer.value?.host ?? ''"
        :ssh-port="hostConnection.challengeServer.value?.sshPort ?? 22"
        :challenge="hostConnection.challenge.value"
        :busy="hostConnection.busyServerId.value === hostConnection.challengeServer.value?.serverId"
        @update:open="value => { if (!value) hostConnection.closeTrust() }"
        @confirm="hostConnection.confirmTrust"/>
    </div>
    <div v-else class="empty-state"><h2>连接记录不存在</h2><NButton @click="close">关闭</NButton></div>
    <template #footer>
      <NButton :disabled="busy" @click="close">关闭</NButton>
      <NButton :disabled="busy || !sshStateLoaded || workspace.preview.value" @click="testConnection">{{ hostBusy ? "测试中…" : "测试连接" }}</NButton>
      <NButton attr-type="submit" form="edit-server" type="primary" :disabled="busy || !sshStateLoaded || workspace.preview.value">{{ saving ? "保存中…" : "保存" }}</NButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.connection-dialog { min-width: 0; }
.connection-block { min-width: 0; }
</style>
