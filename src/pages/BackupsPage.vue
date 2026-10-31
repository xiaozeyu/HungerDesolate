<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Download, Plus, RefreshCw, RotateCcw, Trash2 } from "@lucide/vue";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import AppDialog from "../components/AppDialog.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
import TaskPanel from "../components/TaskPanel.vue";
import { listHostMods } from "../api/hostMods";
import { cancelInstanceBackup, cancelInstanceRestore, createInstanceBackup, deleteInstanceBackup, downloadInstanceBackup, listBackupDownloadTasks, listInstanceBackupTasks, listInstanceBackups, listInstanceRestoreTasks, pickBackupDownloadPath, previewInstanceBackup, restoreInstanceBackup, type BackupCaptureMode, type BackupDownloadResponse, type BackupPreviewResponse, type BackupSummary } from "../api/instances";
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
const mode = ref<BackupCaptureMode | null>(null);
const createOpen = ref(false);
const previews = ref<Record<BackupCaptureMode, BackupPreviewResponse | null>>({ cold: null, quasiHot: null });
const items = ref<BackupSummary[]>([]);
const verifiedCount = computed(() => items.value.filter(item => item.verification === "verified").length);
const totalSize = computed(() => items.value.reduce((total, item) => total + item.sizeBytes, 0));
const activeTaskTone = computed<"running"|"success"|"danger"|"warning"|"muted">(() => {
  const status = activeTask.value?.status;
  return status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "interrupted" ? "warning" : status === "cancelled" ? "muted" : "running";
});
const backupSummary = computed(() => [
  { label: "备份", value: items.value.length },
  { label: "已校验", value: verifiedCount.value, tone: "success" as const },
  { label: "占用空间", value: formatBytes(totalSize.value) },
]);
const preview = computed(() => mode.value ? previews.value[mode.value] : null);
const loading = ref(false);
const previewLoading = ref(false);
const error = ref("");
const restoreError = ref("");
const deleteError = ref("");
const createBusy = ref(false);
const activeTask = computed<TaskSnapshot | null>(() => {
  const key = targetKey.value;
  if (!key) return null;
  const tasks = taskStore.tasks.value.filter(task => taskBelongsHere(task, key))
    .sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0));
  return tasks.find(task => ["queued", "running"].includes(task.status)) ?? tasks[0] ?? null;
});
const restoreBusy = ref(false);
const restoreTarget = ref<BackupSummary | null>(null);
const restoreModWarnings = ref<string[]>([]);
const restoreProtection = ref(true);
const deleteTarget = ref<BackupSummary | null>(null);
const deleteBusy = ref(false);
const cancelBusy = ref(false);
const cancelMessage = ref("");
const downloadTasks = ref<BackupDownloadResponse[]>([]);
let pendingOperationId = "";
let pendingCreate: { requestId: string; backupId: string; captureMode: BackupCaptureMode; revision: number } | null = null;
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
      previews.value = { cold: null, quasiHot: null };
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
    previews.value = { cold: null, quasiHot: null };
    const restoreTasks = await queueSsh(() => listInstanceRestoreTasks({schemaVersion:1,requestId:crypto.randomUUID(),target:instanceTarget,expectedRecordVersion:connection.recordVersion}));
    if (!isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) return;
    const latest = [...tasks, ...restoreTasks].find(task => taskBelongsHere(task.task, targetKey));
    if (latest) {
      taskStore.receive(latest.task);
      if (["queued", "running"].includes(latest.task.status)) {
        createBusy.value = true;
        pendingOperationId = latest.task.operationId;
      }
    }
  } catch (cause) {
    if (isCurrent("list", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause).message;
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
    if (isCurrent("preview", ticket, requestEpoch, requestId, connection.serverId, connection.recordVersion, targetKey)) error.value = describeError(cause).message;
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

async function prepareRestore(item: BackupSummary) {
  if (workspace.preview.value || item.verification !== "verified") return;
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
    await overview.refresh();
    if (revisionBeforeRestore !== null && (revision.value === null || revision.value <= revisionBeforeRestore)) {
      error.value = overview.error.value ?? "恢复已完成，但专服新配置版本尚未确认；备份列表未刷新。";
      return;
    }
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
    cancelMessage.value = "取消请求已提交，等待任务安全结束。";
  } catch (cause) {
    error.value = describeError(cause).message;
  } finally {
    cancelBusy.value = false;
  }
}

function openCreateDialog() {
  if (workspace.preview.value || createBusy.value) return;
  createOpen.value = true;
  if (pendingCreate) {
    mode.value = pendingCreate.captureMode;
    return;
  }
  mode.value = null;
}

function chooseMode(captureMode: BackupCaptureMode) {
  mode.value = captureMode;
  void inspectMode(captureMode);
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
    captureMode: requestedMode, revision: expectedRevision };
  const { requestId, backupId, captureMode, revision: requestRevision } = pendingCreate;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  createBusy.value = true;
  error.value = "";
  previews.value = { ...previews.value, [requestedMode]: null };
  try {
    const result = await queueSsh(() => createInstanceBackup({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: requestRevision, captureMode,
      operationId, backupId, reason: "手动创建" }));
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
    previews.value = { ...previews.value, [captureMode]: null };
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
function taskErrorLabel(code: string | null | undefined, stage: string, operation: string,
  details: Record<string, unknown> = {}) {
  const rollbackSucceeded = details.rollbackSucceeded === true;
  const labels: Record<string, string> = {
    BACKUP_STOP_FAILED: "无法确认分片全部停止，备份未报告成功。",
    BACKUP_RESTART_FAILED: rollbackSucceeded
      ? "恢复后的专服未能通过启动核验；原部署已回滚。"
      : "无法确认恢复后的专服启动状态，请检查专服现场。",
    BACKUP_RECOVERY_REQUIRED: "任务中断后无法自动确认运行状态，请检查专服现场。",
    BACKUP_ROLLBACK_FAILED: "恢复失败且自动回滚未能完成；请勿重复恢复，先检查专服和恢复现场。",
    BACKUP_RESTORE_WORKER_FAILED: "恢复 worker 执行失败；请查看错误码和失败阶段。",
    BACKUP_RESTORE_STAGING_FAILED: "恢复文件准备失败；原部署应保留，请查看错误码和失败阶段。",
    WORLD_TOKEN_MISSING: "恢复配置没有可用的本机 Klei Token 引用；请在当前世界配置中保存 Token 后重试。",
    WORLD_SECRET_FILE_INVALID: "本机受保护的 Token 文件缺失或无效；请重新保存 Token 后重试。",
    WORLD_CONFIG_STATE_INVALID: "当前受保护的凭据引用无效；请重新保存 Token 后重试。",
    BACKUP_SPACE_INSUFFICIENT: "远端可用空间不足。",
    BACKUP_SOURCE_CHANGED: "采集期间有文件发生变化，归档未通过校验。",
    BACKUP_ID_EXISTS: "备份编号已存在，原备份未覆盖。",
    BACKUP_WORKER_FAILED: "备份任务启动失败，任务现场已保留。",
    BACKUP_WORKER_NOT_STARTED: "备份 worker 未开始执行，专服未进入备份阶段。请刷新任务状态后重新创建备份。",
    BACKUP_WORLD_CONFIG_SNAPSHOT_MISSING: "旧备份缺少配置快照，且当前已应用配置版本不同；恢复已阻止。",
    BACKUP_WORLD_CONFIG_SNAPSHOT_INVALID: "备份中的已应用配置快照校验失败；恢复已阻止。",
    MOD_RELEASE_UNAVAILABLE: "主机库中没有可用的 MOD 版本，恢复已停止。",
  };
  const message = code ? labels[code] ?? "任务失败，任务现场已保留。" : "任务失败，任务现场已保留。";
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
    ? ` 回滚${rollbackSucceeded ? "已完成" : "未完成"}：${rollbackStage ?? "未知"}${rollbackFailure ? `；失败原因：${rollbackFailure}` : ""}${rollbackHealth}。`
    : "";
  const failureDetail = failureStep ? ` 失败子步骤：${failureStep}${failureCause ? `；原因：${failureCause}` : ""}。` : "";
  const runtimeDetail = [restoreRuntime ? `恢复版核验：${restoreRuntime}` : null,
    rollbackRuntime ? `原部署核验：${rollbackRuntime}` : null].filter(Boolean).join("；");
  return `${message} 错误码：${code ?? "未知"}；阶段：${stageLabel}。${failureDetail}${runtimeDetail ? ` ${runtimeDetail}。` : ""}${rollbackDetail}`;
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
  downloadTasks.value = [];
  previews.value = { cold: null, quasiHot: null };
  mode.value = null;
  createOpen.value = false;
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
  if (revision.value !== null) {
    void refreshPage();
  }
}, { immediate: true });
function closeCreateDialog(open: boolean) {
  createOpen.value = open;
  if (!open && !createBusy.value && !pendingCreate) mode.value = null;
}
onBeforeUnmount(() => {
  epoch++;
  listSequence++;
  previewSequence++;
  currentListRequest = "";
  currentPreviewRequest = "";
});
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="备份">
      <template #actions>
        <button class="button primary" :disabled="createBusy || loading || revision === null || workspace.preview.value" @click="openCreateDialog">
          <Plus :size="16"/>
          {{ createBusy ? "备份中…" : pendingCreate ? "重试原请求" : "创建备份" }}
        </button>
        <button class="button" :disabled="loading || previewLoading || revision === null || workspace.preview.value" @click="refreshPage">
          <RefreshCw :size="16"/>
          {{ loading || previewLoading ? "读取中…" : "刷新" }}
        </button>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <SummaryBar v-if="items.length" :items="backupSummary" label="备份摘要"/>
    <div v-if="error" class="error-strip" role="alert">{{ error }}</div>
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
              <button class="icon-button danger" :disabled="workspace.preview.value || deleteBusy || !item.checksum"
                :title="!item.checksum ? '缺少校验信息，不能删除' : deleteBusy ? '删除操作进行中' : '删除备份'"
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
<FloatingTask v-if="activeTask" label="备份任务">
    <TaskPanel v-if="activeTask" :title="activeTask.operation === 'backupRestore' ? '恢复备份' : '创建备份'"
      :status-label="activeTaskStatusLabel(activeTask)" :stage-label="activeTaskStageLabel(activeTask)" :tone="activeTaskTone"
      :queue-position="activeTask.queuePosition" :error="activeTask.error"
      :cancellable="['backupCreate','backupRestore'].includes(activeTask.operation) && !!activeTask.cancelAllowed && ['queued','running'].includes(activeTask.status)"
      :cancel-label="activeTask.operation === 'backupRestore' ? '取消恢复' : '取消备份'" :busy="cancelBusy" @cancel="cancelActiveTask">
      <span v-if="activeTask.result?.backupId" class="mono muted-text">{{ activeTask.result.backupId }}</span>
      <span v-if="activeTask.error" class="backup-warning">{{ taskErrorLabel(activeTask.error.code, activeTask.stage, activeTask.operation, activeTask.error.details) }}</span>
      <span v-if="activeTask.result?.modsWarnings?.length" class="backup-warning">{{ activeTask.result.modsWarnings.map(code => code.startsWith('MOD_MISSING:') ? `缺少 MOD ${code.slice(12)}` : `MOD ${code.slice(19)} 已使用主机库最新版本`).join('；') }}。缺少 MOD 时世界可能崩溃。</span>
      <span v-if="cancelMessage" class="muted-text">{{ cancelMessage }}</span>
    </TaskPanel>
</FloatingTask>
<AppDialog :open="createOpen" title="创建备份" description="选择备份模式，选定后校验当前是否具备备份条件。" @update:open="closeCreateDialog">
      <div class="backup-mode-options">
        <button v-for="option in [{ value: 'cold', label: '冷备份' }, { value: 'quasiHot', label: '准热备份' }]" :key="option.value"
          type="button" class="backup-mode-option" :class="{ selected: mode === option.value }" :aria-pressed="mode === option.value"
          :disabled="createBusy || workspace.preview.value" @click="chooseMode(option.value as BackupCaptureMode)">
          <strong>{{ option.label }}</strong>
          <StatusLabel :tone="modeStatus(option.value as BackupCaptureMode).tone" :label="modeStatus(option.value as BackupCaptureMode).label"/>
        </button>
      </div>
      <p v-if="mode && previewLoading" class="muted-text">正在校验{{ modeLabel(mode) }}条件…</p>
      <p v-else-if="mode && preview && !preview.allowed" class="environment-error">当前条件不满足{{ modeLabel(mode) }}。</p>
      <template #footer>
        <button class="button" type="button" :disabled="createBusy" @click="closeCreateDialog(false)">取消</button>
        <button class="button primary" type="button" :disabled="createBusy || previewLoading || !mode || !preview?.allowed || workspace.preview.value" @click="createBackup">
          {{ createBusy ? "提交中…" : "创建备份" }}
        </button>
      </template>
    </AppDialog>
<AppDialog :open="!!restoreTarget" title="恢复备份" description="确认后会先检查归属、校验和、版本产物及空间；检查通过后开始恢复。" @update:open="value => { if (!value && !restoreBusy) { restoreTarget = null; restoreError = ''; restoreModWarnings = []; } }">
      <p v-if="restoreTarget">将恢复 <span class="mono">{{ restoreTarget.backupId }}</span>（{{ formatDate(restoreTarget.createdAt) }}）。</p>
      <p class="muted-text">MOD 使用主机库当前最新版本；主机库缺少 MOD 时会提示，但不会阻止恢复。</p>
      <p v-if="restoreModWarnings.length" class="backup-warning">{{ restoreModWarnings.join("；") }}。缺少 MOD 时继续恢复可能导致世界崩溃。</p>
      <label class="backup-restore-option">
        <input v-model="restoreProtection" type="checkbox" :disabled="restoreBusy" />
        <span>恢复前创建保护备份</span>
      </label>
      <p class="form-hint">保护备份用于失败回滚；不勾选会直接替换当前世界。</p>
      <p v-if="restoreError" class="error-strip" role="alert">{{ restoreError }}</p>
      <template #footer><button class="button" :disabled="restoreBusy" @click="restoreTarget = null; restoreError = ''; restoreModWarnings = []">取消</button><button class="button primary" :disabled="restoreBusy || workspace.preview.value" @click="restoreBackup">{{ restoreBusy ? "检测并开始恢复…" : "确认恢复" }}</button></template>
    </AppDialog>
    <AppDialog :open="!!deleteTarget" title="删除备份" description="此操作会永久删除远端归档，不能撤销。" @update:open="value => { if (!value && !deleteBusy) { deleteTarget = null; deleteError = ''; } }">
      <p v-if="deleteTarget">确定删除 <span class="mono">{{ deleteTarget.backupId }}</span>（{{ formatBytes(deleteTarget.sizeBytes) }}）？</p>
      <p v-if="deleteError" class="error-strip" role="alert">{{ deleteError }}</p>
      <template #footer><button class="button" :disabled="deleteBusy" @click="deleteTarget = null">取消</button><button class="button danger" :disabled="deleteBusy || workspace.preview.value" @click="deleteBackup">{{ deleteBusy ? "删除中…" : "永久删除" }}</button></template>
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
.download-task { display:flex; align-items:center; gap:var(--space-3); min-height:44px; border-bottom:1px solid var(--border); }
</style>
