<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Eye, Library, Pencil, Power, RefreshCw, RotateCcw, Trash2 } from "@lucide/vue";
import AppDialog from "../components/AppDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { applyInstanceMods, getInstanceModApplyTask, listInstanceModApplyTasks,
  listInstanceMods, previewInstanceMods, saveInstanceMods } from "../api/mods";
import { getInstanceOverview } from "../api/instances";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError } from "../lib/commandError";
import type { InstanceModSummary, ModSchemaStatus } from "../types/mods";
import { listHostMods, type HostModItem } from "../api/hostMods";
import { createSerialQueue } from "../lib/serialQueue";
import { formatBytes } from "../lib/environmentView";
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
import { buildModApplyPreview, mergeModApplyBlockers, modNeedsApply, type ModApplyPreview } from "../lib/modApplyPreview";

const workspace = useWorkspace();
const { server, target, instanceName } = usePageTarget();
const taskStore = useTasks();
const queueSsh = createSerialQueue();
const items = ref<InstanceModSummary[]>([]);
const revision = ref<number | null>(null);
const loading = ref(false);
const error = ref("");
const hostLibraryOpen = ref(false), hostLibraryItems = ref<HostModItem[]>([]), hostLibraryLoading = ref(false), hostLibraryError = ref("");
const savingWorkshopId = ref<string | null>(null);
const applyTask = ref<TaskSnapshot | null>(null);
const applyTaskError = ref("");
const removeTarget = ref<InstanceModSummary | null>(null);
const removeBusy = ref(false);
const removeError = ref("");
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
const serverApplyPreview = ref<import("../api/mods").ModApplyPreviewResponse | null>(null);
const applyBusy = ref(false);
const createProtectionBackup = ref(false);
const applyPreviewRevision = ref<number | null>(null);
const applyPreviewRuntime = ref<string | null>(null);
let epoch = 0;
let sequence = 0;
let latestRequestId = "";
const finalizedPrepareTasks = new Set<string>();
let applyPollTimer: ReturnType<typeof setTimeout> | undefined;

const enabledCount = computed(() => items.value.filter(item => item.desiredEnabled === true).length);
const pendingCount = computed(() => items.value.filter(modNeedsApply).length);

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
async function refresh(): Promise<boolean> {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget) return false;
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
      return true;
    }
    const response = await queueSsh(() => listInstanceMods({ schemaVersion: 1, requestId, target: instanceTarget, expectedRecordVersion: connection.recordVersion }));
    if (!isCurrent(requestId, ticket, requestEpoch, connection.serverId, connection.recordVersion, targetKey)) return false;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || !Number.isSafeInteger(response.configRevision)) {
      throw new Error("MOD 查询响应身份或版本不匹配");
    }
    items.value = response.items;
    revision.value = response.configRevision;
    return true;
  } catch (cause) {
    if (isCurrent(requestId, ticket, requestEpoch, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause).message;
    return false;
  } finally {
    if (isCurrent(requestId, ticket, requestEpoch, connection.serverId, connection.recordVersion, targetKey)) loading.value = false;
  }
}
function resetContext() {
  epoch += 1;
  sequence += 1;
  latestRequestId = "";
  items.value = [];
  revision.value = null;
  error.value = "";
  savingWorkshopId.value = null;
  hostLibraryOpen.value = false;
  hostLibraryItems.value = [];
  hostLibraryLoading.value = false;
  hostLibraryError.value = "";
  applyTask.value = null;
  applyTaskError.value = "";
  serverApplyPreview.value = null;
  applyBusy.value = false;
  createProtectionBackup.value = false;
  removeTarget.value = null;
  removeBusy.value = false;
  removeError.value = "";
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
  finalizedPrepareTasks.clear();
  if (applyPollTimer) clearTimeout(applyPollTimer);
  applyPollTimer = undefined;
}
async function openHostLibrary() {
  hostLibraryOpen.value = true;
  await loadHostLibrary();
}

async function loadHostLibrary() {
  const connection = server.value, instanceTarget = target.value;
  if (!connection?.hostId || !instanceTarget || hostLibraryLoading.value) return;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  hostLibraryLoading.value = true;
  hostLibraryError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await listHostMods({ schemaVersion: 1, requestId,
      target: { serverId: connection.serverId, hostId: connection.hostId }, expectedRecordVersion: recordVersion });
    if (requestEpoch !== epoch || target.value === null
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== recordVersion) return;
    if (response.requestId !== requestId || response.recordVersion !== recordVersion
        || response.target.serverId !== connection.serverId || response.target.hostId !== connection.hostId) {
      throw new Error("主机 MOD 库响应身份或版本不匹配");
    }
    hostLibraryItems.value = response.items;
  } catch (cause) {
    if (requestEpoch === epoch) hostLibraryError.value = describeError(cause).message;
  } finally {
    if (requestEpoch === epoch) hostLibraryLoading.value = false;
  }
}

async function addHostLibraryItem(item: HostModItem) {
  const connection = server.value, instanceTarget = target.value, expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || savingWorkshopId.value !== null) return;
  const existing = items.value.find(current => current.workshopId === item.workshopId && current.desiredEnabled !== null);
  if (existing?.artifactDigest === item.contentDigest) return;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const requestEpoch = epoch;
  const mods = items.value.filter(current => current.desiredEnabled !== null).map(current => ({
    workshopId: current.workshopId,
    enabled: current.desiredEnabled === true,
    configurationOptions: current.desiredConfiguration ?? {},
    artifactDigest: current.workshopId === item.workshopId ? item.contentDigest : current.artifactDigest,
  }));
  if (!existing) mods.push({ workshopId: item.workshopId, enabled: true, configurationOptions: {}, artifactDigest: item.contentDigest });
  savingWorkshopId.value = item.workshopId;
  taskError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const operationId = crypto.randomUUID();
    const response = await saveInstanceMods({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: recordVersion, expectedRevision, operationId, mods });
    if (requestEpoch !== epoch || target.value === null
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== recordVersion) return;
    if (response.requestId !== requestId || response.operationId !== operationId
        || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
        || response.target.instanceId !== instanceTarget.instanceId || response.verifiedRevision <= expectedRevision) {
      throw new Error("MOD 保存响应身份或版本不匹配");
    }
    hostLibraryOpen.value = false;
    await refresh();
  } catch (cause) {
    if (requestEpoch === epoch && target.value
        && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey) {
      hostLibraryError.value = describeError(cause).message;
    }
  } finally {
    if (requestEpoch === epoch) savingWorkshopId.value = null;
  }
}

async function setModEnabled(item: InstanceModSummary) {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || item.desiredEnabled === null
      || savingWorkshopId.value !== null || workspace.preview.value) return;
  const savedEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const requestId = crypto.randomUUID();
  const operationId = crypto.randomUUID();
  savingWorkshopId.value = item.workshopId;
  taskError.value = "";
  try {
    const mods = items.value.filter(current => current.desiredEnabled !== null).map(current => ({
      workshopId: current.workshopId,
      enabled: current.workshopId === item.workshopId ? !item.desiredEnabled : current.desiredEnabled === true,
      configurationOptions: current.desiredConfiguration ?? {}, artifactDigest: current.artifactDigest,
    }));
    const response = await queueSsh(() => saveInstanceMods({ schemaVersion: 1, requestId,
      target: instanceTarget, expectedRecordVersion: recordVersion, expectedRevision,
      operationId, mods }));
    if (savedEpoch !== epoch || !target.value
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.operationId !== operationId
        || response.verifiedRevision <= expectedRevision || response.target.serverId !== instanceTarget.serverId
        || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("MOD 状态保存响应身份或版本无效");
    }
    await refresh();
  } catch (cause) {
    if (savedEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) taskError.value = describeError(cause).message;
  } finally {
    if (savedEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) savingWorkshopId.value = null;
  }
}

async function confirmRemoveMod() {
  const item = removeTarget.value;
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!item || !connection || !instanceTarget || expectedRevision === null || savingWorkshopId.value !== null
      || workspace.preview.value) return;
  const savedEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const requestId = crypto.randomUUID();
  const operationId = crypto.randomUUID();
  removeBusy.value = true;
  removeError.value = "";
  savingWorkshopId.value = item.workshopId;
  try {
    const mods = items.value.filter(current => current.desiredEnabled !== null
      && current.workshopId !== item.workshopId).map(current => ({
      workshopId: current.workshopId, enabled: current.desiredEnabled === true,
      configurationOptions: current.desiredConfiguration ?? {}, artifactDigest: current.artifactDigest,
    }));
    const response = await queueSsh(() => saveInstanceMods({ schemaVersion: 1, requestId,
      target: instanceTarget, expectedRecordVersion: recordVersion, expectedRevision,
      operationId, mods }));
    if (savedEpoch !== epoch || !target.value
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.operationId !== operationId
        || response.verifiedRevision <= expectedRevision || response.target.serverId !== instanceTarget.serverId
        || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("MOD 移除响应身份或版本无效");
    }
    removeTarget.value = null;
    await refresh();
  } catch (cause) {
    if (savedEpoch === epoch) removeError.value = describeError(cause).message;
  } finally {
    if (savedEpoch === epoch) {
      removeBusy.value = false;
      savingWorkshopId.value = null;
    }
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

async function openApplyPreview(autoApply = false) {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || workspace.preview.value
      || loading.value || applyPreviewLoading.value
      || !!applyTask.value && ['queued', 'running'].includes(applyTask.value.status)) return;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const requestIds = { overview: crypto.randomUUID(), apply: crypto.randomUUID() };
  applyPreviewOpen.value = true;
  applyPreviewLoading.value = true;
  applyPreviewError.value = "";
  applyPreview.value = null;
  serverApplyPreview.value = null;
  try {
    const [overview, serverPreview] = await Promise.all([
      queueSsh(() => getInstanceOverview(instanceTarget, requestIds.overview, recordVersion)),
      queueSsh(() => previewInstanceMods({ schemaVersion: 1, requestId: requestIds.apply,
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
    if (serverPreview.schemaVersion !== 1 || serverPreview.requestId !== requestIds.apply
      || serverPreview.target.serverId !== instanceTarget.serverId || serverPreview.target.hostId !== instanceTarget.hostId
      || serverPreview.target.instanceId !== instanceTarget.instanceId || serverPreview.configRevision !== expectedRevision
      || !/^[0-9a-f]{64}$/.test(serverPreview.desiredDigest)) {
      throw new Error("服务端 MOD 发布预览目标或摘要无效");
    }
    const freshItems = items.value;
    const preview = buildModApplyPreview({
      mods: freshItems,
      runtimeStatus: overview.data.runtime?.status ?? overview.data.identity.runtimeStatus,
      cavesEnabled: overview.data.manifestSummary.cavesEnabled,
      worldDesiredVsDeployed: overview.data.desiredVsDeployed,
    });
    preview.blockers = mergeModApplyBlockers(preview, serverPreview.blockers);
    preview.canApply = preview.canApply && serverPreview.allowed && serverPreview.blockers.length === 0;
    applyPreview.value = preview;
    serverApplyPreview.value = serverPreview;
    applyPreviewRevision.value = expectedRevision;
    applyPreviewRuntime.value = overview.data.runtime?.status ?? overview.data.identity.runtimeStatus;
    if (autoApply) {
      if (applyPreview.value.canApply) {
        applyPreviewOpen.value = false;
        await applyMods();
      }
    }
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

function displayVersion(value: string | null) {
  if (!value) return "版本未知";
  const normalized = value.trim();
  return /^v/i.test(normalized) ? normalized : `v${normalized}`;
}
const applyPreviewStale = computed(() => applyPreviewRevision.value !== null && revision.value !== applyPreviewRevision.value);

function applyTaskBelongsHere(task: TaskSnapshot, targetKey: string) {
  return task.operation === "modApply" && "instanceId" in task.target
    && `${task.target.serverId}:${task.target.hostId}:${task.target.instanceId}` === targetKey;
}

function acceptApplyTask(task: TaskSnapshot, targetKey: string) {
  if (task.schemaVersion !== 1 || !applyTaskBelongsHere(task, targetKey)) {
    throw new Error("MOD 应用任务目标无效");
  }
  applyTask.value = task;
  taskStore.receive(task);
  if (["queued", "running"].includes(task.status)) scheduleApplyPoll(targetKey, task.operationId);
  else {
    if (applyPollTimer) clearTimeout(applyPollTimer);
    applyPollTimer = undefined;
    if (task.status === "succeeded") {
      applyPreviewOpen.value = false;
      void refresh();
    }
  }
}

async function refreshApplyTasks() {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget || workspace.preview.value) return;
  const requestEpoch = epoch;
  const requestId = crypto.randomUUID();
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  try {
    const response = await queueSsh(() => listInstanceModApplyTasks({ schemaVersion: 1,
      requestId, target: instanceTarget, expectedRecordVersion: connection.recordVersion }));
    if (requestEpoch !== epoch || !target.value
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
        || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
        || response.target.instanceId !== instanceTarget.instanceId) throw new Error("MOD 应用任务列表身份无效");
    for (const task of response.items) {
      if (!applyTaskBelongsHere(task, targetKey)) throw new Error("MOD 应用任务包含其他目标");
      taskStore.receive(task);
    }
    const current = response.items.find(task => ["queued", "running"].includes(task.status)) ?? response.items[0] ?? null;
    if (current) acceptApplyTask(current, targetKey);
  } catch (cause) {
    if (requestEpoch === epoch) applyTaskError.value = describeError(cause).message;
  }
}

function scheduleApplyPoll(targetKey: string, operationId: string) {
  if (applyPollTimer) clearTimeout(applyPollTimer);
  const requestEpoch = epoch;
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget) return;
  const recordVersion = connection.recordVersion;
  applyPollTimer = setTimeout(async () => {
    try {
      const requestId = crypto.randomUUID();
      const response = await queueSsh(() => getInstanceModApplyTask({ schemaVersion: 1,
        requestId, target: instanceTarget, expectedRecordVersion: recordVersion, operationId }));
      if (requestEpoch !== epoch || !target.value
        || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
        || server.value?.recordVersion !== recordVersion) return;
      if (response.schemaVersion !== 1 || response.requestId !== requestId
          || response.task.operationId !== operationId || !applyTaskBelongsHere(response.task, targetKey)) {
        throw new Error("MOD 应用任务响应目标无效");
      }
      acceptApplyTask(response.task, targetKey);
    } catch (cause) {
      if (requestEpoch === epoch) applyTaskError.value = describeError(cause).message;
      if (requestEpoch === epoch) scheduleApplyPoll(targetKey, operationId);
    }
  }, 2500);
}

function applyStageLabel(task: TaskSnapshot) {
  const labels: Record<string, string> = { queued: "等待维护锁", preflight: "复核期望与保护条件",
    prepare: "生成固定 MOD Release", stop: "停止 Master/Caves", backup: "创建冷保护备份",
    publish: "发布 MOD 与配置", verify: "重建分片并验证", rollback: "恢复原部署", finalize: "完成" };
  return labels[task.stage] ?? "应用 MOD";
}

async function applyMods() {
  const connection = server.value;
  const instanceTarget = target.value;
  const preview = serverApplyPreview.value;
  const expectedRevision = applyPreviewRevision.value;
  if (!connection || !instanceTarget || !preview || expectedRevision === null
      || applyBusy.value || !applyPreview.value?.canApply || workspace.preview.value) return;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const operationId = crypto.randomUUID();
  const requestId = crypto.randomUUID();
  applyBusy.value = true;
  applyTaskError.value = "";
  try {
    const response = await queueSsh(() => applyInstanceMods({ schemaVersion: 1,
      requestId, target: instanceTarget, expectedRecordVersion: recordVersion,
      expectedRevision, desiredDigest: preview.desiredDigest, operationId,
      createProtectionBackup: createProtectionBackup.value }));
    if (requestEpoch !== epoch || !target.value
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== recordVersion) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
        || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
        || response.target.instanceId !== instanceTarget.instanceId || response.task.operationId !== operationId) {
      throw new Error("MOD 应用任务接纳响应身份无效");
    }
    acceptApplyTask(response.task, targetKey);
  } catch (cause) {
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) applyTaskError.value = describeError(cause).message;
  } finally {
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && server.value?.recordVersion === recordVersion) applyBusy.value = false;
  }
}

async function refreshAll() {
  await refresh();
  await refreshApplyTasks();
}

function runtimeLabel(status: string | null) {
  return status === "running" ? "运行中" : status === "stopped" ? "已停止"
    : status === "degraded" ? "部分异常" : status === "starting" ? "启动中" : "未知";
}
watch(() => `${server.value?.serverId ?? ""}:${server.value?.recordVersion ?? ""}:${target.value?.hostId ?? ""}:${target.value?.instanceId ?? ""}:${workspace.preview.value}`, () => {
  resetContext();
  void refresh();
  void refreshApplyTasks();
}, { immediate: true });
onBeforeUnmount(() => {
  if (applyPollTimer) clearTimeout(applyPollTimer);
});
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="MOD">
      <template #actions>
        <button class="button" :disabled="workspace.preview.value || !target || loading || applyPreviewLoading || savingWorkshopId !== null
          || !!applyTask && ['queued', 'running'].includes(applyTask.status)
          "
          @click="openApplyPreview(true)"><Eye :size="15"/>应用更改</button>
        <button class="button" :disabled="workspace.preview.value || !target || loading" @click="openHostLibrary"><Library :size="15"/>主机库</button>
        <button class="button" :disabled="loading || !target" @click="refreshAll">
          <RefreshCw :size="16" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="taskError" class="error-strip">{{ taskError }}</div>
    <div v-if="applyTaskError" class="error-strip">{{ applyTaskError }}</div>
    <section v-if="applyTask" class="mod-task-state" aria-live="polite">
      <div>
        <strong>{{ applyTask.status === 'succeeded' ? 'MOD 应用完成' : applyTask.status === 'failed' ? 'MOD 应用失败' : applyTask.status === 'interrupted' ? 'MOD 应用中断' : '正在应用 MOD' }}</strong>
        <span>{{ applyStageLabel(applyTask) }}</span>
        <span v-if="applyTask.status === 'failed'" class="task-failure-detail">{{ applyTask.error?.message ?? '任务错误详情暂不可用' }} <span class="mono">{{ applyTask.error?.code }}</span></span>
      </div>
      <span class="muted-text mono">{{ applyTask.progress.kind === 'determinate' ? `${applyTask.progress.completed}/${applyTask.progress.total}` : '处理中' }}</span>
    </section>
    <section v-if="revision !== null" class="summary-line">
      <div><span class="metric-number">{{ enabledCount }}</span><span>个已启用</span></div>
      <span class="summary-divider" />
      <div><span class="metric-number warning-number">{{ pendingCount }}</span><span>个待应用</span></div>
      <span class="summary-divider" />
      <div class="muted-text">配置版本 {{ revision }}</div>
    </section>
    <section v-if="revision !== null" class="mod-apply-options">
      <label class="mod-backup-choice">
        <input v-model="createProtectionBackup" type="checkbox"
          :disabled="workspace.preview.value || !!applyTask && ['queued', 'running'].includes(applyTask.status)"/>
        应用前创建冷备份
      </label>
      <span v-if="!createProtectionBackup" class="muted-text">不创建时，应用失败无法自动恢复原部署</span>
    </section>

    <div v-if="loading && items.length === 0" class="empty-state"><h2>正在读取 MOD</h2></div>
    <div v-else-if="!loading && items.length === 0" class="empty-state"><h2>尚未发现 MOD</h2></div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>Workshop ID</th><th>期望状态</th><th>实际部署</th><th>配置解析</th><th>版本摘要</th><th class="table-action">操作</th></tr></thead>
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
            <td class="table-action">
              <button v-if="item.desiredEnabled !== null" class="icon-button" type="button"
                :aria-label="item.desiredEnabled ? `禁用 ${item.schema.metadata.name ?? item.workshopId}` : `启用 ${item.schema.metadata.name ?? item.workshopId}`"
                :title="item.desiredEnabled ? '禁用 MOD' : '启用 MOD'"
                :disabled="workspace.preview.value || savingWorkshopId !== null || !!applyTask && ['queued', 'running'].includes(applyTask.status)"
                @click="setModEnabled(item)"><Power :size="15"/></button>
              <button v-if="item.desiredEnabled !== null" class="icon-button" type="button"
                :aria-label="`移除 ${item.schema.metadata.name ?? item.workshopId}`" title="移除 MOD"
                :disabled="workspace.preview.value || savingWorkshopId !== null || !!applyTask && ['queued', 'running'].includes(applyTask.status)"
                @click="removeTarget = item; removeError = ''"><Trash2 :size="15"/></button>
              <button v-if="canEditConfiguration(item)" class="icon-button" type="button"
                :aria-label="`编辑 ${item.schema.metadata.name ?? item.workshopId} 配置`"
                title="编辑配置" :disabled="workspace.preview.value || configSaving || loading"
                @click="openConfigurationEditor(item)"><Pencil :size="15"/></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <AppDialog v-model:open="hostLibraryOpen" title="从主机 MOD 库添加">
      <div v-if="hostLibraryError" class="error-strip">{{ hostLibraryError }}</div>
      <div v-if="hostLibraryLoading" class="muted-text" role="status">读取主机 MOD 库…</div>
      <div v-else-if="!hostLibraryItems.length" class="empty-state"><h2>主机 MOD 库为空</h2><p class="muted-text">请先在主机级“MOD 库”添加内容。</p></div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>Workshop ID</th><th>内容摘要</th><th>大小</th><th class="table-action">操作</th></tr></thead>
          <tbody>
            <tr v-for="item in hostLibraryItems" :key="`${item.workshopId}:${item.contentDigest}`">
              <td class="mono">{{ item.workshopId }}</td>
              <td class="mono">{{ item.contentDigest.slice(0, 12) }}</td>
              <td>{{ item.sizeBytes === null ? "未知" : formatBytes(item.sizeBytes) }}</td>
              <td class="table-action">
                <button class="text-button"
                  :disabled="savingWorkshopId === item.workshopId || items.some(current => current.workshopId === item.workshopId && current.desiredEnabled !== null && current.artifactDigest === item.contentDigest)"
                  @click="addHostLibraryItem(item)">
                  {{ savingWorkshopId === item.workshopId ? "保存中…" : items.some(current => current.workshopId === item.workshopId && current.desiredEnabled !== null && current.artifactDigest === item.contentDigest) ? "已在实例" : items.some(current => current.workshopId === item.workshopId && current.desiredEnabled !== null) ? "更新到此版本" : "加入实例" }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer><button class="button" type="button" @click="hostLibraryOpen = false">关闭</button></template>
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
          <h3>应用前备份</h3>
          <label class="mod-backup-choice">
            <input v-model="createProtectionBackup" type="checkbox"/>
            应用前创建冷备份
          </label>
          <p v-if="!createProtectionBackup" class="muted-text">不创建时，应用失败无法自动恢复原部署</p>
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
          @click="openApplyPreview(false)">重新核对</button>
        <button class="button primary" type="button" :disabled="applyPreviewLoading || applyBusy || !applyPreview?.canApply
          || !serverApplyPreview || applyPreviewStale || !!applyTask && ['queued', 'running'].includes(applyTask.status)"
          @click="applyMods">{{ applyBusy ? '正在提交…' : '应用 MOD' }}</button>
      </template>
    </AppDialog>

    <AppDialog :open="!!removeTarget" title="移除 MOD" description="移除后，世界中由该 MOD 添加的物品或角色数据可能无法读取。" @update:open="value => { if (!value && !removeBusy) { removeTarget = null; removeError = ''; } }">
      <div v-if="removeTarget" class="form-stack">
        <div class="form-facts">
          <div><span>MOD</span><strong>{{ removeTarget.schema.metadata.name ?? removeTarget.workshopId }}</strong></div>
          <div><span>Workshop ID</span><strong class="mono">{{ removeTarget.workshopId }}</strong></div>
        </div>
        <div v-if="removeError" class="error-strip" role="alert">{{ removeError }}</div>
      </div>
      <template #footer>
        <button class="button" type="button" :disabled="removeBusy" @click="removeTarget = null">取消</button>
        <button class="button primary" type="button" :disabled="removeBusy || workspace.preview.value" @click="confirmRemoveMod">
          {{ removeBusy ? '移除中…' : '移除 MOD' }}
        </button>
      </template>
    </AppDialog>
  </div>
</template>
