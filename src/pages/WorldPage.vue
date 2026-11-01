<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import { Eye, EyeOff, RefreshCw, Save, Upload } from "@lucide/vue";
import AppSelect from "../components/AppSelect.vue";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import { applyWorldConfig, getWorldConfig, revealWorldSecrets, saveWorldConfig } from "../api/world";
import { useWorkspace } from "../composables/useWorkspace";
import { useOperations } from "../composables/useOperations";
import { usePageTarget } from "../composables/usePageTarget";
import { describeError, type UserError } from "../lib/commandError";
import { dstGameModeOptions, languageOptions } from "../lib/dstGameModes";
import { defaultWorldSettings, optionValue, optionValuesForShard, updateShardOption, visibleWorldOptions } from "../lib/dstWorldOptions";
import { copyWorldSettings } from "../lib/worldConfigDraft";
import { clearWorldDraft, readWorldDraft, writeWorldDraft } from "../lib/worldDraft";
import { operationTargetKey } from "../lib/operationView";
import type { DstWorldCategory, DstWorldShard, WorldConfig, WorldSettings } from "../types/world";

const workspace = useWorkspace();
const operations = useOperations();
const { server, instance, target, instanceName } = usePageTarget();
const targetKey = computed(() => target.value ? operationTargetKey(target.value) : "");
const applyBusy = computed(() => !!targetKey.value && operations.isRunningFor(targetKey.value, ["worldApply"]));
const data = ref<WorldConfig | null>(null);
const draft = reactive<WorldSettings>(defaultWorldSettings(false));
const baseDesired = ref<WorldSettings | null>(null), baseRevision = ref(0);
const loading = ref(false), saving = ref(false), stale = ref(false);
const error = ref<UserError | null>(null);
const saveMessage = ref("");
const tokenStatus = ref<WorldConfig["tokenStatus"]>("unknown");
const passwordStatus = ref<WorldConfig["serverPasswordStatus"]>("unknown");
const tokenValue = ref(""), passwordValue = ref("");
const showToken = ref(false), showPassword = ref(false);
const revealedToken = ref(""), revealedPassword = ref("");
const revealLoading = ref(false), secretError = ref("");
const passwordInput = computed({
  get: () => passwordValue.value || revealedPassword.value,
  set: (value: string) => { passwordValue.value = value; revealedPassword.value = ""; },
});
const tokenInput = computed({
  get: () => tokenValue.value || revealedToken.value,
  set: (value: string) => { tokenValue.value = value; revealedToken.value = ""; },
});
const activeView = ref<"basic" | "access" | "world">("basic");
const worldView = ref<"worldSettings" | "worldgen">("worldSettings");
const selectedShard = ref<DstWorldShard>("master");
const activeGroup = ref("misc");
const draftVersion = ref(0), pendingOperationId = ref("");
let epoch = 0, querySequence = 0, latestRequestId = "";

const hasChanges = computed(() => {
  if (!baseDesired.value) return false;
  return JSON.stringify(draft) !== JSON.stringify(baseDesired.value)
    || tokenValue.value.length > 0 || passwordValue.value.length > 0;
});
const canSave = computed(() => hasChanges.value && !!data.value && !!target.value
  && !saving.value && !applyBusy.value && !loading.value && !stale.value && !workspace.preview.value);
const selectedCategory = computed<DstWorldCategory | null>(() =>
  activeView.value === "world" ? worldView.value : null);
const visibleOptions = computed(() => selectedCategory.value
  ? visibleWorldOptions(selectedShard.value, selectedCategory.value) : []);
const optionGroups = computed(() => {
  const seen = new Set<string>();
  return visibleOptions.value.flatMap((item) => {
    if (seen.has(item.group)) return [];
    seen.add(item.group);
    return [{ key: item.group, label: item.groupLabel, count: visibleOptions.value.filter((option) => option.group === item.group).length }];
  });
});
const selectedGroupOptions = computed(() => visibleOptions.value.filter((item) => item.group === activeGroup.value));
const activeGroupLabel = computed(() => optionGroups.value.find((group) => group.key === activeGroup.value)?.label ?? "世界选项");

function selectView(view: typeof activeView.value) {
  activeView.value = view;
  if (view === "world") selectWorldView(worldView.value);
}

function selectWorldView(view: typeof worldView.value) {
  worldView.value = view;
  const groups = new Set(visibleWorldOptions(selectedShard.value, view).map((item) => item.group));
  if (!groups.has(activeGroup.value)) activeGroup.value = groups.values().next().value ?? "";
}

function selectShard(shard: DstWorldShard) {
  selectedShard.value = shard;
  const category = selectedCategory.value;
  if (!category) return;
  const groups = new Set(visibleWorldOptions(shard, category).map((item) => item.group));
  if (!groups.has(activeGroup.value)) activeGroup.value = groups.values().next().value ?? "";
}

function copySettings(value: WorldSettings): WorldSettings {
  return copyWorldSettings(value);
}

function assignSettings(value: WorldSettings) {
  Object.assign(draft, copySettings(value));
  if (!draft.cavesEnabled) selectedShard.value = "master";
}

function current(ticket: { epoch: number; sequence: number; requestId: string; serverId: string; recordVersion: number; target: string }) {
  return epoch === ticket.epoch && querySequence === ticket.sequence && latestRequestId === ticket.requestId
    && server.value?.serverId === ticket.serverId && server.value.recordVersion === ticket.recordVersion
    && target.value !== null && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === ticket.target;
}

async function refresh() {
  const connection = server.value, instanceTarget = target.value;
  if (!connection || !instanceTarget) return;
  const preserveDraft = data.value !== null && hasChanges.value;
  const requestId = crypto.randomUUID();
  latestRequestId = requestId;
  const ticket = { epoch, sequence: ++querySequence, requestId, serverId: connection.serverId,
    recordVersion: connection.recordVersion,
    target: `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}` };
  loading.value = true;
  error.value = null;
  try {
    if (workspace.preview.value) {
      const settings = defaultWorldSettings(instance.value?.cavesEnabled ?? false);
      if (!current(ticket)) return;
      data.value = { revision: 0, desired: settings, deployed: null, desiredDigest: "preview",
        deployedDigest: null, deployedRevision: null, tokenStatus: "unknown", serverPasswordStatus: "unknown",
        validationWarnings: [] };
      assignSettings(settings);
      baseDesired.value = copySettings(settings);
      baseRevision.value = 0;
      tokenStatus.value = "unknown";
      passwordStatus.value = "unknown";
      stale.value = false;
      return;
    }
    const response = await getWorldConfig({
      schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion,
    });
    if (!current(ticket)) return;
    if (response.requestId !== requestId || response.target.serverId !== instanceTarget.serverId
      || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId
      || typeof response.configRevision !== "number" || !Number.isSafeInteger(response.configRevision)
      || response.data.revision > response.configRevision) {
      throw new Error("世界配置响应身份或版本不匹配");
    }
    if (baseRevision.value > response.configRevision || (data.value && data.value.revision > response.data.revision)) {
      throw new Error("世界配置早于已读取结果");
    }
    data.value = response.data;
    if (!preserveDraft) {
      const restored = readWorldDraft(instanceTarget, response.configRevision);
      const useRestored = restored !== null && JSON.stringify(restored) !== JSON.stringify(response.data.desired);
      if (!useRestored) clearWorldDraft(instanceTarget);
      assignSettings(useRestored ? restored : response.data.desired);
      baseDesired.value = copySettings(response.data.desired);
      baseRevision.value = response.configRevision;
      tokenValue.value = "";
      passwordValue.value = "";
      showToken.value = false;
      showPassword.value = false;
      revealedToken.value = "";
      revealedPassword.value = "";
      secretError.value = "";
      if (useRestored) saveMessage.value = "已恢复未保存的草稿。";
    } else if (response.configRevision !== baseRevision.value) {
      error.value = { code: "REVISION_CONFLICT", message: "期望配置已被其他操作更新；本地草稿已保留，请比较后重载或重新编辑。", recoverySteps: [], field: null };
    }
    tokenStatus.value = response.data.tokenStatus;
    passwordStatus.value = response.data.serverPasswordStatus;
    stale.value = false;
    if (!preserveDraft) pendingOperationId.value = "";
    if (!preserveDraft && (tokenStatus.value === "configured" || passwordStatus.value === "configured")) {
      void revealSavedSecrets();
    }
  } catch (cause) {
    if (current(ticket)) {
      error.value = describeError(cause);
      stale.value = data.value !== null;
    }
  } finally {
    if (current(ticket)) loading.value = false;
  }
}

function listDraft(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))];
}

async function revealSavedSecrets() {
  const connection = server.value, instanceTarget = target.value;
  if (!connection || !instanceTarget || workspace.preview.value || revealLoading.value
      || (revealedToken.value && revealedPassword.value)) return;
  revealLoading.value = true;
  secretError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await revealWorldSecrets({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion });
    if (response.requestId !== requestId || response.target.serverId !== instanceTarget.serverId
        || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("凭据读取响应身份不匹配");
    }
    revealedToken.value = response.token ?? "";
    revealedPassword.value = response.serverPassword ?? "";
  } catch (cause) {
    secretError.value = describeError(cause).message;
  } finally {
    revealLoading.value = false;
  }
}

function togglePasswordVisibility() {
  showPassword.value = !showPassword.value;
}

function toggleTokenVisibility() {
  showToken.value = !showToken.value;
}

function validateDraft(): string | null {
  if (!draft.roomName.trim()) return "世界名称不能为空";
  if (draft.roomName.trim().length > 64) return "世界名称不能超过 64 个字符";
  if (draft.description.length > 256) return "世界描述不能超过 256 个字符";
  if (!Number.isInteger(draft.maxPlayers) || draft.maxPlayers < 1 || draft.maxPlayers > 128) return "最大玩家数必须在 1 到 128 之间";
  if (!Number.isInteger(draft.advanced.voteKickPercentage) || draft.advanced.voteKickPercentage < 0 || draft.advanced.voteKickPercentage > 100) return "踢人投票比例必须在 0 到 100 之间";
  if (!Number.isInteger(draft.advanced.whitelistSlots) || draft.advanced.whitelistSlots < 0 || draft.advanced.whitelistSlots > draft.maxPlayers) return "白名单预留位不能超过最大玩家数";
  if (draft.advanced.whitelistSlots > 0 && draft.advanced.whitelist.length === 0) return "设置白名单预留位前，请先填写白名单";
  for (const value of [tokenValue.value, passwordValue.value]) if (value.length > 8192 || value.includes("\0")) return "凭据长度或字符无效";
  return null;
}

async function save(): Promise<boolean> {
  const instanceTarget = target.value, connection = server.value, currentConfig = data.value;
  if (saving.value) {
    saveMessage.value = "保存请求正在处理中，请等待结果。";
    return false;
  }
  if (!instanceTarget || !connection || !currentConfig) {
    saveMessage.value = "尚未读取到可保存的专服配置，请先刷新。";
    return false;
  }
  if (loading.value) {
    saveMessage.value = "专服配置仍在读取，请稍后保存。";
    return false;
  }
  if (stale.value) {
    saveMessage.value = "当前配置读取已过期，请刷新后再保存。";
    return false;
  }
  if (workspace.preview.value) {
    saveMessage.value = "界面预览数据不能保存，请在桌面应用中编辑真实专服。";
    return false;
  }
  if (!hasChanges.value) {
    saveMessage.value = "当前配置没有更改，无需保存。";
    return false;
  }
  const validation = validateDraft();
  if (validation) {
    error.value = { code: "WORLD_CONFIG_INPUT_INVALID", message: validation, recoverySteps: [], field: null };
    return false;
  }
  const savedTarget = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const savedEpoch = epoch, version = draftVersion.value;
  const expectedRevision = baseRevision.value;
  const operationId = pendingOperationId.value || crypto.randomUUID();
  pendingOperationId.value = operationId;
  saving.value = true;
  error.value = null;
  saveMessage.value = "正在提交期望配置…";
  try {
    const settings = copySettings(draft);
    const requestId = crypto.randomUUID();
    const tokenChange = tokenValue.value ? { action: "replace" as const, value: tokenValue.value } : null;
    const serverPasswordChange = passwordValue.value ? { action: "replace" as const, value: passwordValue.value } : null;
    const response = await saveWorldConfig({
      schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision,
      operationId, settings, tokenChange, serverPasswordChange,
    });
    if (epoch !== savedEpoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== savedTarget
      || server.value?.recordVersion !== connection.recordVersion) return false;
    if (response.requestId !== requestId || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || response.operationId !== operationId
      || response.data.revision !== response.verifiedRevision || response.verifiedRevision <= expectedRevision) {
      throw new Error("保存结果身份或版本不匹配");
    }
    data.value = response.data;
    baseDesired.value = copySettings(response.data.desired);
    baseRevision.value = response.verifiedRevision;
    tokenStatus.value = response.data.tokenStatus;
    passwordStatus.value = response.data.serverPasswordStatus;
    if (draftVersion.value === version) {
      assignSettings(response.data.desired);
      tokenValue.value = "";
      passwordValue.value = "";
      showToken.value = false;
      showPassword.value = false;
      revealedToken.value = "";
      revealedPassword.value = "";
      secretError.value = "";
    }
    pendingOperationId.value = "";
    saveMessage.value = `期望配置已保存（版本 ${response.data.revision}）。`;
    return true;
  } catch (cause) {
    if (epoch === savedEpoch && target.value && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget) {
      error.value = describeError(cause);
      saveMessage.value = "";
    }
    return false;
  } finally {
    if (epoch === savedEpoch) saving.value = false;
  }
}

async function saveAndApply() {
  if (saving.value || applyBusy.value || loading.value || stale.value || !data.value || !target.value) return;
  error.value = null;
  if (hasChanges.value) {
    if (!(await save())) return;
    await nextTick();
  }
  if (hasChanges.value) return;
  await applyWorld();
}

async function applyWorld() {
  const instanceTarget = target.value, connection = server.value, config = data.value;
  if (!instanceTarget || !connection || !config || applyBusy.value
      || loading.value || saving.value || hasChanges.value || stale.value || workspace.preview.value) return;
  const ticketEpoch = epoch, revision = baseRevision.value;
  const requestId = crypto.randomUUID();
  error.value = null;
  saveMessage.value = "正在检查并部署世界配置…";
  const outcome = await operations.run({
    kind: "worldApply",
    target: instanceTarget,
    title: "应用世界配置",
    stage: "检查并应用",
    work: async setStage => {
      setStage("检查并应用");
      const result = await applyWorldConfig({
        schemaVersion: 1, requestId, target: instanceTarget,
        expectedRecordVersion: connection.recordVersion, expectedRevision: revision,
        operationId: crypto.randomUUID(),
      });
      if (result.requestId !== requestId || result.revision !== revision
          || result.runtimeVerified !== false || !Array.isArray(result.warnings)
          || result.warnings.length > 128
          || !Array.isArray(result.runningShards) || result.runningShards.length > 2
          || result.target.serverId !== instanceTarget.serverId || result.target.hostId !== instanceTarget.hostId
          || result.target.instanceId !== instanceTarget.instanceId) {
        throw new Error("配置应用结果身份、版本或验证状态无效");
      }
      setStage("验证部署结果");
      return result;
    },
  });
  if (epoch !== ticketEpoch || target.value?.instanceId !== instanceTarget.instanceId
      || server.value?.recordVersion !== connection.recordVersion) return;
  if (outcome.error) {
    error.value = outcome.error;
    saveMessage.value = "";
    return;
  }
  const result = outcome.result!;
  saveMessage.value = [result.runningShards.length
    ? "世界配置已部署并重启原运行分片；隔离运行验证仍待完成。"
    : "世界配置已部署，专服保持停止；隔离运行验证仍待完成。", ...result.warnings].join(" ");
  await refresh();
}

function changeWorldOption(itemKey: string, value: string) {
  const item = visibleOptions.value.find((option) => option.key === itemKey);
  if (!item) return;
  draft.worldgen[draft.cavesEnabled ? selectedShard.value : "master"] = updateShardOption(
    draft.worldgen[draft.cavesEnabled ? selectedShard.value : "master"], item, value,
    draft.cavesEnabled ? selectedShard.value : "master");
}

function updateList(key: "adminList" | "whitelist" | "blocklist", value: string) {
  draft.advanced[key] = listDraft(value);
}

const applyBlockerLabels: Record<string, string> = {
  PROTECTION_BACKUP_REQUIRED: "发现现有世界数据；备份功能接入前不会覆盖这些数据",
  WORLD_CONFIG_RENDER_UNSUPPORTED: "当前期望配置无法安全转换为 DST 配置文件",
  WORLD_TOKEN_MISSING: "应用前需要设置 Klei Token",
  WORLD_DEPLOYMENT_MISSING: "专服尚未完成容器部署",
  WORLD_RELEASE_MISSING: "专服缺少活动 DST Release",
};
function applyBlockerLabel(code: string) {
  return Object.hasOwn(applyBlockerLabels, code) ? applyBlockerLabels[code]! : "配置应用条件未满足";
}
const applyBlockers = computed(() => {
  const value = error.value?.details?.blockers;
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
});

watch([() => target.value?.serverId, () => target.value?.hostId, () => target.value?.instanceId,
  () => server.value?.recordVersion, () => workspace.preview.value], () => {
  epoch++;
  querySequence++;
  latestRequestId = "";
  data.value = null;
  baseDesired.value = null;
  baseRevision.value = 0;
  stale.value = false;
  error.value = null;
  loading.value = false;
  saving.value = false;
  pendingOperationId.value = "";
  tokenValue.value = "";
  passwordValue.value = "";
  assignSettings(defaultWorldSettings(instance.value?.cavesEnabled ?? false));
  if (target.value) void refresh();
}, { immediate: true, flush: "sync" });

watch(draft, () => { draftVersion.value++; pendingOperationId.value = ""; if (!applyBusy.value) saveMessage.value = ""; }, { deep: true, flush: "sync" });
watch(draft, () => {
  const selected = target.value;
  if (!selected || !baseDesired.value) return;
  if (hasChanges.value) writeWorldDraft(selected, baseRevision.value, draft);
  else clearWorldDraft(selected);
}, { deep: true, flush: "post" });
watch([tokenValue, passwordValue], () => { draftVersion.value++; pendingOperationId.value = ""; if (!applyBusy.value) saveMessage.value = ""; }, { flush: "sync" });
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="世界配置">
      <template #actions>
        <button class="button" :disabled="loading || saving || applyBusy || !target" @click="refresh"><RefreshCw :size="16"/>{{loading?'读取中…':'刷新'}}</button>
        <button class="button" :disabled="!canSave" @click="save"><Save :size="16"/>{{saving?'保存中…':'仅保存'}}</button>
        <button class="button primary" :disabled="!canSave" @click="saveAndApply"><Upload :size="16"/>{{saving?'保存中…':applyBusy?'应用中…':'保存并应用'}}</button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{error.message}}</strong>
      <ul v-if="applyBlockers.length"><li v-for="code in applyBlockers" :key="code">{{applyBlockerLabel(code)}}</li></ul>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul>
    </div>
    <div v-if="loading && !data" class="empty-state" role="status">读取世界配置…</div>
    <div v-else-if="!target && !workspace.preview.value" class="empty-state"><h2>无法确定专服</h2><p>返回专服列表后重新进入。</p></div>
    <template v-else>
      <div v-if="stale" class="environment-error" role="status">显示上次读取的配置；当前结果已过期，保存已停用。</div>
      <nav class="world-view-tabs" aria-label="世界配置分类">
        <button type="button" :aria-pressed="activeView==='basic'" :class="{active:activeView==='basic'}" @click="selectView('basic')">基本设置</button>
        <button type="button" :aria-pressed="activeView==='access'" :class="{active:activeView==='access'}" @click="selectView('access')">访问与联机</button>
        <button type="button" :aria-pressed="activeView==='world'" :class="{active:activeView==='world'}" @click="selectView('world')">世界</button>
      </nav>

      <section v-if="activeView==='basic'" class="section-block world-form-panel">
        <div class="world-pane-heading"><h2>基本设置</h2></div>
        <div class="world-field-list">
          <div class="world-field-row"><label for="world-room-name">世界名称</label><div class="world-field-control"><input id="world-room-name" v-model="draft.roomName" maxlength="64" autocomplete="off"/></div></div>
          <div class="world-field-row"><label>游戏模式</label><div class="world-field-control"><AppSelect v-model="draft.gameMode" :options="dstGameModeOptions" label="游戏模式"/></div></div>
          <div class="world-field-row"><label for="world-max-players">最大玩家数</label><div class="world-field-control"><input id="world-max-players" v-model.number="draft.maxPlayers" type="number" min="1" max="128"/></div></div>
          <div class="world-field-row"><label>语言</label><div class="world-field-control"><AppSelect v-model="draft.language" :options="languageOptions" label="语言"/></div></div>
          <div class="world-field-row world-field-row-top"><label for="world-description">世界描述</label><div class="world-field-control"><textarea id="world-description" v-model="draft.description" maxlength="256" rows="3"/></div></div>
        </div>

        <div class="world-subsection">
          <div class="world-pane-heading"><h2>联机凭据</h2></div>
          <div class="world-field-list">
            <div class="world-field-row"><label for="world-server-password">服务器密码</label><div class="world-field-control world-secret-input"><input id="world-server-password" v-model="passwordInput" :type="showPassword ? 'text' : 'password'" autocomplete="new-password" maxlength="8192" :placeholder="passwordStatus==='configured'?'留空保持当前密码':'输入服务器密码'"/><button class="world-secret-toggle" type="button" :aria-label="showPassword ? '隐藏服务器密码' : '显示服务器密码'" :aria-pressed="showPassword" @click="togglePasswordVisibility"><EyeOff v-if="showPassword" :size="16"/><Eye v-else :size="16"/></button></div></div>
            <div class="world-field-row"><label for="world-klei-token">Klei Token</label><div class="world-field-control world-secret-input"><input id="world-klei-token" v-model="tokenInput" :type="showToken ? 'text' : 'password'" autocomplete="off" maxlength="8192" :placeholder="tokenStatus==='configured'?'留空保持当前 Token':'输入 Klei Token'"/><button class="world-secret-toggle" type="button" :aria-label="showToken ? '隐藏 Klei Token' : '显示 Klei Token'" :aria-pressed="showToken" @click="toggleTokenVisibility"><EyeOff v-if="showToken" :size="16"/><Eye v-else :size="16"/></button></div></div>
          </div>
          <p v-if="secretError" class="error-strip" role="alert">{{ secretError }}</p>
        </div>

        <div class="world-subsection">
          <div class="world-pane-heading"><h2>世界选项</h2></div>
          <div class="world-field-list">
            <div class="world-field-row"><span><strong>允许 PVP</strong></span><span class="world-switch"><input v-model="draft.pvp" type="checkbox"/><span class="world-switch-track" aria-hidden="true"><span class="world-switch-thumb"/></span></span></div>
            <div class="world-field-row"><span><strong>无人时暂停</strong></span><span class="world-switch"><input v-model="draft.pauseWhenEmpty" type="checkbox"/><span class="world-switch-track" aria-hidden="true"><span class="world-switch-thumb"/></span></span></div>
          </div>
        </div>
      </section>

      <section v-else-if="activeView==='access'" class="section-block world-form-panel">
        <div class="world-pane-heading"><h2>访问与联机</h2></div>
        <div class="world-settings-grid">
          <section class="world-settings-panel">
            <h3>投票</h3>
            <div class="world-field-list">
              <div class="world-field-row"><span><strong>启用踢人投票</strong></span><span class="world-switch"><input v-model="draft.advanced.voteEnabled" type="checkbox"/><span class="world-switch-track" aria-hidden="true"><span class="world-switch-thumb"/></span></span></div>
              <div class="world-field-row"><label for="world-vote-percent">踢人投票比例</label><div class="world-field-control"><input id="world-vote-percent" v-model.number="draft.advanced.voteKickPercentage" type="number" min="0" max="100" :disabled="!draft.advanced.voteEnabled"/></div></div>
            </div>
          </section>
          <section class="world-settings-panel">
            <h3>白名单</h3>
            <div class="world-field-list">
              <div class="world-field-row"><label for="world-whitelist-slots">白名单预留位</label><div class="world-field-control"><input id="world-whitelist-slots" v-model.number="draft.advanced.whitelistSlots" type="number" min="0" :max="draft.maxPlayers"/></div></div>
              <div class="world-field-row world-field-row-top"><label for="world-whitelist">白名单 Klei ID</label><div class="world-field-control"><textarea id="world-whitelist" :value="draft.advanced.whitelist.join('\n')" rows="5" @input="updateList('whitelist',($event.target as HTMLTextAreaElement).value)"/></div></div>
            </div>
          </section>
          <section class="world-settings-panel world-settings-wide">
            <h3>名单</h3>
            <div class="world-list-grid">
              <label>管理员 Klei ID<textarea :value="draft.advanced.adminList.join('\n')" rows="5" @input="updateList('adminList',($event.target as HTMLTextAreaElement).value)"/></label>
              <label>封禁名单 Klei ID<textarea :value="draft.advanced.blocklist.join('\n')" rows="5" @input="updateList('blocklist',($event.target as HTMLTextAreaElement).value)"/></label>
            </div>
          </section>
        </div>
      </section>

      <section v-else class="section-block world-form-panel">
        <div class="world-pane-heading"><h2>世界</h2></div>
        <div class="world-subtabs" aria-label="世界设置">
          <button type="button" :aria-pressed="worldView==='worldSettings'" :class="{active:worldView==='worldSettings'}" @click="selectWorldView('worldSettings')">世界规则</button>
          <button type="button" :aria-pressed="worldView==='worldgen'" :class="{active:worldView==='worldgen'}" @click="selectWorldView('worldgen')">世界生成</button>
        </div>
        <div class="world-layout">
          <aside class="world-sidebar">
            <div class="world-sidebar-label">分片</div>
            <div class="world-shard-switch">
              <button type="button" :aria-pressed="selectedShard==='master'" :class="{active:selectedShard==='master'}" @click="selectShard('master')">Master</button>
              <button v-if="draft.cavesEnabled" type="button" :aria-pressed="selectedShard==='caves'" :class="{active:selectedShard==='caves'}" @click="selectShard('caves')">Caves</button>
            </div>
            <div class="world-sidebar-label">分类</div>
            <nav class="world-nav-list" aria-label="选项分组">
              <button v-for="group in optionGroups" :key="group.key" type="button" :aria-pressed="activeGroup===group.key" :class="{active:activeGroup===group.key}" @click="activeGroup=group.key">{{group.label}} <span>{{group.count}}</span></button>
            </nav>
          </aside>
          <div class="world-option-pane">
            <div class="world-option-heading"><h3>{{ activeGroupLabel }}</h3><span>{{ selectedGroupOptions.length }} 项</span></div>
            <div class="world-option-list">
              <div v-for="item in selectedGroupOptions" :key="item.key" class="world-option-row">
                <span>{{ item.label }}</span>
                <div class="world-field-control">
                  <AppSelect :model-value="optionValue(draft.worldgen[selectedShard],item,selectedShard)"
                    :options="optionValuesForShard(item,selectedShard)" :label="item.label"
                    @update:model-value="changeWorldOption(item.key,$event)"/>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>


<style scoped>
.world-pane-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.world-pane-heading h2 { font-size: 15px; font-weight: 600; }
.world-field-list { border-top: 1px solid var(--border); }
.world-field-row { display: grid; grid-template-columns: minmax(140px, 220px) minmax(0, 1fr); gap: 24px; align-items: center; min-height: 48px; padding: 9px 0; border-bottom: 1px solid var(--border); }
.world-field-row > label, .world-field-row > span:first-child { color: var(--text-secondary); font-size: 12px; }
.world-field-row > label { cursor: pointer; }
.world-field-row > span strong { display: block; color: var(--text); font-size: 12px; font-weight: 500; }
.world-field-row-top { align-items: start; padding-top: 13px; padding-bottom: 13px; }
.world-field-control { width: 100%; max-width: 420px; min-width: 0; }
.world-field-control input, .world-field-control textarea { width: 100%; }
.world-field-control textarea { min-height: 74px; resize: vertical; }
.world-subsection { margin-top: 34px; }
.world-secret-input { position: relative; display: flex; align-items: center; }
.world-secret-input input { width: 100%; padding-right: 38px; }
.world-secret-toggle { position: absolute; right: 3px; top: 50%; display: grid; width: 30px; height: 30px; place-items: center; transform: translateY(-50%); border: 0; border-radius: 2px; background: transparent; color: var(--text-tertiary); }
.world-secret-toggle:hover { color: var(--text); background: var(--surface-hover); }
.world-settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 36px 44px; }
.world-settings-panel { min-width: 0; }
.world-settings-panel h3 { margin: 0 0 8px; color: var(--text-tertiary); font-size: 10px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; }
.world-settings-wide { grid-column: 1 / -1; }
.world-list-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.world-list-grid label { display: flex; flex-direction: column; gap: 6px; color: var(--text-secondary); font-size: 11px; }
.world-subtabs { display: flex; gap: 0; margin-bottom: 28px; border-bottom: 1px solid var(--border); }
.world-subtabs button { min-height: 38px; padding: 0 20px; border-bottom: 2px solid transparent; color: var(--text-secondary); font-size: 12px; }
.world-subtabs button:hover { color: var(--text); }
.world-subtabs button.active { border-bottom-color: var(--text); color: var(--text); font-weight: 500; }
.world-layout { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 44px; align-items: start; }
.world-sidebar { min-width: 0; }
.world-sidebar-label { margin: 0 0 8px; color: var(--text-tertiary); font-size: 9px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; }
.world-sidebar-label:not(:first-child) { margin-top: 26px; }
.world-shard-switch { display: flex; gap: 2px; border-bottom: 1px solid var(--border); }
.world-shard-switch button { min-height: 32px; padding: 0 10px 8px; border-bottom: 2px solid transparent; color: var(--text-secondary); font-size: 11px; }
.world-shard-switch button.active { border-bottom-color: var(--text); color: var(--text); }
.world-nav-list { display: grid; gap: 1px; }
.world-nav-list button { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 31px; padding: 5px 0 5px 12px; border-left: 1px solid transparent; color: var(--text-secondary); text-align: left; font-size: 11px; }
.world-nav-list button:hover { color: var(--text); }
.world-nav-list button.active { border-left-color: var(--text); color: var(--text); }
.world-nav-list button span { color: var(--text-tertiary); font-size: 10px; font-variant-numeric: tabular-nums; }
.world-option-pane { min-width: 0; }
.world-option-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.world-option-heading h3 { margin: 0; font-size: 13px; font-weight: 600; }
.world-option-heading span { color: var(--text-tertiary); font-size: 10px; }
.world-option-list { border-top: 1px solid var(--border); }
.world-option-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(170px, 220px); gap: 24px; align-items: center; min-height: 46px; padding: 7px 0; border-bottom: 1px solid var(--border); }
.world-option-row > span { color: var(--text-secondary); font-size: 12px; }
.world-option-row .world-field-control { max-width: none; }
@media (max-width: 900px) {
  .world-field-row { grid-template-columns: 1fr; gap: 6px; }
  .world-field-row-top { padding-top: 10px; }
  .world-settings-grid, .world-list-grid { grid-template-columns: 1fr; gap: 24px; }
  .world-settings-wide { grid-column: auto; }
  .world-layout { grid-template-columns: 1fr; gap: 28px; }
  .world-sidebar { display: grid; grid-template-columns: auto 1fr; gap: 8px 16px; align-items: center; }
  .world-sidebar-label { margin: 0; }
  .world-sidebar-label:not(:first-child) { margin-top: 0; }
  .world-shard-switch { border-bottom: 0; }
  .world-nav-list { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .world-option-row { grid-template-columns: minmax(0, 1fr) minmax(150px, 200px); gap: 12px; }
}
</style>
