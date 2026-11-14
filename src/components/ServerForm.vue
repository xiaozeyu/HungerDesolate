<script setup lang="ts">
import { computed, reactive, ref, watch, onBeforeUnmount } from "vue";
import { NInput } from "naive-ui";
import { isTauri } from "@tauri-apps/api/core";
import AppSelect from "./AppSelect.vue";
import CredentialFields from "./CredentialFields.vue";
import { emptyCredentials, credentialInput, credentialError, type CredentialFields as CredentialValues } from "../lib/credentialFields";
import { saveNewServer, type NewServerProgress } from "../lib/saveNewServer";
import { saveServer } from "../api/servers";
import { saveCredentials } from "../api/ssh";
import { describeError, type UserError } from "../lib/commandError";
import { serverInput, validateServerForm, type ServerFields, type ServerFormErrors } from "../lib/serverForm";
import { useWorkspace } from "../composables/useWorkspace";
import type { SaveServerRequest, ServerSummary } from "../types/contracts";
const props = defineProps<{server: ServerSummary | null; readonly?: boolean; formId: string; credentialConfigured?: boolean}>();
const emit = defineEmits<{saved: [server: ServerSummary]; tested: [server: ServerSummary]; busy: [value: boolean]}>();
const workspace = useWorkspace();
const fields = reactive<ServerFields>({displayName: "", host: "", sshPort: "22", username: "root", authKind: "key"});
const errors = ref<ServerFormErrors>({});
const failure = ref<UserError | null>(null);
const busy = ref(false);
const baseVersion = ref<number | null>(null);
const credentials = ref(emptyCredentials());
const credentialFailure = ref<string | null>(null);
const progress = ref<NewServerProgress | null>(null);
let credentialEditVersion = 0;
let credentialAttempt: {editVersion: number; baseVersion: number; operationId: string} | null = null;
function editCredentials(value: CredentialValues) {
  credentials.value = value; credentialFailure.value = null; credentialEditVersion++;
  if (progress.value && !busy.value) progress.value.credentialOperationId = crypto.randomUUID();
}
const dirty = ref(false);
const changedElsewhere = computed(() => dirty.value && props.server !== null && baseVersion.value !== props.server.recordVersion);
const authOptions = [{label: "SSH 密钥", value: "key"}, {label: "密码", value: "password"}];
const authKindChanged = computed(() => !!props.server && props.server.authKind !== fields.authKind);
const hasStoredCredential = computed(() => !!props.server && !!props.credentialConfigured);
const credentialOptional = computed(() => !!props.server && hasStoredCredential.value && !authKindChanged.value);
function hasNewCredential() {
  if (fields.authKind === "password") return credentials.value.password.length > 0;
  return (credentials.value.keySource === "file" ? credentials.value.privateKeyPath.trim() : credentials.value.privateKey.trim()).length > 0;
}
let attempt: {payload: string; request: SaveServerRequest} | null = null;
let contextEpoch = 0;
onBeforeUnmount(() => { contextEpoch++; credentials.value = emptyCredentials(); progress.value = null; });
function reset() {
  const server = props.server;
  Object.assign(fields, {displayName: server?.displayName ?? "", host: server?.host ?? "", sshPort: String(server?.sshPort ?? 22), username: server?.username ?? "root", authKind: server?.authKind ?? "key"});
  baseVersion.value = server?.recordVersion ?? null; dirty.value = false;
  errors.value = {}; failure.value = null; attempt = null; credentials.value = emptyCredentials(); credentialFailure.value = null; progress.value = null;
  credentialAttempt = null; credentialEditVersion++;
}
watch(() => props.server?.serverId, () => { contextEpoch++; busy.value = false; emit("busy", false); reset(); }, {immediate: true});
watch(() => props.server?.recordVersion, () => { if (!dirty.value) reset(); });
function edited() { dirty.value = true; }
function authChanged(value: string) { if (value === "password" || value === "key") { fields.authKind = value; edited(); } }
async function save(test = false) {
  errors.value = validateServerForm(fields); failure.value = null;
  const credentialRequired = !props.server || authKindChanged.value || !hasStoredCredential.value;
  const newCredential = hasNewCredential();
  credentialFailure.value = (credentialRequired || newCredential)
    ? credentialError(fields.authKind, credentials.value) : null;
  if (Object.keys(errors.value).length || credentialFailure.value || busy.value || props.readonly) return;
  if (!isTauri()) { failure.value = describeError(new Error("请用 npm run desktop:dev 启动桌面环境后保存")); return; }
  if (props.server && !dirty.value && !authKindChanged.value && !newCredential) {
    if (test) emit("tested", props.server); else emit("saved", props.server);
    return;
  }
  const epoch = contextEpoch;
  const payload = JSON.stringify({serverId: props.server?.serverId ?? null, expectedRecordVersion: baseVersion.value, input: serverInput(fields)});
  if (attempt?.payload !== payload) attempt = {payload, request: {schemaVersion: 1, requestId: crypto.randomUUID(), operationId: crypto.randomUUID(), serverId: props.server?.serverId ?? null, expectedRecordVersion: baseVersion.value, input: serverInput(fields)}};
  busy.value = true; emit("busy", true);
  try {
    if (!props.server) {
      progress.value ??= {metadataRequest: attempt.request, server: null, credentialOperationId: crypto.randomUUID()};
      const result = await saveNewServer(progress.value, credentialInput(fields.authKind,credentials.value), saveServer, saveCredentials, () => epoch === contextEpoch);
      await workspace.refresh();
      if (epoch === contextEpoch && result) {
        credentials.value = emptyCredentials(); progress.value = null; attempt = null;
        dirty.value = false;
        if (test) emit("tested", result); else emit("saved", result);
      }
      return;
    }
    let current = props.server;
    if (dirty.value || authKindChanged.value) {
      const result = await workspace.save(attempt.request);
      if (epoch !== contextEpoch) return;
      const latest = workspace.servers.value.find(item => item.serverId === result.serverId);
      if (latest && latest.recordVersion !== result.recordVersion) {
        failure.value = {code: "REVISION_CONFLICT", message: "原保存操作已完成，但记录随后再次被修改", recoverySteps: ["保留输入，与最新记录比较后再保存"], field: null};
        return;
      }
      current = latest ?? {...props.server, recordVersion: result.recordVersion};
      baseVersion.value = result.recordVersion; dirty.value = false; attempt = null;
    }
    if (newCredential && baseVersion.value !== null) {
      if (!credentialAttempt || credentialAttempt.editVersion !== credentialEditVersion
          || credentialAttempt.baseVersion !== baseVersion.value)
        credentialAttempt = {editVersion: credentialEditVersion, baseVersion: baseVersion.value,
          operationId: crypto.randomUUID()};
      const requestId = crypto.randomUUID();
      const response = await saveCredentials({
        schemaVersion: 1, requestId, serverId: current.serverId,
        expectedRecordVersion: baseVersion.value, operationId: credentialAttempt.operationId,
        credential: credentialInput(fields.authKind, credentials.value),
      });
      if (epoch !== contextEpoch) return;
      if (response.schemaVersion !== 1 || response.requestId !== requestId
          || response.serverId !== current.serverId || response.recordVersion !== baseVersion.value + 1)
        throw new Error("凭据响应与当前主机不匹配，请刷新核实");
      baseVersion.value = response.recordVersion;
      credentials.value = emptyCredentials();
      credentialFailure.value = null;
      credentialAttempt = null;
    }
    await workspace.refresh();
    if (epoch !== contextEpoch) return;
    const latest = workspace.servers.value.find(item => item.serverId === props.server!.serverId)
      ?? {...current, recordVersion: baseVersion.value ?? current.recordVersion};
    baseVersion.value = latest.recordVersion;
    dirty.value = false;
    if (test) emit("tested", latest); else emit("saved", latest);
  } catch (cause) {
    if (epoch === contextEpoch) {
      const error = describeError(cause);
      if (error.code === "REVISION_CONFLICT") await workspace.refresh();
      if (epoch !== contextEpoch) return;
      failure.value = error;
      if (!props.server) {
        if (!progress.value?.server && ["INPUT_INVALID","SCHEMA_UNSUPPORTED"].includes(error.code)) {progress.value = null; attempt = null;}
        credentialFailure.value = progress.value?.server ? error.message : null;
        await workspace.refresh();
        if (epoch !== contextEpoch) return;
      }
      const field = failure.value.field;
      if (field && field in fields) errors.value[field as keyof ServerFields] = failure.value.message;
    }
  } finally { if (epoch === contextEpoch) {busy.value = false; emit("busy", false);} }
}
function testConnection() {
  if (!busy.value && !props.readonly) void save(true);
}
defineExpose({testConnection});
</script>
<template>
  <form :id="formId" novalidate @submit.prevent="save(false)" @input="edited">
    <p v-if="progress?.server && failure" class="form-hint" role="status">主机“{{progress.server.displayName}}”已创建，凭据保存未完成。可重试或到连接设置补充。</p>
    <div v-if="changedElsewhere" class="error-strip" role="alert">此记录已在其他位置修改。当前输入已保留，请先比较最新记录。<p class="muted-text">当前记录：{{server?.displayName}} · {{server?.username}}@{{server?.host}}:{{server?.sshPort}} · 版本 {{server?.recordVersion}}</p></div>
    <div v-if="failure" class="error-strip" role="alert"><strong>{{failure.message}}</strong><p v-for="step in failure.recoverySteps" :key="step">{{step}}</p></div>
    <fieldset :disabled="busy || readonly || !!progress" class="server-form-fields">
      <div class="form-grid">
        <label :for="`${formId}-name`">显示名称<NInput v-model:value="fields.displayName" placeholder="" maxlength="64" :input-props="{ id: `${formId}-name`, autocomplete: 'off', 'aria-invalid': !!errors.displayName, 'aria-describedby': errors.displayName ? `${formId}-name-error` : undefined }"/><span v-if="errors.displayName" :id="`${formId}-name-error`" class="field-error">{{errors.displayName}}</span></label>
        <label :for="`${formId}-host`">主机地址<NInput v-model:value="fields.host" placeholder="192.0.2.24" :input-props="{ id: `${formId}-host`, autocomplete: 'off', spellcheck: 'false', class: 'mono', 'aria-invalid': !!errors.host, 'aria-describedby': errors.host ? `${formId}-host-error` : undefined }"/><span v-if="errors.host" :id="`${formId}-host-error`" class="field-error">{{errors.host}}</span></label>
        <label :for="`${formId}-port`">SSH 端口<NInput v-model:value="fields.sshPort" placeholder="" :input-props="{ id: `${formId}-port`, inputmode: 'numeric', 'aria-invalid': !!errors.sshPort, 'aria-describedby': errors.sshPort ? `${formId}-port-error` : undefined }"/><span v-if="errors.sshPort" :id="`${formId}-port-error`" class="field-error">{{errors.sshPort}}</span></label>
        <label :for="`${formId}-user`">用户名<NInput v-model:value="fields.username" placeholder="" maxlength="64" :input-props="{ id: `${formId}-user`, autocomplete: 'off', 'aria-invalid': !!errors.username, 'aria-describedby': errors.username ? `${formId}-user-error` : undefined }"/><span v-if="errors.username" :id="`${formId}-user-error`" class="field-error">{{errors.username}}</span></label>
        <div class="form-field"><span>认证方式</span><AppSelect :model-value="fields.authKind" :options="authOptions" label="认证方式" :disabled="busy || readonly || !!progress" @update:model-value="authChanged"/></div>
      </div>
    </fieldset>
    <CredentialFields :model-value="credentials" :auth-kind="fields.authKind" :id-prefix="`${formId}-credential`" :disabled="busy || readonly || !!progress" :error="credentialFailure" :allow-paste="false" @update:model-value="editCredentials"/>
    <p v-if="credentialOptional" class="form-hint">留空保持当前凭据；输入新值将替换</p>
    <div v-if="$slots.actions" class="form-actions"><slot name="actions" :busy="busy"/></div>
  </form>
</template>
