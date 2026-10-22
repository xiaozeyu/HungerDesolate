<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { RefreshCw } from "@lucide/vue";
import AppDialog from "../components/AppDialog.vue";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import { getInstanceModTask, listInstanceModTasks, listInstanceMods, prepareInstanceMods, saveInstanceMods } from "../api/mods";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { describeError } from "../lib/commandError";
import type { InstanceModSummary, ModSchemaStatus } from "../types/mods";
import { getWorkshopMetadata } from "../api/workshop";
import type { WorkshopItem } from "../types/workshop";
import { normalizeWorkshopIds } from "../lib/workshopInput";
import { createSerialQueue } from "../lib/serialQueue";
import { useTasks } from "../composables/useTasks";
import type { TaskSnapshot } from "../types/contracts";

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
const prepareTask = ref<TaskSnapshot | null>(null);
const taskError = ref("");
let epoch = 0;
let sequence = 0;
let latestRequestId = "";
let workshopSequence = 0;
let taskPollTimer: ReturnType<typeof setTimeout> | undefined;

const enabledCount = computed(() => items.value.filter(item => item.desiredEnabled === true).length);
const pendingCount = computed(() => items.value.filter(item => item.desiredEnabled !== item.deployedEnabled).length);

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
  latestRequestId = "";
  items.value = [];
  revision.value = null;
  error.value = "";
  savingWorkshopId.value = null;
  workshopBusy.value = false;
  workshopItems.value = [];
  workshopError.value = "";
  addOpen.value = false;
  prepareTask.value = null;
  taskError.value = "";
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
    if (task.status === "succeeded") void refresh();
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

async function prepareMod(item: InstanceModSummary) {
  const connection = server.value;
  const instanceTarget = target.value;
  if (!connection || !instanceTarget || revision.value === null || item.desiredEnabled !== true
    || item.preparedDigest || savingWorkshopId.value !== null || workspace.preview.value
    || prepareTask.value && ["queued", "running"].includes(prepareTask.value.status)) return;
  const requestEpoch = epoch;
  const targetKey = `${instanceTarget.serverId}:${instanceTarget.hostId}:${instanceTarget.instanceId}`;
  const operationId = crypto.randomUUID();
  taskError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await queueSsh(() => prepareInstanceMods({ schemaVersion: 1, requestId, target: instanceTarget,
      expectedRecordVersion: connection.recordVersion, expectedRevision: revision.value!, operationId,
      workshopIds: [item.workshopId] }));
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
    if (requestEpoch === epoch && target.value
      && `${target.value.serverId}:${target.value.hostId}:${target.value.instanceId}` === targetKey) taskError.value = describeError(cause).message;
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
        <button class="button primary" :disabled="workspace.preview.value || !target" @click="openWorkshopDialog">添加 MOD</button>
        <button class="button" :disabled="loading || !target" @click="refresh">
          <RefreshCw :size="16" :class="{ spinning: loading }" />
          刷新
        </button>
      </template>
    </PageHeader>

    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="taskError" class="error-strip">{{ taskError }}</div>
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
        <thead><tr><th>Workshop ID</th><th>期望状态</th><th>实际部署</th><th>配置解析</th><th>版本摘要</th><th>内容</th><th class="table-action">操作</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="item.workshopId">
            <td class="mono">{{ item.workshopId }}</td>
            <td><StatusLabel :tone="item.desiredEnabled === null ? 'warning' : item.desiredEnabled ? 'success' : 'muted'" :label="item.desiredEnabled === null ? '已移出期望' : item.desiredEnabled ? '启用' : '禁用'" /></td>
            <td><StatusLabel :tone="item.deployedEnabled === item.desiredEnabled ? 'success' : 'warning'" :label="item.deployedEnabled === item.desiredEnabled ? '一致' : '待应用'" /></td>
            <td><StatusLabel :tone="statusTone(item.schema.status)" :label="statusLabel(item.schema.status)" /></td>
            <td class="mono">{{ item.schema.metadata.version ?? '未知' }}</td>
            <td><StatusLabel :tone="item.preparedDigest ? 'success' : 'warning'" :label="item.preparedDigest ? '已准备' : '未准备'" /></td>
            <td class="table-action">
              <button v-if="item.desiredEnabled === true && !item.preparedDigest" class="text-button"
                :disabled="workspace.preview.value || savingWorkshopId !== null || !!prepareTask && ['queued', 'running'].includes(prepareTask.status)"
                @click="prepareMod(item)">{{ taskIncludesWorkshop(prepareTask, item.workshopId) && prepareTask?.status === 'running' ? '准备中…' : '准备内容' }}</button>
              <span v-else-if="item.preparedDigest" class="muted-text">固定内容</span>
              <span v-else class="muted-text">启用后可准备</span>
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
  </div>
</template>
