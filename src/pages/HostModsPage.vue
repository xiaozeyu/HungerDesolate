<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import { ArrowLeftRight, DownloadCloud, Package, Plus, RefreshCw, Trash2 } from "@lucide/vue";
import { useRoute } from "vue-router";
import { NButton, NDataTable, NInput, type DataTableColumns } from "naive-ui";
import IconButton from "../components/IconButton.vue";
import { notifyError, notifyInfo, notifySuccess } from "../naive/notify";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import LoadingState from "../components/LoadingState.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
import TaskPanel from "../components/TaskPanel.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useTasks } from "../composables/useTasks";
import { useOperations } from "../composables/useOperations";
import { useHostModBatch } from "../composables/useHostModBatch";
import { cancelHostModTask, deleteHostMod, listHostMods, prepareHostMods, type HostModItem } from "../api/hostMods";
import { listInstances } from "../api/instances";
import { listInstanceMods, saveInstanceMods } from "../api/mods";
import { createSerialQueue } from "../lib/serialQueue";
import { getWorkshopCollection, getWorkshopMetadata } from "../api/workshop";
import type { WorkshopItem } from "../types/workshop";
import { normalizeWorkshopCollection, normalizeWorkshopIds } from "../lib/workshopInput";
import { describeError } from "../lib/commandError";
import { inSession } from "../lib/session";
import { useQuery } from "../lib/queryClient";
import { formatBytes } from "../lib/environmentView";
import { stageLabel } from "../lib/operationView";
import type { TaskSnapshot } from "../types/contracts";

const route = useRoute();
const workspace = useWorkspace();
const taskStore = useTasks();
const hostModBatch = useHostModBatch();
const operations = useOperations();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const target = computed(() => server.value?.hostId ? { serverId: server.value.serverId, hostId: server.value.hostId } : null);
const key = computed(() => {
  const connection = server.value;
  return connection?.hostId
    ? `hostmods:${connection.serverId}:${connection.hostId}:${connection.recordVersion}`
    : null;
});
const libraryQuery = useQuery(key, async () => {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget) throw new Error("缺少主机身份，无法读取主机 MOD 库");
  const requestId = crypto.randomUUID();
  const response = await listHostMods({ schemaVersion: 1, requestId, target: hostTarget, expectedRecordVersion: connection.recordVersion });
  if (server.value?.serverId !== connection.serverId || server.value.hostId !== connection.hostId
      || server.value.recordVersion !== connection.recordVersion) throw new Error("主机 MOD 库请求上下文已失效");
  if (response.requestId !== requestId || response.recordVersion !== connection.recordVersion
      || response.target.serverId !== hostTarget.serverId || response.target.hostId !== hostTarget.hostId) {
    throw new Error("主机 MOD 库响应身份或版本不匹配");
  }
  return response;
});
const items = computed(() => libraryQuery.data.value?.items ?? []);
const taskTone = computed<"running"|"success"|"danger"|"warning"|"muted">(() => {
  const status = task.value?.status;
  return status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "interrupted" ? "warning" : status === "cancelled" ? "muted" : "running";
});
const taskStatusLabel = computed(() => {
  const status = task.value?.status;
  return status === "succeeded" ? "已加入主机库" : status === "failed" ? "准备失败" : status === "interrupted" ? "准备中断" : status === "cancelled" ? "已取消" : status === "queued" ? "排队中" : "准备 MOD";
});
const dismissedTaskId = ref<string | null>(null);
const visibleTask = computed<TaskSnapshot | null>(() =>
  task.value && task.value.taskId !== dismissedTaskId.value ? task.value : null);
const batchState = hostModBatch.state;
const batchPercent = computed(() =>
  batchState.total ? Math.round((batchState.accepted + batchState.failed) * 100 / batchState.total) : null);
const dockTone = computed(() => batchState.running ? "running" : taskTone.value);
const dockSummary = computed(() => {
  if (batchState.running) {
    const done = batchState.accepted + batchState.failed;
    return batchState.failed ? `已提交 ${done}/${batchState.total} · 失败 ${batchState.failed}` : `已提交 ${done}/${batchState.total}`;
  }
  return taskStatusLabel.value;
});
const dockProgress = computed(() => {
  if (batchState.running) return batchPercent.value;
  const progress = task.value?.progress;
  return progress?.kind === "determinate" && progress.total > 0
    ? Math.round(progress.completed * 100 / progress.total) : null;
});
const dockDismissible = computed(() => !batchState.running && !!task.value
  && ["succeeded", "failed", "cancelled", "interrupted"].includes(task.value.status));
function dismissDock() {
  if (task.value) dismissedTaskId.value = task.value.taskId;
}
const loading = libraryQuery.loading;
const error = libraryQuery.error;
const mutationError = ref("");
const cancelBusy = ref(false);
const cancelMessage = ref("");
const refresh = libraryQuery.refresh;
const addOpen = ref(false);
const addMode = ref<"manual" | "collection">("manual");
const workshopInput = ref("");
const collectionInput = ref("");
const collectionTitle = ref("");
const collectionSelection = ref<Set<string>>(new Set());
const manualSelection = ref<Set<string>>(new Set());
const queryBusy = ref(false);
const queryError = ref("");
let queryGeneration = 0;
function invalidateQuery() { queryGeneration += 1; queryBusy.value = false; manualSelection.value = new Set(); }
const candidates = ref<WorkshopItem[]>([]);
const isHostModPresent = (item: WorkshopItem) => items.value.some(entry => entry.workshopId === item.workshopId);
const versionedPreview = (url: string | null, digest: string | null) => {
  if (!url || !digest) return url ?? undefined;
  return `${url}${url.includes("?") ? "&" : "?"}v=${digest.slice(0, 12)}`;
};
const sourceHandlesFor = (entries: WorkshopItem[]) => Object.fromEntries(entries
  .filter(item => item.contentHandle)
  .map(item => [item.workshopId, item.contentHandle!]));
const addingWorkshopId = ref<string | null>(null);
const importingCollection = ref(false);
type UpdateCheckStatus = "checking" | "latest" | "outdated" | "failed";
interface UpdateCheck { status: UpdateCheckStatus; message: string; candidate: WorkshopItem | null; }
const updateChecks = ref<Record<string, UpdateCheck>>({});
const checkingUpdates = ref(false);
const latestHostItem = (item: HostModItem | WorkshopItem) => {
  const versions = items.value.filter(entry => entry.workshopId === item.workshopId);
  return versions.sort((left, right) => (Date.parse(right.preparedAt ?? "") || 0) - (Date.parse(left.preparedAt ?? "") || 0))[0] ?? null;
};
const outdatedCandidates = computed(() => {
  const result = new Map<string, WorkshopItem>();
  for (const [workshopId, check] of Object.entries(updateChecks.value)) {
    if (check.status === "outdated" && check.candidate) result.set(workshopId, check.candidate);
  }
  return [...result.values()];
});
function versionTone(item: HostModItem) {
  const status = updateChecks.value[item.workshopId]?.status;
  return status === "latest" ? "latest" : status === "outdated" ? "outdated" : "neutral";
}
function isChecking(item: HostModItem) {
  return updateChecks.value[item.workshopId]?.status === "checking";
}

async function checkUpdates(targets: HostModItem[]) {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || checkingUpdates.value || hostModBatch.state.running) return;
  const latest = new Map<string, HostModItem>();
  for (const item of targets) {
    const current = latest.get(item.workshopId);
    if (!current || (Date.parse(item.preparedAt ?? "") || 0) > (Date.parse(current.preparedAt ?? "") || 0)) latest.set(item.workshopId, item);
  }
  const ids = [...latest.keys()];
  if (!ids.length) return;
  checkingUpdates.value = true;
  const checks = { ...updateChecks.value };
  for (const id of ids) checks[id] = { status: "checking", message: "", candidate: null };
  updateChecks.value = checks;
  try {
    for (let offset = 0; offset < ids.length; offset += 100) {
      const chunk = ids.slice(offset, offset + 100);
      const requestId = crypto.randomUUID();
      const response = await getWorkshopMetadata({ schemaVersion: 1, requestId,
        serverId: connection.serverId, expectedRecordVersion: connection.recordVersion, workshopIds: chunk });
      if (server.value?.serverId !== connection.serverId || server.value.recordVersion !== connection.recordVersion
          || response.requestId !== requestId || response.serverId !== connection.serverId
          || response.recordVersion !== connection.recordVersion) throw new Error("Workshop 更新检查响应身份不匹配");
      const byId = new Map(response.items.map(item => [item.workshopId, item]));
      const next = { ...updateChecks.value };
      for (const id of chunk) {
        const candidate = byId.get(id);
        const item = latest.get(id)!;
        if (!candidate || candidate.result !== 1) {
          next[id] = { status: "failed", message: "Workshop 条目当前不可用", candidate: null };
        } else if (item.sourceHandle && candidate.contentHandle === item.sourceHandle) {
          next[id] = { status: "latest", message: "", candidate };
        } else {
          next[id] = { status: "outdated", message: "", candidate };
        }
      }
      updateChecks.value = next;
    }
    const statuses = ids.map(id => updateChecks.value[id]?.status);
    const latestCount = statuses.filter(status => status === "latest").length;
    const outdatedCount = statuses.filter(status => status === "outdated").length;
    const failedCount = statuses.filter(status => status === "failed").length;
    if (ids.length === 1) {
      const name = latest.get(ids[0])?.name ?? ids[0];
      if (statuses[0] === "outdated") notifyInfo("发现新版本", name);
      else if (statuses[0] === "latest") notifySuccess("已是最新版本", name);
      else notifyError("更新检查失败", updateChecks.value[ids[0]]?.message || name);
    } else {
      const summary = `${latestCount} 个已是最新${outdatedCount ? `，${outdatedCount} 个可更新` : ""}${failedCount ? `，${failedCount} 个检查失败` : ""}`;
      if (failedCount) notifyError("更新检查完成", summary);
      else notifySuccess("更新检查完成", summary);
    }
  } catch (cause) {
    const failure = describeError(cause);
    for (const id of ids) {
      if (updateChecks.value[id]?.status === "checking") {
        updateChecks.value = { ...updateChecks.value, [id]: { status: "failed", message: failure.message, candidate: null } };
      }
    }
    operations.recordFailure({ kind: "hostModCheck", target: hostTarget, title: "检查主机 MOD 更新", stage: "查询 Workshop", error: failure });
    notifyError("更新检查失败", failure.message);
  } finally {
    checkingUpdates.value = false;
  }
}

async function checkAllUpdates() {
  await checkUpdates(items.value);
}

async function checkOneUpdate(item: HostModItem) {
  await checkUpdates([latestHostItem(item) ?? item]);
}

async function startBatchUpdate() {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || !outdatedCandidates.value.length) return;
  const count = outdatedCandidates.value.length;
  dismissedTaskId.value = null;
  await hostModBatch.start({ serverId: connection.serverId, hostId: hostTarget.hostId, recordVersion: connection.recordVersion }, outdatedCandidates.value);
  updateChecks.value = {};
  notifySuccess("已提交批量更新", `${count} 个 MOD`);
}

async function refreshLibrary() {
  updateChecks.value = {};
  await refresh();
}

const selectableCandidates = computed(() => candidates.value.filter(item => item.result === 1 && !isHostModPresent(item)));
const selectedCollectionItems = computed(() => selectableCandidates.value.filter(item => collectionSelection.value.has(item.workshopId)));
const allCandidatesSelected = computed(() => selectableCandidates.value.length > 0
  && selectableCandidates.value.every(item => collectionSelection.value.has(item.workshopId)));
const selectedManualItems = computed(() => selectableCandidates.value.filter(item => manualSelection.value.has(item.workshopId)));
const allManualSelected = computed(() => selectableCandidates.value.length > 0
  && selectableCandidates.value.every(item => manualSelection.value.has(item.workshopId)));
const task = computed<TaskSnapshot | null>(() => {
  const selected = target.value;
  if (!selected) return null;
  const tasks = taskStore.tasks.value
    .filter(item => item.operation === "hostModPrepare" && "hostId" in item.target
      && item.target.serverId === selected.serverId && item.target.hostId === selected.hostId)
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
  return tasks.find(item => ["queued", "running"].includes(item.status))
    ?? tasks.find(item => inSession(item.createdAt)) ?? null;
});
const deleteTarget = ref<HostModItem | null>(null);
const deleteBusy = ref(false);
const queueSsh = createSerialQueue();
interface SyncEntry {
  instanceId: string;
  displayName: string;
  currentDigest: string | null;
  revision: number;
  mods: { workshopId: string; enabled: boolean; configurationOptions: Record<string, unknown>; artifactDigest: string | null }[];
}
const syncTarget = ref<HostModItem | null>(null);
const syncEntries = ref<SyncEntry[]>([]);
const syncErrors = ref<string[]>([]);
const syncLoading = ref(false);
const syncBusy = ref(false);
const syncProgress = ref("");
async function query() {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || queryBusy.value) return;
  const parsed = normalizeWorkshopIds(workshopInput.value);
  if (parsed.error) { queryError.value = parsed.error; return; }
  const generation = ++queryGeneration;
  queryBusy.value = true;
  queryError.value = "";
  candidates.value = [];
  manualSelection.value = new Set();
  try {
    const requestId = crypto.randomUUID();
    const response = await getWorkshopMetadata({ schemaVersion: 1, requestId,
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion, workshopIds: parsed.ids });
    if (generation !== queryGeneration) return;
    if (response.requestId !== requestId || response.serverId !== connection.serverId
        || response.recordVersion !== connection.recordVersion) throw new Error("Workshop 查询响应身份不匹配");
    candidates.value = response.items;
  } catch (cause) {
    if (generation === queryGeneration) queryError.value = describeError(cause).message;
  } finally {
    if (generation === queryGeneration) queryBusy.value = false;
  }
}

function setAddMode(mode: "manual" | "collection") {
  invalidateQuery();
  addMode.value = mode;
  candidates.value = [];
  queryError.value = "";
  collectionTitle.value = "";
  collectionSelection.value = new Set();
}

async function loadCollection() {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || queryBusy.value) return;
  const parsed = normalizeWorkshopCollection(collectionInput.value);
  if (parsed.error || !parsed.id) { queryError.value = parsed.error ?? "请输入 Steam 合集链接"; return; }
  const collectionId = parsed.id;
  const generation = ++queryGeneration;
  queryBusy.value = true;
  queryError.value = "";
  candidates.value = [];
  collectionTitle.value = "";
  collectionSelection.value = new Set();
  try {
    const collectionRequestId = crypto.randomUUID();
    const collection = await getWorkshopCollection({ schemaVersion: 1, requestId: collectionRequestId, collectionId });
    if (collection.requestId !== collectionRequestId || collection.collectionId !== collectionId) throw new Error("合集查询响应身份不匹配");
    if (!collection.children.length) throw new Error("合集里没有可导入的 MOD");
    if (server.value?.serverId !== connection.serverId || server.value.hostId !== connection.hostId
        || server.value.recordVersion !== connection.recordVersion) throw new Error("主机连接已变化，已停止查询合集");
    const ids = [...new Set([collectionId, ...collection.children])];
    const fetched = new Map<string, WorkshopItem>();
    for (let offset = 0; offset < ids.length; offset += 100) {
      const requestId = crypto.randomUUID();
      const response = await getWorkshopMetadata({ schemaVersion: 1, requestId,
        serverId: connection.serverId, expectedRecordVersion: connection.recordVersion,
        workshopIds: ids.slice(offset, offset + 100) });
      if (response.requestId !== requestId || response.serverId !== connection.serverId
          || response.recordVersion !== connection.recordVersion) throw new Error("Workshop 查询响应身份不匹配");
      if (server.value?.serverId !== connection.serverId || server.value.hostId !== connection.hostId
          || server.value.recordVersion !== connection.recordVersion) throw new Error("主机连接已变化，已停止查询合集");
      for (const item of response.items) fetched.set(item.workshopId, item);
    }
    if (generation !== queryGeneration) return;
    collectionTitle.value = fetched.get(collectionId)?.title ?? `合集 ${collectionId}`;
    const childIds = ids.filter(id => id !== collectionId);
    if (!childIds.length) throw new Error("合集里没有可导入的 MOD");
    candidates.value = childIds.map(id => fetched.get(id) ?? {
      workshopId: id, result: 0, title: null, description: null, creatorSteamId: null,
      author: null, creatorAppId: null, consumerAppId: null, timeCreated: null,
      timeUpdated: null, fileSize: null, previewUrl: null, contentHandle: null,
    });
  } catch (cause) {
    if (generation === queryGeneration) queryError.value = describeError(cause).message;
  } finally {
    if (generation === queryGeneration) queryBusy.value = false;
  }
}

function toggleCollectionItem(id: string) {
  if (!selectableCandidates.value.some(item => item.workshopId === id)) return;
  const next = new Set(collectionSelection.value);
  if (next.has(id)) next.delete(id);
  else if (next.size >= 50) { queryError.value = "一次最多导入 50 个 MOD"; return; }
  else next.add(id);
  collectionSelection.value = next;
}

function toggleAllCollectionItems() {
  if (allCandidatesSelected.value) {
    collectionSelection.value = new Set();
    return;
  }
  const eligible = selectableCandidates.value;
  if (eligible.length > 50) queryError.value = "合集超过 50 个可用 MOD，已选择前 50 个";
  collectionSelection.value = new Set(eligible.slice(0, 50).map(item => item.workshopId));
}

function toggleManualItem(id: string) {
  if (!selectableCandidates.value.some(item => item.workshopId === id)) return;
  const next = new Set(manualSelection.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  manualSelection.value = next;
}

function toggleAllManualItems() {
  manualSelection.value = allManualSelected.value ? new Set()
    : new Set(selectableCandidates.value.map(item => item.workshopId));
}

async function addSelectedCandidates() {
  const connection = server.value;
  const hostTarget = target.value;
  const selected = selectedManualItems.value;
  if (!connection || !hostTarget || !selected.length || hostModBatch.state.running) return;
  const count = selected.length;
  addOpen.value = false;
  candidates.value = [];
  manualSelection.value = new Set();
  await hostModBatch.start({ serverId: connection.serverId, hostId: hostTarget.hostId, recordVersion: connection.recordVersion }, selected, "add");
  if (hostModBatch.state.failed) notifyError("部分主机 MOD 添加失败", `${hostModBatch.state.failed}/${hostModBatch.state.total}`);
  else notifySuccess("已提交添加到主机库", `${count} 个 MOD`);
}

async function importCollection() {
  const connection = server.value;
  const hostTarget = target.value;
  const selected = selectedCollectionItems.value;
  if (!connection || !hostTarget || importingCollection.value || !selected.length) return;
  if (selected.length > 50) { queryError.value = "一次最多导入 50 个 MOD"; return; }
  importingCollection.value = true;
  queryError.value = "";
  try {
    const operationId = crypto.randomUUID();
    const requestId = crypto.randomUUID();
    const metadata = Object.fromEntries(selected.map(item => [item.workshopId, {
      name: item.title, author: item.author, previewUrl: item.previewUrl,
    }]));
    const response = await prepareHostMods({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, operationId,
      workshopIds: selected.map(item => item.workshopId), sourceHandles: sourceHandlesFor(selected), metadata });
    if (response.requestId !== requestId || response.task.operationId !== operationId) throw new Error("主机 MOD 准备响应身份不匹配");
    taskStore.receive(response.task);
    addOpen.value = false;
    candidates.value = [];
    collectionSelection.value = new Set();
    notifySuccess("已提交合集导入", `${selected.length} 个 MOD`);
  } catch (cause) {
    const failure = describeError(cause);
    queryError.value = failure.message;
    notifyError("合集导入失败", failure.message);
  } finally {
    importingCollection.value = false;
  }
}

async function addCandidate(item: WorkshopItem) {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || addingWorkshopId.value !== null || item.result !== 1 || isHostModPresent(item)) return;
  const operationId = crypto.randomUUID();
  addingWorkshopId.value = item.workshopId;
  queryError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await prepareHostMods({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, operationId, workshopIds: [item.workshopId],
      sourceHandles: sourceHandlesFor([item]),
      metadata: { [item.workshopId]: { name: item.title, author: item.author, previewUrl: item.previewUrl } } });
    if (response.requestId !== requestId || response.task.operationId !== operationId) throw new Error("主机 MOD 准备响应身份不匹配");
    taskStore.receive(response.task);
    addOpen.value = false;
    candidates.value = [];
    notifySuccess("已提交添加到主机库", item.title ?? item.workshopId);
  } catch (cause) {
    const failure = describeError(cause);
    queryError.value = failure.message;
    notifyError("添加主机 MOD 失败", failure.message);
  } finally {
    addingWorkshopId.value = null;
  }
}

async function updateHostMod(item: HostModItem) {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || addingWorkshopId.value !== null || workspace.preview.value) return;
  const requestEpoch = connection.recordVersion;
  addingWorkshopId.value = item.workshopId;
  queryError.value = "";
  mutationError.value = "";
  const operationId = operations.begin({ kind: "hostModUpdate", target: hostTarget, title: "更新主机 MOD", stage: "检查最新版本" });
  try {
    const metadataRequestId = crypto.randomUUID();
    const metadata = await getWorkshopMetadata({ schemaVersion: 1, requestId: metadataRequestId,
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion, workshopIds: [item.workshopId] });
    if (metadata.requestId !== metadataRequestId || metadata.serverId !== connection.serverId
        || metadata.recordVersion !== requestEpoch) throw new Error("Workshop 更新查询响应身份不匹配");
    const candidate = metadata.items.find(entry => entry.workshopId === item.workshopId);
    if (!candidate || candidate.result !== 1) throw new Error("Workshop 条目当前不可用");
    if (item.sourceHandle && candidate.contentHandle === item.sourceHandle) {
      updateChecks.value = { ...updateChecks.value, [item.workshopId]: { status: "latest", message: "", candidate } };
      operations.succeed(operationId);
      operations.dismiss(operationId);
      notifyInfo("已是最新版本", item.name ?? item.workshopId);
      return;
    }
    operations.setStage(operationId, "启动更新任务");
    const requestId = crypto.randomUUID();
    const response = await prepareHostMods({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, operationId, workshopIds: [item.workshopId],
      sourceHandles: sourceHandlesFor([candidate]),
      metadata: { [item.workshopId]: { name: candidate.title, author: candidate.author, previewUrl: candidate.previewUrl } } });
    if (response.requestId !== requestId || response.task.operationId !== operationId) throw new Error("主机 MOD 更新响应身份不匹配");
    taskStore.receive(response.task);
    operations.succeed(operationId);
    operations.dismiss(operationId);
    notifySuccess("已提交 MOD 更新", item.name ?? item.workshopId);
  } catch (cause) {
    const failure = describeError(cause);
    updateChecks.value = { ...updateChecks.value, [item.workshopId]: { status: "failed", message: failure.message, candidate: null } };
    operations.fail(operationId, failure);
    notifyError("MOD 更新失败", failure.message);
  } finally {
    addingWorkshopId.value = null;
  }
}

async function previewSync(item: HostModItem) {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || syncLoading.value || workspace.preview.value) return;
  syncTarget.value = item;
  syncEntries.value = [];
  syncErrors.value = [];
  syncLoading.value = true;
  const recordVersion = connection.recordVersion;
  try {
    const listRequestId = crypto.randomUUID();
    const result = await queueSsh(() => listInstances({ schemaVersion: 1, requestId: listRequestId,
      serverId: connection.serverId, expectedRecordVersion: recordVersion }));
    if (server.value?.recordVersion !== recordVersion) throw new Error("主机连接已变化，请重新同步");
    if (result.requestId !== listRequestId || result.serverId !== connection.serverId
        || result.recordVersion !== recordVersion || result.hostId !== hostTarget.hostId) {
      throw new Error("专服列表响应身份不匹配");
    }
    const entries: SyncEntry[] = [];
    for (const instance of result.instances) {
      if (instance.lifecycle !== "ready" || instance.hostId !== hostTarget.hostId) continue;
      try {
        const requestId = crypto.randomUUID();
        const mods = await queueSsh(() => listInstanceMods({ schemaVersion: 1, requestId,
          target: { serverId: connection.serverId, hostId: hostTarget.hostId, instanceId: instance.instanceId },
          expectedRecordVersion: recordVersion }));
        if (mods.requestId !== requestId || mods.target.instanceId !== instance.instanceId
            || mods.target.hostId !== hostTarget.hostId || server.value?.recordVersion !== recordVersion) {
          throw new Error("MOD 列表响应身份不匹配");
        }
        const current = mods.items.find(entry => entry.workshopId === item.workshopId && entry.desiredEnabled === true);
        if (!current || current.desiredArtifactDigest === item.contentDigest) continue;
        entries.push({
          instanceId: instance.instanceId,
          displayName: instance.displayName,
          currentDigest: current.desiredArtifactDigest,
          revision: mods.configRevision,
          mods: mods.items.filter(entry => entry.desiredEnabled !== null).map(entry => ({
            workshopId: entry.workshopId,
            enabled: entry.desiredEnabled === true,
            configurationOptions: entry.desiredConfiguration ?? {},
            artifactDigest: entry.workshopId === item.workshopId ? item.contentDigest : entry.desiredArtifactDigest,
          })),
        });
      } catch (cause) {
        syncErrors.value.push(`${instance.displayName}：${describeError(cause).message}`);
      }
    }
    if (server.value?.recordVersion === recordVersion) syncEntries.value = entries;
  } catch (cause) {
    syncErrors.value.push(describeError(cause).message);
  } finally {
    syncLoading.value = false;
  }
}

async function confirmSync() {
  const connection = server.value;
  const hostTarget = target.value;
  const entries = syncEntries.value;
  if (!connection || !hostTarget || !syncTarget.value || !entries.length || syncBusy.value || workspace.preview.value) return;
  syncBusy.value = true;
  syncErrors.value = [];
  let done = 0;
  const recordVersion = connection.recordVersion;
  try {
    for (const [index, entry] of entries.entries()) {
      if (server.value?.recordVersion !== recordVersion) throw new Error("主机连接已变化，已停止同步");
      syncProgress.value = `${index + 1}/${entries.length} · ${entry.displayName}`;
      try {
        const requestId = crypto.randomUUID();
        const operationId = crypto.randomUUID();
        const response = await queueSsh(() => saveInstanceMods({ schemaVersion: 1, requestId, operationId,
          target: { serverId: connection.serverId, hostId: hostTarget.hostId, instanceId: entry.instanceId },
          expectedRecordVersion: recordVersion, expectedRevision: entry.revision, mods: entry.mods }));
        if (response.requestId !== requestId || response.target.instanceId !== entry.instanceId
            || response.target.hostId !== hostTarget.hostId || response.operationId !== operationId
            || response.verifiedRevision <= entry.revision) throw new Error("保存响应身份或版本不匹配");
        done += 1;
      } catch (cause) {
        syncErrors.value.push(`${entry.displayName}：${describeError(cause).message}`);
      }
    }
    await refresh();
    if (!syncErrors.value.length) syncTarget.value = null;
  } catch (cause) {
    syncErrors.value.push(describeError(cause).message);
  } finally {
    syncBusy.value = false;
    syncProgress.value = "";
  }
  if (syncErrors.value.length) notifyError("同步未全部完成", `${done}/${entries.length} 个专服已同步`);
  else notifySuccess("期望版本已同步", `${done} 个专服`);
}

async function cancelPrepare() {
  const connection = server.value, hostTarget = target.value, current = task.value;
  if (!connection || !hostTarget || !current || cancelBusy.value || !["queued", "running"].includes(current.status)) return;
  cancelBusy.value = true;
  cancelMessage.value = "";
  try {
    const response = await cancelHostModTask({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: hostTarget, expectedRecordVersion: connection.recordVersion, operationId: current.operationId });
    taskStore.receive(response.task);
    cancelMessage.value = "取消请求已提交，等待任务安全结束。";
    notifyInfo("已提交取消请求");
  } catch (cause) {
    const failure = describeError(cause);
    mutationError.value = failure.message;
    notifyError("取消任务失败", failure.message);
  } finally {
    cancelBusy.value = false;
  }
}

async function remove() {
  const connection = server.value;
  const hostTarget = target.value;
  const item = deleteTarget.value;
  if (!connection || !hostTarget || !item || deleteBusy.value) return;
  deleteBusy.value = true;
  mutationError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await deleteHostMod({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, workshopId: item.workshopId, contentDigest: item.contentDigest });
    if (response.requestId !== requestId) throw new Error("主机 MOD 删除响应身份不匹配");
    deleteTarget.value = null;
    await refresh();
    notifySuccess("主机 MOD 版本已删除", item.name ?? item.workshopId);
  } catch (cause) {
    const failure = describeError(cause);
    mutationError.value = failure.message;
    notifyError("删除主机 MOD 失败", failure.message);
  } finally {
    deleteBusy.value = false;
  }
}

watch(task, async (next, previous) => {
  if (!next || next.status === "queued" || next.status === "running") return;
  if (previous?.taskId === next.taskId && previous.status === next.status) return;
  if (next.status === "failed" || next.status === "interrupted") {
    notifyError("主机 MOD 任务未完成", next.error?.message ?? "任务未完成");
    return;
  }
  if (next.status === "cancelled") {
    notifyInfo("主机 MOD 任务已取消");
    return;
  }
  if (next.status !== "succeeded") return;
  addOpen.value = false;
  candidates.value = [];
  updateChecks.value = {};
  notifySuccess("主机 MOD 操作完成");
  await refresh();
}, { flush: "sync" });

function openAdd() {
  invalidateQuery();
  workshopInput.value = "";
  collectionInput.value = "";
  addMode.value = "manual";
  collectionTitle.value = "";
  collectionSelection.value = new Set();
  candidates.value = [];
  queryError.value = "";
  addOpen.value = true;
}

watch([() => server.value?.serverId, () => server.value?.recordVersion, () => workspace.preview.value], () => {
  mutationError.value = "";
  cancelMessage.value = "";
  deleteTarget.value = null;
}, { immediate: true });
watch(workshopInput, () => { invalidateQuery(); if (addMode.value === 'manual') { candidates.value = []; queryError.value = ""; } });
watch(collectionInput, () => { invalidateQuery(); if (addMode.value === 'collection') { candidates.value = []; collectionTitle.value = ""; collectionSelection.value = new Set(); queryError.value = ""; } });

const candidateColumns = computed<DataTableColumns<WorkshopItem>>(() => {
  void items.value;
  void collectionSelection.value;
  void manualSelection.value;
  void addingWorkshopId.value;
  void workspace.preview.value;
  void hostModBatch.state.running;
  const collection = addMode.value === "collection";
  const columns: DataTableColumns<WorkshopItem> = [];
  columns.push({
    title: "",
    key: "select",
    width: 34,
    render: item => h("input", {
      type: "checkbox",
      checked: (collection ? collectionSelection.value : manualSelection.value).has(item.workshopId),
      disabled: item.result !== 1 || isHostModPresent(item) || (!collection && hostModBatch.state.running),
      onChange: () => collection ? toggleCollectionItem(item.workshopId) : toggleManualItem(item.workshopId),
    }),
  });
  columns.push(
    {
      title: "图片",
      key: "preview",
      render: item => item.previewUrl
        ? h("img", { class: "workshop-thumbnail", src: item.previewUrl, alt: item.title ?? item.workshopId, loading: "lazy" })
        : h("span", { class: "muted-text" }, "无图"),
    },
    {
      title: "条目",
      key: "title",
      render: item => h("div", null, [
        h("strong", item.title ?? item.workshopId),
        h("small", { class: "table-sub mono" }, item.workshopId),
      ]),
    },
    { title: "作者", key: "author", render: item => item.author ?? item.creatorSteamId ?? "未知" },
  );
  if (collection) {
    columns.push({ title: "大小", key: "size", render: item => item.fileSize === null ? "未知" : formatBytes(item.fileSize) });
  }
  columns.push({
    title: "状态",
    key: "status",
    render: item => h(StatusLabel, {
      tone: item.result !== 1 ? "danger" : isHostModPresent(item) ? "muted" : "success",
      label: item.result !== 1 ? "不可用" : isHostModPresent(item) ? "已在主机库" : collection ? "可导入" : "可添加",
    }),
  });
  if (!collection) {
    columns.push({
      title: "",
      key: "actions",
      align: "center",
      render: item => h(IconButton, {
        label: isHostModPresent(item) ? "已在主机库" : addingWorkshopId.value === item.workshopId ? "添加中…" : "添加到主机库",
        disabled: addingWorkshopId.value !== null || item.result !== 1 || isHostModPresent(item),
        onClick: () => void addCandidate(item),
      }, () => h(Plus, { size: 15 })),
    });
  }
  return columns;
});
const candidateRowKey = (item: WorkshopItem) => item.workshopId;

const syncColumns = computed<DataTableColumns<SyncEntry>>(() => [
  { title: "专服", key: "displayName", render: entry => entry.displayName },
  { title: "当前期望版本", key: "currentDigest", render: entry => h("span", { class: "mono" }, entry.currentDigest ? entry.currentDigest.slice(0, 12) : "未固定版本") },
]);
const syncRowKey = (entry: SyncEntry) => entry.instanceId;
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="主机公共资源" title="MOD 库">
      <template #actions>
        <NButton :disabled="!items.length || checkingUpdates || hostModBatch.state.running || workspace.preview.value" @click="checkAllUpdates">
          <template #icon><RefreshCw :size="15" :class="{ spinning: checkingUpdates }"/></template>{{ checkingUpdates ? "检查中…" : "批量检查更新" }}
        </NButton>
        <NButton :disabled="loading || hostModBatch.state.running" @click="refreshLibrary">
          <template #icon><RefreshCw :size="15" :class="{ spinning: loading }"/></template>刷新
        </NButton>
        <NButton v-if="outdatedCandidates.length || hostModBatch.state.running" type="primary" :disabled="hostModBatch.state.running || !outdatedCandidates.length" @click="startBatchUpdate">
          {{ hostModBatch.state.running ? `更新中 ${hostModBatch.state.accepted}/${hostModBatch.state.total}` : `批量更新 (${outdatedCandidates.length})` }}
        </NButton>
        <NButton type="primary" :disabled="!target || workspace.preview.value" @click="openAdd"><template #icon><Plus :size="16"/></template>添加 MOD</NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <ErrorNotice :error="error"/>
    <ErrorNotice :error="mutationError"/>

    <LoadingState v-if="loading && !items.length" description="正在读取主机 MOD 库"/>
    <div v-else-if="!items.length" class="empty-state">
      <h2>主机 MOD 库为空</h2>
      <p class="muted-text">统一管理MOD，通过Workshop ID或合集链接添加</p>
    </div>
    <div v-else class="host-mod-grid">
      <article v-for="item in items" :key="`${item.workshopId}:${item.contentDigest}`" class="host-mod-card" :class="{ checking: isChecking(item) }">
        <div class="host-mod-cover">
          <img v-if="item.previewUrl" class="host-mod-cover-image" :src="versionedPreview(item.previewUrl, item.contentDigest)" :alt="item.name ?? item.workshopId" loading="lazy"/>
          <div v-else class="host-mod-cover-placeholder"><Package :size="28"/></div>
          <span class="host-mod-version" :class="`tone-${versionTone(item)}`">{{ item.version ?? "—" }}</span>
        </div>
        <div class="host-mod-card-info">
          <strong class="host-mod-card-title">{{ item.name ?? item.workshopId }}</strong>
          <span class="host-mod-card-id mono">{{ item.workshopId }}</span>
          <span class="host-mod-card-author">{{ item.author ?? "未知作者" }}</span>
        </div>
        <div class="host-mod-card-actions">
          <IconButton label="检查此 MOD 的最新版本" :disabled="workspace.preview.value || checkingUpdates || hostModBatch.state.running || addingWorkshopId !== null || deleteBusy" @click="checkOneUpdate(item)"><RefreshCw :size="15"/></IconButton>
          <IconButton label="同步该版本到使用此 MOD 的专服" :disabled="workspace.preview.value || syncLoading || addingWorkshopId !== null || deleteBusy" @click="previewSync(item)"><ArrowLeftRight :size="15"/></IconButton>
          <IconButton :label="updateChecks[item.workshopId]?.status === 'latest' ? '已是最新' : addingWorkshopId === item.workshopId ? '更新中…' : '更新到 Workshop 最新版本'" :disabled="workspace.preview.value || addingWorkshopId !== null || deleteBusy || updateChecks[item.workshopId]?.status === 'latest' || hostModBatch.state.running" @click="updateHostMod(item)"><DownloadCloud :size="15"/></IconButton>
          <IconButton label="删除该版本" tone="danger" :disabled="workspace.preview.value || deleteBusy || addingWorkshopId !== null" @click="deleteTarget = item"><Trash2 :size="15"/></IconButton>
        </div>
      </article>
    </div>
<FloatingTask v-if="visibleTask" label="主机 MOD" :tone="dockTone" :summary="dockSummary" :progress="dockProgress" :dismissible="dockDismissible" @dismiss="dismissDock">
    <TaskPanel v-if="visibleTask" title="准备主机 MOD" dock :status-label="taskStatusLabel" :stage-label="stageLabel(visibleTask.stage)" :tone="taskTone"
      :queue-position="visibleTask.queuePosition" :error="visibleTask.error"
      :cancellable="!!visibleTask.cancelAllowed && ['queued','running'].includes(visibleTask.status)" :busy="cancelBusy" @cancel="cancelPrepare">
      <span v-if="visibleTask.status === 'succeeded' && visibleTask.result?.autoCleanupError" class="muted-text">内容已固定 · 旧版本清理未完成，下次更新会重试</span>
      <span v-else-if="visibleTask.status === 'succeeded' && visibleTask.result?.autoCleanupDeleted" class="muted-text">内容已固定，可供所有专服选择 · 已自动清理 {{ visibleTask.result.autoCleanupDeleted }} 个旧版本</span>
      <span v-else-if="visibleTask.status === 'succeeded'" class="muted-text">内容已固定，可供所有专服选择</span>
      <span v-if="cancelMessage" class="muted-text">{{ cancelMessage }}</span>
    </TaskPanel>
</FloatingTask>
    <AppDialog v-model:open="addOpen" title="添加主机 MOD" size="lg">
      <div class="add-mod-tabs" role="tablist" aria-label="添加方式">
        <button type="button" :class="{ active: addMode === 'manual' }" @click="setAddMode('manual')">手动输入</button>
        <button type="button" :class="{ active: addMode === 'collection' }" @click="setAddMode('collection')">从合集导入</button>
      </div>
      <div v-if="addMode === 'manual'" class="form-field">
        <label for="host-mod-input">Workshop ID 或链接</label>
        <NInput v-model:value="workshopInput" type="textarea" :autosize="{ minRows: 3 }" placeholder="每行一个 ID 或链接" :input-props="{ id: 'host-mod-input' }" @keydown.ctrl.enter="query"/>
      </div>
      <div v-else class="form-field">
        <label for="host-mod-collection">Steam 合集链接或 ID</label>
        <NInput v-model:value="collectionInput" placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=..." :input-props="{ id: 'host-mod-collection' }" @keydown.enter="loadCollection"/>
      </div>
      <ErrorNotice :error="queryError"/>
      <div v-if="queryBusy" class="muted-text" role="status">{{ addMode === 'collection' ? "正在读取合集…" : "正在查询 Workshop…" }}</div>
      <template v-else-if="candidates.length">
        <div v-if="addMode === 'collection'" class="collection-toolbar">
          <div><strong>{{ collectionTitle }}</strong><span class="muted-text">{{ candidates.length }} 项</span></div>
          <div class="collection-selection">
            <NButton text size="small" :disabled="!selectableCandidates.length" @click="toggleAllCollectionItems">{{ allCandidatesSelected ? "取消全选" : "全选可导入" }}</NButton>
            <span class="muted-text">已选 {{ collectionSelection.size }} / 50</span>
          </div>
        </div>
        <div v-else class="collection-toolbar">
          <div><strong>查询结果</strong><span class="muted-text">{{ candidates.length }} 项</span></div>
          <div class="collection-selection">
            <NButton text size="small" :disabled="!selectableCandidates.length || hostModBatch.state.running" @click="toggleAllManualItems">{{ allManualSelected ? "取消全选" : "全选可添加" }}</NButton>
            <span class="muted-text">已选 {{ manualSelection.size }}</span>
          </div>
        </div>
        <div class="table-wrap">
          <NDataTable :columns="candidateColumns" :data="candidates" :bordered="false" :single-line="true" size="medium" :row-key="candidateRowKey"/>
        </div>
      </template>

      <template #footer>
        <NButton @click="addOpen = false">关闭</NButton>
        <NButton v-if="addMode === 'manual'" :type="candidates.length ? 'default' : 'primary'" :disabled="queryBusy" @click="query">{{ queryBusy ? "查询中…" : "查询" }}</NButton>
        <NButton v-else :disabled="queryBusy" @click="loadCollection">{{ queryBusy ? "读取中…" : "读取合集" }}</NButton>
        <NButton v-if="addMode === 'manual'" type="primary" :disabled="queryBusy || hostModBatch.state.running || !selectedManualItems.length" @click="addSelectedCandidates">{{ hostModBatch.state.running ? "添加中…" : `添加所选${selectedManualItems.length ? ` (${selectedManualItems.length})` : ""}` }}</NButton>
        <NButton v-if="addMode === 'collection'" type="primary" :disabled="queryBusy || importingCollection || !selectedCollectionItems.length" @click="importCollection">{{ importingCollection ? "导入中…" : `导入所选${selectedCollectionItems.length ? ` (${selectedCollectionItems.length})` : ""}` }}</NButton>
      </template>
    </AppDialog>
    <AppDialog :open="!!syncTarget" title="同步到专服" @update:open="value => { if (!value && !syncBusy) syncTarget = null }">
      <div v-if="syncLoading" class="muted-text" role="status">正在检查使用此 MOD 的专服…</div>
      <template v-else-if="syncTarget">
        <p>将把 <strong>{{ syncTarget.name ?? syncTarget.workshopId }}</strong> - <span class="mono">{{ syncTarget.version ?? "" }}</span> 的期望版本同步到 {{ syncEntries.length }} 个专服</p>
        <p class="form-hint">只更新期望 MOD 版本，不会自动应用或重启专服</p>
        <div v-if="syncEntries.length" class="table-wrap">
          <NDataTable :columns="syncColumns" :data="syncEntries" :bordered="false" :single-line="true" size="medium" :row-key="syncRowKey"/>
        </div>
        <p v-else-if="!syncErrors.length" class="muted-text">没有使用此 MOD 的专服需要同步</p>
        <div v-if="syncErrors.length" class="environment-error" role="alert">
          <strong>同步检查未完成</strong>
          <ul><li v-for="error in syncErrors" :key="error">{{ error }}</li></ul>
        </div>
        <p v-if="syncProgress" class="muted-text" role="status">{{ syncProgress }}</p>
      </template>
      <template #footer>
        <NButton :disabled="syncBusy" @click="syncTarget = null">取消</NButton>
        <NButton type="primary" :disabled="syncBusy || syncLoading || !syncEntries.length || syncErrors.length > 0" @click="confirmSync">
          {{ syncBusy ? "同步中…" : "同步期望版本" }}
        </NButton>
      </template>
    </AppDialog>
    <AppDialog :open="!!deleteTarget" title="删除主机 MOD" @update:open="value => { if (!value && !deleteBusy) deleteTarget = null }">
      <p>删除后，新专服将无法选择该版本。仍被实例引用的版本不会被删除；引用该版本的备份可能无法完整恢复。</p>
      <p class="mono">{{ deleteTarget?.workshopId }} · {{ deleteTarget?.contentDigest.slice(0, 12) }}</p>
      <template #footer>
        <NButton :disabled="deleteBusy" @click="deleteTarget = null">取消</NButton>
        <NButton type="error" ghost :disabled="deleteBusy" @click="remove">{{ deleteBusy ? "删除中…" : "确认删除" }}</NButton>
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.add-mod-tabs { display: flex; gap: 4px; margin-bottom: 22px; border-bottom: 1px solid var(--border); }
.add-mod-tabs button { padding: 8px 0; margin-right: 20px; border-bottom: 1px solid transparent; color: var(--text-tertiary); font-size: 12px; }
.add-mod-tabs button.active { border-bottom-color: var(--text); color: var(--text); }
.collection-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 0; border-top: 1px solid var(--border); font-size: 12px; }
.collection-toolbar > div { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.collection-toolbar strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.collection-selection { flex: 0 0 auto; align-items: center; }
.collection-check { width: 34px; padding-right: 0 !important; }
.collection-check input { width: 14px; height: 14px; accent-color: var(--accent); }
</style>
