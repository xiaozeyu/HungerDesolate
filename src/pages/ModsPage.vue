<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import { ArrowUpCircle, Check, CircleHelp, Eye, Library, Package, Pencil, Plus, Power, RefreshCw, RotateCcw, Trash2 } from "@lucide/vue";
import { NButton, NCheckbox, NDataTable, NInput, NInputNumber, NTooltip, type DataTableColumns } from "naive-ui";
import AppDialog from "../components/AppDialog.vue";
import OptionSelect, { type OptionSelectItem } from "../components/OptionSelect.vue";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import IconButton from "../components/IconButton.vue";
import LoadingState from "../components/LoadingState.vue";
import { applyInstanceMods, listInstanceModApplyTasks,
  cancelInstanceModApply, listInstanceMods, previewInstanceMods, saveInstanceMods } from "../api/mods";
import { getInstanceOverview } from "../api/instances";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError } from "../lib/commandError";
import type { InstanceModSummary, ModSchemaStatus } from "../types/mods";
import { listHostMods, type HostModItem } from "../api/hostMods";
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
const hostLibrarySelected = ref<Set<string>>(new Set());
const hostLibraryKey = (item: HostModItem) => `${item.workshopId}:${item.contentDigest}`;
const currentHostMod = (item: HostModItem) => items.value.find(current => current.workshopId === item.workshopId && current.desiredEnabled !== null);
const isHostLibraryCurrent = (item: HostModItem) => currentHostMod(item)?.artifactDigest === item.contentDigest;
const hostLibraryCandidates = computed(() => {
  const latest = new Map<string, HostModItem>();
  for (const item of hostLibraryItems.value) {
    if (isHostLibraryCurrent(item)) continue;
    const current = latest.get(item.workshopId);
    if (!current || (Date.parse(item.preparedAt ?? "") || 0) > (Date.parse(current.preparedAt ?? "") || 0)) latest.set(item.workshopId, item);
  }
  return [...latest.values()];
});
const selectedHostLibraryItems = computed(() => hostLibraryItems.value.filter(item => hostLibrarySelected.value.has(hostLibraryKey(item)) && !isHostLibraryCurrent(item)));
const allHostLibrarySelected = computed(() => hostLibraryCandidates.value.length > 0
  && hostLibraryCandidates.value.every(item => hostLibrarySelected.value.has(hostLibraryKey(item))));
const hostModMetadata = computed(() => {
  const byWorkshop = new Map<string, HostModItem>();
  for (const item of hostLibraryItems.value) {
    const current = byWorkshop.get(item.workshopId);
    if (!current || (Date.parse(item.preparedAt ?? "") || 0) > (Date.parse(current.preparedAt ?? "") || 0)) byWorkshop.set(item.workshopId, item);
  }
  return byWorkshop;
});
const hostMetadataFor = (item: InstanceModSummary) => {
  const versions = hostLibraryItems.value.filter(entry => entry.workshopId === item.workshopId);
  return versions.find(entry => entry.contentDigest === item.artifactDigest) ?? hostModMetadata.value.get(item.workshopId);
};
const instanceModName = (item: InstanceModSummary) => item.schema.metadata.name ?? hostMetadataFor(item)?.name ?? item.workshopId;
const modNameFor = (workshopId: string) => items.value.find(item => item.workshopId === workshopId)?.schema.metadata.name
  ?? hostModMetadata.value.get(workshopId)?.name ?? workshopId;
const instanceModVersion = (item: InstanceModSummary) => item.schema.metadata.version ?? hostMetadataFor(item)?.version ?? null;
const schemaDiagnostic = (item: InstanceModSummary) => item.schema.diagnostics[0]?.message ?? "";
const versionedPreview = (url: string | null, digest: string | null) => {
  if (!url || !digest) return url ?? undefined;
  return `${url}${url.includes("?") ? "&" : "?"}v=${digest.slice(0, 12)}`;
};
const savingWorkshopId = ref<string | null>(null);
const applyTask = computed<TaskSnapshot | null>(() => {
  const selected = target.value;
  if (!selected) return null;
  const key = `${selected.serverId}:${selected.hostId}:${selected.instanceId}`;
  const tasks = taskStore.tasks.value.filter(task => applyTaskBelongsHere(task, key))
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
  return tasks.find(task => ["queued", "running"].includes(task.status)) ?? tasks[0] ?? null;
});
const applyTaskError = ref("");
const applyTaskTone = computed<"running"|"success"|"danger"|"warning">(() => {
  const status = applyTask.value?.status;
  return status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "interrupted" ? "warning" : "running";
});
const applyTaskStatusLabel = computed(() => {
  const status = applyTask.value?.status;
  return status === "succeeded" ? "完成" : status === "failed" ? "失败" : status === "interrupted" ? "待核实" : status === "cancelled" ? "已取消" : "进行中";
});
const cancelBusy = ref(false);
const cancelMessage = ref("");
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
let epoch = 0;
let sequence = 0;
let latestRequestId = "";
const finalizedPrepareTasks = new Set<string>();

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
  hostLibrarySelected.value = new Set();
  hostLibraryLoading.value = false;
  hostLibraryError.value = "";
  applyTaskError.value = "";
  cancelMessage.value = "";
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
  finalizedPrepareTasks.clear();
}
async function openHostLibrary() {
  hostLibrarySelected.value = new Set();
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

function toggleHostLibraryItem(item: HostModItem) {
  if (isHostLibraryCurrent(item)) return;
  const next = new Set(hostLibrarySelected.value);
  const key = hostLibraryKey(item);
  if (next.has(key)) next.delete(key);
  else {
    for (const current of next) {
      if (current.startsWith(`${item.workshopId}:`)) next.delete(current);
    }
    next.add(key);
  }
  hostLibrarySelected.value = next;
}

function toggleAllHostLibrary() {
  if (allHostLibrarySelected.value) {
    hostLibrarySelected.value = new Set();
    return;
  }
  hostLibrarySelected.value = new Set(hostLibraryCandidates.value.map(hostLibraryKey));
}

async function addSelectedHostLibrary() {
  await saveHostLibraryItems(selectedHostLibraryItems.value);
}

async function saveHostLibraryItems(selectedItems: HostModItem[]) {
  const connection = server.value, instanceTarget = target.value, expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || savingWorkshopId.value !== null || !selectedItems.length) return;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const recordVersion = connection.recordVersion;
  const requestEpoch = epoch;
  const mods = items.value.filter(current => current.desiredEnabled !== null).map(current => ({
    workshopId: current.workshopId,
    enabled: current.desiredEnabled === true,
    configurationOptions: current.desiredConfiguration ?? {},
    artifactDigest: current.artifactDigest,
  }));
  const positions = new Map(mods.map((mod, index) => [mod.workshopId, index]));
  for (const item of selectedItems) {
    const position = positions.get(item.workshopId);
    if (position === undefined) {
      positions.set(item.workshopId, mods.length);
      mods.push({ workshopId: item.workshopId, enabled: true, configurationOptions: {}, artifactDigest: item.contentDigest });
    } else {
      mods[position] = { ...mods[position]!, artifactDigest: item.contentDigest };
    }
  }
  savingWorkshopId.value = selectedItems.length === 1 ? selectedItems[0]!.workshopId : "__batch__";
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
    hostLibrarySelected.value = new Set();
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

async function addHostLibraryItem(item: HostModItem) {
  await saveHostLibraryItems([item]);
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

function configOptionsFor(group: ModFieldGroup): OptionSelectItem[] {
  const options: OptionSelectItem[] = [];
  if (configOptionSelection.value[group.name] === "__unmapped__") {
    options.push({ label: "当前值不在此版本选项中", value: "__unmapped__", disabled: true });
  }
  options.push({ label: "使用 MOD 默认值", value: "" });
  options.push(...group.options.map((option, index) => ({ label: option.label, value: String(index) })));
  return options;
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

function setScalarField(group: ModFieldGroup, value: unknown) {
  configDraft.value = { ...configDraft.value, [group.name]: value };
  markConfigurationChanged(group.name);
}
function scalarNumberValue(group: ModFieldGroup): number | null {
  const value = scalarFieldValue(group);
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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
    if (revision.value !== expectedRevision) throw new Error("MOD 配置已变化，请刷新后重新生成预览");
    if (overview.schemaVersion !== 1 || overview.requestId !== requestIds.overview
      || overview.recordVersion !== recordVersion || overview.configRevision !== expectedRevision
      || overview.target.serverId !== instanceTarget.serverId || overview.target.hostId !== instanceTarget.hostId
      || overview.target.instanceId !== instanceTarget.instanceId || !overview.data.manifestSummary
      || overview.data.manifestSummary.configRevision !== expectedRevision) {
      throw new Error("运行概览与 MOD 配置不一致，请刷新后重试");
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
  taskStore.receive(task);
}

async function cancelApply() {
  const instanceTarget = target.value, connection = server.value, currentTask = applyTask.value;
  if (!instanceTarget || !connection || !currentTask || cancelBusy.value || !["queued", "running"].includes(currentTask.status)) return;
  cancelBusy.value = true;
  applyTaskError.value = "";
  cancelMessage.value = "";
  try {
    const response = await cancelInstanceModApply({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: instanceTarget, expectedRecordVersion: connection.recordVersion, operationId: currentTask.operationId });
    taskStore.receive(response.task);
    cancelMessage.value = "取消请求已提交，等待任务安全结束。";
  } catch (cause) {
    applyTaskError.value = describeError(cause).message;
  } finally {
    cancelBusy.value = false;
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
    if (current) taskStore.receive(current);
  } catch (cause) {
    if (requestEpoch === epoch) applyTaskError.value = describeError(cause).message;
  }
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
    if (response.schemaVersion !== 1 || response.requestId !== requestId
        || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
        || response.target.instanceId !== instanceTarget.instanceId || response.task.operationId !== operationId) {
      throw new Error("MOD 应用任务接纳响应身份无效");
    }
    taskStore.receive(response.task);
    if (requestEpoch !== epoch || !target.value
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey
      || server.value?.recordVersion !== recordVersion) return;
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

watch(applyTask, (next, previous) => {
  if (next?.status === "succeeded" && previous?.status !== "succeeded") {
    applyPreviewOpen.value = false;
    void refresh();
  }
}, { flush: "sync" });

async function refreshAll() {
  await refresh();
  await refreshApplyTasks();
  await loadHostLibrary();
}

watch(() => `${server.value?.serverId ?? ""}:${server.value?.recordVersion ?? ""}:${target.value?.hostId ?? ""}:${target.value?.instanceId ?? ""}:${workspace.preview.value}`, () => {
  resetContext();
  void refresh();
  void refreshApplyTasks();
  void loadHostLibrary();
}, { immediate: true });

const modColumns = computed<DataTableColumns<InstanceModSummary>>(() => {
  void workspace.preview.value;
  void savingWorkshopId.value;
  void configSaving.value;
  void loading.value;
  void applyTask.value;
  void hostLibraryItems.value;
  return [
  {
    title: "MOD",
    key: "name",
    render: item => {
      const metadata = hostMetadataFor(item);
      return h("div", { class: "instance-mod-main" }, [
        metadata?.previewUrl
          ? h("img", { class: "instance-mod-thumb", src: versionedPreview(metadata.previewUrl, metadata.contentDigest), alt: instanceModName(item), width: 40, height: 40, loading: "lazy" })
          : h("span", { class: "instance-mod-thumb instance-mod-placeholder" }, [h(Package, { size: 17 })]),
        h("span", { class: "instance-mod-copy" }, [
          h("strong", instanceModName(item)),
          h("small", { class: "table-sub mono" }, `Workshop · ${item.workshopId}`),
        ]),
      ]);
    },
  },
  {
    title: "启用状态",
    key: "desiredEnabled",
    render: item => h(StatusLabel, {
      tone: item.desiredEnabled === null ? "warning" : item.desiredEnabled ? "success" : "muted",
      label: item.desiredEnabled === null ? "已移出" : item.desiredEnabled ? "启用" : "禁用",
    }),
  },
  {
    title: "实际部署",
    key: "deployed",
    render: item => h("div", null, [
      h(StatusLabel, { tone: modNeedsApply(item) ? "warning" : "success", label: modNeedsApply(item) ? "待应用" : "一致" }),
      h("small", { class: "table-sub" }, item.deployedVersion ? displayVersion(item.deployedVersion) : "运行版本未知"),
    ]),
  },
  {
    title: "配置解析",
    key: "schema",
    render: item => h(StatusLabel, { tone: statusTone(item.schema.status), label: statusLabel(item.schema.status) }),
  },
  {
    title: "版本",
    key: "version",
    render: item => h("span", { class: "mono", title: schemaDiagnostic(item) }, displayVersion(instanceModVersion(item))),
  },
  {
    title: "操作",
    key: "actions",
    align: "center",
    render: item => {
      const applying = !!applyTask.value && ["queued", "running"].includes(applyTask.value.status);
      const actions = [];
      if (item.desiredEnabled !== null) {
        actions.push(h(IconButton, {
          label: item.desiredEnabled ? "禁用 MOD" : "启用 MOD",
          disabled: workspace.preview.value || savingWorkshopId.value !== null || applying,
          onClick: () => void setModEnabled(item),
        }, () => h(Power, { size: 15 })));
        actions.push(h(IconButton, {
          label: "移除 MOD",
          disabled: workspace.preview.value || savingWorkshopId.value !== null || applying,
          tone: "danger",
          onClick: () => { removeTarget.value = item; removeError.value = ""; },
        }, () => h(Trash2, { size: 15 })));
      }
      if (canEditConfiguration(item)) {
        actions.push(h(IconButton, {
          label: "编辑配置",
          disabled: workspace.preview.value || configSaving.value || loading.value,
          onClick: () => void openConfigurationEditor(item),
        }, () => h(Pencil, { size: 15 })));
      }
      return h("div", { class: "row-actions" }, actions);
    },
  },
  ];
});
const modRowKey = (item: InstanceModSummary) => item.workshopId;

const hostLibraryColumns = computed<DataTableColumns<HostModItem>>(() => {
  void hostLibrarySelected.value;
  void savingWorkshopId.value;
  void items.value;
  void hostLibraryItems.value;
  return [
  {
    title: "",
    key: "select",
    width: 34,
    render: item => h("input", {
      type: "checkbox",
      checked: hostLibrarySelected.value.has(hostLibraryKey(item)),
      disabled: isHostLibraryCurrent(item) || savingWorkshopId.value !== null,
      onChange: () => toggleHostLibraryItem(item),
    }),
  },
  {
    title: "图片",
    key: "preview",
    width: 56,
    render: item => item.previewUrl
      ? h("img", { class: "host-library-thumb", src: item.previewUrl, alt: item.name ?? item.workshopId, width: 40, height: 40, loading: "lazy" })
      : h("span", { class: "host-library-thumb host-library-placeholder" }, [h(Package, { size: 16 })]),
  },
  { title: "名称", key: "name", width: 250, ellipsis: { tooltip: true }, render: item => h("strong", item.name ?? "—") },
  { title: "ID", key: "workshopId", width: 140, ellipsis: { tooltip: true }, render: item => h("span", { class: "mono" }, item.workshopId) },
  { title: "作者", key: "author", width: 150, ellipsis: { tooltip: true }, render: item => item.author ?? "未知" },
  { title: "版本", key: "version", width: 80, ellipsis: { tooltip: true }, render: item => h("span", { class: "mono" }, item.version ?? "—") },
  {
    title: "",
    key: "actions",
    width: 56,
    align: "center",
    render: item => {
      const current = isHostLibraryCurrent(item);
      const updating = !!currentHostMod(item);
      const Icon = current ? Check : updating ? ArrowUpCircle : Plus;
      return h(IconButton, {
        label: current ? "已在实例" : updating ? "更新到此版本" : savingWorkshopId.value === item.workshopId ? "保存中…" : "加入实例",
        disabled: savingWorkshopId.value !== null || current,
        onClick: () => void addHostLibraryItem(item),
      }, () => h(Icon, { size: 15 }));
    },
  },
  ];
});
const hostLibraryRowKey = (item: HostModItem) => hostLibraryKey(item);
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="MOD">
      <template #actions>
        <NButton :disabled="workspace.preview.value || !target || loading || applyPreviewLoading || savingWorkshopId !== null
          || !!applyTask && ['queued', 'running'].includes(applyTask.status)
          "
          @click="openApplyPreview"><template #icon><Eye :size="15"/></template>应用更改</NButton>
        <NButton :disabled="workspace.preview.value || !target || loading" @click="openHostLibrary"><template #icon><Library :size="15"/></template>主机库</NButton>
        <NButton :disabled="loading || !target" @click="refreshAll">
          <template #icon><RefreshCw :size="16" :class="{ spinning: loading }"/></template>
          刷新
        </NButton>
      </template>
    </PageHeader>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="taskError" class="error-strip">{{ taskError }}</div>
    <div v-if="applyTaskError" class="error-strip">{{ applyTaskError }}</div>

    <template v-if="loading || items.length">
      <div class="table-wrap">
        <NDataTable :columns="modColumns" :data="items" :bordered="false" :single-line="true" size="medium" :row-key="modRowKey" :loading="loading"/>
      </div>
    </template>
    <div v-else class="empty-state"><h2>尚未发现 MOD</h2></div>

<FloatingTask v-if="applyTask" label="MOD 应用">
    <TaskPanel v-if="applyTask" title="应用 MOD" :status-label="applyTaskStatusLabel" :stage-label="applyStageLabel(applyTask)"
      :tone="applyTaskTone" :queue-position="applyTask.queuePosition" :error="applyTask.error"
      :progress="applyTask.progress.kind === 'determinate' ? `${applyTask.progress.completed}/${applyTask.progress.total}` : '处理中'"
      :cancellable="!!applyTask.cancelAllowed && ['queued', 'running'].includes(applyTask.status)" :busy="cancelBusy" @cancel="cancelApply">
      <span v-if="cancelMessage" class="muted-text">{{ cancelMessage }}</span>
    </TaskPanel>
</FloatingTask>
    <AppDialog v-model:open="hostLibraryOpen" title="从主机 MOD 库添加" size="lg">
      <div v-if="hostLibraryError" class="error-strip">{{ hostLibraryError }}</div>
      <div v-if="hostLibraryLoading" class="muted-text" role="status">读取主机 MOD 库…</div>
      <template v-else-if="hostLibraryItems.length">
        <div class="host-library-toolbar">
          <span class="muted-text">已选 {{ selectedHostLibraryItems.length }}</span>
          <NButton text size="small" :disabled="!hostLibraryCandidates.length || savingWorkshopId !== null" @click="toggleAllHostLibrary">{{ allHostLibrarySelected ? "取消全选" : "全选未加入" }}</NButton>
        </div>
        <div class="table-wrap">
          <NDataTable :columns="hostLibraryColumns" :data="hostLibraryItems" :bordered="false" :single-line="true" size="medium" :row-key="hostLibraryRowKey"/>
        </div>
      </template>
      <div v-else class="empty-state"><h2>主机 MOD 库为空</h2><p class="muted-text">请先在主机级MOD库添加内容</p></div>
      <template #footer>
        <NButton @click="hostLibraryOpen = false">关闭</NButton>
        <NButton type="primary" :disabled="savingWorkshopId !== null || !selectedHostLibraryItems.length" @click="addSelectedHostLibrary">
          {{ savingWorkshopId === "__batch__" ? "保存中…" : `加入所选${selectedHostLibraryItems.length ? ` (${selectedHostLibraryItems.length})` : ""}` }}
        </NButton>
      </template>
    </AppDialog>

    <AppDialog v-model:open="configEditorOpen" :title="`编辑配置 · ${editingMod ? instanceModName(editingMod) : ''}`" size="md">
      <div v-if="editingMod" class="mod-config-editor">
        <div v-for="group in configFieldGroups" :key="group.name" class="mod-config-field">
          <div class="mod-config-field-label">
            <label :for="`mod-config-${group.name}`">{{ group.label }}<span v-if="group.shared" class="mod-config-shared">共用</span></label>
            <NTooltip v-if="group.hover" trigger="hover" placement="top-start" :style="{ maxWidth: '360px' }">
              <template #trigger>
                <button type="button" class="mod-config-help" :aria-label="`${group.label} 配置说明`">
                  <CircleHelp :size="14"/>
                </button>
              </template>
              {{ group.hover }}
            </NTooltip>
          </div>
          <div class="mod-config-field-control">
            <OptionSelect v-if="group.control === 'choice'" :model-value="configOptionSelection[group.name] ?? ''"
              :options="configOptionsFor(group)" :label="group.label" :disabled="configSaving"
              @update:model-value="updateOptionSelection(group, $event)"/>
            <NCheckbox v-else-if="group.control === 'boolean'" class="mod-config-toggle"
              :checked="scalarFieldValue(group) === true" :disabled="configSaving"
              @update:checked="value => setScalarField(group, value)">
              {{ scalarFieldValue(group) === true ? '开启' : '关闭' }}
            </NCheckbox>
            <NInputNumber v-else-if="group.control === 'number'" :value="scalarNumberValue(group)" placeholder=""
              :disabled="configSaving" :show-button="false" :input-props="{ id: `mod-config-${group.name}` }"
              @update:value="value => setScalarField(group, value ?? '')"/>
            <NInput v-else-if="group.control === 'text'" :value="String(scalarFieldValue(group) ?? '')" placeholder=""
              :disabled="configSaving" :input-props="{ id: `mod-config-${group.name}` }"
              @update:value="value => setScalarField(group, value)"/>
            <div v-else class="mod-config-unsupported">{{ group.issue ?? group.fields[0]?.reason ?? '此配置项的类型无法安全编辑' }}</div>
            <small v-if="fieldValueMismatch(group)" class="mod-config-warning">当前保存值与此版本可选值不匹配；保存其他项不会覆盖它</small>
          </div>
          <IconButton v-if="group.control !== 'unsupported'" class="mod-config-reset"
            :label="`恢复 ${group.label} 的 MOD 默认值`"
            :disabled="configSaving || !configChangedFields.includes(group.name) && !Object.hasOwn(configBase, group.name)"
            @click="resetConfigurationField(group)">
            <RotateCcw :size="14"/>
          </IconButton>
        </div>
      </div>
      <div v-if="configError" class="error-strip">{{ configError }}</div>
      <div v-if="configValidationError" class="error-strip">{{ configValidationError }}</div>
      <template #footer>
        <NButton :disabled="configSaving" @click="configEditorOpen = false">取消</NButton>
        <NButton type="primary"
          :disabled="configSaving || workspace.preview.value || configChangedFields.length === 0 || !!configValidationError"
          @click="saveConfiguration">{{ configSaving ? '保存中…' : '保存配置' }}</NButton>
      </template>
    </AppDialog>

    <AppDialog v-model:open="applyPreviewOpen" title="MOD 应用预览" description="只读核对目标内容与恢复条件" size="lg">
      <LoadingState v-if="applyPreviewLoading" compact description="正在核对应用条件"/>
      <div v-else-if="applyPreviewError" class="inline-error">{{ applyPreviewError }}</div>
      <div v-else-if="applyPreview" class="mod-apply-preview">
        <div v-if="applyPreviewStale" class="error-strip">MOD 配置已变化，此预览已失效。请重新生成。</div>
        <section class="mod-apply-section">
          <h3>MOD 变更 <span class="muted-text">{{ applyPreview.changes.length }}</span></h3>
          <p v-if="!applyPreview.changes.length" class="muted-text">期望与已部署状态一致</p>
          <div v-for="change in applyPreview.changes" :key="change.workshopId" class="mod-apply-change">
            <span>{{ modNameFor(change.workshopId) }}</span>
            <StatusLabel tone="warning" :label="modActionChangeLabel(change)" />
            <span v-if="change.configurationKeys.length" class="muted-text">配置：{{ change.configurationKeys.join('、') }}</span>
          </div>
        </section>
        <section v-if="applyPreview.changes.length" class="mod-apply-section">
          <h3>应用前备份</h3>
          <NCheckbox v-model:checked="createProtectionBackup" class="mod-backup-choice">应用前创建冷备份</NCheckbox>
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
        <NButton @click="applyPreviewOpen = false">关闭</NButton>
        <NButton type="primary" :disabled="applyPreviewLoading || !applyPreview || applyPreviewStale"
          @click="openApplyPreview">重新核对</NButton>
        <NButton type="primary" :disabled="applyPreviewLoading || applyBusy || !applyPreview?.canApply
          || !serverApplyPreview || applyPreviewStale || !!applyTask && ['queued', 'running'].includes(applyTask.status)"
          @click="applyMods">{{ applyBusy ? '正在提交…' : '应用 MOD' }}</NButton>
      </template>
    </AppDialog>

    <AppDialog :open="!!removeTarget" title="移除 MOD" description="移除后，世界中由该 MOD 添加的物品或角色数据可能无法读取。" @update:open="value => { if (!value && !removeBusy) { removeTarget = null; removeError = ''; } }">
      <div v-if="removeTarget" class="form-stack">
        <div class="form-facts">
          <div><span>MOD</span><strong>{{ instanceModName(removeTarget) }}</strong></div>
          <div><span>Workshop ID</span><strong class="mono">{{ removeTarget.workshopId }}</strong></div>
        </div>
        <div v-if="removeError" class="error-strip" role="alert">{{ removeError }}</div>
      </div>
      <template #footer>
        <NButton :disabled="removeBusy" @click="removeTarget = null">取消</NButton>
        <NButton type="primary" :disabled="removeBusy || workspace.preview.value" @click="confirmRemoveMod">
          {{ removeBusy ? '移除中…' : '移除 MOD' }}
        </NButton>
      </template>
    </AppDialog>
  </div>
</template>


<style scoped>
.instance-mod-main { display: flex; align-items: center; gap: 10px; min-width: 0; }
.instance-mod-thumb { display: block; width: 40px; height: 40px; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; object-fit: cover; border: 1px solid var(--border); border-radius: 2px; background: var(--surface-muted); }
.instance-mod-placeholder { display: grid; place-items: center; color: var(--text-tertiary); }
.instance-mod-copy { display: grid; min-width: 0; gap: 3px; }
.instance-mod-copy strong { overflow-wrap: anywhere; font-weight: 500; }
.instance-mod-copy .table-sub { margin: 0; }
.host-library-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px solid var(--border); font-size: 12px; }
.host-library-check { width: 34px; padding-right: 0 !important; }
.host-library-check input { width: 14px; height: 14px; accent-color: var(--accent); }
.host-library-thumb {
  display: block;
  width: 40px;
  height: 40px;
  min-width: 40px;
  max-width: 40px;
  min-height: 40px;
  max-height: 40px;
  object-fit: cover;
  border: 1px solid var(--border);
  border-radius: 2px;
  background: var(--surface-muted);
}
.host-library-placeholder { display: grid; place-items: center; color: var(--text-tertiary); }
</style>
