<script setup lang="ts">
import { ref, watch } from "vue";
import { NButton, NCheckbox } from "naive-ui";
import AppDialog from "./AppDialog.vue";
import type { FingerprintChallenge } from "../types/contracts";

const props = defineProps<{
  open: boolean;
  displayName: string;
  host: string;
  sshPort: number;
  challenge: FingerprintChallenge | null;
  busy: boolean;
}>();
const emit = defineEmits<{"update:open": [value: boolean]; confirm: []}>();
const acknowledged = ref(false);

watch(() => [props.open, props.challenge?.challengeId], () => { acknowledged.value = false; }, { immediate: true });

function confirm() {
  if (acknowledged.value && !props.busy) emit("confirm");
}
</script>

<template>
  <AppDialog :open="open" title="确认 SSH 主机指纹"  @update:open="emit('update:open', $event)">
    <template v-if="challenge">
      <p>{{ displayName }} · {{ host }}:{{ sshPort }}</p>
      <p v-if="challenge.previousFingerprint && challenge.previousFingerprint !== challenge.fingerprint" class="field-error">指纹已变化：请先核实是否更换主机或重新安装 SSH 服务</p>
      <dl class="fingerprint-details">
        <dt>原指纹</dt>
        <dd class="mono">{{ challenge.previousFingerprint ?? "尚未信任" }}</dd>
        <dt>新指纹 · {{ challenge.algorithm }}</dt>
        <dd class="mono">{{ challenge.fingerprint }}</dd>
      </dl>
      <NCheckbox v-model:checked="acknowledged" :disabled="busy" class="trust-acknowledgement">我已通过可信渠道核对上述主机指纹</NCheckbox>
      <p class="form-hint">有效至 {{ new Date(challenge.expiresAtUnixSeconds * 1000).toLocaleTimeString() }}</p>
    </template>
    <p v-else class="form-hint">主机指纹尚未读取</p>
    <template #footer>
      <NButton @click="emit('update:open', false)">关闭</NButton>
      <NButton type="primary" :disabled="!acknowledged || busy" @click="confirm">{{ busy ? "处理中…" : "确认指纹" }}</NButton>
    </template>
  </AppDialog>
</template>
