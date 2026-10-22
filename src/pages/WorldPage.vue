<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { Eye, RefreshCw, RotateCw, Save } from "@lucide/vue";
import AppDialog from "../components/AppDialog.vue";
import AppSelect from "../components/AppSelect.vue";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { applyWorldConfig, getWorldConfig, previewWorldApply, saveWorldConfig, type WorldApplyPreview } from "../api/world";
import { useWorkspace } from "../composables/useWorkspace";
import { usePageTarget } from "../composables/usePageTarget";
import { describeError, type UserError } from "../lib/commandError";
import { dstGameModeOptions, languageOptions } from "../lib/dstGameModes";
import { defaultWorldSettings, optionValue, optionValuesForShard, updateShardOption, visibleWorldOptions } from "../lib/dstWorldOptions";
import { copyWorldSettings } from "../lib/worldConfigDraft";
import type { DstWorldCategory, DstWorldShard, WorldConfig, WorldSettings } from "../types/world";

const workspace = useWorkspace();
const { server, instance, target, instanceName } = usePageTarget();
const data = ref<WorldConfig | null>(null);
const applyPreview = ref<WorldApplyPreview|null>(null), previewBusy = ref(false);
const applyBusy = ref(false);
const draft = reactive<WorldSettings>(defaultWorldSettings(false));
const baseDesired = ref<WorldSettings | null>(null), baseRevision = ref(0);
const loading = ref(false), saving = ref(false), stale = ref(false);
const error = ref<UserError | null>(null);
const saveMessage = ref("");
const tokenStatus = ref<WorldConfig["tokenStatus"]>("unknown");
const passwordStatus = ref<WorldConfig["serverPasswordStatus"]>("unknown");
const tokenValue = ref(""), passwordValue = ref("");
const activeView = ref<"basic" | "access" | "worldgen" | "worldSettings">("basic");
const selectedShard = ref<DstWorldShard>("master");
const activeGroup = ref("misc");
const draftVersion = ref(0), pendingOperationId = ref("");
let epoch = 0, querySequence = 0, latestRequestId = "";

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
      assignSettings(response.data.desired);
      baseDesired.value = copySettings(response.data.desired);
      baseRevision.value = response.configRevision;
      tokenValue.value = "";
      passwordValue.value = "";
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

async function save() {
  const instanceTarget = target.value, connection = server.value, currentConfig = data.value;
  if (saving.value) {
    saveMessage.value = "保存请求正在处理中，请等待结果。";
    return;
  }
  if (!instanceTarget || !connection || !currentConfig) {
    saveMessage.value = "尚未读取到可保存的专服配置，请先刷新。";
    return;
  }
  if (loading.value) {
    saveMessage.value = "专服配置仍在读取，请稍后保存。";
    return;
  }
  if (stale.value) {
    saveMessage.value = "当前配置读取已过期，请刷新后再保存。";
    return;
  }
  if (workspace.preview.value) {
    saveMessage.value = "界面预览数据不能保存，请在桌面应用中编辑真实专服。";
    return;
  }
  if (!hasChanges.value) {
    saveMessage.value = "当前配置没有更改，无需保存。";
    return;
  }
  const validation = validateDraft();
  if (validation) {
    error.value = { code: "WORLD_CONFIG_INPUT_INVALID", message: validation, recoverySteps: [], field: null };
    return;
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
      || server.value?.recordVersion !== connection.recordVersion) return;
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
    }
    pendingOperationId.value = "";
    saveMessage.value = `期望配置已保存（版本 ${response.data.revision}），尚未应用到运行中的世界。`;
  } catch (cause) {
    if (epoch === savedEpoch && target.value && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget) {
      error.value = describeError(cause);
      saveMessage.value = "";
    }
  } finally {
    if (epoch === savedEpoch) saving.value = false;
  }
}

async function previewApply() {
  const instanceTarget=target.value,connection=server.value,config=data.value;
  if(!instanceTarget||!connection||!config||loading.value||saving.value||hasChanges.value||stale.value)return;
  const ticketEpoch=epoch,version=draftVersion.value,revision=baseRevision.value,domainRevision=config.revision,requestId=crypto.randomUUID();
  previewBusy.value=true;error.value=null;
  try {
    const result=await previewWorldApply({
      schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: revision,
    });
    if(epoch!==ticketEpoch||draftVersion.value!==version||target.value?.instanceId!==instanceTarget.instanceId
      ||server.value?.recordVersion!==connection.recordVersion||baseRevision.value!==revision||data.value?.revision!==domainRevision)return;
    if(result.requestId!==requestId||result.revision!==revision||result.target.serverId!==instanceTarget.serverId
      ||result.target.hostId!==instanceTarget.hostId||result.target.instanceId!==instanceTarget.instanceId)
      throw new Error("配置应用预览身份或版本不匹配");
    applyPreview.value=result;
  }catch(cause){if(epoch===ticketEpoch)error.value=describeError(cause);}
  finally{if(epoch===ticketEpoch)previewBusy.value=false;}
}

async function applyWorld() {
  const instanceTarget = target.value, connection = server.value, preview = applyPreview.value;
  if (!instanceTarget || !connection || !preview || !preview.allowed || applyBusy.value
      || !preview.activeReleaseId || loading.value || saving.value || hasChanges.value || stale.value || workspace.preview.value) return;
  const ticketEpoch = epoch, revision = preview.revision, desiredDigest = preview.desiredDigest;
  const requestId = crypto.randomUUID();
  applyBusy.value = true;
  error.value = null;
  saveMessage.value = "正在部署世界配置…";
  try {
    const result = await applyWorldConfig({
      schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: revision,
      desiredDigest, activeReleaseId: preview.activeReleaseId, operationId: crypto.randomUUID(),
    });
    if (epoch !== ticketEpoch || target.value?.instanceId !== instanceTarget.instanceId
        || server.value?.recordVersion !== connection.recordVersion) return;
    if (result.requestId !== requestId || result.revision !== revision || result.desiredDigest !== desiredDigest
        || result.activeReleaseId !== preview.activeReleaseId
        || result.runtimeVerified !== false || result.target.serverId !== instanceTarget.serverId
        || result.target.hostId !== instanceTarget.hostId || result.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("配置应用结果身份、版本或验证状态无效");
    }
    applyPreview.value = null;
    saveMessage.value = result.runningShards.length
      ? "世界配置已部署并重启原运行分片；隔离运行验证仍待完成。"
      : "世界配置已部署，专服保持停止；隔离运行验证仍待完成。";
    await refresh();
  } catch (cause) {
    if (epoch === ticketEpoch) {
      error.value = describeError(cause);
      saveMessage.value = "";
    }
  } finally {
    if (epoch === ticketEpoch) applyBusy.value = false;
  }
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

watch([() => target.value?.serverId, () => target.value?.hostId, () => target.value?.instanceId,
  () => server.value?.recordVersion, () => workspace.preview.value], () => {
  epoch++;
  querySequence++;
  latestRequestId = "";
  data.value = null;
  applyPreview.value=null;
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

watch(draft, () => { draftVersion.value++; pendingOperationId.value = ""; applyPreview.value=null; saveMessage.value = ""; }, { deep: true, flush: "sync" });
watch([tokenValue, passwordValue], () => { draftVersion.value++; pendingOperationId.value = ""; saveMessage.value = ""; }, { flush: "sync" });
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="世界配置">
      <template #actions>
        <StatusLabel v-if="status==='inSync'" tone="success" label="已应用"/>
        <StatusLabel v-else-if="status==='pending'" tone="warning" label="待应用"/>
        <button class="button" :disabled="loading || saving || !target" @click="refresh"><RefreshCw :size="16"/>{{loading?'读取中…':'刷新'}}</button>
        <button v-if="status==='pending'&&!hasChanges" class="button" :disabled="previewBusy || stale" @click="previewApply"><Eye :size="16"/>{{previewBusy?'预览中…':'预览应用'}}</button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{error.message}}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{step}}</li></ul>
    </div>
    <p v-if="saveMessage" class="success-message" role="status" aria-live="polite">{{saveMessage}}</p>
    <div v-if="loading && !data" class="empty-state" role="status">读取世界配置…</div>
    <div v-else-if="!target && !workspace.preview.value" class="empty-state"><h2>无法确定专服</h2><p>返回专服列表后重新进入。</p></div>
    <template v-else>
      <div v-if="stale" class="environment-error" role="status">显示上次读取的配置；当前结果已过期，保存已停用。</div>
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
            <label>服务器密码<input v-model="passwordValue" type="password" autocomplete="new-password" maxlength="8192" :placeholder="passwordStatus==='configured'?'留空保持当前密码':'输入服务器密码'"/></label>
            <label>Klei Token<input v-model="tokenValue" type="password" autocomplete="off" maxlength="8192" :placeholder="tokenStatus==='configured'?'留空保持当前 Token':'输入 Klei Token'"/></label>
          </div>
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
        <button class="button primary" :disabled="saving || loading || stale || !data || !target" @click="save"><Save :size="16"/>{{saving?'保存中…':'保存期望配置'}}</button>
      </div>
    </template>
    <AppDialog :open="!!applyPreview" title="应用世界配置预览" @update:open="value=>{if(!value)applyPreview=null}">
      <div v-if="applyPreview" class="form-stack">
        <dl class="creation-facts"><div><dt>配置版本</dt><dd>{{applyPreview.revision}}</dd></div><div><dt>世界存档</dt><dd>{{applyPreview.worldDataPresent?`${applyPreview.worldDataFileCount} 个文件`:"未发现现有存档文件"}}</dd></div><div><dt>Token</dt><dd>{{applyPreview.tokenStatus==='configured'?'已配置':'缺失'}}</dd></div><div><dt>世界生成选项</dt><dd>{{applyPreview.newWorldOptions.length}} 项会影响新世界生成</dd></div></dl>
        <p v-if="applyPreview.backupRequirement==='required'">应用前需要创建并验证保护备份。</p>
        <ul v-if="applyPreview.newWorldOptions.length"><li v-for="option in applyPreview.newWorldOptions.slice(0,12)" :key="`${option.shard}:${option.key}`">{{option.shard==='master'?'Master':'Caves'}} · {{option.label}}</li></ul>
        <ul v-if="applyPreview.blockers.length" class="environment-error"><li v-for="blocker in applyPreview.blockers" :key="blocker">{{blocker==='PROTECTION_BACKUP_REQUIRED'?'发现现有世界数据；备份功能接入前不会覆盖这些数据':blocker==='WORLD_CONFIG_RENDER_UNSUPPORTED'?'当前期望配置无法安全转换为 DST 配置文件':blocker==='WORLD_TOKEN_MISSING'?'应用前需要设置 Klei Token':blocker==='WORLD_DEPLOYMENT_MISSING'?'专服尚未完成容器部署':blocker==='WORLD_RELEASE_MISSING'?'专服缺少活动 DST Release':'配置应用条件未满足'}}</li></ul>
        <ul v-if="applyPreview.warnings.length"><li v-for="warning in applyPreview.warnings" :key="warning">{{warning}}</li></ul>
      </div>
      <template #footer><button class="button" :disabled="applyBusy" @click="applyPreview=null">关闭</button><button class="button primary" :disabled="applyBusy || !applyPreview?.allowed" @click="applyWorld"><RotateCw :size="16"/>{{applyBusy?'部署中…':'应用配置'}}</button></template>
    </AppDialog>
  </div>
</template>
