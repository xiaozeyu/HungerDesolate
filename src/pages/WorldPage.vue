<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import { Eye, EyeOff, RefreshCw, Save } from "@lucide/vue";
import AppSelect from "../components/AppSelect.vue";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
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
const activeView = ref<"basic" | "access" | "worldgen" | "worldSettings">("basic");
const selectedShard = ref<DstWorldShard>("master");
const activeGroup = ref("misc");
const draftVersion = ref(0), pendingOperationId = ref("");
let epoch = 0, querySequence = 0, latestRequestId = "";

const worldSummary = computed(() => [
  { label: "期望版本", value: data.value?.revision ?? "—" },
  { label: "已部署版本", value: data.value?.deployedRevision ?? "—", tone: status.value === "inSync" ? "success" as const : "warning" as const },
  { label: "状态", value: status.value === "inSync" ? "已应用" : "待应用", tone: status.value === "inSync" ? "success" as const : "warning" as const },
  { label: "编辑", value: hasChanges.value ? "未保存" : "已同步", tone: hasChanges.value ? "warning" as const : "default" as const },
]);
const hasChanges = computed(() => {
  if (!baseDesired.value) return false;
  return JSON.stringify(draft) !== JSON.stringify(baseDesired.value)
    || tokenValue.value.length > 0 || passwordValue.value.length > 0;
});
const status = computed(() => {
  if (!data.value) return "unknown";
  return data.value.deployedRevision === data.value.revision
      && data.value.deployedDigest === data.value.desiredDigest
    ? "inSync" : "pending";
});
const selectedCategory = computed<DstWorldCategory | null>(() =>
  activeView.value === "worldgen" || activeView.value === "worldSettings" ? activeView.value : null);
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

function selectView(view: typeof activeView.value) {
  activeView.value = view;
  if (view === "worldgen" || view === "worldSettings") {
    const groups = new Set(visibleWorldOptions(selectedShard.value, view).map((item) => item.group));
    if (!groups.has(activeGroup.value)) activeGroup.value = groups.values().next().value ?? "";
  }
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
      throw new Error("世界配置版本早于已读取结果");
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

async function togglePasswordVisibility() {
  if (!showPassword.value && !passwordValue.value && passwordStatus.value === "configured" && !revealedPassword.value) {
    await revealSavedSecrets();
  }
  showPassword.value = !showPassword.value;
}

async function toggleTokenVisibility() {
  if (!showToken.value && !tokenValue.value && tokenStatus.value === "configured" && !revealedToken.value) {
    await revealSavedSecrets();
  }
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
        <StatusLabel v-if="status==='inSync'" tone="success" label="已应用"/>
        <StatusLabel v-else-if="status==='pending'" tone="warning" label="待应用"/>
        <button class="button" :disabled="loading || saving || applyBusy || !target" @click="refresh"><RefreshCw :size="16"/>{{loading?'读取中…':'刷新'}}</button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{error.message}}</strong>
      <ul v-if="applyBlockers.length"><li v-for="code in applyBlockers" :key="code">{{applyBlockerLabel(code)}}</li></ul>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul>
    </div>
    <p v-if="saveMessage" class="success-message" role="status" aria-live="polite">{{saveMessage}}</p>
    <div v-if="loading && !data" class="empty-state" role="status">读取世界配置…</div>
    <div v-else-if="!target && !workspace.preview.value" class="empty-state"><h2>无法确定专服</h2><p>返回专服列表后重新进入。</p></div>
    <template v-else>
      <div v-if="stale" class="environment-error" role="status">显示上次读取的配置；当前结果已过期，保存已停用。</div>
      <SummaryBar v-if="data" :items="worldSummary" label="世界配置摘要"/>
      <nav class="world-view-tabs" aria-label="世界配置分类">
        <button type="button" :aria-pressed="activeView==='basic'" :class="{active:activeView==='basic'}" @click="selectView('basic')">基本设置</button>
        <button type="button" :aria-pressed="activeView==='access'" :class="{active:activeView==='access'}" @click="selectView('access')">访问与联机</button>
        <button type="button" :aria-pressed="activeView==='worldgen'" :class="{active:activeView==='worldgen'}" @click="selectView('worldgen')">世界生成</button>
        <button type="button" :aria-pressed="activeView==='worldSettings'" :class="{active:activeView==='worldSettings'}" @click="selectView('worldSettings')">世界规则</button>
      </nav>

      <section v-if="activeView==='basic'" class="section-block world-form-panel">
        <div class="section-heading"><h2>基本设置</h2><span class="muted-text">配置版本 {{data?.revision ?? 0}}</span></div>
        <div class="form-grid world-basic-grid">
          <label>世界名称<input v-model="draft.roomName" maxlength="64" autocomplete="off"/></label>
          <label>游戏模式<AppSelect v-model="draft.gameMode" :options="dstGameModeOptions" label="游戏模式"/></label>
          <label>最大玩家数<input v-model.number="draft.maxPlayers" type="number" min="1" max="128"/></label>
          <label>语言<AppSelect v-model="draft.language" :options="languageOptions" label="语言"/></label>
          <label class="world-description-field">世界描述<textarea v-model="draft.description" maxlength="256" rows="3"/></label>
        </div>

        <div class="world-subsection">
          <div class="section-heading"><h2>联机凭据</h2></div>
          <div class="form-grid world-credential-grid">
            <div class="world-secret-field">
              <label for="world-server-password">服务器密码</label>
              <div class="world-secret-input">
                <input id="world-server-password" v-model="passwordInput" :type="showPassword ? 'text' : 'password'" autocomplete="new-password" maxlength="8192" :placeholder="passwordStatus==='configured'?'留空保持当前密码':'输入服务器密码'"/>
                <button class="world-secret-toggle" type="button" :aria-label="showPassword ? '隐藏服务器密码' : '显示服务器密码'" :aria-pressed="showPassword" @click="togglePasswordVisibility">
                  <EyeOff v-if="showPassword" :size="16"/><Eye v-else :size="16"/>
                </button>
              </div>
            </div>
            <div class="world-secret-field">
              <label for="world-klei-token">Klei Token</label>
              <div class="world-secret-input">
                <input id="world-klei-token" v-model="tokenInput" :type="showToken ? 'text' : 'password'" autocomplete="off" maxlength="8192" :placeholder="tokenStatus==='configured'?'留空保持当前 Token':'输入 Klei Token'"/>
                <button class="world-secret-toggle" type="button" :aria-label="showToken ? '隐藏 Klei Token' : '显示 Klei Token'" :aria-pressed="showToken" @click="toggleTokenVisibility">
                  <EyeOff v-if="showToken" :size="16"/><Eye v-else :size="16"/>
                </button>
              </div>
            </div>
          </div>
          <p v-if="secretError" class="error-strip" role="alert">{{ secretError }}</p>
          <p v-if="revealLoading" class="muted-text" role="status">正在读取已保存凭据…</p>
        </div>

        <div class="world-subsection">
          <div class="section-heading"><h2>世界选项</h2><span class="muted-text">普通开关不会重置世界</span></div>
          <div class="world-switch-grid">
            <label class="world-switch-row"><span><strong>允许 PVP</strong><small>玩家之间可以互相造成伤害</small></span><span class="world-switch"><input v-model="draft.pvp" type="checkbox"/><span class="world-switch-track" aria-hidden="true"><span class="world-switch-thumb"/></span></span></label>
            <label class="world-switch-row"><span><strong>无人时暂停</strong><small>没有玩家在线时暂停世界时间</small></span><span class="world-switch"><input v-model="draft.pauseWhenEmpty" type="checkbox"/><span class="world-switch-track" aria-hidden="true"><span class="world-switch-thumb"/></span></span></label>
          </div>
        </div>

        <div class="world-subsection">
          <div class="section-heading"><h2>世界分片</h2><span class="muted-text">由实例创建时确定</span></div>
          <div class="world-shard-summary"><div><strong>Master</strong><StatusLabel :tone="'success'" label="已启用"/></div><div><strong>Caves</strong><StatusLabel :tone="draft.cavesEnabled?'success':'muted'" :label="draft.cavesEnabled?'已启用':'未启用'"/></div></div>
        </div>
        <p v-if="data?.validationWarnings.length" class="muted-text">{{data.validationWarnings.join('；')}}</p>
      </section>

      <section v-else-if="activeView==='access'" class="section-block world-form-panel">
        <div class="section-heading"><h2>访问与联机</h2></div>
        <div class="access-settings-grid">
          <div class="access-control-group">
            <div class="section-heading"><h3>投票</h3></div>
            <label class="world-switch-row"><span><strong>启用踢人投票</strong><small>允许玩家发起踢人投票</small></span><span class="world-switch"><input v-model="draft.advanced.voteEnabled" type="checkbox"/><span class="world-switch-track" aria-hidden="true"><span class="world-switch-thumb"/></span></span></label>
            <label>踢人投票比例<input v-model.number="draft.advanced.voteKickPercentage" type="number" min="0" max="100" :disabled="!draft.advanced.voteEnabled"/></label>
          </div>
          <div class="access-control-group">
            <div class="section-heading"><h3>白名单</h3></div>
            <label>白名单预留位<input v-model.number="draft.advanced.whitelistSlots" type="number" min="0" :max="draft.maxPlayers"/></label>
            <label>白名单 Klei ID<textarea :value="draft.advanced.whitelist.join('\n')" rows="6" @input="updateList('whitelist',($event.target as HTMLTextAreaElement).value)"/></label>
          </div>
          <div class="access-control-group access-list-group">
            <div class="section-heading"><h3>名单</h3></div>
            <label>管理员 Klei ID<textarea :value="draft.advanced.adminList.join('\n')" rows="5" @input="updateList('adminList',($event.target as HTMLTextAreaElement).value)"/></label>
            <label>封禁名单 Klei ID<textarea :value="draft.advanced.blocklist.join('\n')" rows="5" @input="updateList('blocklist',($event.target as HTMLTextAreaElement).value)"/></label>
          </div>
        </div>
      </section>

      <section v-else class="section-block world-form-panel">
        <div class="section-heading"><h2>{{activeView==='worldgen'?'世界生成':'世界规则'}}</h2><span class="muted-text">{{optionGroups.reduce((total, group) => total + group.count, 0)}} 项</span></div>
        <div class="world-shard-tabs" aria-label="选择分片">
          <button type="button" :aria-pressed="selectedShard==='master'" :class="{active:selectedShard==='master'}" @click="selectShard('master')">Master</button>
          <button v-if="draft.cavesEnabled" type="button" :aria-pressed="selectedShard==='caves'" :class="{active:selectedShard==='caves'}" @click="selectShard('caves')">Caves</button>
        </div>
        <nav class="world-group-tabs" aria-label="选项分组">
          <button v-for="group in optionGroups" :key="group.key" type="button" :aria-pressed="activeGroup===group.key" :class="{active:activeGroup===group.key}" @click="activeGroup=group.key">{{group.label}} <span>{{group.count}}</span></button>
        </nav>
        <div class="form-grid world-option-grid">
          <label v-for="item in selectedGroupOptions" :key="item.key">{{item.label}}
            <AppSelect :model-value="optionValue(draft.worldgen[selectedShard],item,selectedShard)"
              :options="optionValuesForShard(item,selectedShard)" :label="item.label"
              @update:model-value="changeWorldOption(item.key,$event)"/>
          </label>
        </div>
      </section>
      <div v-if="activeView!=='basic' && data?.validationWarnings.length" class="muted-text world-validation-warnings">{{data.validationWarnings.join('；')}}</div>
      <div class="world-page-actions">
        <span class="world-action-feedback" :class="{'world-action-error':!!error}" :role="error?'alert':'status'" aria-live="polite">{{error?.message || saveMessage || (loading?'正在读取配置…':stale?'读取结果已过期':hasChanges?'有未保存的更改':'没有未保存的更改')}}</span>
        <button class="button" :disabled="saving || applyBusy || loading || stale || !data || !target" @click="save">仅保存</button>
        <button class="button primary" :disabled="saving || applyBusy || loading || stale || !data || !target" @click="saveAndApply"><Save :size="16"/>{{saving?'保存中…':applyBusy?'检查并部署中…':'保存并应用'}}</button>
      </div>
    </template>
  </div>
</template>


<style scoped>
.world-secret-field { display: flex; flex-direction: column; gap: 6px; }
.world-secret-input { position: relative; display: flex; align-items: center; }
.world-secret-input input { width: 100%; padding-right: 38px; }
.world-secret-toggle { position: absolute; right: 3px; top: 50%; display: grid; width: 30px; height: 30px; place-items: center; transform: translateY(-50%); border: 0; border-radius: 2px; background: transparent; color: var(--text-tertiary); }
.world-secret-toggle:hover { color: var(--text); background: var(--surface-hover); }
</style>
