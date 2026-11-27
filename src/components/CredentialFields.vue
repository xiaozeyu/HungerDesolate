<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from "vue";
import { FolderOpen } from "@lucide/vue";
import { NButton, NInput } from "naive-ui";
import AppSelect from "./AppSelect.vue";
import { pickPrivateKeyFile } from "../api/ssh";
import { describeError } from "../lib/commandError";
import { notifyError } from "../naive/notify";
import { emptyCredentials, type CredentialFields } from "../lib/credentialFields";

const props = withDefaults(defineProps<{
  modelValue: CredentialFields; authKind: "password" | "key"; idPrefix: string;
  disabled?: boolean; error?: string | null; allowPaste?: boolean;
}>(), {disabled: false, error: null, allowPaste: true});
const emit = defineEmits<{"update:modelValue": [value: CredentialFields]}>();
const picking = ref(false);
let epoch = 0;
function update(field: keyof CredentialFields, value: string) {
  epoch++; picking.value = false;
  emit("update:modelValue", {...props.modelValue, [field]: value});
}
watch(() => props.authKind, () => {epoch++; picking.value = false; emit("update:modelValue",emptyCredentials());});
watch(() => props.disabled, () => {epoch++; picking.value = false;});
onBeforeUnmount(() => {epoch++;});
function changeSource(value: string) {
  if (value !== "file" && value !== "text") return;
  epoch++; picking.value = false;
  emit("update:modelValue", {...emptyCredentials(), keySource: value});
}
async function chooseFile() {
  const captured = ++epoch;
  picking.value = true;
  try {
    const path = await pickPrivateKeyFile();
    if (captured === epoch && path && !props.disabled) emit("update:modelValue", {...props.modelValue,privateKeyPath:path});
  } catch (cause) { if (captured === epoch) notifyError("选择私钥文件失败", describeError(cause)); }
  finally { if (captured === epoch) picking.value = false; }
}
</script>
<template>
  <fieldset class="server-form-fields credential-form" :disabled="disabled">
    <label v-if="authKind === 'password'" :for="`${idPrefix}-password`">SSH 密码
      <NInput :value="modelValue.password" :status="error ? 'error' : undefined" type="password" placeholder="" maxlength="8192" :input-props="{ id: `${idPrefix}-password`, autocomplete: 'off', 'aria-invalid': !!error, 'aria-describedby': error ? `${idPrefix}-error` : undefined }" @update:value="value => update('password', value)"/>
    </label>
    <template v-else>
      <div v-if="allowPaste" class="form-field"><span>私钥来源</span><AppSelect :model-value="modelValue.keySource" :options="[{label:'本机私钥文件',value:'file'},{label:'粘贴私钥内容',value:'text'}]" label="私钥来源" :disabled="disabled" @update:model-value="changeSource"/></div>
      <div v-if="modelValue.keySource === 'file'" class="form-field">
        <label :for="`${idPrefix}-path`">SSH 私钥文件路径</label>
        <div class="file-path-control"><NInput :value="modelValue.privateKeyPath" :status="error ? 'error' : undefined" placeholder="~/.ssh/id_ed25519" :input-props="{ id: `${idPrefix}-path`, autocomplete: 'off', spellcheck: 'false', class: 'mono', 'aria-invalid': !!error, 'aria-describedby': error ? `${idPrefix}-error` : undefined }" @update:value="value => update('privateKeyPath', value)"/><NButton size="small" :disabled="disabled || picking" @click="chooseFile"><template #icon><FolderOpen :size="16"/></template>{{picking ? '选择中…' : '选择文件'}}</NButton></div>
      </div>
      <label v-else :for="`${idPrefix}-key`">SSH 私钥
        <NInput :value="modelValue.privateKey" :status="error ? 'error' : undefined" type="textarea" :autosize="{ minRows: 6 }" maxlength="65536" placeholder="PEM / OpenSSH" :input-props="{ id: `${idPrefix}-key`, autocomplete: 'off', spellcheck: 'false', 'aria-invalid': !!error, 'aria-describedby': error ? `${idPrefix}-error` : undefined }" @update:value="value => update('privateKey', value)"/>
      </label>
      <label :for="`${idPrefix}-passphrase`">私钥口令（可选）<NInput :value="modelValue.passphrase" type="password" placeholder="" maxlength="8192" :input-props="{ id: `${idPrefix}-passphrase`, autocomplete: 'off' }" @update:value="value => update('passphrase', value)"/></label>
    </template>
    <p v-if="error" :id="`${idPrefix}-error`" class="visually-hidden">{{error}}</p>
  </fieldset>
</template>
