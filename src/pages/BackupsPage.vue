<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Download, Plus, RefreshCw, RotateCcw, Trash2 } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import AppDialog from "../components/AppDialog.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { createInstanceBackup, deleteInstanceBackup, downloadInstanceBackup, getInstanceBackupTask, getInstanceRestoreTask, listBackupDownloadTasks, listInstanceBackupTasks, listInstanceBackups, listInstanceRestoreTasks, pickBackupDownloadPath, previewInstanceBackup, restoreInstanceBackup, type BackupCaptureMode, type BackupDownloadResponse, type BackupPreviewResponse, type BackupSummary } from "../api/instances";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { useInstanceOverview } from "../composables/useInstanceOverview";
import { describeError } from "../lib/commandError";
import { useTasks } from "../composables/useTasks";
import type { TaskSnapshot } from "../types/contracts";
import { createSerialQueue } from "../lib/serialQueue";

const workspace = useWorkspace();
const { server, target, instanceName } = usePageTarget();
const overview = useInstanceOverview({ poll: false });
const taskStore = useTasks();
const mode = ref<BackupCaptureMode>("cold");
const items = ref<BackupSummary[]>([]);
const preview = ref<BackupPreviewResponse | null>(null);
const loading = ref(false);
const previewLoading = ref(false);
const error = ref("");
const restoreError = ref("");
const deleteError = ref("");
const createBusy = ref(false);
const activeTask = ref<TaskSnapshot | null>(null);
const restoreBusy = ref(false);
const restoreTarget = ref<BackupSummary | null>(null);
const deleteTarget = ref<BackupSummary | null>(null);
const deleteBusy = ref(false);
const downloadTasks = ref<BackupDownloadResponse[]>([]);
let pendingOperationId = "";
let pendingCreate: { requestId: string; backupId: string; captureMode: BackupCaptureMode; revision: number } | null = null;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let epoch = 0;
let listSequence = 0;
let previewSequence = 0;
let currentListRequest = "";
let currentPreviewRequest = "";
const queueSsh = createSerialQueue();
const revision = computed(() => overview.data.value?.runtime?.configRevision ?? null);
const targetKey = computed(() => {
  const currentTarget = target.value;
  return currentTarget
    ? `${currentTarget.serverId}:${currentTarget.hostId}:${currentTarget.instanceId}`
    : "";
});
const latestProtectionBackupId = computed(() => items.value
  .filter(item => item.reason === "恢复前保护点")
  .sort((left, right) => right.createdAt.localeCompare(left.createdAt)
    || right.backupId.localeCompare(left.backupId))[0]?.backupId ?? null);

function isCurrent(kind: "list" | "preview", ticket: number, requestEpoch: number,
  requestId: string, serverId: string, recordVersion: number, targetKey: string) {
  const currentTarget = target.value;
  const latestSequence = kind === "list" ? listSequence : previewSequence;
  const latestRequest = kind === "list" ? currentListRequest : currentPreviewRequest;
  return ticket === latestSequence && requestEpoch === epoch && latestRequest === requestId
    && server.value?.serverId === serverId && server.value.recordVersion === recordVersion
    && currentTarget !== null && `${currentTarget.serverId}:${currentTarget.hostId}:${currentTarget.instanceId}` === targetKey;
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
      preview.value = null;
      return;
    }
    const tasksRequestId = crypto.randomUUID();
    const downloads = await queueSsh(() => listBackupDownloadTasks({ schemaVersion: 1, requestId: tasksRequestId,
      target: instanceTarget, expectedRecordVersion: connection.recordVersion }));
    if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    downloadTasks.value = downloads.filter(task => task.target.serverId === instanceTarget.serverId
      && task.target.hostId === instanceTarget.hostId && task.target.instanceId === instanceTarget.instanceId);
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
    preview.value = null;
    const restoreTasks = await queueSsh(() => listInstanceRestoreTasks({schemaVersion:1,requestId:crypto.randomUUID(),target:instanceTarget,expectedRecordVersion:connection.recordVersion}));
    if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    const latest = [...tasks, ...restoreTasks].find(task => taskBelongsHere(task.task, targetKey));
    if (latest) {
      activeTask.value = latest.task;
      taskStore.receive(latest.task);
      if (["queued", "running"].includes(latest.task.status)) {
        createBusy.value = true;
        pendingOperationId = latest.task.operationId;
        if (latest.task.operation === "backupRestore") scheduleRestorePoll(targetKey, latest.task.operationId, connection.recordVersion);
        else scheduleTaskPoll(targetKey, latest.task.operationId, connection.recordVersion);
      }
    }
  } catch (cause) {
    if (isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause).message;
  } finally {
    if (isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) loading.value = false;
  }
}

async function inspectMode() {
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
      expectedRecordVersion: connection.recordVersion, expectedRevision, captureMode: mode.value }));
    if (!isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.configRevision !== expectedRevision
      || response.captureMode !== mode.value || response.target.serverId !== instanceTarget.serverId
      || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId) {
      throw new Error("备份预览身份或版本不匹配");
    }
    preview.value = response;
  } catch (cause) {
    if (isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause).message;
  } finally {
    if (isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) previewLoading.value = false;
  }
}

async function refreshPage() {
  await refresh();
  await inspectMode();
}

function taskBelongsHere(task: TaskSnapshot, key: string) {
  return ["backupCreate", "backupRestore"].includes(task.operation) && "instanceId" in task.target
    && `${task.target.serverId}:${task.target.hostId}:${task.target.instanceId}` === key;
}

async function downloadBackup(item: BackupSummary) {
  if (workspace.preview.value) return;
  const selectionKey = targetKey.value;
  const recordVersion = server.value?.recordVersion;
  const requestEpoch = epoch;
  const destination = await pickBackupDownloadPath();
  const connection = server.value; const instanceTarget = target.value;
  if (!destination || !connection || !instanceTarget || connection.recordVersion !== recordVersion
    || targetKey.value !== selectionKey || requestEpoch !== epoch) return;
  const operationId = crypto.randomUUID();
  await runDownload(item.backupId, operationId, destination, connection.recordVersion, instanceTarget);
}

async function retryDownload(task: BackupDownloadResponse) {
  const connection = server.value; const instanceTarget = target.value;
  if (!connection || !instanceTarget || !task.destination || workspace.preview.value) return;
  await runDownload(task.backupId, task.operationId, task.destination, connection.recordVersion, instanceTarget);
}

async function runDownload(backupId:string, operationId:string, destination:string, recordVersion:number, instanceTarget:NonNullable<typeof target.value>) {
  const requestEpoch = epoch;
  const task: BackupDownloadResponse = {schemaVersion:1,target:instanceTarget,backupId,operationId,status:"running",stage:"transfer",destination};
  downloadTasks.value = [task, ...downloadTasks.value.filter(item => item.operationId !== operationId)];
  try {
    const result = await queueSsh(() => downloadInstanceBackup({schemaVersion:1,requestId:crypto.randomUUID(),target:instanceTarget,
      expectedRecordVersion:recordVersion,backupId,operationId,destination}));
    if (requestEpoch !== epoch || targetKey.value !== `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}` || server.value?.recordVersion !== recordVersion) return;
    downloadTasks.value = [result, ...downloadTasks.value.filter(item => item.operationId !== operationId)];
  } catch (cause) {
    const stillCurrent = requestEpoch === epoch && targetKey.value === `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}` && server.value?.recordVersion === recordVersion;
    if (stillCurrent) {
      const message = describeError(cause).message;
      error.value = message;
      downloadTasks.value = downloadTasks.value.map(item => item.operationId === operationId ? {...item,status:"interrupted",errorCode:"BACKUP_DOWNLOAD_INTERRUPTED"} : item);
    }
  }
}

function prepareRestore(item: BackupSummary) {
  if (workspace.preview.value || item.verification !== "verified") return;
  error.value = "";
  restoreError.value = "";
  restoreTarget.value = item;
}

function prepareDelete(item: BackupSummary) {
  if (workspace.preview.value || !item.checksum || item.backupId === latestProtectionBackupId.value) return;
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
    expectedRecordVersion: connection.recordVersion, expectedRevision, backupId: current.backupId, operationId }));
    if (requestEpoch !== epoch || targetKey.value !== requestTarget || server.value?.recordVersion !== connection.recordVersion) return;
    if (response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || response.task.operationId !== operationId
      || response.task.operation !== "backupRestore") throw new Error("恢复任务回执身份不匹配");
    activeTask.value = response.task; taskStore.receive(response.task); restoreTarget.value = null;
    scheduleRestorePoll(requestTarget, operationId, connection.recordVersion);
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

function scheduleRestorePoll(key: string, operationId: string, recordVersion: number) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => void pollRestore(key, operationId, recordVersion), 2500);
}
async function pollRestore(key: string, operationId: string, recordVersion: number) {
  const instanceTarget = target.value;
  if (!instanceTarget || !server.value || `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}` !== key) return;
  const requestEpoch = epoch; const requestId = crypto.randomUUID();
  try { const response = await queueSsh(() => getInstanceRestoreTask({ schemaVersion: 1, requestId, target: instanceTarget, expectedRecordVersion: recordVersion, operationId }));
    if (requestEpoch !== epoch || !target.value || server.value?.recordVersion !== recordVersion || !taskBelongsHere(response.task, key)) return;
    if (response.requestId !== requestId || response.target.serverId !== instanceTarget.serverId || response.target.hostId !== instanceTarget.hostId
      || response.target.instanceId !== instanceTarget.instanceId || response.task.operationId !== operationId || response.task.operation !== "backupRestore") throw new Error("恢复任务响应身份不匹配");
    activeTask.value = response.task; taskStore.receive(response.task);
    if (["queued", "running"].includes(response.task.status)) scheduleRestorePoll(key, operationId, recordVersion);
    else {
      createBusy.value = false;
      pendingOperationId = "";
      const revisionBeforeRestore = revision.value;
      await overview.refresh();
      if (requestEpoch !== epoch || targetKey.value !== key || server.value?.recordVersion !== recordVersion) return;
      const revisionAfterRefresh = revision.value;
      if (response.task.status === "succeeded" && revisionBeforeRestore !== null
        && (revisionAfterRefresh === null || revisionAfterRefresh <= revisionBeforeRestore)) {
        error.value = overview.error.value ?? "恢复已完成，但专服新配置版本尚未确认；备份列表未刷新。";
        return;
      }
      await refreshPage();
    }
  } catch (cause) { if (requestEpoch === epoch) error.value = describeError(cause).message; }
}

function scheduleTaskPoll(targetKey: string, operationId: string, recordVersion: number) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => void pollTask(targetKey, operationId, recordVersion), 2500);
}

async function pollTask(targetKey: string, operationId: string, recordVersion: number) {
  const instanceTarget = target.value;
  if (!instanceTarget || !server.value || !activeTask.value
    || `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}` !== targetKey
    || activeTask.value.operationId !== operationId) return;
  try {
    const requestId = crypto.randomUUID();
    const response = await queueSsh(() => getInstanceBackupTask({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: recordVersion, operationId }));
    const stillCurrent = target.value && server.value?.recordVersion === recordVersion
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey
      && activeTask.value?.operationId === operationId;
    if (!stillCurrent) return;
    if (response.schemaVersion !== 1 || response.requestId !== requestId || response.target.serverId !== instanceTarget.serverId
      || response.target.hostId !== instanceTarget.hostId || response.target.instanceId !== instanceTarget.instanceId
      || response.task.operationId !== operationId || !taskBelongsHere(response.task, targetKey)) {
      throw new Error("备份任务响应身份不匹配");
    }
    activeTask.value = response.task;
    taskStore.receive(response.task);
    if (["succeeded", "failed", "interrupted"].includes(response.task.status)) {
      createBusy.value = false;
      pendingOperationId = "";
      await refresh();
      await inspectMode();
      return;
    }
    scheduleTaskPoll(targetKey, operationId, recordVersion);
  } catch (cause) {
    if (activeTask.value?.operationId === operationId) {
      error.value = describeError(cause).message;
      scheduleTaskPoll(targetKey, operationId, recordVersion);
    }
  }
}

async function createBackup() {
  const connection = server.value;
  const instanceTarget = target.value;
  const currentPreview = preview.value;
  const expectedRevision = pendingCreate?.revision ?? revision.value;
  if (!connection || !instanceTarget || expectedRevision === null || createBusy.value || workspace.preview.value) return;
  if (!pendingCreate && (!currentPreview || !currentPreview.allowed
    || currentPreview.configRevision !== expectedRevision || currentPreview.captureMode !== mode.value)) return;
  const operationId = pendingOperationId || crypto.randomUUID();
  pendingOperationId = operationId;
  pendingCreate ??= { requestId: crypto.randomUUID(), backupId: crypto.randomUUID(),
    captureMode: mode.value, revision: expectedRevision };
  const { requestId, backupId, captureMode, revision: requestRevision } = pendingCreate;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  createBusy.value = true;
  error.value = "";
  preview.value = null;
  try {
    const result = await queueSsh(() => createInstanceBackup({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: requestRevision, captureMode,
      operationId, backupId, reason: "手动创建" }));
    if (target.value === null || server.value?.recordVersion !== connection.recordVersion
      || `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` !== targetKey) return;
    if (result.schemaVersion !== 1 || result.requestId !== requestId
      || result.target.serverId !== instanceTarget.serverId || result.target.hostId !== instanceTarget.hostId
      || result.target.instanceId !== instanceTarget.instanceId || result.task.operationId !== operationId
      || !taskBelongsHere(result.task, targetKey)) throw new Error("备份任务回执身份不匹配");
    activeTask.value = result.task;
    pendingCreate = null;
    pendingOperationId = "";
    taskStore.receive(result.task);
    preview.value = null;
    scheduleTaskPoll(targetKey, operationId, connection.recordVersion);
  } catch (cause) {
    if (target.value && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey) {
      error.value = describeError(cause).message;
      createBusy.value = false;
    }
  }
}

function formatBytes(value: number) {
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
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
function taskErrorLabel(code: string | null | undefined) {
  const labels: Record<string, string> = {
    BACKUP_STOP_FAILED: "无法确认分片全部停止，备份未报告成功。",
    BACKUP_RESTART_FAILED: "备份后无法确认恢复原运行状态，请检查专服运行状态。",
    BACKUP_RECOVERY_REQUIRED: "任务中断后无法自动确认运行状态，请检查专服现场。",
    BACKUP_SPACE_INSUFFICIENT: "远端可用空间不足。",
    BACKUP_SOURCE_CHANGED: "采集期间有文件发生变化，归档未通过校验。",
    BACKUP_ID_EXISTS: "备份编号已存在，原备份未覆盖。",
    BACKUP_WORKER_FAILED: "备份任务启动失败，任务现场已保留。",
    BACKUP_WORKER_NOT_STARTED: "备份 worker 未开始执行，专服未进入备份阶段。请刷新任务状态后重新创建备份。",
    BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING: "旧备份缺少配置快照，且当前已应用配置版本不同；恢复已阻止。",
    BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID: "备份中的已应用配置快照校验失败；恢复已阻止。",
  };
  return code ? labels[code] ?? "备份任务失败，任务现场已保留。" : "备份任务失败，任务现场已保留。";
}
function activeTaskStatusLabel(task: TaskSnapshot) {
  const restoring = task.operation === "backupRestore";
  if (task.status === "succeeded") return restoring ? "恢复完成" : "备份完成";
  if (task.status === "failed") return restoring ? "恢复失败" : "备份失败";
  if (task.status === "interrupted") return "结果待核对";
  return restoring ? "正在恢复" : "备份进行中";
}
function activeTaskStageLabel(task: TaskSnapshot) {
  if (task.operation === "backupRestore") {
    const labels: Record<string,string> = {queued:"等待任务",preflight:"校验备份与版本",stagingVerify:"准备恢复文件",stop:"停止专服",backup:"创建当前世界保护点",restoreCommit:"替换世界与配置",start:"恢复原运行状态",verify:"验证恢复结果",rollback:"回滚保护点",finalize:"完成恢复"};
    return labels[task.stage] ?? "处理恢复任务";
  }
  const labels: Record<string,string> = {queued:"等待任务",preflight:"复核目标和空间",stop:"停止专服",backup:"采集文件",backupVerify:"校验归档",start:"恢复原运行状态",verify:"核验备份",finalize:"完成任务"};
  return labels[task.stage] ?? "处理备份任务";
}

watch([targetKey, () => server.value?.recordVersion, revision, () => workspace.preview.value], () => {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = undefined;
  epoch++;
  listSequence++;
  previewSequence++;
  currentListRequest = "";
  currentPreviewRequest = "";
  items.value = [];
  downloadTasks.value = [];
  preview.value = null;
  restoreTarget.value = null;
  deleteTarget.value = null;
  restoreError.value = "";
  deleteError.value = "";
  loading.value = false;
  previewLoading.value = false;
  error.value = "";
  activeTask.value = null;
  createBusy.value = false;
  pendingOperationId = "";
  pendingCreate = null;
  if (revision.value !== null) {
    void refreshPage();
  }
}, { immediate: true });
watch(mode, () => { preview.value = null; void inspectMode(); });
onBeforeUnmount(() => {
  epoch++;
  listSequence++;
  previewSequence++;
  currentListRequest = "";
  currentPreviewRequest = "";
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="备份">
      <template #actions>
        <button class="button" :disabled="loading || previewLoading || revision === null || workspace.preview.value" @click="refreshPage">
          <RefreshCw :size="16"/>
          {{ loading || previewLoading ? "读取中…" : "刷新" }}
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="error-strip" role="alert">{{ error }}</div>
    <section class="backup-controls" aria-label="备份预览">
      <div class="segmented" role="group" aria-label="备份模式">
        <button :aria-pressed="mode === 'cold'" :disabled="workspace.preview.value || !!pendingCreate" @click="mode = 'cold'">冷备份</button>
        <button :aria-pressed="mode === 'quasiHot'" :disabled="workspace.preview.value || !!pendingCreate" @click="mode = 'quasiHot'">准热备份</button>
      </div>
      <button class="button primary" :disabled="(!pendingCreate && (!preview?.allowed || preview.captureMode !== mode)) || createBusy || workspace.preview.value" @click="createBackup">
        <Plus :size="16"/>
        {{ createBusy ? "备份中…" : pendingCreate ? "重试原请求" : "创建备份" }}
      </button>
    </section>
    <section v-if="activeTask" class="backup-task" aria-live="polite">
      <StatusLabel :tone="activeTask.status === 'succeeded' ? 'success' : activeTask.status === 'failed' ? 'danger' : 'muted'" :label="activeTaskStatusLabel(activeTask)"/>
      <span>{{ activeTaskStageLabel(activeTask) }}</span>
      <span v-if="activeTask.result?.backupId" class="mono">{{ activeTask.result.backupId }}</span>
      <p v-if="activeTask.error" class="backup-warning">{{ taskErrorLabel(activeTask.error.code) }}</p>
    </section>
    <section v-if="preview" class="backup-preview" aria-live="polite">
      <div class="backup-preview-heading">
        <strong>{{ modeLabel(preview.captureMode) }}</strong>
        <StatusLabel :tone="preview.allowed ? 'success' : 'danger'" :label="preview.allowed ? '条件通过' : '暂不可执行'"/>
      </div>
      <dl class="backup-facts">
        <div><dt>当前运行状态</dt><dd>{{ preview.runtimeStatus === "unknown" ? "未知" : preview.runtimeStatus === "running" ? "运行中" : preview.runtimeStatus === "stopped" ? "已停止" : "异常" }}</dd></div>
        <div><dt>世界文件</dt><dd>{{ preview.fileCount }} 个，约 {{ formatBytes(preview.estimatedArchiveBytes) }}</dd></div>
        <div><dt>可用空间</dt><dd>{{ formatBytes(preview.availableBytes) }}</dd></div>
      </dl>
    </section>
    <div v-if="loading" class="empty-state" role="status"><h2>正在读取备份</h2></div>
    <div v-else-if="!items.length" class="empty-state"><h2>暂无备份</h2></div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>模式</th><th>原因</th><th>DST build</th><th>大小</th><th>校验</th><th>恢复能力</th><th/></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="item.backupId">
            <td>{{ formatDate(item.createdAt) }}</td>
            <td>{{ modeLabel(item.captureMode) }}</td>
            <td>{{ item.reason }}</td>
            <td class="mono">{{ item.dstBuild ?? "未知" }}</td>
            <td>{{ formatBytes(item.sizeBytes) }}</td>
            <td><StatusLabel :tone="verificationTone(item.verification)" :label="verificationLabel(item.verification)"/></td>
            <td>{{ item.restorability === "restorable" ? "可恢复" : item.restorability === "missingArtifacts" ? "缺少产物" : item.restorability === "incompatible" ? "不兼容" : "待确认" }}</td>
            <td class="table-action">
              <button class="icon-button" :disabled="workspace.preview.value" title="下载" @click="downloadBackup(item)"><Download :size="15"/></button>
              <button class="icon-button" :disabled="workspace.preview.value || restoreBusy || item.verification !== 'verified'" title="恢复" @click="prepareRestore(item)"><RotateCcw :size="15"/></button>
              <button class="icon-button danger" :disabled="workspace.preview.value || deleteBusy || !item.checksum || item.backupId === latestProtectionBackupId"
                :title="item.backupId === latestProtectionBackupId ? '最新恢复保护点不能删除' : !item.checksum ? '缺少校验信息，不能删除' : deleteBusy ? '删除操作进行中' : '删除备份'"
                @click="prepareDelete(item)"><Trash2 :size="15"/></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <section v-if="downloadTasks.length" class="backup-task-list" aria-label="备份下载任务">
      <div v-for="task in downloadTasks" :key="task.operationId" class="download-task">
        <span>{{ task.status === "succeeded" ? "下载完成" : task.status === "running" ? "正在下载" : "下载中断" }}</span>
        <span class="mono">{{ task.backupId }}</span>
        <span>{{ task.bytesTransferred ? formatBytes(task.bytesTransferred) : "待开始" }}</span>
        <button v-if="task.status === 'interrupted'" class="button" :disabled="workspace.preview.value" @click="retryDownload(task)">继续下载</button>
      </div>
    </section>
    <AppDialog :open="!!restoreTarget" title="恢复备份" description="确认后会先检查归属、校验和、版本产物及空间；检查通过才会创建当前世界保护点并开始恢复。" @update:open="value => { if (!value && !restoreBusy) { restoreTarget = null; restoreError = ''; } }">
      <p v-if="restoreTarget">将恢复 <span class="mono">{{ restoreTarget.backupId }}</span>（{{ formatDate(restoreTarget.createdAt) }}）。</p>
      <p v-if="restoreError" class="error-strip" role="alert">{{ restoreError }}</p>
      <template #footer><button class="button" :disabled="restoreBusy" @click="restoreTarget = null; restoreError = ''">取消</button><button class="button primary" :disabled="restoreBusy || workspace.preview.value" @click="restoreBackup">{{ restoreBusy ? "检测并开始恢复…" : "确认恢复" }}</button></template>
    </AppDialog>
    <AppDialog :open="!!deleteTarget" title="删除备份" description="此操作会永久删除远端归档，不能撤销。" @update:open="value => { if (!value && !deleteBusy) { deleteTarget = null; deleteError = ''; } }">
      <p v-if="deleteTarget">确定删除 <span class="mono">{{ deleteTarget.backupId }}</span>（{{ formatBytes(deleteTarget.sizeBytes) }}）？</p>
      <p v-if="deleteError" class="error-strip" role="alert">{{ deleteError }}</p>
      <template #footer><button class="button" :disabled="deleteBusy" @click="deleteTarget = null">取消</button><button class="button danger" :disabled="deleteBusy || workspace.preview.value" @click="deleteBackup">{{ deleteBusy ? "删除中…" : "永久删除" }}</button></template>
    </AppDialog>
  </div>
</template>

<style scoped>
.backup-controls { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-4) 0; border-bottom: 1px solid var(--border); }
.segmented { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-control); overflow: hidden; }
.segmented button { min-height: 36px; padding: 0 var(--space-4); border: 0; background: var(--surface); color: var(--text-secondary); cursor: pointer; }
.segmented button + button { border-left: 1px solid var(--border); }
.segmented button[aria-pressed="true"] { background: var(--surface-muted); color: var(--text); font-weight: 600; }
.segmented button:focus-visible { outline: 2px solid var(--focus); outline-offset: -2px; }
.backup-preview { max-width: 720px; padding: var(--space-4) 0; border-bottom: 1px solid var(--border); }
.backup-preview-heading { display: flex; align-items: center; gap: var(--space-3); }
.backup-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-4); margin: var(--space-4) 0 0; }
.backup-facts div { min-width: 0; }
.backup-facts dt { color: var(--text-secondary); font-size: 12px; }
.backup-facts dd { margin: var(--space-1) 0 0; overflow-wrap: anywhere; }
.backup-warning { margin: var(--space-3) 0 0; color: var(--text-secondary); font-size: 13px; }
.download-task { display:flex; align-items:center; gap:var(--space-3); min-height:44px; border-bottom:1px solid var(--border); }
@media (max-width: 680px) { .backup-facts { grid-template-columns: 1fr; } }
</style>
