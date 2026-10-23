<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Eye, Pencil, RefreshCw, RotateCcw } from "@lucide/vue";
import AppDialog from "../components/AppDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { getInstanceModTask, listInstanceModTasks, listInstanceMods, listModApplyProtectionBackups, prepareInstanceMods, saveInstanceMods } from "../api/mods";
import { getInstanceOverview } from "../api/instances";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError } from "../lib/commandError";
import type { InstanceModSummary, ModSchemaStatus } from "../types/mods";
import { getWorkshopMetadata } from "../api/workshop";
import type { WorkshopItem } from "../types/workshop";
import { normalizeWorkshopIds } from "../lib/workshopInput";
import { compareWorkshopRevision, type ModUpdateHint } from "../lib/modUpdateCheck";
import { createSerialQueue } from "../lib/serialQueue";
import { useTasks } from "../composables/useTasks";
import type { TaskSnapshot } from "../types/contracts";
import {
  cloneConfiguration,
  groupModFields,
  mergeChangedConfiguration,
  sameJsonValue,
  validateChangedConfiguration,
} from "../lib/modConfiguration";
import type { ModFieldGroup } from "../lib/modConfiguration";
import { buildModApplyPreview, modNeedsApply, type ModApplyPreview } from "../lib/modApplyPreview";

const workspace = useWorkspace();
const { server, target, instanceName } = usePageTarget();
const taskStore = useTasks();
const queueSsh = createSerialQueue();
const items = ref<InstanceModSummary[]>([]);
const revision = ref<number | null>(null);
const loading = ref(false);
const error = ref("");
const addOpen = ref(false);
const workshopInput = ref("");
const workshopItems = ref<WorkshopItem[]>([]);
const workshopBusy = ref(false);
const workshopError = ref("");
const savingWorkshopId = ref<string | null>(null);
const preparingWorkshopId = ref<string | null>(null);
const prepareTask = ref<TaskSnapshot | null>(null);
const taskError = ref("");
const configEditorOpen = ref(false);
const editingMod = ref<InstanceModSummary | null>(null);
const configBase = ref<Record<string, unknown>>({});
const configFieldGroups = computed(() => editingMod.value
  ? groupModFields(editingMod.value.schema, configBase.value) : []);
const configDraft = ref<Record<string, unknown>>({});
const configChangedFields = ref<string[]>([]);
const configOptionSelection = ref<Record<string, string>>({});
const configError = ref("");
const configSaving = ref(false);
const applyPreviewOpen = ref(false);
const applyPreviewLoading = ref(false);
const applyPreviewError = ref("");
const applyPreview = ref<ModApplyPreview | null>(null);
const applyPreviewRevision = ref<number | null>(null);
const applyPreviewRuntime = ref<string | null>(null);
const updateCheckBusy = ref(false);
const updateCheckError = ref("");
const updateCheckResults = ref<Record<string, { hint: ModUpdateHint; checkedAt: string; sourceHandle: string | null }>>({});
let epoch = 0;
let sequence = 0;
let latestRequestId = "";
let workshopSequence = 0;
let updateSequence = 0;
let candidateFetchActive = false;
let taskPollTimer: ReturnType<typeof setTimeout> | undefined;

const enabledCount = computed(() => items.value.filter(item => item.desiredEnabled === true).length);
const pendingCount = computed(() => items.value.filter(modNeedsApply).length);
const checkableModsCount = computed(() => items.value.filter(item => item.desiredEnabled === true && !!item.preparedDigest).length);

function statusLabel(status: ModSchemaStatus) {
  return { available: "可配置", partial: "部分支持", noOptions: "无配置项", unavailable: "未准备", failed: "解析失败" }[status];
}
function statusTone(status: ModSchemaStatus) {
  return status === "available" ? "success" : status === "failed" ? "danger" : status === "unavailable" ? "warning" : "muted";
}
function isCurrent(requestId: string, ticket: number, requestEpoch: number, serverId: string, recordVersion: number, targetKey: string) {
  const current = target.value;
  return requestId === latestRequestId && ticket === sequence && requestEpoch === epoch
    && server.value?.serverId === serverId && server.value.recordVersion === recordVersion
    && current !== null && `${current.serverId}:${current.hostId}:${current.instanceId}` === targetKey;
}
async function refresh() {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget) return;
  const requestId = crypto.randomUUID();
  const ticket = ++sequence;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  latestRequestId = requestId;
  loading.value = true;
  error.value = "";
  try {
    if (workspace.preview.value) {
      items.value = [];
      revision.value = 0;
      return;
    }
    const response = await queueSsh(() => listInstanceMods({ schemaVersion: 1, requestId, target: instanceTarget, expectedRecordVersion: connection.recordVersion }));
    if (!isCurrent(requestId, ticket, requestEpoch, connection.serverId, connection.recordVersion, targetKey)) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || !Number.isSafeInteger(response.configRevision)) {
      throw new Error("MOD 查询响应身份或版本不匹配");
    }
    items.value = response.items;
    revision.value = response.configRevision;
  } catch (cause) {
    if (isCurrent(requestId, ticket, requestEpoch, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause).message;
  } finally {
    if (isCurrent(requestId, ticket, requestEpoch, connection.serverId, connection.recordVersion, targetKey)) loading.value = false;
  }
}
function resetContext() {
  epoch += 1;
  sequence += 1;
  workshopSequence += 1;
  updateSequence += 1;
  latestRequestId = "";
  items.value = [];
  revision.value = null;
  error.value = "";
  savingWorkshopId.value = null;
  preparingWorkshopId.value = null;
  workshopBusy.value = false;
  workshopItems.value = [];
  workshopError.value = "";
  addOpen.value = false;
  prepareTask.value = null;
  taskError.value = "";
  configEditorOpen.value = false;
  editingMod.value = null;
  configSaving.value = false;
  applyPreviewOpen.value = false;
  applyPreviewLoading.value = false;
  applyPreviewError.value = "";
  applyPreview.value = null;
  applyPreviewRevision.value = null;
  applyPreviewRuntime.value = null;
  updateCheckBusy.value = false;
  updateCheckError.value = "";
  updateCheckResults.value = {};
  candidateFetchActive = false;
  if (taskPollTimer) clearTimeout(taskPollTimer);
  taskPollTimer = undefined;
}
function openWorkshopDialog() {
  workshopInput.value = "";
  workshopItems.value = [];
  workshopError.value = "";
  addOpen.value = true;
}
async function resolveWorkshop() {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget || workspace.preview.value) return;
  const parsed = normalizeWorkshopIds(workshopInput.value);
  if (parsed.error) { workshopError.value = parsed.error; return; }
  const ticket = ++workshopSequence;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  workshopBusy.value = true;
  workshopError.value = "";
  try {
    const response = await queueSsh(() => getWorkshopMetadata({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      serverId: connection.serverId,
      expectedRecordVersion: connection.recordVersion,
      workshopIds: parsed.ids,
    }));
    if (ticket !== workshopSequence || requestEpoch !== epoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.schemaVersion !== 1 || response.serverId !== connection.serverId
      || response.recordVersion !== connection.recordVersion) throw new Error("Workshop 查询响应身份不匹配");
    workshopItems.value = response.items;
  } catch (cause) {
    if (ticket === workshopSequence && requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === connection.recordVersion) workshopError.value = describeError(cause).message;
  } finally {
    if (ticket === workshopSequence && requestEpoch === epoch) workshopBusy.value = false;
  }
}
async function addWorkshopItem(item: WorkshopItem) {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget || revision.value === null || item.result !== 1 || workspace.preview.value) return;
  const savedEpoch = epoch;
  const savedTarget = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const savedRecordVersion = connection.recordVersion;
  const expectedRevision = revision.value;
  const existing = items.value.filter(current => current.desiredEnabled !== null).map(current => ({
    workshopId: current.workshopId,
    enabled: current.desiredEnabled === true,
    configurationOptions: current.desiredConfiguration ?? {},
    artifactDigest: current.artifactDigest,
  }));
  if (!existing.some(current => current.workshopId === item.workshopId)) {
    existing.push({ workshopId: item.workshopId, enabled: true, configurationOptions: {}, artifactDigest: null });
  }
  savingWorkshopId.value = item.workshopId;
  workshopError.value = "";
  try {
    const response = await queueSsh(() => saveInstanceMods({ schemaVersion: 1, requestId: crypto.randomUUID(), target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision, operationId: crypto.randomUUID(), mods: existing }));
    if (epoch !== savedEpoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== savedTarget
      || server.value?.recordVersion !== savedRecordVersion) return;
    if (response.schemaVersion !== 1 || response.target.serverId !== instanceTarget.serverId
      || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId
      || response.verifiedRevision <= revision.value) throw new Error("MOD 保存响应身份或版本不匹配");
    addOpen.value = false;
    await refresh();
    await refreshPrepareTasks();
  } catch (cause) {
    if (epoch === savedEpoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget
      && server.value?.recordVersion === savedRecordVersion) workshopError.value = describeError(cause).message;
  } finally {
    if (epoch === savedEpoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget
      && server.value?.recordVersion === savedRecordVersion) savingWorkshopId.value = null;
  }
}

function canEditConfiguration(item: InstanceModSummary) {
  return item.desiredEnabled !== null && !!item.preparedDigest
    && (item.schema.status === "available" || item.schema.status === "partial")
    && item.schema.fields.length > 0;
}

function openConfigurationEditor(item: InstanceModSummary) {
  if (!canEditConfiguration(item)) {
    error.value = "此 MOD 没有可编辑的配置项";
    return;
  }
  editingMod.value = item;
  configBase.value = cloneConfiguration(item.desiredConfiguration);
  configDraft.value = {};
  configChangedFields.value = [];
  configError.value = "";
  const selections: Record<string, string> = {};
  for (const group of configFieldGroups.value) {
    if (group.control !== "choice") continue;
    const hasValue = Object.hasOwn(configBase.value, group.name);
    const current = hasValue ? configBase.value[group.name] : group.hasDefault ? group.defaultValue : undefined;
    const index = group.options.findIndex(option => sameJsonValue(option.value, current));
    selections[group.name] = index < 0 ? (hasValue ? "__unmapped__" : "") : String(index);
  }
  configOptionSelection.value = selections;
  configEditorOpen.value = true;
}

function fieldValueMismatch(group: ModFieldGroup) {
  if (!Object.hasOwn(configBase.value, group.name)) return false;
  const current = configBase.value[group.name];
  if (group.control === "choice") return !group.options.some(option => sameJsonValue(option.value, current));
  return group.control === "boolean" ? typeof current !== "boolean"
    : group.control === "number" ? typeof current !== "number" || !Number.isFinite(current)
      : group.control === "text" ? typeof current !== "string" : true;
}

function scalarFieldValue(group: ModFieldGroup): string | number | boolean {
  if (Object.hasOwn(configDraft.value, group.name)) return configDraft.value[group.name] as string | number | boolean;
  const current = configBase.value[group.name];
  if (!fieldValueMismatch(group) && Object.hasOwn(configBase.value, group.name)) return current as string | number | boolean;
  if (group.hasDefault && (typeof group.defaultValue === "string" || typeof group.defaultValue === "number"
    || typeof group.defaultValue === "boolean")) return group.defaultValue;
  return group.control === "boolean" ? false : "";
}

function markConfigurationChanged(name: string) {
  if (!configChangedFields.value.includes(name)) configChangedFields.value = [...configChangedFields.value, name];
}

function updateOptionSelection(group: ModFieldGroup, value: string) {
  configOptionSelection.value = { ...configOptionSelection.value, [group.name]: value };
  markConfigurationChanged(group.name);
  const index = Number(value);
  if (value === "" || !Number.isInteger(index) || index < 0 || index >= group.options.length) {
    const draft = { ...configDraft.value };
    delete draft[group.name];
    configDraft.value = draft;
    return;
  }
  configDraft.value = { ...configDraft.value, [group.name]: group.options[index]!.value };
}

function updateScalarField(group: ModFieldGroup, event: Event) {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;
  const value: unknown = group.control === "boolean" ? input.checked
    : group.control === "number" ? input.value === "" ? "" : Number(input.value)
      : input.value;
  configDraft.value = { ...configDraft.value, [group.name]: value };
  markConfigurationChanged(group.name);
}

function resetConfigurationField(group: ModFieldGroup) {
  const draft = { ...configDraft.value };
  delete draft[group.name];
  configDraft.value = draft;
  markConfigurationChanged(group.name);
  if (group.options.length > 0) {
    const index = group.hasDefault
      ? group.options.findIndex(option => sameJsonValue(option.value, group.defaultValue)) : -1;
    configOptionSelection.value = { ...configOptionSelection.value, [group.name]: index < 0 ? "" : String(index) };
  }
}

const configValidationError = computed(() => editingMod.value
  ? validateChangedConfiguration(editingMod.value.schema, configBase.value, configDraft.value, configChangedFields.value)
  : null);

async function saveConfiguration() {
  const connection = server.value;
  const instanceTarget = target.value;
  const item = editingMod.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || !item || expectedRevision === null || workspace.preview.value
      || configSaving.value || configChangedFields.value.length === 0 || configValidationError.value) return;
  if (!item.preparedDigest || !item.modinfoSha256) {
    configError.value = "固定 MOD 产物或配置摘要不可用，请刷新后重试";
    return;
  }
  const requestId = crypto.randomUUID();
  const operationId = crypto.randomUUID();
  const savedEpoch = epoch;
  const savedTarget = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const savedRecordVersion = connection.recordVersion;
  const nextConfiguration = mergeChangedConfiguration(configBase.value, configDraft.value, configChangedFields.value);
  const existing = items.value.filter(current => current.desiredEnabled !== null).map(current => ({
    workshopId: current.workshopId,
    enabled: current.desiredEnabled === true,
    configurationOptions: current.workshopId === item.workshopId
      ? nextConfiguration : current.desiredConfiguration ?? {},
    artifactDigest: current.workshopId === item.workshopId
      ? item.preparedDigest : current.artifactDigest,
  }));
  configSaving.value = true;
  configError.value = "";
  try {
    const response = await queueSsh(() => saveInstanceMods({
      schemaVersion: 1, requestId, target: instanceTarget, expectedRecordVersion: savedRecordVersion,
      expectedRevision, operationId, mods: existing,
      schemaBindings: [{ workshopId: item.workshopId, artifactDigest: item.preparedDigest!,
        modinfoSha256: item.modinfoSha256!, changedFields: [...configChangedFields.value].sort() }],
    }));
    if (savedEpoch !== epoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== savedTarget
      || server.value?.recordVersion !== savedRecordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.operationId !== operationId
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || response.verifiedRevision <= expectedRevision) {
      throw new Error("MOD 配置保存响应身份或版本不匹配");
    }
    configEditorOpen.value = false;
    editingMod.value = null;
    await refresh();
  } catch (cause) {
    if (savedEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget
      && server.value?.recordVersion === savedRecordVersion) configError.value = describeError(cause).message;
  } finally {
    if (savedEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === savedTarget
      && server.value?.recordVersion === savedRecordVersion) configSaving.value = false;
  }
}

function modActionChangeLabel(change: NonNullable<ModApplyPreview["changes"]>[number]) {
  return change.actionLabel;
}

async function openApplyPreview() {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || workspace.preview.value
      || loading.value || applyPreviewLoading.value) return;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const requestIds = { overview: crypto.randomUUID(), backups: crypto.randomUUID() };
  applyPreviewOpen.value = true;
  applyPreviewLoading.value = true;
  applyPreviewError.value = "";
  applyPreview.value = null;
  try {
    const [overview, backups] = await Promise.all([
      queueSsh(() => getInstanceOverview(instanceTarget, requestIds.overview, recordVersion)),
      queueSsh(() => listModApplyProtectionBackups({ schemaVersion: 1, requestId: requestIds.backups,
        target: instanceTarget, expectedRecordVersion: recordVersion, expectedRevision })),
    ]);
    if (requestEpoch !== epoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== recordVersion) return;
    if (revision.value !== expectedRevision) throw new Error("MOD 配置版本已变化，请刷新后重新生成预览");
    if (overview.schemaVersion !== 1 || overview.requestId !== requestIds.overview
      || overview.recordVersion !== recordVersion || overview.configRevision !== expectedRevision
      || overview.target.serverId !== instanceTarget.serverId || overview.target.hostId !== instanceTarget.hostId
      || overview.target.instanceId !== instanceTarget.instanceId || !overview.data.manifestSummary
      || overview.data.manifestSummary.configRevision !== expectedRevision) {
      throw new Error("运行概览与 MOD 配置版本不一致，请刷新后重试");
    }
    if (backups.schemaVersion !== 1 || backups.requestId !== requestIds.backups
      || backups.configRevision !== expectedRevision || backups.target.serverId !== instanceTarget.serverId
      || backups.target.hostId !== instanceTarget.hostId || backups.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("保护备份快照身份或版本不匹配");
    }
    const freshItems = items.value;
    const preview = buildModApplyPreview({
      mods: freshItems,
      backups: backups.items,
      runtimeStatus: overview.data.runtime?.status ?? overview.data.identity.runtimeStatus,
      cavesEnabled: overview.data.manifestSummary.cavesEnabled,
      worldDesiredVsDeployed: overview.data.desiredVsDeployed,
      restoreAcceptancePassed: backups.restoreAcceptancePassed,
    });
    applyPreview.value = preview;
    applyPreviewRevision.value = expectedRevision;
    applyPreviewRuntime.value = overview.data.runtime?.status ?? overview.data.identity.runtimeStatus;
  } catch (cause) {
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) applyPreviewError.value = describeError(cause).message;
  } finally {
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) applyPreviewLoading.value = false;
  }
}

async function checkModUpdates() {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || workspace.preview.value
      || updateCheckBusy.value || loading.value) return;
  const candidates = items.value.filter(item => item.desiredEnabled === true && item.preparedDigest);
  if (!candidates.length) {
    updateCheckError.value = "没有已准备且启用的 MOD 可检查";
    return;
  }
  const requestEpoch = epoch;
  const ticket = ++updateSequence;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const checkedAt = new Date().toISOString();
  updateCheckBusy.value = true;
  updateCheckError.value = "";
  const isCurrentCheck = () => ticket === updateSequence && requestEpoch === epoch
    && target.value !== null && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
    && server.value?.recordVersion === recordVersion && revision.value === expectedRevision;
  try {
    for (let offset = 0; offset < candidates.length; offset += 100) {
      const batch = candidates.slice(offset, offset + 100);
      const requestId = crypto.randomUUID();
      const response = await queueSsh(() => getWorkshopMetadata({
        schemaVersion: 1, requestId, serverId: connection.serverId,
        expectedRecordVersion: recordVersion, workshopIds: batch.map(item => item.workshopId),
      }));
      if (!isCurrentCheck()) return;
      if (response.schemaVersion !== 1 || response.requestId !== requestId
        || response.serverId !== connection.serverId || response.recordVersion !== recordVersion) {
        throw new Error("Workshop 更新检查响应身份不匹配");
      }
      const metadata = new Map(response.items.map(item => [item.workshopId, item]));
      const next = { ...updateCheckResults.value };
      for (const item of batch) {
        const current = metadata.get(item.workshopId);
        next[item.workshopId] = {
          hint: current?.result === 1 ? compareWorkshopRevision(current.contentHandle, item.preparedSourceHandle) : "unknown",
          checkedAt,
          sourceHandle: current?.contentHandle ?? null,
        };
      }
      updateCheckResults.value = next;
    }
  } catch (cause) {
    if (isCurrentCheck()) updateCheckError.value = describeError(cause).message;
  } finally {
    if (ticket === updateSequence && requestEpoch === epoch
      && target.value && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) updateCheckBusy.value = false;
  }
}

function updateHintLabel(workshopId: string) {
  const result = updateCheckResults.value[workshopId];
  if (!result) return "未检查";
  const item = items.value.find(current => current.workshopId === workshopId);
  const candidateCurrent = !!item?.candidateDigest && !!result.sourceHandle
    && result.sourceHandle === item.candidateSourceHandle;
  const candidateIsCurrentContent = candidateCurrent && item?.candidateDigest === item.preparedDigest;
  if (candidateIsCurrentContent || result.hint === "unchanged") return "已是最新";
  if (candidateCurrent && item?.candidateVersion
      && item.candidateVersion !== item.deployedVersion) return `${displayVersion(item.candidateVersion)} 可用`;
  if (candidateCurrent) return "内容有更新，版本号未声明";
  return result.hint === "candidate"
    ? item?.preparedSourceHandle ? "发现更新，可获取版本" : "首次获取版本基线"
    : "无法判断";
}
function updateHintTone(workshopId: string) {
  const result = updateCheckResults.value[workshopId];
  return result?.hint === "candidate" && updateHintLabel(workshopId) !== "已是最新" ? "warning"
    : result?.hint === "unchanged" || updateHintLabel(workshopId) === "已是最新" ? "success" : "muted";
}
function updateCandidateHandle(workshopId: string) {
  const result = updateCheckResults.value[workshopId];
  const item = items.value.find(current => current.workshopId === workshopId);
  if (!result?.sourceHandle) return null;
  return result.sourceHandle === item?.candidateSourceHandle
    || result.sourceHandle === item?.preparedSourceHandle ? null : result.sourceHandle;
}
function hasCurrentCandidate(workshopId: string) {
  const result = updateCheckResults.value[workshopId];
  const item = items.value.find(current => current.workshopId === workshopId);
  return !!result?.sourceHandle && !!item?.candidateDigest
    && item.candidateSourceHandle === result.sourceHandle;
}
function displayVersion(value: string | null) {
  if (!value) return "版本未知";
  const normalized = value.trim();
  return /^v/i.test(normalized) ? normalized : `v${normalized}`;
}
const applyPreviewStale = computed(() => applyPreviewRevision.value !== null && revision.value !== applyPreviewRevision.value);

function taskBelongsHere(task: TaskSnapshot, targetKey: string) {
  return task.operation === "modPrepare" && "instanceId" in task.target
    && `${task.target.serverId}:${task.target.hostId}:${task.target.instanceId}` === targetKey;
}

function acceptPrepareTask(task: TaskSnapshot, targetKey: string) {
  if (task.schemaVersion !== 1 || !taskBelongsHere(task, targetKey)) throw new Error("MOD 任务响应目标无效");
  prepareTask.value = task;
  taskStore.receive(task);
  if (["queued", "running"].includes(task.status)) scheduleTaskPoll(targetKey, task.operationId);
  else {
    if (taskPollTimer) clearTimeout(taskPollTimer);
    taskPollTimer = undefined;
    if (task.status === "succeeded") {
      const checkCandidate = candidateFetchActive;
      candidateFetchActive = false;
      void refresh().then(() => checkCandidate ? checkModUpdates() : undefined);
    } else if (task.status === "failed" || task.status === "interrupted") {
      candidateFetchActive = false;
    }
  }
}

async function refreshPrepareTasks() {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget || workspace.preview.value) return;
  const requestEpoch = epoch;
  const requestId = crypto.randomUUID();
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  try {
    const response = await queueSsh(() => listInstanceModTasks({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion }));
    if (requestEpoch !== epoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId) throw new Error("MOD 任务列表身份不匹配");
    const tasks = response.items.filter(task => taskBelongsHere(task, targetKey));
    for (const task of tasks) taskStore.receive(task);
    const current = tasks.find(task => task.status === "queued" || task.status === "running") ?? tasks[0] ?? null;
    if (current) acceptPrepareTask(current, targetKey);
  } catch (cause) {
    if (requestEpoch === epoch) taskError.value = describeError(cause).message;
  }
}

function scheduleTaskPoll(targetKey: string, operationId: string) {
  if (taskPollTimer) clearTimeout(taskPollTimer);
  const requestEpoch = epoch;
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget) return;
  const recordVersion = connection.recordVersion;
  taskPollTimer = setTimeout(async () => {
    try {
      const requestId = crypto.randomUUID();
      const response = await queueSsh(() => getInstanceModTask({ schemaVersion: 1, requestId, target: instanceTarget,
        expectedRecordVersion: recordVersion, operationId }));
      if (requestEpoch !== epoch || target.value === null
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== recordVersion) return;
      if (response.schemaVersion !== 1 || response.requestId !== requestId || response.task.operationId !== operationId
        || !taskBelongsHere(response.task, targetKey)) throw new Error("MOD 任务查询响应身份无效");
      acceptPrepareTask(response.task, targetKey);
    } catch (cause) {
      if (requestEpoch === epoch) taskError.value = describeError(cause).message;
      if (requestEpoch === epoch) scheduleTaskPoll(targetKey, operationId);
    }
  }, 3000);
}

async function adoptModCandidate(item: InstanceModSummary) {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || !item.candidateDigest
      || !hasCurrentCandidate(item.workshopId) || item.candidateDigest === item.desiredArtifactDigest
      || workspace.preview.value || savingWorkshopId.value !== null) return;
  const savedEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const requestId = crypto.randomUUID();
  const operationId = crypto.randomUUID();
  savingWorkshopId.value = item.workshopId;
  taskError.value = "";
  try {
    const mods = items.value.filter(current => current.desiredEnabled !== null).map(current => ({
      workshopId: current.workshopId,
      enabled: current.desiredEnabled === true,
      configurationOptions: current.desiredConfiguration ?? {},
      artifactDigest: current.workshopId === item.workshopId
        ? item.candidateDigest : current.artifactDigest,
    }));
    const response = await queueSsh(() => saveInstanceMods({
      schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision, operationId, mods,
    }));
    if (savedEpoch !== epoch || target.value === null
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
        || response.operationId !== operationId || response.verifiedRevision <= expectedRevision
        || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
        || response.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("MOD 候选版本保存响应身份或版本不匹配");
    }
    await refresh();
    await checkModUpdates();
  } catch (cause) {
    if (savedEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === connection.recordVersion) taskError.value = describeError(cause).message;
  } finally {
    if (savedEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === connection.recordVersion) savingWorkshopId.value = null;
  }
}

async function prepareMod(item: InstanceModSummary, sourceHandle?: string) {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget || revision.value === null || item.desiredEnabled !== true
    || (!sourceHandle && item.preparedDigest) || savingWorkshopId.value !== null
    || preparingWorkshopId.value !== null || workspace.preview.value
    || prepareTask.value && ["queued", "running"].includes(prepareTask.value.status)) return;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const operationId = crypto.randomUUID();
  taskError.value = "";
  candidateFetchActive = !!sourceHandle;
  preparingWorkshopId.value = item.workshopId;
  try {
    const metadataRequestId = crypto.randomUUID();
    const metadata = await queueSsh(() => getWorkshopMetadata({
      schemaVersion: 1, requestId: metadataRequestId, serverId: connection.serverId,
      expectedRecordVersion: connection.recordVersion, workshopIds: [item.workshopId],
    }));
    if (requestEpoch !== epoch || target.value === null
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== connection.recordVersion) return;
    if (metadata.schemaVersion !== 1 || metadata.requestId !== metadataRequestId
        || metadata.serverId !== connection.serverId || metadata.recordVersion !== connection.recordVersion) {
      throw new Error("Workshop 候选版本响应身份无效");
    }
    const currentSourceHandle = metadata.items.find(entry => entry.workshopId === item.workshopId)?.contentHandle ?? undefined;
    if (sourceHandle && currentSourceHandle !== sourceHandle) {
      throw new Error("Workshop 内容已再次更新，请重新检查后获取");
    }
    const pinnedSourceHandle = sourceHandle ?? currentSourceHandle;
    const requestId = crypto.randomUUID();
    const response = await queueSsh(() => prepareInstanceMods({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: revision.value!, operationId,
      workshopIds: [item.workshopId], sourceHandles: pinnedSourceHandle ? { [item.workshopId]: pinnedSourceHandle } : {} }));
    if (requestEpoch !== epoch || target.value === null
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || response.task.operationId !== operationId) {
      throw new Error("MOD 准备任务响应身份不匹配");
    }
    acceptPrepareTask(response.task, targetKey);
  } catch (cause) {
    candidateFetchActive = false;
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey) taskError.value = describeError(cause).message;
  } finally {
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey) preparingWorkshopId.value = null;
  }
}

function prepareStageLabel(task: TaskSnapshot) {
  const labels: Record<string, string> = { queued: "等待任务", prepare: "复核 MOD 与 Release", download: "下载 Workshop 内容", verify: "校验并固定内容", finalize: "完成准备", worker: "任务恢复中" };
  return labels[task.stage] ?? "准备 MOD 内容";
}
function prepareFailureLabel(task: TaskSnapshot) {
  const error = task.error;
  if (!error) return "任务错误详情暂不可用";
  const labels: Record<string, string> = {
    MOD_STEAMCMD_DOWNLOAD_FAILED: "SteamCMD 未能完成 Workshop 下载",
    MOD_STEAMCMD_TIMEOUT: "SteamCMD Workshop 下载超时",
    MOD_WORKSHOP_CONTENT_INVALID: "下载结束，但 Workshop 内容不完整",
    MOD_WORKSHOP_OUTPUT_CONFLICT: "Workshop 下载输出目录冲突",
    MOD_WORKSHOP_STEAMCMD_MISSING: "Release 镜像缺少 SteamCMD 文件",
    MOD_WORKSHOP_CONTAINER_FAILED: "下载容器启动或执行失败",
    MOD_WORKSHOP_DOWNLOAD_FAILED: "Workshop 下载失败",
  };
  const exitCode = error.details?.exitCode;
  const suffix = Number.isSafeInteger(exitCode) ? `（${error.code}，退出码 ${exitCode}）` : `（${error.code}）`;
  const failureKinds: Record<string, string> = {
    daemon: "Docker daemon 不可用",
    network: "Docker bridge 网络启动失败",
    mount: "任务目录 bind mount 失败",
    image: "下载工具镜像不可用",
    permission: "宿主权限或安全策略拒绝启动",
    runtime: "Docker 容器运行时启动失败",
    unknown: "Docker 未返回可识别的失败类别",
  };
  const kind = error.details?.failureKind;
  const label = error.code === "MOD_WORKSHOP_CONTAINER_FAILED" && typeof kind === "string"
    ? failureKinds[kind] ?? labels[error.code]
    : labels[error.code] ?? "MOD 准备失败";
  return `${label}${suffix}`;
}
function runtimeLabel(status: string | null) {
  return status === "running" ? "运行中" : status === "stopped" ? "已停止"
    : status === "degraded" ? "部分异常" : status === "starting" ? "启动中" : "未知";
}
function taskIncludesWorkshop(task: TaskSnapshot | null, workshopId: string) {
  if (!task || !task.result || typeof task.result !== "object" || !("requestedWorkshopIds" in task.result)) return false;
  const requested = task.result.requestedWorkshopIds;
  return Array.isArray(requested) && requested.includes(workshopId);
}
watch(() => `${server.value?.serverId ?? ""}:${server.value?.recordVersion ?? ""}:${target.value?.hostId ?? ""}:${target.value?.instanceId ?? ""}:${workspace.preview.value}`, () => {
  resetContext();
  void refresh();
  void refreshPrepareTasks();
}, { immediate: true });
onBeforeUnmount(() => { if (taskPollTimer) clearTimeout(taskPollTimer); });
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="MOD">
      <template #actions>
        <button class="button" :disabled="workspace.preview.value || !target || loading || applyPreviewLoading"
          @click="openApplyPreview"><Eye :size="15"/>应用预览</button>
        <button class="button" :disabled="workspace.preview.value || !target || !checkableModsCount || loading || updateCheckBusy"
          @click="checkModUpdates">{{ updateCheckBusy ? '检查中…' : '检查更新' }}</button>
        <button class="button primary" :disabled="workspace.preview.value || !target" @click="openWorkshopDialog">添加 MOD</button>
        <button class="button" :disabled="loading || !target" @click="refresh">
          <RefreshCw :size="16" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="taskError" class="error-strip">{{ taskError }}</div>
    <div v-if="updateCheckError" class="error-strip">{{ updateCheckError }}</div>
    <section v-if="prepareTask" class="mod-task-state" aria-live="polite">
      <div>
        <strong>{{ prepareTask.status === 'succeeded' ? 'MOD 内容已准备' : prepareTask.status === 'failed' ? 'MOD 准备失败' : prepareTask.status === 'interrupted' ? 'MOD 任务中断' : '正在准备 MOD 内容' }}</strong>
        <span>{{ prepareStageLabel(prepareTask) }}</span>
        <span v-if="prepareTask.status === 'failed'" class="task-failure-detail">{{ prepareFailureLabel(prepareTask) }}</span>
      </div>
      <span class="muted-text mono">{{ prepareTask.progress.kind === 'determinate' ? `${prepareTask.progress.completed}/${prepareTask.progress.total}` : '处理中' }}</span>
    </section>
    <section v-if="revision !== null" class="summary-line">
      <div><span class="metric-number">{{ enabledCount }}</span><span>个已启用</span></div>
      <span class="summary-divider" />
      <div><span class="metric-number warning-number">{{ pendingCount }}</span><span>个待应用</span></div>
      <span class="summary-divider" />
      <div class="muted-text">配置版本 {{ revision }}</div>
    </section>

    <div v-if="loading && items.length === 0" class="empty-state"><h2>正在读取 MOD</h2></div>
    <div v-else-if="!loading && items.length === 0" class="empty-state"><h2>尚未发现 MOD</h2></div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>Workshop ID</th><th>期望状态</th><th>实际部署</th><th>配置解析</th><th>版本摘要</th><th>更新检查</th><th>内容</th><th class="table-action">操作</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="item.workshopId">
            <td class="mono">{{ item.workshopId }}</td>
            <td><StatusLabel :tone="item.desiredEnabled === null ? 'warning' : item.desiredEnabled ? 'success' : 'muted'" :label="item.desiredEnabled === null ? '已移出期望' : item.desiredEnabled ? '启用' : '禁用'" /></td>
            <td>
              <StatusLabel :tone="modNeedsApply(item) ? 'warning' : 'success'" :label="modNeedsApply(item) ? '待应用' : '一致'" />
              <small class="table-sub">{{ item.deployedVersion ? displayVersion(item.deployedVersion) : '运行版本未知' }}</small>
            </td>
            <td><StatusLabel :tone="statusTone(item.schema.status)" :label="statusLabel(item.schema.status)" /></td>
            <td class="mono">{{ displayVersion(item.schema.metadata.version) }}</td>
            <td>
              <StatusLabel :tone="updateHintTone(item.workshopId)" :label="updateHintLabel(item.workshopId)" />
              <div v-if="updateCheckResults[item.workshopId]?.hint === 'candidate' && updateCandidateHandle(item.workshopId)" class="table-inline-actions">
                <button class="text-button" :disabled="workspace.preview.value || preparingWorkshopId !== null || !!prepareTask && ['queued', 'running'].includes(prepareTask.status)"
                  @click="prepareMod(item, updateCandidateHandle(item.workshopId) ?? undefined)">{{ item.preparedSourceHandle ? '获取版本' : '建立基线' }}</button>
              </div>
              <div v-if="hasCurrentCandidate(item.workshopId) && item.candidateDigest !== item.desiredArtifactDigest" class="table-inline-actions">
                <small v-if="item.candidateVersion" class="table-sub">可用 {{ displayVersion(item.candidateVersion) }}</small>
                <button class="text-button" :disabled="workspace.preview.value || savingWorkshopId !== null || !!prepareTask && ['queued', 'running'].includes(prepareTask.status)"
                  @click="adoptModCandidate(item)">采用版本</button>
              </div>
            </td>
            <td><StatusLabel :tone="item.preparedDigest ? 'success' : 'warning'" :label="item.preparedDigest ? '已准备' : '未准备'" /></td>
            <td class="table-action">
              <button v-if="item.desiredEnabled === true && !item.preparedDigest" class="text-button"
                :disabled="workspace.preview.value || savingWorkshopId !== null || preparingWorkshopId !== null || !!prepareTask && ['queued', 'running'].includes(prepareTask.status)"
                @click="prepareMod(item)">{{ taskIncludesWorkshop(prepareTask, item.workshopId) && prepareTask?.status === 'running' ? '准备中…' : '准备内容' }}</button>
              <span v-if="item.preparedDigest" class="muted-text">固定内容</span>
              <span v-else class="muted-text">启用后可准备</span>
              <button v-if="canEditConfiguration(item)" class="icon-button" type="button"
                :aria-label="`编辑 ${item.schema.metadata.name ?? item.workshopId} 配置`"
                title="编辑配置" :disabled="workspace.preview.value || configSaving || loading"
                @click="openConfigurationEditor(item)"><Pencil :size="15"/></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <AppDialog v-model:open="addOpen" title="添加 MOD">
      <div class="form-field">
        <label for="workshop-input">Workshop ID 或链接</label>
        <textarea id="workshop-input" v-model="workshopInput" rows="4" placeholder="每行一个 ID 或链接" />
      </div>
      <div v-if="workshopError" class="error-strip">{{ workshopError }}</div>
      <div v-if="workshopItems.length" class="table-wrap">
        <table>
          <thead><tr><th>图片</th><th>条目</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="item in workshopItems" :key="item.workshopId">
              <td>
                <img v-if="item.previewUrl" class="workshop-thumbnail" :src="item.previewUrl" :alt="item.title ?? item.workshopId" loading="lazy" />
                <span v-else class="muted-text">无</span>
              </td>
              <td><strong>{{ item.title ?? item.workshopId }}</strong><small class="table-sub mono">{{ item.workshopId }}</small></td>
              <td><StatusLabel :tone="item.result === 1 ? 'success' : 'danger'" :label="item.result === 1 ? '可用' : '不可用'" /></td>
              <td class="mono">{{ item.timeUpdated ?? '未知' }}</td>
              <td>
                <button class="text-button"
                  :disabled="savingWorkshopId === item.workshopId || item.result !== 1 || items.some(current => current.workshopId === item.workshopId && current.desiredEnabled !== null)"
                  @click="addWorkshopItem(item)">
                  {{ savingWorkshopId === item.workshopId ? '保存中…' : items.some(current => current.workshopId === item.workshopId && current.desiredEnabled !== null) ? '已在列表' : '加入列表' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer>
        <button class="button" type="button" @click="addOpen = false">关闭</button>
        <button class="button primary" type="button" :disabled="workshopBusy || workspace.preview.value" @click="resolveWorkshop">{{ workshopBusy ? '查询中…' : '查询' }}</button>
      </template>
    </AppDialog>

    <AppDialog v-model:open="configEditorOpen" :title="`编辑配置 · ${editingMod?.schema.metadata.name ?? editingMod?.workshopId ?? ''}`">
      <div v-if="editingMod" class="mod-config-editor">
        <div class="mod-config-meta">
          <span class="mono">{{ editingMod.workshopId }}</span>
          <span>{{ editingMod.schema.metadata.version ?? '版本未知' }}</span>
          <span class="mono">{{ editingMod.preparedDigest?.slice(0, 12) }}</span>
        </div>
        <div v-for="group in configFieldGroups" :key="group.name" class="mod-config-field">
          <div class="mod-config-field-heading">
            <label :for="`mod-config-${group.name}`">{{ group.label }}<span v-if="group.shared" class="mod-config-shared">共用</span></label>
            <button v-if="group.control !== 'unsupported'" class="icon-button" type="button"
              :aria-label="`恢复 ${group.label} 的 MOD 默认值`" title="恢复 MOD 默认值"
              :disabled="configSaving || !configChangedFields.includes(group.name) && !Object.hasOwn(configBase, group.name)"
              @click="resetConfigurationField(group)"><RotateCcw :size="14"/></button>
          </div>
          <select v-if="group.control === 'choice'" :id="`mod-config-${group.name}`"
            :value="configOptionSelection[group.name] ?? ''" :disabled="configSaving"
            @change="updateOptionSelection(group, ($event.target as HTMLSelectElement).value)">
            <option v-if="configOptionSelection[group.name] === '__unmapped__'" value="__unmapped__" disabled>当前值不在此版本选项中</option>
            <option value="">使用 MOD 默认值</option>
            <option v-for="(option, index) in group.options" :key="index" :value="String(index)" :title="option.hover ?? undefined">
              {{ option.label }}
            </option>
          </select>
          <label v-else-if="group.control === 'boolean'" class="mod-config-toggle" :for="`mod-config-${group.name}`">
            <input :id="`mod-config-${group.name}`" type="checkbox" :checked="scalarFieldValue(group) === true"
              :disabled="configSaving" @change="updateScalarField(group, $event)" />
            <span>{{ scalarFieldValue(group) === true ? '开启' : '关闭' }}</span>
          </label>
          <input v-else-if="group.control === 'number'" :id="`mod-config-${group.name}`" type="number" step="any"
            :value="scalarFieldValue(group)" :disabled="configSaving" @input="updateScalarField(group, $event)" />
          <input v-else-if="group.control === 'text'" :id="`mod-config-${group.name}`" type="text"
            :value="scalarFieldValue(group)" :disabled="configSaving" @input="updateScalarField(group, $event)" />
          <div v-else class="mod-config-unsupported">{{ group.issue ?? group.fields[0]?.reason ?? '此配置项的类型无法安全编辑' }}</div>
          <small v-if="group.hover" class="mod-config-hover">{{ group.hover }}</small>
          <small v-if="fieldValueMismatch(group)" class="mod-config-warning">当前保存值与此版本可选值不匹配；保存其他项不会覆盖它</small>
        </div>
      </div>
      <div v-if="configError" class="error-strip">{{ configError }}</div>
      <div v-if="configValidationError" class="error-strip">{{ configValidationError }}</div>
      <template #footer>
        <button class="button" type="button" :disabled="configSaving" @click="configEditorOpen = false">取消</button>
        <button class="button primary" type="button"
          :disabled="configSaving || workspace.preview.value || configChangedFields.length === 0 || !!configValidationError"
          @click="saveConfiguration">{{ configSaving ? '保存中…' : '保存配置' }}</button>
      </template>
    </AppDialog>

    <AppDialog v-model:open="applyPreviewOpen" title="MOD 应用预览" description="只读核对目标内容、分片与恢复条件">
      <div v-if="applyPreviewLoading" class="empty-state"><h2>正在核对应用条件</h2></div>
      <div v-else-if="applyPreviewError" class="inline-error">{{ applyPreviewError }}</div>
      <div v-else-if="applyPreview" class="mod-apply-preview">
        <div class="mod-apply-summary">
          <div><span>配置版本</span><strong class="mono">{{ applyPreviewRevision }}</strong></div>
          <div><span>专服状态</span><strong>{{ runtimeLabel(applyPreviewRuntime) }}</strong></div>
          <div><span>目标分片</span><strong>{{ applyPreview.shards.map(shard => shard === 'master' ? 'Master' : 'Caves').join(' / ') || '无待应用变更' }}</strong></div>
        </div>
        <div v-if="applyPreviewStale" class="error-strip">MOD 配置版本已变化，此预览已失效。请重新生成。</div>
        <section class="mod-apply-section">
          <h3>MOD 变更 <span class="muted-text">{{ applyPreview.changes.length }}</span></h3>
          <p v-if="!applyPreview.changes.length" class="muted-text">期望与已部署状态一致</p>
          <div v-for="change in applyPreview.changes" :key="change.workshopId" class="mod-apply-change">
            <span class="mono">{{ change.workshopId }}</span>
            <StatusLabel tone="warning" :label="modActionChangeLabel(change)" />
            <span v-if="change.configurationKeys.length" class="muted-text">配置：{{ change.configurationKeys.join('、') }}</span>
            <span v-if="change.contentDigest" class="mono muted-text">{{ change.contentDigest.slice(0, 12) }}</span>
          </div>
        </section>
        <section v-if="applyPreview.changes.length" class="mod-apply-section">
          <h3>冷保护备份</h3>
          <div v-if="applyPreview.protectionBackup" class="mod-protection-backup">
            <StatusLabel tone="success" label="可恢复" />
            <span>{{ applyPreview.protectionBackup.createdAt }}</span>
            <span class="mono">{{ applyPreview.protectionBackup.backupId }}</span>
          </div>
          <p v-else class="mod-preview-warning">尚无可用冷保护点；应用任务必须先创建并验证冷备份。</p>
        </section>
        <div v-if="applyPreview.warnings.length" class="mod-preview-warning-list">
          <p v-for="warning in applyPreview.warnings" :key="warning">{{ warning }}</p>
        </div>
        <div v-if="applyPreview.blockers.length" class="mod-preview-blockers">
          <strong>当前不能发布</strong>
          <p v-for="blocker in applyPreview.blockers" :key="blocker">{{ blocker }}</p>
        </div>
      </div>
      <template #footer>
        <button class="button" type="button" @click="applyPreviewOpen = false">关闭</button>
        <button class="button primary" type="button" :disabled="applyPreviewLoading || !applyPreview || applyPreviewStale"
          @click="openApplyPreview">重新核对</button>
      </template>
    </AppDialog>
  </div>
</template>
