<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Package, Plus, RefreshCw } from "@lucide/vue";
import { useRoute } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
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
const totalSize = computed(() => items.value.reduce((total, item) => total + (item.sizeBytes ?? 0), 0));
const usedCount = computed(() => items.value.filter(item => item.usedBy.length > 0).length);
const taskTone = computed<"running"|"success"|"danger"|"warning"|"muted">(() => {
  const status = task.value?.status;
  return status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "interrupted" ? "warning" : status === "cancelled" ? "muted" : "running";
});
const taskStatusLabel = computed(() => {
  const status = task.value?.status;
  return status === "succeeded" ? "已加入主机库" : status === "failed" ? "准备失败" : status === "interrupted" ? "准备中断" : status === "cancelled" ? "已取消" : status === "queued" ? "排队中" : "准备 MOD";
});
const taskActive = computed(() => !!task.value && ["queued", "running"].includes(task.value.status));
const modLibrarySummary = computed(() => [
  { label: "内容版本", value: items.value.length },
  { label: "使用或备份引用", value: usedCount.value },
  { label: "占用空间", value: formatBytes(totalSize.value) },
  { label: "任务", value: taskActive.value ? "进行中" : "空闲", tone: taskActive.value ? "warning" as const : "default" as const },
]);
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
const queryBusy = ref(false);
const queryError = ref("");
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
const checkLabel = (status: UpdateCheckStatus) => status === "latest" ? "已是最新" : status === "outdated" ? "可更新" : status === "failed" ? "检查失败" : "检查中";

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
  } catch (cause) {
    const failure = describeError(cause);
    for (const id of ids) {
      if (updateChecks.value[id]?.status === "checking") {
        updateChecks.value = { ...updateChecks.value, [id]: { status: "failed", message: failure.message, candidate: null } };
      }
    }
    operations.recordFailure({ kind: "hostModCheck", target: hostTarget, title: "检查主机 MOD 更新", stage: "查询 Workshop", error: failure });
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
  await hostModBatch.start({ serverId: connection.serverId, hostId: hostTarget.hostId, recordVersion: connection.recordVersion }, outdatedCandidates.value);
  updateChecks.value = {};
}

async function refreshLibrary() {
  updateChecks.value = {};
  await refresh();
}

const selectableCandidates = computed(() => candidates.value.filter(item => item.result === 1 && !isHostModPresent(item)));
const selectedCollectionItems = computed(() => selectableCandidates.value.filter(item => collectionSelection.value.has(item.workshopId)));
const allCandidatesSelected = computed(() => selectableCandidates.value.length > 0
  && selectableCandidates.value.every(item => collectionSelection.value.has(item.workshopId)));
const task = computed<TaskSnapshot | null>(() => {
  const selected = target.value;
  if (!selected) return null;
  const tasks = taskStore.tasks.value
    .filter(item => item.operation === "hostModPrepare" && "hostId" in item.target
      && item.target.serverId === selected.serverId && item.target.hostId === selected.hostId)
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
  return tasks.find(item => ["queued", "running"].includes(item.status)) ?? tasks[0] ?? null;
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
  queryBusy.value = true;
  queryError.value = "";
  candidates.value = [];
  try {
    const requestId = crypto.randomUUID();
    const response = await getWorkshopMetadata({ schemaVersion: 1, requestId,
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion, workshopIds: parsed.ids });
    if (response.requestId !== requestId || response.serverId !== connection.serverId
        || response.recordVersion !== connection.recordVersion) throw new Error("Workshop 查询响应身份不匹配");
    candidates.value = response.items;
  } catch (cause) {
    queryError.value = describeError(cause).message;
  } finally {
    queryBusy.value = false;
  }
}

function setAddMode(mode: "manual" | "collection") {
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
    collectionTitle.value = fetched.get(collectionId)?.title ?? `合集 ${collectionId}`;
    const childIds = ids.filter(id => id !== collectionId);
    if (!childIds.length) throw new Error("合集里没有可导入的 MOD");
    candidates.value = childIds.map(id => fetched.get(id) ?? {
      workshopId: id, result: 0, title: null, description: null, creatorSteamId: null,
      author: null, creatorAppId: null, consumerAppId: null, timeCreated: null,
      timeUpdated: null, fileSize: null, previewUrl: null, contentHandle: null,
    });
  } catch (cause) {
    queryError.value = describeError(cause).message;
  } finally {
    queryBusy.value = false;
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
  } catch (cause) {
    queryError.value = describeError(cause).message;
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
  } catch (cause) {
    queryError.value = describeError(cause).message;
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
  } catch (cause) {
    const failure = describeError(cause);
    updateChecks.value = { ...updateChecks.value, [item.workshopId]: { status: "failed", message: failure.message, candidate: null } };
    operations.fail(operationId, failure);
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
  } catch (cause) {
    mutationError.value = describeError(cause).message;
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
  } catch (cause) {
    mutationError.value = describeError(cause).message;
  } finally {
    deleteBusy.value = false;
  }
}

watch(task, async (next, previous) => {
  if (!next || next.status === "queued" || next.status === "running") return;
  if (next.status !== "succeeded") return;
  if (previous?.taskId === next.taskId && previous.status === next.status) return;
  addOpen.value = false;
  candidates.value = [];
  updateChecks.value = {};
  await refresh();
}, { flush: "sync" });

function openAdd() {
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
watch(workshopInput, () => { if (addMode.value === 'manual') { candidates.value = []; queryError.value = ""; } });
watch(collectionInput, () => { if (addMode.value === 'collection') { candidates.value = []; collectionTitle.value = ""; collectionSelection.value = new Set(); queryError.value = ""; } });
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="主机公共资源" title="MOD 库">
      <template #actions>
        <button class="button" :disabled="!items.length || checkingUpdates || hostModBatch.state.running || workspace.preview.value" @click="checkAllUpdates">
          <RefreshCw :size="15" :class="{ spinning: checkingUpdates }"/>{{ checkingUpdates ? "检查中…" : "批量检查更新" }}
        </button>
        <button class="button" :disabled="loading || hostModBatch.state.running" @click="refreshLibrary">
          <RefreshCw :size="15" :class="{ spinning: loading }"/>刷新
        </button>
        <button v-if="outdatedCandidates.length || hostModBatch.state.running" class="button primary" :disabled="hostModBatch.state.running || !outdatedCandidates.length" @click="startBatchUpdate">
          {{ hostModBatch.state.running ? `更新中 ${hostModBatch.state.accepted}/${hostModBatch.state.total}` : `批量更新 (${outdatedCandidates.length})` }}
        </button>
        <button class="button primary" :disabled="!target || workspace.preview.value" @click="openAdd"><Plus :size="16"/>添加 MOD</button>
      </template>
    </PageHeader>
    <SummaryBar v-if="items.length" :items="modLibrarySummary" label="主机 MOD 摘要"/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>
    <div v-if="mutationError" class="environment-error" role="alert"><strong>{{ mutationError }}</strong></div>

    <div v-if="loading && !items.length" class="empty-state" role="status"><h2>正在读取主机 MOD 库</h2></div>
    <div v-else-if="!items.length" class="empty-state">
      <h2>主机 MOD 库为空</h2>
      <p class="muted-text">统一管理MOD，通过Workshop ID或合集链接添加</p>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>MOD</th><th>作者</th><th>大小</th><th>版本</th><th class="table-action">操作</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="`${item.workshopId}:${item.contentDigest}`">
            <td>
              <div class="host-mod-main">
                <img v-if="item.previewUrl" class="host-mod-thumb" :src="versionedPreview(item.previewUrl, item.contentDigest)" :alt="item.name ?? item.workshopId" width="40" height="40" loading="lazy"/>
                <span v-else class="host-mod-thumb host-mod-placeholder"><Package :size="17"/></span>
                <span class="host-mod-copy">
                  <strong>{{ item.name ?? item.workshopId }}</strong>
                  <small class="mono">{{ item.workshopId }}</small>
                </span>
              </div>
            </td>
            <td>{{ item.author ?? "未知" }}</td>
            <td>{{ item.sizeBytes === null ? "未知" : formatBytes(item.sizeBytes) }}</td>
            <td>
              <span class="mono">{{ item.version ?? "—" }}</span>
              <small v-if="updateChecks[item.workshopId]" class="table-sub" :class="`update-${updateChecks[item.workshopId]!.status}`">{{ checkLabel(updateChecks[item.workshopId]!.status) }}</small>
            </td>
            <td class="table-action">
              <div class="host-mod-actions">
                <button class="text-button" type="button" title="检查此 MOD 的最新版本" :disabled="workspace.preview.value || checkingUpdates || hostModBatch.state.running || addingWorkshopId !== null || deleteBusy"
                  @click="checkOneUpdate(item)">检查</button>
                <button class="text-button" type="button" title="同步该版本到使用此 MOD 的专服" :disabled="workspace.preview.value || syncLoading || addingWorkshopId !== null || deleteBusy"
                  @click="previewSync(item)">同步</button>
                <button class="text-button" type="button" title="更新到 Workshop 最新版本" :disabled="workspace.preview.value || addingWorkshopId !== null || deleteBusy || updateChecks[item.workshopId]?.status === 'latest' || hostModBatch.state.running"
                  @click="updateHostMod(item)">{{ updateChecks[item.workshopId]?.status === 'latest' ? "已是最新" : addingWorkshopId === item.workshopId ? "更新中…" : "更新" }}</button>
                <button class="text-button host-mod-delete" type="button" title="删除该版本" :disabled="workspace.preview.value || deleteBusy || addingWorkshopId !== null"
                  @click="deleteTarget = item">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
<FloatingTask v-if="task" label="主机 MOD">
    <TaskPanel v-if="task" title="准备主机 MOD" :status-label="taskStatusLabel" :stage-label="stageLabel(task.stage)" :tone="taskTone"
      :queue-position="task.queuePosition" :error="task.error"
      :cancellable="!!task.cancelAllowed && ['queued','running'].includes(task.status)" :busy="cancelBusy" @cancel="cancelPrepare">
      <span v-if="task.status === 'succeeded' && task.result?.autoCleanupError" class="muted-text">内容已固定 · 旧版本清理未完成，下次更新会重试</span>
      <span v-else-if="task.status === 'succeeded' && task.result?.autoCleanupDeleted" class="muted-text">内容已固定，可供所有专服选择 · 已自动清理 {{ task.result.autoCleanupDeleted }} 个旧版本</span>
      <span v-else-if="task.status === 'succeeded'" class="muted-text">内容已固定，可供所有专服选择</span>
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
        <textarea id="host-mod-input" v-model="workshopInput" rows="3" placeholder="每行一个 ID 或链接" @keydown.ctrl.enter="query" />
      </div>
      <div v-else class="form-field">
        <label for="host-mod-collection">Steam 合集链接或 ID</label>
        <input id="host-mod-collection" v-model="collectionInput" type="text" placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=..." @keydown.enter="loadCollection"/>
      </div>
      <div v-if="queryError" class="error-strip" role="alert">{{ queryError }}</div>
      <div v-if="queryBusy" class="muted-text" role="status">{{ addMode === 'collection' ? "正在读取合集…" : "正在查询 Workshop…" }}</div>
      <template v-else-if="candidates.length">
        <div v-if="addMode === 'collection'" class="collection-toolbar">
          <div><strong>{{ collectionTitle }}</strong><span class="muted-text">{{ candidates.length }} 项</span></div>
          <div class="collection-selection">
            <button class="text-button" type="button" :disabled="!selectableCandidates.length" @click="toggleAllCollectionItems">{{ allCandidatesSelected ? "取消全选" : "全选可导入" }}</button>
            <span class="muted-text">已选 {{ collectionSelection.size }} / 50</span>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th v-if="addMode === 'collection'" class="collection-check"/><th>图片</th><th>条目</th><th>作者</th><th v-if="addMode === 'collection'">大小</th><th>状态</th><th v-if="addMode === 'manual'" class="table-action">操作</th></tr></thead>
            <tbody>
              <tr v-for="item in candidates" :key="item.workshopId">
                <td v-if="addMode === 'collection'" class="collection-check"><input type="checkbox" :checked="collectionSelection.has(item.workshopId)" :disabled="item.result !== 1 || isHostModPresent(item)" @change="toggleCollectionItem(item.workshopId)"/></td>
                <td><img v-if="item.previewUrl" class="workshop-thumbnail" :src="item.previewUrl" :alt="item.title ?? item.workshopId" loading="lazy" /><span v-else class="muted-text">无图</span></td>
                <td><strong>{{ item.title ?? item.workshopId }}</strong><small class="table-sub mono">{{ item.workshopId }}</small></td>
                <td>{{ item.author ?? item.creatorSteamId ?? "未知" }}</td>
                <td v-if="addMode === 'collection'">{{ item.fileSize === null ? "未知" : formatBytes(item.fileSize) }}</td>
                <td><StatusLabel :tone="item.result !== 1 ? 'danger' : isHostModPresent(item) ? 'muted' : 'success'" :label="item.result !== 1 ? '不可用' : isHostModPresent(item) ? '已在主机库' : addMode === 'collection' ? '可导入' : '可添加'" /></td>
                <td v-if="addMode === 'manual'" class="table-action">
                  <button class="button button-compact primary" :disabled="addingWorkshopId !== null || item.result !== 1 || isHostModPresent(item)" @click="addCandidate(item)">
                    {{ isHostModPresent(item) ? "已在主机库" : addingWorkshopId === item.workshopId ? "添加中…" : "添加到主机库" }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template #footer>
        <button class="button" type="button" @click="addOpen = false">关闭</button>
        <button v-if="addMode === 'manual'" class="button primary" type="button" :disabled="queryBusy" @click="query">{{ queryBusy ? "查询中…" : "查询" }}</button>
        <button v-else class="button" type="button" :disabled="queryBusy" @click="loadCollection">{{ queryBusy ? "读取中…" : "读取合集" }}</button>
        <button v-if="addMode === 'collection'" class="button primary" type="button" :disabled="queryBusy || importingCollection || !selectedCollectionItems.length" @click="importCollection">{{ importingCollection ? "导入中…" : `导入所选${selectedCollectionItems.length ? ` (${selectedCollectionItems.length})` : ""}` }}</button>
      </template>
    </AppDialog>
    <AppDialog :open="!!syncTarget" title="同步到专服" @update:open="value => { if (!value && !syncBusy) syncTarget = null }">
      <div v-if="syncLoading" class="muted-text" role="status">正在检查使用此 MOD 的专服…</div>
      <template v-else-if="syncTarget">
        <p>将把 <strong>{{ syncTarget.name ?? syncTarget.workshopId }}</strong> <span class="mono">{{ syncTarget.version ?? "" }}</span> 的期望版本同步到 {{ syncEntries.length }} 个专服。</p>
        <p class="form-hint">只更新期望 MOD 版本，不会自动应用或重启专服。</p>
        <div v-if="syncEntries.length" class="table-wrap">
          <table>
            <thead><tr><th>专服</th><th>当前期望版本</th></tr></thead>
            <tbody><tr v-for="entry in syncEntries" :key="entry.instanceId"><td>{{ entry.displayName }}</td><td class="mono">{{ entry.currentDigest ? entry.currentDigest.slice(0, 12) : "未固定版本" }}</td></tr></tbody>
          </table>
        </div>
        <p v-else-if="!syncErrors.length" class="muted-text">没有使用此 MOD 的专服需要同步。</p>
        <div v-if="syncErrors.length" class="environment-error" role="alert">
          <strong>同步检查未完成</strong>
          <ul><li v-for="error in syncErrors" :key="error">{{ error }}</li></ul>
        </div>
        <p v-if="syncProgress" class="muted-text" role="status">{{ syncProgress }}</p>
      </template>
      <template #footer>
        <button class="button" type="button" :disabled="syncBusy" @click="syncTarget = null">取消</button>
        <button class="button primary" type="button" :disabled="syncBusy || syncLoading || !syncEntries.length || syncErrors.length > 0" @click="confirmSync">
          {{ syncBusy ? "同步中…" : "同步期望版本" }}
        </button>
      </template>
    </AppDialog>
    <AppDialog :open="!!deleteTarget" title="删除主机 MOD" @update:open="value => { if (!value && !deleteBusy) deleteTarget = null }">
      <p>删除后，新专服将无法选择该版本。仍被实例引用的版本不会被删除；引用该版本的备份可能无法完整恢复。</p>
      <p class="mono">{{ deleteTarget?.workshopId }} · {{ deleteTarget?.contentDigest.slice(0, 12) }}</p>
      <template #footer>
        <button class="button" :disabled="deleteBusy" @click="deleteTarget = null">取消</button>
        <button class="button danger" :disabled="deleteBusy" @click="remove">{{ deleteBusy ? "删除中…" : "确认删除" }}</button>
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.host-mod-main { display: flex; align-items: center; gap: 12px; min-width: 0; }
.host-mod-thumb {
  display: block;
  width: 40px;
  height: 40px;
  min-width: 40px;
  max-width: 40px;
  min-height: 40px;
  max-height: 40px;
  flex: 0 0 40px;
  object-fit: cover;
  border: 1px solid var(--border);
  border-radius: 2px;
  background: var(--surface-muted);
}
.host-mod-placeholder { display: grid; place-items: center; color: var(--text-tertiary); }
.host-mod-copy { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.host-mod-copy strong { font-weight: 500; overflow-wrap: anywhere; }
.host-mod-copy small { color: var(--text-tertiary); font-size: 10px; }
.host-mod-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-height: 28px; }
.update-latest { color: var(--success); }
.update-outdated { color: var(--warning); }
.update-failed { color: var(--danger); }
.update-checking { color: var(--text-tertiary); }

.host-mod-delete { color: var(--danger); }

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
