<script setup lang="ts">
import { computed, h, nextTick, reactive, ref, watch } from "vue";
import { RefreshCw, RotateCcw, Upload } from "@lucide/vue";
import {
  NAlert, NButton, NCard, NDivider, NEmpty, NForm, NFormItem, NFormItemGi, NGrid,
  NInput, NInputNumber, NMenu, NPopconfirm, NRadioButton, NRadioGroup, NSwitch,
  NTabs, NTabPane, NTag, NText, type MenuOption,
} from "naive-ui";
import AppSelect from "../components/AppSelect.vue";
import LoadingState from "../components/LoadingState.vue";
import { notifyError, notifyInfo, notifySuccess } from "../naive/notify";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import { applyWorldConfig, getWorldConfig, revealWorldSecrets, saveWorldConfig } from "../api/world";
import { useWorkspace } from "../composables/useWorkspace";
import { useOperations } from "../composables/useOperations";
import { usePageTarget } from "../composables/usePageTarget";
import { describeError, type UserError } from "../lib/commandError";
import { dstGameModeOptions, languageOptions } from "../lib/dstGameModes";
import { defaultWorldSettings, optionDefaultValue, optionValue, optionValuesForShard, resetShardGroup, updateShardOption, visibleWorldOptions } from "../lib/dstWorldOptions";
import { copyWorldSettings } from "../lib/worldConfigDraft";
import { clearWorldDraft, readWorldDraft, writeWorldDraft } from "../lib/worldDraft";
import { operationTargetKey } from "../lib/operationView";
import type { DstWorldCategory, DstWorldOption, DstWorldShard, WorldConfig, WorldSettings } from "../types/world";

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
const tokenStatus = ref<WorldConfig["tokenStatus"]>("unknown");
const passwordStatus = ref<WorldConfig["serverPasswordStatus"]>("unknown");
const tokenValue = ref(""), passwordValue = ref("");
const revealedToken = ref(""), revealedPassword = ref("");
const revealLoading = ref(false), secretError = ref("");
const listText = reactive({ whitelist: "", adminList: "", blocklist: "" });
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
const optionMenuOptions = computed<MenuOption[]>(() => optionGroups.value.map((group) => ({
  key: group.key,
  label: () => h("div", { class: "world-menu-item" }, [
    h("span", { class: "world-menu-item-label" }, group.label),
    h("span", { class: "world-menu-item-count" }, String(group.count)),
  ]),
})));
function optionChanged(item: DstWorldOption) {
  return optionValue(draft.worldgen[selectedShard.value], item, selectedShard.value)
    !== optionDefaultValue(item, selectedShard.value);
}
const activeGroupChangedCount = computed(() => selectedGroupOptions.value.filter(optionChanged).length);

function resetActiveGroup() {
  const category = selectedCategory.value;
  if (!category || activeGroupChangedCount.value === 0) return;
  const shard = selectedShard.value;
  draft.worldgen[shard] = resetShardGroup(draft.worldgen[shard], shard, activeGroup.value, category);
  draftVersion.value++;
}

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
  listText.whitelist = draft.advanced.whitelist.join("\n");
  listText.adminList = draft.advanced.adminList.join("\n");
  listText.blocklist = draft.advanced.blocklist.join("\n");
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
      revealedToken.value = "";
      revealedPassword.value = "";
      secretError.value = "";
      if (useRestored) notifyInfo("草稿已恢复", "已恢复未保存的草稿。");
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
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function listStats(key: "adminList" | "whitelist" | "blocklist") {
  const values = draft.advanced[key];
  return { count: values.length, duplicates: values.length - new Set(values).size };
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

function validateDraft(): string | null {
  if (!draft.roomName.trim()) return "世界名称不能为空";
  if (draft.roomName.trim().length > 64) return "世界名称不能超过 64 个字符";
  if (draft.description.length > 256) return "世界描述不能超过 256 个字符";
  if (!Number.isInteger(draft.maxPlayers) || draft.maxPlayers < 1 || draft.maxPlayers > 128) return "最大玩家数必须在 1 到 128 之间";
  if (!Number.isInteger(draft.advanced.voteKickPercentage) || draft.advanced.voteKickPercentage < 0 || draft.advanced.voteKickPercentage > 100) return "踢人投票比例必须在 0 到 100 之间";
  if (!Number.isInteger(draft.advanced.whitelistSlots) || draft.advanced.whitelistSlots < 0 || draft.advanced.whitelistSlots > draft.maxPlayers) return "白名单预留位不能超过最大玩家数";
  if (draft.advanced.whitelistSlots > 0 && draft.advanced.whitelist.length === 0) return "设置白名单预留位前，请先填写白名单";
  for (const [key, label] of [
    ["whitelist", "白名单"], ["adminList", "管理员名单"], ["blocklist", "封禁名单"],
  ] as const) {
    const values = draft.advanced[key];
    if (new Set(values).size !== values.length) return `${label}存在重复 Klei ID`;
  }
  for (const value of [tokenValue.value, passwordValue.value]) if (value.length > 8192 || value.includes("\0")) return "凭据长度或字符无效";
  return null;
}

async function save(): Promise<boolean> {
  const instanceTarget = target.value, connection = server.value, currentConfig = data.value;
  if (saving.value) {
    notifyInfo("保存请求正在处理中", "请等待结果。");
    return false;
  }
  if (!instanceTarget || !connection || !currentConfig) {
    notifyError("无法保存", "尚未读取到可保存的专服配置，请先刷新。");
    return false;
  }
  if (loading.value) {
    notifyInfo("专服配置仍在读取", "请稍后保存。");
    return false;
  }
  if (stale.value) {
    notifyError("配置读取已过期", "请刷新后再保存。");
    return false;
  }
  if (workspace.preview.value) {
    notifyInfo("界面预览数据不能保存", "请在桌面应用中编辑真实专服。");
    return false;
  }
  if (!hasChanges.value) {
    notifyInfo("没有需要保存的更改");
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
      revealedToken.value = "";
      revealedPassword.value = "";
      secretError.value = "";
    }
    pendingOperationId.value = "";
    return true;
  } catch (cause) {
    if (epoch === savedEpoch && target.value && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget) {
      error.value = describeError(cause);
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
    return;
  }
  const result = outcome.result!;
  notifySuccess("世界配置已应用", [result.runningShards.length
    ? "已部署并重启原运行分片；隔离运行验证仍待完成。"
    : "已部署，专服保持停止；隔离运行验证仍待完成。", ...result.warnings].join(" "));
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
  listText[key] = value;
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

watch(draft, () => { draftVersion.value++; pendingOperationId.value = ""; }, { deep: true, flush: "sync" });
watch(draft, () => {
  const selected = target.value;
  if (!selected || !baseDesired.value) return;
  if (hasChanges.value) writeWorldDraft(selected, baseRevision.value, draft);
  else clearWorldDraft(selected);
}, { deep: true, flush: "post" });
watch([tokenValue, passwordValue], () => { draftVersion.value++; pendingOperationId.value = ""; }, { flush: "sync" });
</script>

<template>
  <div class="page world-page">
    <PageHeader :eyebrow="instanceName" title="世界配置">
      <template #actions>
        <NTag v-if="hasChanges" size="small" type="warning" :bordered="false">未保存</NTag>
        <NButton size="small" :disabled="loading || saving || applyBusy || !target" @click="refresh">
          <template #icon><RefreshCw :size="16"/></template>{{ loading ? "读取中…" : "刷新" }}
        </NButton>
        <NButton size="small" type="primary" :disabled="!canSave" @click="saveAndApply">
          <template #icon><Upload :size="16"/></template>{{ saving ? "保存中…" : applyBusy ? "应用中…" : "保存并应用" }}
        </NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <NAlert v-if="error" type="error" :bordered="false" show-icon class="world-alert" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="applyBlockers.length"><li v-for="code in applyBlockers" :key="code">{{ applyBlockerLabel(code) }}</li></ul>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </NAlert>
    <LoadingState v-if="loading && !data" description="读取世界配置…"/>
    <NEmpty v-else-if="!target && !workspace.preview.value" class="world-empty" description="无法确定专服">
      <template #extra><span class="muted-text">返回专服列表后重新进入。</span></template>
    </NEmpty>
    <template v-else>
      <NAlert v-if="stale" type="warning" :bordered="false" show-icon class="world-alert" role="status">
        显示上次读取的配置；当前结果已过期，保存已停用。
      </NAlert>
      <NTabs :value="activeView" type="line" class="world-tabs"
        @update:value="value => selectView(value as 'basic' | 'access' | 'world')">
        <NTabPane name="basic" tab="基本设置" display-directive="show">
          <section class="world-section">
            <NForm label-placement="left" label-width="110" :show-feedback="false" class="world-form">
              <NGrid cols="1 s:1 m:2 l:2" :x-gap="28" :y-gap="2" responsive="screen">
                <NFormItemGi label="世界名称" :label-props="{ for: 'world-room-name' }">
                  <NInput v-model:value="draft.roomName" placeholder="" maxlength="64" :input-props="{ id: 'world-room-name', autocomplete: 'off' }"/>
                </NFormItemGi>
                <NFormItemGi label="游戏模式">
                  <AppSelect v-model="draft.gameMode" :options="dstGameModeOptions" label="游戏模式"/>
                </NFormItemGi>
                <NFormItemGi label="最大玩家数" :label-props="{ for: 'world-max-players' }">
                  <NInputNumber :value="draft.maxPlayers" placeholder="" :min="1" :max="128" :precision="0" :show-button="false" :input-props="{ id: 'world-max-players' }" @update:value="value => draft.maxPlayers = value ?? 1"/>
                </NFormItemGi>
                <NFormItemGi label="语言">
                  <AppSelect v-model="draft.language" :options="languageOptions" label="语言"/>
                </NFormItemGi>
              </NGrid>
              <NFormItem label="世界描述" :label-props="{ for: 'world-description' }">
                <NInput v-model:value="draft.description" type="textarea" placeholder="" maxlength="256" :autosize="{ minRows: 3 }" :input-props="{ id: 'world-description' }"/>
              </NFormItem>
            </NForm>
          </section>

          <section class="world-section">
            <NDivider title-placement="left">联机凭据</NDivider>
            <NForm label-placement="left" label-width="110" :show-feedback="false" class="world-form">
              <NGrid cols="1 s:1 m:2 l:2" :x-gap="28" :y-gap="2" responsive="screen">
                <NFormItemGi label="服务器密码" :label-props="{ for: 'world-server-password' }">
                  <NInput v-model:value="passwordInput" type="password" show-password-on="click" maxlength="8192"
                    :placeholder="passwordStatus === 'configured' ? '留空保持当前密码' : '输入服务器密码'"
                    :input-props="{ id: 'world-server-password', autocomplete: 'new-password' }"/>
                </NFormItemGi>
                <NFormItemGi label="Klei Token" :label-props="{ for: 'world-klei-token' }">
                  <NInput v-model:value="tokenInput" type="password" show-password-on="click" maxlength="8192"
                    :placeholder="tokenStatus === 'configured' ? '留空保持当前 Token' : '输入 Klei Token'"
                    :input-props="{ id: 'world-klei-token', autocomplete: 'off' }"/>
                </NFormItemGi>
              </NGrid>
            </NForm>
            <NAlert v-if="secretError" type="error" :bordered="false" show-icon class="world-alert" role="alert">{{ secretError }}</NAlert>
          </section>

          <section class="world-section">
            <NDivider title-placement="left">世界选项</NDivider>
            <NForm label-placement="left" label-width="110" :show-feedback="false" class="world-form">
              <NGrid cols="1 s:1 m:2 l:2" :x-gap="28" :y-gap="2" responsive="screen">
                <NFormItemGi label="允许 PVP"><NSwitch v-model:value="draft.pvp"/></NFormItemGi>
                <NFormItemGi label="无人时暂停"><NSwitch v-model:value="draft.pauseWhenEmpty"/></NFormItemGi>
              </NGrid>
            </NForm>
          </section>
        </NTabPane>

        <NTabPane name="access" tab="访问与联机" display-directive="show">
          <section class="world-section">
            <NForm label-placement="left" label-width="120" :show-feedback="false" class="world-form">
              <NGrid cols="1 s:1 m:2 l:2" :x-gap="28" :y-gap="2" responsive="screen">
                <NFormItemGi label="启用踢人投票"><NSwitch v-model:value="draft.advanced.voteEnabled"/></NFormItemGi>
                <NFormItemGi label="踢人投票比例" :label-props="{ for: 'world-vote-percent' }">
                  <NInputNumber :value="draft.advanced.voteKickPercentage" placeholder="" :min="0" :max="100" :precision="0" :show-button="false" :disabled="!draft.advanced.voteEnabled" :input-props="{ id: 'world-vote-percent' }" @update:value="value => draft.advanced.voteKickPercentage = value ?? 0"/>
                </NFormItemGi>
                <NFormItemGi label="白名单预留位" :label-props="{ for: 'world-whitelist-slots' }">
                  <NInputNumber :value="draft.advanced.whitelistSlots" placeholder="" :min="0" :max="draft.maxPlayers" :precision="0" :show-button="false" :input-props="{ id: 'world-whitelist-slots' }" @update:value="value => draft.advanced.whitelistSlots = value ?? 0"/>
                </NFormItemGi>
              </NGrid>
              <NFormItem label="白名单 Klei ID" :label-props="{ for: 'world-whitelist' }">
                <div class="world-list-field">
                  <NInput :value="listText.whitelist" type="textarea" placeholder="一行一个 Klei ID" :autosize="{ minRows: 5 }" :input-props="{ id: 'world-whitelist' }" @update:value="value => updateList('whitelist', value)"/>
                  <div class="world-list-meta">
                    <NText depth="3">{{ listStats('whitelist').count }} 个 ID</NText>
                    <NTag v-if="listStats('whitelist').duplicates" size="small" type="warning" :bordered="false">{{ listStats('whitelist').duplicates }} 个重复</NTag>
                  </div>
                </div>
              </NFormItem>
              <NGrid cols="1 s:1 m:2 l:2" :x-gap="28" :y-gap="2" responsive="screen">
                <NFormItemGi label="管理员 Klei ID" :label-props="{ for: 'world-admin-list' }">
                  <div class="world-list-field">
                    <NInput :value="listText.adminList" type="textarea" placeholder="一行一个 Klei ID" :autosize="{ minRows: 5 }" :input-props="{ id: 'world-admin-list' }" @update:value="value => updateList('adminList', value)"/>
                    <div class="world-list-meta">
                      <NText depth="3">{{ listStats('adminList').count }} 个 ID</NText>
                      <NTag v-if="listStats('adminList').duplicates" size="small" type="warning" :bordered="false">{{ listStats('adminList').duplicates }} 个重复</NTag>
                    </div>
                  </div>
                </NFormItemGi>
                <NFormItemGi label="封禁名单 Klei ID" :label-props="{ for: 'world-block-list' }">
                  <div class="world-list-field">
                    <NInput :value="listText.blocklist" type="textarea" placeholder="一行一个 Klei ID" :autosize="{ minRows: 5 }" :input-props="{ id: 'world-block-list' }" @update:value="value => updateList('blocklist', value)"/>
                    <div class="world-list-meta">
                      <NText depth="3">{{ listStats('blocklist').count }} 个 ID</NText>
                      <NTag v-if="listStats('blocklist').duplicates" size="small" type="warning" :bordered="false">{{ listStats('blocklist').duplicates }} 个重复</NTag>
                    </div>
                  </div>
                </NFormItemGi>
              </NGrid>
            </NForm>
          </section>
        </NTabPane>

        <NTabPane name="world" tab="世界" display-directive="show">
          <section class="world-section">
            <div class="world-world-head">
              <NRadioGroup :value="worldView" size="small" @update:value="value => selectWorldView(value as 'worldSettings' | 'worldgen')">
                <NRadioButton value="worldSettings">世界规则</NRadioButton>
                <NRadioButton value="worldgen">世界生成</NRadioButton>
              </NRadioGroup>
            </div>
            <div class="world-layout">
              <NCard size="small" class="world-nav-card">
                <div class="world-nav-block">
                  <NText depth="3" class="world-nav-label">分片</NText>
                  <NRadioGroup :value="selectedShard" size="small" @update:value="value => selectShard(value as 'master' | 'caves')">
                    <NRadioButton value="master">Master</NRadioButton>
                    <NRadioButton v-if="draft.cavesEnabled" value="caves">Caves</NRadioButton>
                  </NRadioGroup>
                </div>
                <div class="world-nav-block">
                  <NText depth="3" class="world-nav-label">分类</NText>
                  <NMenu :value="activeGroup" :options="optionMenuOptions" :indent="0" class="world-menu" @update:value="value => activeGroup = String(value)"/>
                </div>
              </NCard>
              <div class="world-option-pane">
                <div class="world-option-heading">
                  <div class="world-option-title">
                    <h3>{{ activeGroupLabel }}</h3>
                    <NTag size="small" :bordered="false">{{ selectedGroupOptions.length }} 项</NTag>
                    <NTag v-if="activeGroupChangedCount" size="small" type="warning" :bordered="false">已修改 {{ activeGroupChangedCount }}</NTag>
                  </div>
                  <NPopconfirm @positive-click="resetActiveGroup">
                    <template #trigger>
                      <NButton size="small" class="world-reset-button" :disabled="!activeGroupChangedCount">
                        <template #icon><RotateCcw :size="14"/></template>重置本组
                      </NButton>
                    </template>
                    恢复本组世界选项的默认值？
                  </NPopconfirm>
                </div>
                <NEmpty v-if="!selectedGroupOptions.length" description="没有可配置项"/>
                <NForm v-else label-placement="left" label-width="150" size="small" :show-feedback="false" class="world-options-form">
                  <NGrid cols="1 s:1 m:2 l:2" :x-gap="28" :y-gap="2" responsive="screen">
                    <NFormItemGi v-for="item in selectedGroupOptions" :key="item.key">
                      <template #label>
                        <span class="world-option-label">{{ item.label }}</span>
                        <span v-if="optionChanged(item)" class="world-option-dot" role="img" aria-label="已修改" title="已修改"></span>
                      </template>
                      <AppSelect :model-value="optionValue(draft.worldgen[selectedShard],item,selectedShard)"
                        :options="optionValuesForShard(item,selectedShard)" :label="item.label"
                        @update:model-value="changeWorldOption(item.key,$event)"/>
                    </NFormItemGi>
                  </NGrid>
                </NForm>
              </div>
            </div>
          </section>
        </NTabPane>
      </NTabs>
    </template>
  </div>
</template>


<style scoped>
.world-page { min-width: 0; }
.world-alert { margin-bottom: 16px; }
.world-alert :deep(ul) { margin: 8px 0 0; padding-left: 20px; }
.world-empty { padding: 48px 0; }
.world-tabs { margin-top: 2px; }
.world-tabs :deep(.n-tabs-nav) { margin-bottom: 22px; }
.world-section { margin-bottom: 32px; }
.world-section :deep(.n-divider) { margin: 0 0 18px; }
.world-form { max-width: 100%; }
.world-form :deep(.n-form-item) { margin-bottom: 8px; }
.world-list-field { display: flex; flex-direction: column; gap: 6px; width: 100%; min-width: 0; }
.world-list-meta { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.world-world-head { display: flex; justify-content: flex-start; margin-bottom: 20px; }
.world-layout { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 24px; align-items: start; }
.world-nav-card { position: sticky; top: 16px; }
.world-nav-card :deep(.n-card__content) { padding: 14px; }
.world-nav-block + .world-nav-block { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.world-nav-label { display: block; margin-bottom: 8px; font-size: 10px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; }
.world-menu { margin: 0 -6px; }
.world-menu :deep(.n-menu-item) { margin: 2px 0; }
.world-menu :deep(.n-menu-item-content) { height: 34px; padding: 0 10px !important; border-radius: 7px; transition: background 140ms ease, color 140ms ease, box-shadow 140ms ease; }
.world-menu :deep(.n-menu-item-content::before) { display: none; }
.world-menu :deep(.n-menu-item-content:hover) { background: var(--surface-hover); box-shadow: 0 1px 2px color-mix(in srgb, var(--text) 5%, transparent); }
.world-menu :deep(.n-menu-item-content--selected) { background: color-mix(in srgb, var(--accent) 9%, var(--surface)); box-shadow: inset 2px 0 0 var(--accent), 0 1px 2px color-mix(in srgb, var(--text) 5%, transparent); }
.world-menu :deep(.n-menu-item-content--selected .n-menu-item-content-header) { color: var(--accent); font-weight: 600; }
.world-menu :deep(.n-menu-item-content--selected .world-menu-item-count) { color: var(--accent); opacity: .75; }
.world-menu :deep(.n-menu-item-content:focus-visible) { outline: 2px solid var(--accent); outline-offset: -2px; }
.world-menu :deep(.n-menu-item-content-header) { font-size: 12px; }
.world-menu-item { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; }
.world-menu-item-count { color: var(--text-tertiary); font-size: 10px; font-variant-numeric: tabular-nums; }
.world-reset-button.n-button { border-radius: 8px; box-shadow: 0 1px 2px color-mix(in srgb, var(--text) 8%, transparent); }
.world-reset-button.n-button:hover { box-shadow: 0 4px 12px color-mix(in srgb, var(--text) 10%, transparent); transform: translateY(-1px); }
.world-option-pane { min-width: 0; }
.world-option-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; margin-bottom: 12px; }
.world-option-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.world-option-title h3 { margin: 0; font-size: 15px; font-weight: 600; }
.world-options-form :deep(.n-form-item) { margin-bottom: 4px; }
.world-option-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.world-option-dot { display: inline-block; width: 6px; height: 6px; margin-left: 6px; border-radius: 50%; background: var(--warning); vertical-align: middle; }
@media (max-width: 900px) {
  .world-layout { grid-template-columns: 1fr; }
  .world-nav-card { position: static; }
  .world-nav-card :deep(.n-card__content) { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  .world-nav-block + .world-nav-block { margin-top: 0; }
}
@media (max-width: 760px) {
  .world-nav-card :deep(.n-card__content) { grid-template-columns: 1fr; }
  .world-nav-block + .world-nav-block { margin-top: 16px; }
}
</style>
