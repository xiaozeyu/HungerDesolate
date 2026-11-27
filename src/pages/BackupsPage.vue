<script setup lang="ts">
import { computed, h, onBeforeUnmount, reactive, ref, watch } from "vue";
import { Download, Pencil, Plus, RefreshCw, RotateCcw, Trash2, Upload } from "@lucide/vue";
import { NButton, NCheckbox, NDataTable, NInput, type DataTableColumns } from "naive-ui";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import AppDialog from "../components/AppDialog.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import IconButton from "../components/IconButton.vue";
import TaskPanel from "../components/TaskPanel.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
import DownloadProgressNotice from "../components/DownloadProgressNotice.vue";
import { listHostMods } from "../api/hostMods";
import { notification } from "../naive/notify";
import { cancelInstanceBackup, cancelInstanceRestore, createInstanceBackup, deleteInstanceBackup, downloadInstanceBackup, getBackupDownloadTask, importInstanceBackup, listBackupDownloadTasks, listInstanceBackupTasks, listInstanceBackups, listInstanceRestoreTasks, listInstances, pickBackupDownloadPath, pickBackupImportPath, previewInstanceBackup, restoreInstanceBackup, updateInstanceBackupNote, type BackupCaptureMode, type BackupDownloadResponse, type BackupPreviewResponse, type BackupSummary } from "../api/instances";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError, type UserError } from "../lib/commandError";
import { inSession } from "../lib/session";
import { formatBytes } from "../lib/environmentView";
import { useTasks } from "../composables/useTasks";
import type { TaskSnapshot } from "../types/contracts";
import { createSerialQueue } from "../lib/serialQueue";

const workspace = useWorkspace();
const { server, target, instanceName } = usePageTarget();
const taskStore = useTasks();
const mode = ref<BackupCaptureMode | null>(null);
const createOpen = ref(false);
const previews = ref<Record<BackupCaptureMode, BackupPreviewResponse | null>>({ cold: null, quasiHot: null });
const items = ref<BackupSummary[]>([]);
const activeTaskTone = computed<"running"|"success"|"danger"|"warning"|"muted">(() => {
  const status = activeTask.value?.status;
  return status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "interrupted" ? "warning" : status === "cancelled" ? "muted" : "running";
});
const preview = computed(() => mode.value ? previews.value[mode.value] : null);
const loading = ref(false);
const contextLoading = ref(false);
const previewLoading = ref(false);
const error = ref<UserError | string | null>(null);
const restoreError = ref("");
const deleteError = ref("");
const createBusy = ref(false);
const activeTask = computed<TaskSnapshot | null>(() => {
  const key = targetKey.value;
  if (!key) return null;
  const tasks = taskStore.tasks.value.filter(task => taskBelongsHere(task, key))
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
  return tasks.find(task => ["queued", "running"].includes(task.status))
    ?? tasks.find(task => inSession(task.createdAt)) ?? null;
});
const dismissedTaskId = ref<string | null>(null);
const visibleActiveTask = computed<TaskSnapshot | null>(() =>
  activeTask.value && activeTask.value.taskId !== dismissedTaskId.value ? activeTask.value : null);
const activeTaskPercent = computed(() => {
  const progress = activeTask.value?.progress;
  return progress?.kind === "determinate" && progress.total > 0
    ? Math.round(progress.completed * 100 / progress.total) : null;
});
const activeTaskDismissible = computed(() => !!activeTask.value
  && ["succeeded", "failed", "cancelled", "interrupted"].includes(activeTask.value.status));
function dismissActiveTask() {
  if (activeTask.value) dismissedTaskId.value = activeTask.value.taskId;
}
const restoreBusy = ref(false);
const restoreTarget = ref<BackupSummary | null>(null);
const restoreModWarnings = ref<string[]>([]);
const restoreProtection = ref(true);
const deleteTarget = ref<BackupSummary | null>(null);
const deleteBusy = ref(false);
const cancelBusy = ref(false);
const cancelMessage = ref("");
interface DownloadNoticeState {
  status: "running" | "succeeded" | "interrupted" | "failed";
  bytesTransferred: number;
  sizeBytes: number | null;
  error: string;
}
const downloadCleanups = new Map<string, () => void>();
const shownInterrupted = new Set<string>();
const activeDownloadIds = reactive(new Set<string>());
const downloading = computed(() => activeDownloadIds.size > 0);
const importTask = ref<{ path: string; operationId: string; status: "running" | "succeeded" | "failed" } | null>(null);
const importing = computed(() => importTask.value?.status === "running");
const importFileName = computed(() => fileName(importTask.value?.path));
function fileName(value: string | undefined) {
  return value ? value.split(/[\\/]/).pop() || value : "";
}
const createNote = ref("");
const noteTarget = ref<BackupSummary | null>(null);
const noteDraft = ref("");
const noteBusy = ref(false);
const noteError = ref("");
let pendingOperationId = "";
let pendingCreate: { requestId: string; backupId: string; captureMode: BackupCaptureMode; revision: number; reason: string } | null = null;
let epoch = 0;
let listSequence = 0;
let previewSequence = 0;
let currentListRequest = "";
let currentPreviewRequest = "";
const queueSsh = createSerialQueue();
const instanceSummary = computed(() => {
  const current = target.value;
  return current ? workspace.instances(current.hostId).find(item => item.instanceId === current.instanceId) ?? null : null;
});
const revision = computed(() => instanceSummary.value?.configRevision ?? null);
const pageLoading = computed(() => loading.value || contextLoading.value);
const targetKey = computed(() => {
  const currentTarget = target.value;
  return currentTarget
    ? `${currentTarget.serverId}:${currentTarget.hostId}:${currentTarget.instanceId}`
    : "";
});
function isCurrent(kind: "list" | "preview", ticket: number, requestEpoch: number,
  requestId: string, serverId: string, recordVersion: number, targetKey: string) {
  const currentTarget = target.value;
  const latestSequence = kind === "list" ? listSequence : previewSequence;
  const latestRequest = kind === "list" ? currentListRequest : currentPreviewRequest;
  return ticket === latestSequence && requestEpoch === epoch && latestRequest === requestId
    && server.value?.serverId === serverId && server.value.recordVersion === recordVersion
    && currentTarget !== null && `${currentTarget.serverId}:${currentTarget.hostId}:${currentTarget.instanceId}` === targetKey;
}

let summarySequence = 0;
async function refreshInstanceSummaries() {
  const connection = server.value;
  const current = target.value;
  if (!connection || !current) return;
  const ticket = ++summarySequence;
  const requestEpoch = epoch;
  const requestId = crypto.randomUUID();
  contextLoading.value = true;
  try {
    const response = await listInstances({ schemaVersion: 1, requestId,
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion });
    if (ticket !== summarySequence || requestEpoch !== epoch) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId
      || response.serverId !== connection.serverId || response.hostId !== current.hostId) {
      throw new Error("实例列表响应身份不匹配");
    }
    workspace.commitInstances(response);
  } catch (cause) {
    if (ticket === summarySequence && requestEpoch === epoch) error.value = describeError(cause);
  } finally {
    if (ticket === summarySequence && requestEpoch === epoch) contextLoading.value = false;
  }
}

async function refresh() {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null) return;
  const requestId = crypto.randomUUID();
  const ticket = ++listSequence;
  const requestEpoch = epoch;
  currentListRequest = requestId;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  loading.value = true;
  error.value = "";
  try {
    if (workspace.preview.value) {
      items.value = [];
      previews.value = { cold: null, quasiHot: null };
      return;
    }
    const tasksRequestId = crypto.randomUUID();
    if (!downloading.value) {
      const downloads = await queueSsh(() => listBackupDownloadTasks({ schemaVersion: 1, requestId: crypto.randomUUID(),
        target: instanceTarget, expectedRecordVersion: connection.recordVersion }));
      if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
      const interrupted = downloads.find(task => task.status === "interrupted" && task.destination
        && task.target.serverId === instanceTarget.serverId && task.target.hostId === instanceTarget.hostId
        && task.target.instanceId === instanceTarget.instanceId);
      if (interrupted) surfaceInterruptedDownload(interrupted);
    }
    const tasks = await queueSsh(() => listInstanceBackupTasks({ schemaVersion: 1, requestId: tasksRequestId,
      target: instanceTarget, expectedRecordVersion: connection.recordVersion }));
    if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    if (tasks.some(task => task.schemaVersion !== 1 || task.requestId !== tasksRequestId
      || task.target.serverId !== instanceTarget.serverId || task.target.hostId !== instanceTarget.hostId
      || task.target.instanceId !== instanceTarget.instanceId)) throw new Error("备份任务列表身份不匹配");
    const response = await queueSsh(() => listInstanceBackups({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision }));
    if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.configRevision !== expectedRevision
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId) throw new Error("备份列表身份或版本不匹配");
    items.value = response.items;
    previews.value = { cold: null, quasiHot: null };
    const restoreTasks = await queueSsh(() => listInstanceRestoreTasks({schemaVersion:1,requestId:crypto.randomUUID(),target:instanceTarget,expectedRecordVersion:connection.recordVersion}));
    if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    const serverTasks = [...tasks, ...restoreTasks];
    const serverTaskIds = new Set(serverTasks.map(entry => entry.task.taskId));
    for (const local of taskStore.tasks.value) {
      if (taskBelongsHere(local, targetKey)
          && ["backupCreate", "backupRestore"].includes(local.operation)
          && ["queued", "running"].includes(local.status)
          && !serverTaskIds.has(local.taskId)) {
        taskStore.discard(local.taskId);
      }
    }
    const latest = serverTasks.find(task => taskBelongsHere(task.task, targetKey));
    if (latest) {
      taskStore.receive(latest.task);
      if (["queued", "running"].includes(latest.task.status)) {
        createBusy.value = true;
        pendingOperationId = latest.task.operationId;
      }
    }
  } catch (cause) {
    if (isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause);
  } finally {
    if (isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) loading.value = false;
  }
}

async function inspectMode(captureMode: BackupCaptureMode) {
  const connection = server.value;
  const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || workspace.preview.value) return;
  const requestId = crypto.randomUUID();
  const ticket = ++previewSequence;
  const requestEpoch = epoch;
  currentPreviewRequest = requestId;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  previewLoading.value = true;
  error.value = "";
  try {
    const response = await queueSsh(() => previewInstanceBackup({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision, captureMode }));
    if (!isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.configRevision !== expectedRevision
      || response.captureMode !== captureMode || response.target.serverId !== instanceTarget.serverId
      || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("备份预览身份或版本不匹配");
    }
    previews.value = { ...previews.value, [captureMode]: response };
  } catch (cause) {
    if (isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause);
  } finally {
    if (isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) previewLoading.value = false;
  }
}

async function refreshPage() {
  await refresh();
  if (revision.value === null || workspace.preview.value) return;
  await inspectMode("cold");
  await inspectMode("quasiHot");
}

function taskBelongsHere(task: TaskSnapshot, key: string) {
  return ["backupCreate", "backupRestore"].includes(task.operation) && "instanceId" in task.target
    && `${task.target.serverId}:${task.target.hostId}:${task.target.instanceId}` === key;
}

async function downloadBackup(item: BackupSummary) {
  if (workspace.preview.value || downloading.value) return;
  const selectionKey = targetKey.value;
  const recordVersion = server.value?.recordVersion;
  const requestEpoch = epoch;
  const destination = await pickBackupDownloadPath(`backup-${item.backupId.slice(0, 8)}.tar.gz`);
  const connection = server.value; const instanceTarget = target.value;
  if (!destination || !connection || !instanceTarget || connection.recordVersion !== recordVersion
    || targetKey.value !== selectionKey || requestEpoch !== epoch) return;
  const operationId = crypto.randomUUID();
  await runDownload(item.backupId, operationId, destination, connection.recordVersion, instanceTarget);
}

async function retryDownload(task: BackupDownloadResponse) {
  const connection = server.value; const instanceTarget = target.value;
  if (!connection || !instanceTarget || !task.destination || workspace.preview.value || downloading.value) return;
  await runDownload(task.backupId, task.operationId, task.destination, connection.recordVersion, instanceTarget);
}

function destroyDownloadNotices() {
  for (const cleanup of [...downloadCleanups.values()]) cleanup();
  downloadCleanups.clear();
}

function showDownloadNotice(operationId: string, destination: string, progress: DownloadNoticeState,
  onRetry: () => void, onClose: () => void) {
  let destroyed = false;
  let notice: { destroy: () => void } | null = null;
  const close = () => {
    if (destroyed) return;
    destroyed = true;
    notice?.destroy();
    notice = null;
    downloadCleanups.delete(operationId);
    onClose();
  };
  notice = notification.open({
    title: "下载备份",
    content: () => h(DownloadProgressNotice, {
      name: fileName(destination),
      progress,
      onRetry: () => { close(); onRetry(); },
      onClose: close,
    }),
    duration: 0,
    closable: true,
    keepAliveOnHover: true,
    onClose: close,
  });
  downloadCleanups.set(operationId, close);
  return close;
}

function surfaceInterruptedDownload(task: BackupDownloadResponse) {
  if (!task.destination || shownInterrupted.has(task.operationId)) return;
  shownInterrupted.add(task.operationId);
  const progress = reactive<DownloadNoticeState>({ status: "interrupted", bytesTransferred: task.bytesTransferred ?? 0,
    sizeBytes: task.sizeBytes ?? null, error: "上次下载未完成，可继续下载" });
  showDownloadNotice(task.operationId, task.destination, progress, () => void retryDownload(task), () => {});
}

async function runDownload(backupId:string, operationId:string, destination:string, recordVersion:number, instanceTarget:NonNullable<typeof target.value>) {
  const requestEpoch = epoch;
  const requestKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const progress = reactive<DownloadNoticeState>({ status: "running", bytesTransferred: 0, sizeBytes: null, error: "" });
  let poll = 0;
  const stopPolling = () => {
    if (poll) {
      clearInterval(poll);
      poll = 0;
    }
  };
  activeDownloadIds.add(operationId);
  const close = showDownloadNotice(operationId, destination, progress,
    () => void runDownload(backupId, operationId, destination, recordVersion, instanceTarget), stopPolling);
  poll = window.setInterval(async () => {
    try {
      const task = await getBackupDownloadTask({ schemaVersion: 1, requestId: crypto.randomUUID(), target: instanceTarget,
        expectedRecordVersion: recordVersion, backupId, operationId, destination });
      if (!poll) return;
      if (typeof task.sizeBytes === "number") progress.sizeBytes = task.sizeBytes;
      if (typeof task.bytesTransferred === "number") progress.bytesTransferred = task.bytesTransferred;
      if (task.status === "interrupted" || task.status === "failed") {
        progress.status = "interrupted";
        progress.error = "下载中断，可重试";
      }
    } catch {
      // 状态文件可能尚未写入，保持当前进度
    }
  }, 800);
  try {
    const result = await queueSsh(() => downloadInstanceBackup({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: instanceTarget, expectedRecordVersion: recordVersion, backupId, operationId, destination }));
    stopPolling();
    progress.status = "succeeded";
    progress.bytesTransferred = result.bytesTransferred ?? progress.bytesTransferred;
    progress.sizeBytes = result.sizeBytes ?? progress.sizeBytes;
    progress.error = "";
    window.setTimeout(close, 3500);
  } catch (cause) {
    stopPolling();
    const failure = describeError(cause);
    progress.status = "interrupted";
    progress.error = failure.message;
    if (requestEpoch !== epoch || targetKey.value !== requestKey || server.value?.recordVersion !== recordVersion) close();
  } finally {
    activeDownloadIds.delete(operationId);
  }
}

async function importBackup() {
  if (workspace.preview.value || importing.value) return;
  const selectionKey = targetKey.value;
  const recordVersion = server.value?.recordVersion;
  const requestEpoch = epoch;
  const path = await pickBackupImportPath();
  const connection = server.value; const instanceTarget = target.value; const expectedRevision = revision.value;
  if (!path || !connection || !instanceTarget || expectedRevision === null
    || connection.recordVersion !== recordVersion || targetKey.value !== selectionKey
    || requestEpoch !== epoch) return;
  await runImport(path, crypto.randomUUID(), connection.recordVersion, expectedRevision, instanceTarget);
}

async function retryImport() {
  const task = importTask.value; const connection = server.value; const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!task || task.status !== "failed" || !connection || !instanceTarget
    || expectedRevision === null || workspace.preview.value) return;
  await runImport(task.path, task.operationId, connection.recordVersion, expectedRevision, instanceTarget);
}

async function runImport(path: string, operationId: string, recordVersion: number, expectedRevision: number,
  instanceTarget: NonNullable<typeof target.value>) {
  const requestEpoch = epoch;
  const requestKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  importTask.value = { path, operationId, status: "running" };
  error.value = "";
  try {
    const result = await queueSsh(() => importInstanceBackup({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: instanceTarget, expectedRecordVersion: recordVersion, expectedRevision, operationId, localPath: path }));
    if (requestEpoch !== epoch || targetKey.value !== requestKey || server.value?.recordVersion !== recordVersion) return;
    if (result.schemaVersion !== 1 || result.status !== "succeeded" || result.operationId !== operationId
      || result.target.serverId !== instanceTarget.serverId || result.target.hostId !== instanceTarget.hostId
      || result.target.instanceId !== instanceTarget.instanceId) throw new Error("备份导入回执身份不匹配");
    importTask.value = { path, operationId, status: "succeeded" };
    await refresh();
  } catch (cause) {
    if (requestEpoch === epoch && targetKey.value === requestKey) {
      error.value = describeError(cause);
      importTask.value = { path, operationId, status: "failed" };
    }
  }
}

function editNote(item: BackupSummary) {
  if (workspace.preview.value) return;
  error.value = "";
  noteError.value = "";
  noteDraft.value = item.reason;
  noteTarget.value = item;
}

async function saveNote() {
  const current = noteTarget.value; const connection = server.value; const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!current || !connection || !instanceTarget || expectedRevision === null
    || noteBusy.value || workspace.preview.value) return;
  const requestEpoch = epoch;
  const requestKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  noteBusy.value = true; noteError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await queueSsh(() => updateInstanceBackupNote({ schemaVersion: 1, requestId,
      target: instanceTarget, expectedRecordVersion: connection.recordVersion, expectedRevision,
      backupId: current.backupId, note: noteDraft.value, expectedNote: current.reason }));
    if (requestEpoch !== epoch || targetKey.value !== requestKey
      || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.requestId !== requestId || response.backupId !== current.backupId
      || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId) throw new Error("备份备注回执身份不匹配");
    items.value = items.value.map(item => item.backupId === current.backupId
      ? { ...item, reason: response.reason }
      : item);
    noteTarget.value = null;
  } catch (cause) {
    if (requestEpoch === epoch) noteError.value = describeError(cause).message;
  } finally {
    if (requestEpoch === epoch) noteBusy.value = false;
  }
}

async function prepareRestore(item: BackupSummary) {
  if (workspace.preview.value || item.verification !== "verified" || item.restorability !== "restorable") return;
  error.value = "";
  restoreError.value = "";
  restoreProtection.value = true;
  restoreTarget.value = item;
  restoreModWarnings.value = [];
  const connection = server.value;
  const instanceTarget = target.value;
  const enabled = (item.mods ?? []).filter(ref => ref.enabled);
  if (!connection?.hostId || !instanceTarget || !enabled.length) return;
  try {
    const response = await listHostMods({ schemaVersion: 1, requestId: crypto.randomUUID(),
      target: { serverId: connection.serverId, hostId: connection.hostId }, expectedRecordVersion: connection.recordVersion });
    if (restoreTarget.value !== item) return;
    const versions = new Map(response.items.map(entry => [entry.workshopId, new Set<string>()]));
    for (const entry of response.items) versions.get(entry.workshopId)?.add(entry.contentDigest);
    restoreModWarnings.value = [];
    for (const ref of enabled) {
      const digests = versions.get(ref.workshopId);
      if (!digests?.size) restoreModWarnings.value.push(`缺少 MOD ${ref.workshopId}`);
      else if (ref.artifactDigest && !digests.has(ref.artifactDigest)) restoreModWarnings.value.push(`MOD ${ref.workshopId} 将使用主机库最新版本`);
    }
  } catch (cause) {
    if (restoreTarget.value === item) restoreModWarnings.value = [];
  }
}

function prepareDelete(item: BackupSummary) {
  if (workspace.preview.value || !item.checksum) return;
  error.value = "";
  deleteError.value = "";
  deleteTarget.value = item;
}

async function restoreBackup() {
  const current = restoreTarget.value; const connection = server.value; const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!current || !connection || !instanceTarget || expectedRevision === null || restoreBusy.value || workspace.preview.value) return;
  restoreBusy.value = true; restoreError.value = ""; const operationId = crypto.randomUUID();
  const requestEpoch = epoch;
  const requestTarget = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  try { const response = await queueSsh(() => restoreInstanceBackup({ schemaVersion: 1, requestId: crypto.randomUUID(), target: instanceTarget,
    expectedRecordVersion: connection.recordVersion, expectedRevision, backupId: current.backupId, operationId,
    createProtectionBackup: restoreProtection.value }));
    if (response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || response.task.operationId !== operationId
      || response.task.operation !== "backupRestore") throw new Error("恢复任务回执身份不匹配");
    taskStore.receive(response.task);
    if (requestEpoch !== epoch || targetKey.value !== requestTarget || server.value?.recordVersion !== connection.recordVersion) return;
    restoreTarget.value = null;
  } catch (cause) { if (requestEpoch === epoch) restoreError.value = describeError(cause).message; }
  finally { if (requestEpoch === epoch) restoreBusy.value = false; }
}

async function deleteBackup() {
  const backup = deleteTarget.value; const connection = server.value; const instanceTarget = target.value;
  const expectedRevision = revision.value;
  if (!backup || !backup.checksum || !connection || !instanceTarget || expectedRevision === null || deleteBusy.value || workspace.preview.value) return;
  const requestEpoch = epoch; const requestTarget = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  deleteBusy.value = true; deleteError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await queueSsh(() => deleteInstanceBackup({schemaVersion:1,requestId,target:instanceTarget,
      expectedRecordVersion:connection.recordVersion,expectedRevision,backupId:backup.backupId,expectedChecksum:backup.checksum!}));
    if (requestEpoch !== epoch || targetKey.value !== requestTarget || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.requestId !== requestId || !response.deleted || response.backupId !== backup.backupId
      || `${response.target.serverId}:${response.target.hostId}:${response.target.instanceId}` !== requestTarget) throw new Error("备份删除回执身份不匹配");
    deleteTarget.value = null;
    await refreshPage();
  } catch (cause) { if (requestEpoch === epoch) deleteError.value = describeError(cause).message; }
  finally { if (requestEpoch === epoch) deleteBusy.value = false; }
}

watch(activeTask, async (next, previous) => {
  if (!next || ["queued", "running"].includes(next.status)) return;
  createBusy.value = false;
  pendingOperationId = "";
  if (previous?.taskId === next.taskId && previous.status === next.status) return;
  if (loading.value || previewLoading.value) return;
  if (next.operation === "backupRestore" && next.status === "succeeded") {
    const revisionBeforeRestore = revision.value;
    await refreshInstanceSummaries();
    if (revisionBeforeRestore !== null && (revision.value === null || revision.value <= revisionBeforeRestore)) {
      if (!error.value) error.value = "恢复已完成，但专服新配置版本尚未确认；备份列表未刷新";
      return;
    }
    return;
  }
  await refreshPage();
}, { flush: "sync" });

async function cancelActiveTask() {
  const connection = server.value, instanceTarget = target.value, task = activeTask.value;
  if (!connection || !instanceTarget || !task || !["backupCreate", "backupRestore"].includes(task.operation) || cancelBusy.value) return;
  cancelBusy.value = true;
  cancelMessage.value = "";
  try {
    const request = { schemaVersion: 1 as const, requestId: crypto.randomUUID(), target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, operationId: task.operationId };
    const response = task.operation === "backupCreate"
      ? await cancelInstanceBackup(request)
      : await cancelInstanceRestore(request);
    taskStore.receive(response.task);
    cancelMessage.value = "取消请求已提交，等待任务安全结束";
  } catch (cause) {
    error.value = describeError(cause);
  } finally {
    cancelBusy.value = false;
  }
}

function openCreateDialog() {
  if (workspace.preview.value || createBusy.value) return;
  createOpen.value = true;
  if (pendingCreate) {
    mode.value = pendingCreate.captureMode;
    createNote.value = pendingCreate.reason;
    return;
  }
  mode.value = null;
  createNote.value = "";
}

function chooseMode(captureMode: BackupCaptureMode) {
  mode.value = captureMode;
}

function modeStatus(captureMode: BackupCaptureMode) {
  if (previewLoading.value && mode.value === captureMode) return { label: "校验中", tone: "muted" as const };
  const current = previews.value[captureMode];
  if (!current) return { label: "待校验", tone: "muted" as const };
  return current.allowed ? { label: "可备份", tone: "success" as const } : { label: "不可备份", tone: "danger" as const };
}

async function createBackup() {
  const connection = server.value;
  const instanceTarget = target.value;
  const currentPreview = preview.value;
  const expectedRevision = pendingCreate?.revision ?? revision.value;
  const requestedMode = pendingCreate?.captureMode ?? mode.value;
  if (!connection || !instanceTarget || expectedRevision === null || !requestedMode || createBusy.value || workspace.preview.value) return;
  if (!pendingCreate && (!currentPreview || !currentPreview.allowed
    || currentPreview.configRevision !== expectedRevision || currentPreview.captureMode !== requestedMode)) return;
  const operationId = pendingOperationId || crypto.randomUUID();
  pendingOperationId = operationId;
  pendingCreate ??= { requestId: crypto.randomUUID(), backupId: crypto.randomUUID(),
    captureMode: requestedMode, revision: expectedRevision,
    reason: createNote.value.trim() || "手动创建" };
  const { requestId, backupId, captureMode, reason, revision: requestRevision } = pendingCreate;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  createBusy.value = true;
  error.value = "";
  previews.value = { ...previews.value, [requestedMode]: null };
  try {
    const result = await queueSsh(() => createInstanceBackup({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: requestRevision, captureMode,
      operationId, backupId, reason }));
    if (result.schemaVersion !== 1 || result.requestId !== requestId
      || result.target.serverId !== instanceTarget.serverId || result.target.hostId !== instanceTarget.hostId
      || result.target.instanceId !== instanceTarget.instanceId || result.task.operationId !== operationId
      || !taskBelongsHere(result.task, targetKey)) throw new Error("备份任务回执身份不匹配");
    taskStore.receive(result.task);
    if (target.value === null || server.value?.recordVersion !== connection.recordVersion
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey) return;
    pendingCreate = null;
    pendingOperationId = "";
    createOpen.value = false;
    createNote.value = "";
    previews.value = { ...previews.value, [captureMode]: null };
  } catch (cause) {
    if (target.value && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey) {
      error.value = describeError(cause);
      createBusy.value = false;
    }
  }
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
function modeLabel(value: BackupCaptureMode) { return value === "cold" ? "冷备份" : "准热备份"; }
function verificationLabel(value: BackupSummary["verification"]) {
  return value === "verified" ? "已校验" : value === "pending" ? "校验中" : "校验失败";
}
function verificationTone(value: BackupSummary["verification"]) {
  return value === "verified" ? "success" : value === "failed" ? "danger" : "muted";
}
function taskErrorLabel(code: string | null | undefined, stage: string, operation: string,
  details: Record<string, unknown> = {}) {
  const rollbackSucceeded = details.rollbackSucceeded === true;
  const labels: Record<string, string> = {
    BACKUP_STOP_FAILED: "无法确认分片全部停止，备份未报告成功",
    BACKUP_RESTART_FAILED: rollbackSucceeded
      ? "恢复后的专服未能通过启动核验；原部署已回滚"
      : "无法确认恢复后的专服启动状态，请检查专服现场",
    BACKUP_RECOVERY_REQUIRED: "任务中断后无法自动确认运行状态，请检查专服现场",
    BACKUP_ROLLBACK_FAILED: "恢复失败且自动回滚未能完成；请勿重复恢复，先检查专服和恢复现场",
    BACKUP_RESTORE_WORKER_FAILED: "恢复 worker 执行失败；请查看错误码和失败阶段",
    BACKUP_RESTORE_STAGING_FAILED: "恢复文件准备失败；原部署应保留，请查看错误码和失败阶段",
    WORLD_TOKEN_MISSING: "恢复配置没有可用的本机 Klei Token 引用；请在当前世界配置中保存 Token 后重试",
    WORLD_SECRET_FILE_INVALID: "本机受保护的 Token 文件缺失或无效；请重新保存 Token 后重试",
    WORLD_CONFIG_STATE_INVALID: "当前受保护的凭据引用无效；请重新保存 Token 后重试",
    BACKUP_SPACE_INSUFFICIENT: "远端可用空间不足",
    BACKUP_SOURCE_CHANGED: "采集期间有文件发生变化，归档未通过校验",
    BACKUP_ID_EXISTS: "备份编号已存在，原备份未覆盖",
    BACKUP_WORKER_FAILED: "备份任务启动失败，任务现场已保留",
    BACKUP_WORKER_NOT_STARTED: "备份 worker 未开始执行，专服未进入备份阶段请刷新任务状态后重新创建备份",
    BACKUP_WORLD_DATA_MISSING: "专服没有可备份的世界存档；请确认世界已生成并至少保存过一次",
    BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING: "旧备份缺少配置快照，且当前已应用配置版本不同；恢复已阻止",
    BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID: "备份中的已应用配置快照校验失败；恢复已阻止",
    MOD_RELEASE_UNAVAILABLE: "主机库中没有可用的 MOD 版本，恢复已停止",
  };
  const message = code ? labels[code] ?? "任务失败，任务现场已保留" : "任务失败，任务现场已保留";
  const stageLabel = activeTaskStageLabel({ operation, stage });
  const rollbackStages: Record<string, string> = {
    restoreFiles: "恢复原文件", restoreActiveRelease: "写回活动 Release", restoreManifest: "写回实例清单",
    readRestoreTransaction: "读取回滚记录", restoreDesiredWorld: "写回期望世界配置",
    restoreDeployedWorld: "写回已部署世界配置", removeDesiredMods: "移除新 MOD 期望快照",
    restoreDesiredMods: "写回期望 MOD 快照", removeDeployedMods: "移除新 MOD 部署快照",
    restoreDeployedMods: "写回已部署 MOD 快照", restoreCompose: "写回 Compose 配置",
    recreateOriginalContainers: "重建原容器",
    restartOriginalContainers: "启动原容器", verifyOriginalRuntime: "核验原专服运行状态",
    verifyOriginalStoppedState: "确认原停服状态", complete: "完成",
  };
  const rollbackStage = typeof details.rollbackStage === "string" ? rollbackStages[details.rollbackStage] : null;
  const rollbackFailure = typeof details.rollbackFailure === "string"
    && /^[A-Z0-9_]{1,80}$/.test(details.rollbackFailure) ? details.rollbackFailure : null;
  const rollbackHealth = details.rollbackHealth === "sameDegradedStateAsBeforeRestore"
    ? "；原专服在恢复前已存在相同运行异常"
    : details.rollbackHealth === "degradedStateBaselineUnavailable"
      ? "；原专服已回滚，但恢复前运行基线不可用，当前仍处于降级状态"
      : "";
  const failureSteps: Record<string, string> = {
    readBackupMetadata: "读取备份元数据", inspectBackupArchive: "校验备份归档", extractBackupArchive: "解包备份",
    verifyWorldData: "校验世界存档",
    readWorldSnapshot: "读取世界配置快照", readModSnapshot: "读取 MOD 快照", rebaseSnapshots: "校验配置快照",
    restoreCredentials: "重建本机凭据文件", assignRuntimeOwnership: "设置运行目录权限",
    validateModReleaseSnapshot: "校验 MOD Release 快照", validateDstRelease: "校验 DST Release",
    writeRestoreTransaction: "写入恢复事务记录", stopCurrentRuntime: "停止当前专服",
    renderRestoredCompose: "生成恢复版 Compose", readCurrentCompose: "读取当前 Compose",
    createProtectionPoint: "创建恢复前保护点", commitRestoredFiles: "提交恢复文件",
    createRestoredContainers: "创建恢复版容器", startRestoredContainers: "启动恢复版专服",
    verifyRestoredRuntime: "核验恢复版专服",
    stop: "停止当前专服", start: "启动恢复版专服", verify: "核验恢复版专服",
  };
  const failureStep = typeof details.failureStep === "string" ? failureSteps[details.failureStep] : null;
  const failureCause = typeof details.failureCause === "string"
    && /^[A-Z0-9_]{1,80}$/.test(details.failureCause) ? details.failureCause : null;
  const runtimeCheckLabel = (value: unknown) => {
    if (!value || typeof value !== "object") return null;
    const checkLabels: Record<string, string> = {
      containerInventory: "容器数量或列表", containerInspection: "容器检查", containerIdentityOrState: "分片身份或运行状态",
      containerLabels: "容器标签", shardSet: "分片集合", configuredPorts: "配置端口", serverConfiguration: "服务端配置",
      dstProcessOrUdpProbe: "DST 进程或 UDP 探测", udpSocketData: "UDP 监听数据", udpListeners: "UDP 监听端口",
      containerRuntimeState: "容器运行状态", passed: "通过",
    };
    const evidence = value as Record<string, unknown>;
    const check = typeof evidence.check === "string" ? checkLabels[evidence.check] : null;
    const shard = evidence.shard === "master" ? "Master" : evidence.shard === "caves" ? "Caves" : null;
    const missingPorts = Array.isArray(evidence.missingPorts)
      ? evidence.missingPorts.filter((port): port is number => Number.isSafeInteger(port) && port > 0 && port <= 65535).slice(0, 16)
      : [];
    const state = typeof evidence.state === "string" && /^[a-z]{1,24}$/.test(evidence.state) ? evidence.state : null;
    return [check, shard, missingPorts.length ? `缺少端口 ${missingPorts.join(", ")}` : null,
      state ? `状态 ${state}` : null].filter(Boolean).join("；") || null;
  };
  const restoreRuntime = runtimeCheckLabel(details.restoreRuntimeCheck);
  const rollbackRuntime = runtimeCheckLabel(details.rollbackRuntimeCheck);
  const rollbackDetail = typeof details.rollbackStage === "string"
    ? ` 回滚${rollbackSucceeded ? "已完成" : "未完成"}：${rollbackStage ?? "未知"}${rollbackFailure ? `；失败原因：${rollbackFailure}` : ""}${rollbackHealth}`
    : "";
  const failureDetail = failureStep ? ` 失败子步骤：${failureStep}${failureCause ? `；原因：${failureCause}` : ""}` : "";
  const runtimeDetail = [restoreRuntime ? `恢复版核验：${restoreRuntime}` : null,
    rollbackRuntime ? `原部署核验：${rollbackRuntime}` : null].filter(Boolean).join("；");
  return `${message} 错误码：${code ?? "未知"}；阶段：${stageLabel}${failureDetail}${runtimeDetail ? ` ${runtimeDetail}` : ""}${rollbackDetail}`;
}
function activeTaskStatusLabel(task: TaskSnapshot) {
  const restoring = task.operation === "backupRestore";
  if (task.status === "succeeded") return restoring ? "恢复完成" : "备份完成";
  if (task.status === "failed") return restoring ? "恢复失败" : "备份失败";
  if (task.status === "interrupted") return "结果待核对";
  return restoring ? "正在恢复" : "备份进行中";
}
function activeTaskStageLabel(task: Pick<TaskSnapshot, "operation" | "stage">) {
  if (task.operation === "backupRestore") {
    const labels: Record<string,string> = {queued:"等待任务",preflight:"校验备份与版本",stagingVerify:"准备恢复文件",stop:"停止专服",backup:"创建当前世界保护点",restoreCommit:"替换世界与配置",start:"恢复原运行状态",verify:"验证恢复结果",rollback:"回滚保护点",finalize:"完成恢复"};
    return labels[task.stage] ?? "处理恢复任务";
  }
  const labels: Record<string,string> = {queued:"等待任务",preflight:"复核目标和空间",stop:"停止专服",backup:"采集文件",backupVerify:"校验归档",start:"恢复原运行状态",verify:"核验备份",finalize:"完成任务"};
  return labels[task.stage] ?? "处理备份任务";
}

watch([targetKey, () => server.value?.recordVersion, revision, () => workspace.preview.value], () => {
  epoch++;
  listSequence++;
  previewSequence++;
  currentListRequest = "";
  currentPreviewRequest = "";
  items.value = [];
  destroyDownloadNotices();
  shownInterrupted.clear();
  activeDownloadIds.clear();
  importTask.value = null;
  previews.value = { cold: null, quasiHot: null };
  mode.value = null;
  createNote.value = "";
  createOpen.value = false;
  noteTarget.value = null;
  noteDraft.value = "";
  noteError.value = "";
  noteBusy.value = false;
  restoreTarget.value = null;
  deleteTarget.value = null;
  restoreError.value = "";
  deleteError.value = "";
  loading.value = false;
  previewLoading.value = false;
  error.value = "";
  createBusy.value = false;
  pendingOperationId = "";
  pendingCreate = null;
  contextLoading.value = revision.value === null;
  if (revision.value !== null) {
    void refreshPage();
  } else {
    void refreshInstanceSummaries();
  }
}, { immediate: true });
function closeCreateDialog(open: boolean) {
  createOpen.value = open;
  if (!open && !createBusy.value && !pendingCreate) {
    mode.value = null;
    createNote.value = "";
  }
}
onBeforeUnmount(() => {
  epoch++;
  listSequence++;
  previewSequence++;
  currentListRequest = "";
  currentPreviewRequest = "";
});

const tableItems = computed(() => [...items.value].sort((left, right) =>
  (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0)));
const columns = computed<DataTableColumns<BackupSummary>>(() => {
  void workspace.preview.value;
  void restoreBusy.value;
  void deleteBusy.value;
  void noteBusy.value;
  void downloading.value;
  return [
  { title: "时间", key: "createdAt", width: 100, ellipsis: { tooltip: true }, render: item => formatDate(item.createdAt) },
  { title: "模式", key: "captureMode", width: 72, ellipsis: true, render: item => modeLabel(item.captureMode) },
  { title: "备注", key: "reason", width: 90, ellipsis: { tooltip: true }, render: item => item.reason || "—" },
  { title: "大小", key: "sizeBytes", width: 72, ellipsis: true, render: item => formatBytes(item.sizeBytes) },
  {
    title: "校验",
    key: "verification",
    width: 72,
    ellipsis: true,
    render: item => h(StatusLabel, { tone: verificationTone(item.verification), label: verificationLabel(item.verification) }),
  },
  {
    title: "恢复能力",
    key: "restorability",
    width: 72,
    ellipsis: true,
    render: item => item.restorability === "restorable" ? "可恢复"
      : item.restorability === "missingArtifacts" ? "缺少产物"
      : item.restorability === "incompatible" ? "不兼容" : "待确认",
  },
  {
    title: "",
    key: "actions",
    width: 148,
    align: "center",
    render: item => h("div", { class: "row-actions" }, [
      h(IconButton, {
        label: "编辑备注",
        disabled: workspace.preview.value || noteBusy.value,
        onClick: () => editNote(item),
      }, () => h(Pencil, { size: 15 })),
      h(IconButton, {
        label: downloading.value ? "已有下载任务进行中" : "下载备份",
        disabled: workspace.preview.value || downloading.value,
        onClick: () => void downloadBackup(item),
      }, () => h(Download, { size: 15 })),
      h(IconButton, {
        label: item.restorability === "restorable" ? "恢复备份" : "备份不包含可恢复的世界存档",
        disabled: workspace.preview.value || restoreBusy.value || item.verification !== "verified"
          || item.restorability !== "restorable",
        onClick: () => void prepareRestore(item),
      }, () => h(RotateCcw, { size: 15 })),
      h(IconButton, {
        label: !item.checksum ? "缺少校验信息，不能删除" : deleteBusy.value ? "删除操作进行中" : "删除备份",
        disabled: workspace.preview.value || deleteBusy.value || !item.checksum,
        tone: "danger",
        onClick: () => prepareDelete(item),
      }, () => h(Trash2, { size: 15 })),
    ]),
  },
  ];
});
const rowKey = (item: BackupSummary) => item.backupId;
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="备份">
      <template #actions>
        <NButton type="primary" :disabled="createBusy || pageLoading || revision === null || workspace.preview.value" @click="openCreateDialog">
          <template #icon><Plus :size="16"/></template>
          {{ createBusy ? "备份中…" : pendingCreate ? "重试原请求" : "创建备份" }}
        </NButton>
        <NButton :disabled="pageLoading || importing || revision === null || workspace.preview.value" @click="importBackup">
          <template #icon><Upload :size="16"/></template>
          {{ importing ? "导入中…" : "导入备份" }}
        </NButton>
        <NButton :disabled="pageLoading || previewLoading || revision === null || workspace.preview.value" @click="refreshPage">
          <template #icon><RefreshCw :size="16"/></template>
          {{ pageLoading || previewLoading ? "读取中…" : "刷新" }}
        </NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <ErrorNotice title="备份操作失败" :error="error" :pending="pageLoading || previewLoading"/>
    <div class="table-wrap">
      <NDataTable :columns="columns" :data="tableItems" :bordered="false" :single-line="true" size="medium" :row-key="rowKey" :loading="pageLoading">
        <template #empty><span class="muted-text">暂无备份</span></template>
      </NDataTable>
    </div>
    <section v-if="importTask" class="backup-task-list" aria-label="备份导入任务">
      <div class="import-task">
        <span>{{ importTask.status === "succeeded" ? "导入完成" : importTask.status === "running" ? "正在导入" : "导入失败" }}</span>
        <span class="mono">{{ importFileName }}</span>
        <NButton v-if="importTask.status === 'failed'" size="small" :disabled="workspace.preview.value" @click="retryImport">重试导入</NButton>
      </div>
    </section>
<FloatingTask v-if="visibleActiveTask" label="备份任务" :tone="activeTaskTone" :summary="visibleActiveTask ? activeTaskStatusLabel(visibleActiveTask) : ''" :progress="activeTaskPercent" :dismissible="activeTaskDismissible" @dismiss="dismissActiveTask">
    <TaskPanel v-if="visibleActiveTask" dock :title="visibleActiveTask.operation === 'backupRestore' ? '恢复备份' : '创建备份'"
      :status-label="activeTaskStatusLabel(visibleActiveTask)" :stage-label="activeTaskStageLabel(visibleActiveTask)" :tone="activeTaskTone"
      :queue-position="visibleActiveTask.queuePosition" :error="visibleActiveTask.error"
      :error-detail="visibleActiveTask.error ? taskErrorLabel(visibleActiveTask.error.code, visibleActiveTask.stage, visibleActiveTask.operation, visibleActiveTask.error.details) : undefined"
      :cancellable="['backupCreate','backupRestore'].includes(visibleActiveTask.operation) && !!visibleActiveTask.cancelAllowed && ['queued','running'].includes(visibleActiveTask.status)"
      :cancel-label="visibleActiveTask.operation === 'backupRestore' ? '取消恢复' : '取消备份'" :busy="cancelBusy" @cancel="cancelActiveTask">
      <span v-if="visibleActiveTask.result?.backupId" class="mono muted-text">{{ visibleActiveTask.result.backupId }}</span>
      <span v-if="visibleActiveTask.result?.modsWarnings?.length" class="backup-warning">{{ visibleActiveTask.result.modsWarnings.map(code => code.startsWith('MOD_MISSING:') ? `缺少 MOD ${code.slice(12)}` : `MOD ${code.slice(19)} 已使用主机库最新版本`).join('；') }}缺少 MOD 时世界可能崩溃</span>
      <span v-if="cancelMessage" class="muted-text">{{ cancelMessage }}</span>
    </TaskPanel>
</FloatingTask>
<AppDialog :open="createOpen" title="创建备份" description="冷备份:需重启世界；准热备份:无需重启世界,有延迟" @update:open="closeCreateDialog">
      <div class="backup-mode-options">
        <button v-for="option in [{ value: 'cold', label: '冷备份' }, { value: 'quasiHot', label: '准热备份' }]" :key="option.value"
          type="button" class="backup-mode-option" :class="{ selected: mode === option.value }" :aria-pressed="mode === option.value"
          :disabled="createBusy || workspace.preview.value" @click="chooseMode(option.value as BackupCaptureMode)">
          <strong>{{ option.label }}</strong>
          <StatusLabel :tone="modeStatus(option.value as BackupCaptureMode).tone" :label="modeStatus(option.value as BackupCaptureMode).label"/>
        </button>
      </div>
      <label class="backup-note-field">
        <span>备注</span>
        <NInput v-model:value="createNote" :maxlength="96" show-count placeholder="可选"
          :disabled="createBusy || workspace.preview.value"/>
      </label>
      <p v-if="mode && previewLoading" class="muted-text">正在校验{{ modeLabel(mode) }}条件…</p>
      <p v-else-if="mode && preview && !preview.worldDataPresent" class="environment-error">没有可备份的世界存档，请确认世界已生成并至少保存过一次</p>
      <p v-else-if="mode && preview && !preview.allowed" class="environment-error">当前条件不满足{{ modeLabel(mode) }}</p>
      <template #footer>
        <NButton :disabled="createBusy" @click="closeCreateDialog(false)">取消</NButton>
        <NButton type="primary" :disabled="createBusy || previewLoading || !mode || !preview?.allowed || workspace.preview.value" @click="createBackup">
          {{ createBusy ? "提交中…" : "创建备份" }}
        </NButton>
      </template>
    </AppDialog>
<AppDialog :open="!!restoreTarget" title="恢复备份" @update:open="value => { if (!value && !restoreBusy) { restoreTarget = null; restoreError = ''; restoreModWarnings = []; } }">
      <p v-if="restoreTarget">将恢复 <span class="mono">{{ restoreTarget.backupId }}</span></p>
      <p class="muted-text">MOD 使用主机库当前最新版本；主机库缺少 MOD 时会提示，但不会阻止恢复</p>
      <p v-if="restoreModWarnings.length" class="backup-warning">{{ restoreModWarnings.join("；") }}缺少 MOD 时继续恢复可能导致世界崩溃</p>
      <NCheckbox v-model:checked="restoreProtection" :disabled="restoreBusy" class="backup-restore-option">恢复前创建备份</NCheckbox>
      <p class="form-hint">保护备份用于失败回滚；不勾选会直接替换当前世界</p>
      <ErrorNotice title="未能提交备份恢复" :error="restoreError" :pending="restoreBusy"/>
      <template #footer><NButton :disabled="restoreBusy" @click="restoreTarget = null; restoreError = ''; restoreModWarnings = []">取消</NButton><NButton type="primary" :disabled="restoreBusy || workspace.preview.value" @click="restoreBackup">{{ restoreBusy ? "检测并开始恢复…" : "确认恢复" }}</NButton></template>
    </AppDialog>
    <AppDialog :open="!!noteTarget" title="编辑备注" @update:open="value => { if (!value && !noteBusy) { noteTarget = null; noteError = ''; } }">
      <label class="backup-note-field">
        <span>备注</span>
        <NInput v-model:value="noteDraft" :maxlength="96" show-count autofocus placeholder="例如：换 MOD 前"
          :disabled="noteBusy"/>
      </label>
      <ErrorNotice title="保存备份备注失败" :error="noteError" :pending="noteBusy"/>
      <template #footer>
        <NButton :disabled="noteBusy" @click="noteTarget = null; noteError = ''">取消</NButton>
        <NButton type="primary" :disabled="noteBusy || workspace.preview.value" @click="saveNote">{{ noteBusy ? "保存中…" : "保存" }}</NButton>
      </template>
    </AppDialog>
    <AppDialog :open="!!deleteTarget" title="删除备份"  @update:open="value => { if (!value && !deleteBusy) { deleteTarget = null; deleteError = ''; } }">
      <p v-if="deleteTarget">确定删除 <span class="mono">{{ deleteTarget.backupId }}</span>（{{ formatBytes(deleteTarget.sizeBytes) }}）？</p>
      <ErrorNotice title="删除备份失败" :error="deleteError" :pending="deleteBusy"/>
      <template #footer><NButton :disabled="deleteBusy" @click="deleteTarget = null">取消</NButton><NButton type="error" ghost :disabled="deleteBusy || workspace.preview.value" @click="deleteBackup">{{ deleteBusy ? "删除中…" : "永久删除" }}</NButton></template>
    </AppDialog>
  </div>
</template>

<style scoped>
.backup-mode-options { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.backup-mode-option { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 56px; padding: 12px 14px; border: 1px solid var(--border-strong); border-radius: 2px; background: transparent; text-align: left; }
.backup-mode-option:hover { background: var(--surface-hover); }
.backup-mode-option.selected { border-color: var(--text); background: var(--surface-muted); }
.backup-warning { margin: var(--space-3) 0 0; color: var(--text-secondary); font-size: 13px; }
.backup-restore-option { display: flex; align-items: center; gap: var(--space-2); margin: var(--space-4) 0 var(--space-2); font-size: 13px; }
.backup-restore-option input { width: 16px; height: 16px; accent-color: var(--text); }
.table-wrap :deep(.row-actions) { gap:4px; }
.import-task { display:flex; align-items:center; gap:var(--space-3); min-height:44px; border-bottom:1px solid var(--border); }
.import-task span:nth-child(2) { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.backup-note-field { display:flex; align-items:center; gap:var(--space-3); margin:var(--space-4) 0 var(--space-2); font-size:13px; }
.backup-note-field > span { flex:none; color:var(--text-secondary); }
.backup-note-field :deep(.n-input) { flex:1; }
</style>
