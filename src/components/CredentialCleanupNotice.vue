<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { NButton } from "naive-ui";
import { isTauri } from "@tauri-apps/api/core";
import { useWorkspace } from "../composables/useWorkspace";
import { cleanupCredentials, getCleanupStatus } from "../api/ssh";
import { describeError } from "../lib/commandError";
const workspace = useWorkspace();
const count = ref(0), error = ref(""), busy = ref(false);
let epoch = 0, sequence = 0;
onBeforeUnmount(() => { epoch++; });
watch([workspace.servers, workspace.preview], async () => {
  const ticket = ++sequence, context = ++epoch; busy.value = false; error.value = "";
  if (!isTauri() || workspace.preview.value) {count.value = 0; return;}
  try { const result = await getCleanupStatus(); if (context === epoch && ticket === sequence) count.value = result.pendingCount; }
  catch (cause) { if (context === epoch && ticket === sequence) error.value = describeError(cause).message; }
}, {immediate:true});
async function cleanup() {
  const context = epoch; busy.value = true; error.value = "";
  try { const result = await cleanupCredentials(); if (context === epoch) {count.value = result.pendingCount; if (result.failedCount) error.value = "部分旧凭据未能清理，请检查系统凭据库权限后重试";} }
  catch (cause) { if (context === epoch) error.value = describeError(cause).message; }
  finally { if (context === epoch) busy.value = false; }
}
</script>
<template><div v-if="count || error" class="credential-cleanup" role="status"><div><p v-if="count">{{count}} 项凭据待清理</p><p v-if="error" class="field-error">{{error}}</p></div><NButton size="small" :disabled="busy" @click="cleanup">{{busy ? '清理中…' : '清理旧凭据'}}</NButton></div></template>
