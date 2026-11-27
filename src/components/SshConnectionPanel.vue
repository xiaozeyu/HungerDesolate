<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { isTauri } from "@tauri-apps/api/core";
import { Fingerprint } from "@lucide/vue";
import CredentialCleanupNotice from "./CredentialCleanupNotice.vue";
import * as api from "../api/ssh";
import { describeError } from "../lib/commandError";
import { notifyError } from "../naive/notify";
import { currentConnection, matchesConnection, type ConnectionTicket } from "../lib/connectionGuard";
import type { ConnectionRequest, ServerSummary, SshConnectionState } from "../types/contracts";

const props = defineProps<{server: ServerSummary; preview: boolean}>();
const emit = defineEmits<{state: [value: SshConnectionState | null]}>();
const state = ref<SshConnectionState | null>(null);
let epoch = 0, sequence = 0;
let latestRequestId = "";

function request(captured: ConnectionTicket): ConnectionRequest {
  return {schemaVersion: 1, requestId: captured.requestId, serverId: props.server.serverId,
    expectedRecordVersion: props.server.recordVersion};
}
function ticket(): ConnectionTicket {
  latestRequestId = crypto.randomUUID();
  return {serverId: props.server.serverId, recordVersion: props.server.recordVersion,
    epoch, sequence: ++sequence, requestId: latestRequestId};
}
function current(value: ConnectionTicket) {
  return currentConnection(value, {serverId: props.server.serverId,
    recordVersion: props.server.recordVersion, epoch, sequence, requestId: latestRequestId});
}
function clearContext() {
  epoch++;
  sequence++;
  state.value = null;
  emit("state", null);
}
watch([() => props.server.serverId, () => props.server.recordVersion, () => props.preview], async () => {
  clearContext();
  if (!isTauri() || props.preview) return;
  const captured = ticket();
  try {
    const result = await api.getSshState(request(captured));
    if (current(captured) && matchesConnection(captured, result)) {
      state.value = result;
      emit("state", result);
    }
  } catch (cause) {
    if (current(captured)) notifyError("读取 SSH 状态失败", describeError(cause));
  }
}, {immediate: true});
onBeforeUnmount(clearContext);
</script>

<template>
  <section class="connection-section ssh-panel">
    <div class="section-heading"><div><h2>SSH 主机指纹</h2></div></div>
    <div class="ssh-step">
      <Fingerprint :size="18"/>
      <div>
        <strong>主机指纹</strong>
        <p v-if="state?.fingerprint" class="mono fingerprint-value">{{ state.algorithm }} · {{ state.fingerprint }}</p>
        <p v-else>尚未确认；测试连接或连接主机时自动获取并校验。</p>
      </div>
    </div>
    <CredentialCleanupNotice/>
  </section>
</template>
