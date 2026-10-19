<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { isTauri } from "@tauri-apps/api/core";
import { Fingerprint, KeyRound, PlugZap } from "@lucide/vue";
import AppDialog from "./AppDialog.vue";
import CredentialFields from "./CredentialFields.vue";
import { emptyCredentials, credentialError, credentialInput } from "../lib/credentialFields";
import CredentialCleanupNotice from "./CredentialCleanupNotice.vue";
import { useWorkspace } from "../composables/useWorkspace";
import * as api from "../api/ssh";
import { describeError } from "../lib/commandError";
import { currentConnection, matchesConnection, type ConnectionTicket } from "../lib/connectionGuard";
import type { ConnectionRequest, FingerprintChallenge, HostConnection, ServerSummary, SshConnectionState } from "../types/contracts";
const props = defineProps<{server: ServerSummary; preview: boolean; disabled?: boolean}>();
const emit = defineEmits<{busy: [value: boolean]}>();
const workspace = useWorkspace();
const state = ref<SshConnectionState | null>(null), challenge = ref<FingerprintChallenge | null>(null);
const verification = ref<HostConnection | null>(null);
const failure = ref(""), message = ref(""), busy = ref(false);
const trustOpen = ref(false), credentialOpen = ref(false);
watch(busy, value => emit("busy", value), {flush: "sync"});
const credentials = ref(emptyCredentials());
const credentialFailure = ref<string | null>(null);
const acknowledged = ref(false);
let epoch = 0, sequence = 0, credentialEditVersion = 0;
let latestRequestId = "";
let attempt: {editVersion: number; operationId: string} | null = null;
const canUseSsh = computed(() => isTauri() && !props.preview && !props.disabled && !busy.value);
function clearSecrets() {credentials.value = emptyCredentials(); credentialFailure.value = null; attempt = null;}
function edited() { credentialEditVersion++; }
function request(captured: ConnectionTicket): ConnectionRequest { return {schemaVersion:1, requestId:captured.requestId, serverId:props.server.serverId, expectedRecordVersion:props.server.recordVersion}; }
function ticket(): ConnectionTicket { latestRequestId = crypto.randomUUID(); return {serverId:props.server.serverId, recordVersion:props.server.recordVersion, epoch, sequence:++sequence, requestId:latestRequestId}; }
function current(value: ConnectionTicket) { return currentConnection(value, {serverId:props.server.serverId, recordVersion:props.server.recordVersion, epoch, sequence, requestId:latestRequestId}); }
function clearContext() { epoch++; sequence++; state.value = null; verification.value = null; challenge.value = null; busy.value = false; trustOpen.value = false; credentialOpen.value = false; failure.value = ""; message.value = ""; clearSecrets(); }
onBeforeUnmount(() => {clearContext();emit("busy",false);});
watch([() => props.server.serverId, () => props.server.recordVersion, () => props.preview], async () => {
  clearContext();
  if (!isTauri() || props.preview) return;
  const captured = ticket();
  try { const result = await api.getSshState(request(captured)); if (current(captured) && matchesConnection(captured,result)) state.value = result; }
  catch (cause) { if (current(captured)) failure.value = describeError(cause).message; }
}, {immediate:true});
watch(credentialOpen, open => {if (!open) clearSecrets();});
async function scan() {
  if (!canUseSsh.value) return;
  const captured = ticket();busy.value = true;failure.value = "";message.value = "";
  try { const result = await api.scanFingerprint(request(captured)); if (current(captured) && matchesConnection(captured,result)) { challenge.value = result; acknowledged.value = false; trustOpen.value = true; } }
  catch (cause) { if (current(captured)) failure.value = describeError(cause).message; }
  finally { if (current(captured)) busy.value = false; }
}
async function confirm() {
  if (!canUseSsh.value || !challenge.value || !acknowledged.value) return;
  const captured = ticket();busy.value = true;failure.value = "";
  try { const result = await api.confirmFingerprint({...request(captured),challengeId:challenge.value.challengeId,fingerprint:challenge.value.fingerprint});
    if (current(captured) && matchesConnection(captured,result,true)) {trustOpen.value = false; await workspace.refresh();} }
  catch (cause) { if (current(captured)) failure.value = describeError(cause).message; }
  finally { if (current(captured)) busy.value = false; }
}
async function saveCredential() {
  if (!canUseSsh.value) return;
  credentialFailure.value = credentialError(props.server.authKind,credentials.value);
  if (credentialFailure.value) return;
  const captured = ticket();busy.value = true;failure.value = "";
  if (attempt?.editVersion !== credentialEditVersion) attempt = {editVersion:credentialEditVersion,operationId:crypto.randomUUID()};
  const credential = credentialInput(props.server.authKind,credentials.value);
  try { const result = await api.saveCredentials({...request(captured),operationId:attempt.operationId,credential});
    if (current(captured) && matchesConnection(captured,result,true)) {credentialOpen.value = false; clearSecrets();await workspace.refresh();} }
  catch (cause) { if (current(captured)) failure.value = describeError(cause).message; }
  finally { if (current(captured)) busy.value = false; }
}
async function connect() {
  if (!canUseSsh.value) return;
  const captured = ticket();busy.value = true;failure.value = "";verification.value = null;
  try { const result = await api.connectServer(request(captured)); if (current(captured) && matchesConnection(captured,result)) { verification.value = result; message.value = result.managementState === "ready" ? "登录通过 · 主机已初始化" : "登录通过 · 主机未初始化"; await workspace.refresh(); } }
  catch (cause) { if (current(captured)) {const error=describeError(cause); failure.value = [error.message,...error.recoverySteps].join("；");} }
  finally { if (current(captured)) busy.value = false; }
}
</script>
<template>
  <section class="connection-section ssh-panel">
    <div class="section-heading"><div><h2>SSH 信任与凭据</h2></div></div>
    <p v-if="failure && !trustOpen && !credentialOpen" class="error-strip" role="alert">{{failure}}</p>
    <div class="ssh-step"><Fingerprint :size="18"/><div><strong>主机指纹</strong><p v-if="state?.fingerprint" class="mono fingerprint-value">{{state.algorithm}} · {{state.fingerprint}}</p><p v-else>未确认</p></div><button class="button" :disabled="!canUseSsh" @click="scan">{{busy ? '处理中…' : '扫描指纹'}}</button></div>
    <div class="ssh-step"><KeyRound :size="18"/><div><strong>系统凭据库</strong><p>{{state?.credentialConfigured ? '已配置' : '未配置'}} · {{server.authKind === 'password' ? '密码认证' : '私钥认证'}}</p></div><button class="button" :disabled="disabled || busy || (!preview && !canUseSsh)" @click="credentialOpen = true; failure = ''">{{state?.credentialConfigured ? '替换凭据' : preview ? '预览凭据表单' : '配置凭据'}}</button></div>
    <div class="ssh-step"><PlugZap :size="18"/><div><strong>登录验证</strong></div><button class="button primary" :disabled="!canUseSsh || !state?.fingerprint || !state?.credentialConfigured" @click="connect">验证登录</button></div>
    <div v-if="verification" class="ssh-verification" role="status"><p>{{message}}</p><p v-if="verification.hostId" class="mono">hostId：{{verification.hostId}}</p><small>核验时间：{{new Date(verification.verifiedAtUnixSeconds * 1000).toLocaleString()}}</small></div>
    <CredentialCleanupNotice/>
    <AppDialog :open="trustOpen" title="确认 SSH 主机指纹" description="请通过可信渠道核对以下主机指纹。" @update:open="trustOpen = $event">
      <template v-if="challenge"><p>{{server.displayName}} · {{server.host}}:{{server.sshPort}}</p><p v-if="challenge.previousFingerprint && challenge.previousFingerprint !== challenge.fingerprint" class="field-error">指纹已变化：请先核实是否更换主机或重新安装 SSH 服务。</p><dl class="fingerprint-details"><dt>原指纹</dt><dd class="mono">{{challenge.previousFingerprint ?? '尚未信任'}}</dd><dt>新指纹 · {{challenge.algorithm}}</dt><dd class="mono">{{challenge.fingerprint}}</dd></dl><label class="trust-acknowledgement"><input v-model="acknowledged" type="checkbox" :disabled="busy"/>我已通过可信渠道核对上述主机指纹</label><p class="form-hint">有效至 {{new Date(challenge.expiresAtUnixSeconds * 1000).toLocaleTimeString()}}</p></template>
      <p v-if="failure" class="error-strip" role="alert">{{failure}}</p>
      <template #footer><button class="button" @click="trustOpen = false">关闭</button><button class="button primary" :disabled="!acknowledged || !canUseSsh" @click="confirm">确认指纹</button></template>
    </AppDialog>
    <AppDialog :open="credentialOpen" title="保存 SSH 凭据" :description="`${server.displayName} · ${server.username}@${server.host}`" @update:open="credentialOpen = $event">
      <p v-if="preview" class="form-hint">演示数据</p>
      <form id="ssh-credential-form" @submit.prevent="saveCredential"><CredentialFields v-model="credentials" :auth-kind="server.authKind" id-prefix="ssh-credential" :disabled="busy || preview" :error="credentialFailure" @update:model-value="edited"/></form><p v-if="failure" class="error-strip" role="alert">{{failure}}</p>
      <template #footer><button class="button" @click="credentialOpen = false">关闭</button><button class="button primary" form="ssh-credential-form" type="submit" :disabled="!canUseSsh">{{busy ? '保存中…' : '保存到系统凭据库'}}</button></template>
    </AppDialog>
  </section>
</template>
