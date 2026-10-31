<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Package, Plus } from "@lucide/vue";
import { useRoute } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import SummaryBar from "../components/SummaryBar.vue";
import TaskPanel from "../components/TaskPanel.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useTasks } from "../composables/useTasks";
import { cancelHostModTask, deleteHostMod, listHostMods, prepareHostMods, type HostModItem } from "../api/hostMods";
import { listInstances } from "../api/instances";
import { listInstanceMods, saveInstanceMods } from "../api/mods";
import { createSerialQueue } from "../lib/serialQueue";
import { getWorkshopMetadata } from "../api/workshop";
import type { WorkshopItem } from "../types/workshop";
import { normalizeWorkshopIds } from "../lib/workshopInput";
import { describeError } from "../lib/commandError";
import { useQuery } from "../lib/queryClient";
import { formatBytes } from "../lib/environmentView";
import type { TaskSnapshot } from "../types/contracts";

const route = useRoute();
const workspace = useWorkspace();
const taskStore = useTasks();
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
  return status === "succeeded" ? "已加入主机库" : status === "failed" ? "准备失败" : status === "interrupted" ? "准备中断" : status === "cancelled" ? "已取消" : "准备中";
});
const taskActive = computed(() => !!task.value && ["queued", "running"].includes(task.value.status));
const modLibrarySummary = computed(() => [
  { label: "内容版本", value: items.value.length },
  { label: "被实例使用", value: usedCount.value },
  { label: "占用空间", value: formatBytes(totalSize.value) },
  { label: "任务", value: taskActive.value ? "进行中" : "空闲", tone: taskActive.value ? "warning" as const : "default" as const },
]);
const loading = libraryQuery.loading;
const error = libraryQuery.error;
const mutationError = ref("");
const cancelBusy = ref(false);
const cancelMessage = ref("");
const refresh = libraryQuery.refresh;
const message = ref("");
const addOpen = ref(false);
const workshopInput = ref("");
const queryBusy = ref(false);
const queryError = ref("");
const candidates = ref<WorkshopItem[]>([]);
const addingWorkshopId = ref<string | null>(null);
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
const pendingUpdateSync = ref<{ workshopId: string } | null>(null);
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

async function addCandidate(item: WorkshopItem) {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget || addingWorkshopId.value !== null || item.result !== 1) return;
  const operationId = crypto.randomUUID();
  addingWorkshopId.value = item.workshopId;
  queryError.value = "";
  try {
    const requestId = crypto.randomUUID();
    const response = await prepareHostMods({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, operationId, workshopIds: [item.workshopId],
      metadata: { [item.workshopId]: { name: item.title, author: item.author, previewUrl: item.previewUrl } } });
    if (response.requestId !== requestId || response.task.operationId !== operationId) throw new Error("主机 MOD 准备响应身份不匹配");
    taskStore.receive(response.task);
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
  pendingUpdateSync.value = { workshopId: item.workshopId };
  try {
    const metadataRequestId = crypto.randomUUID();
    const metadata = await getWorkshopMetadata({ schemaVersion: 1, requestId: metadataRequestId,
      serverId: connection.serverId, expectedRecordVersion: connection.recordVersion, workshopIds: [item.workshopId] });
    if (metadata.requestId !== metadataRequestId || metadata.serverId !== connection.serverId
        || metadata.recordVersion !== requestEpoch) throw new Error("Workshop 更新查询响应身份不匹配");
    const candidate = metadata.items.find(entry => entry.workshopId === item.workshopId);
    if (!candidate || candidate.result !== 1) throw new Error("Workshop 条目当前不可用");
    const operationId = crypto.randomUUID();
    const requestId = crypto.randomUUID();
    const response = await prepareHostMods({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, operationId, workshopIds: [item.workshopId],
      metadata: { [item.workshopId]: { name: candidate.title, author: candidate.author, previewUrl: candidate.previewUrl } } });
    if (response.requestId !== requestId || response.task.operationId !== operationId) throw new Error("主机 MOD 更新响应身份不匹配");
    taskStore.receive(response.task);
    message.value = "已提交更新，正在检查最新版本";
  } catch (cause) {
    mutationError.value = describeError(cause).message;
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
    message.value = `已同步 ${done}/${entries.length} 个专服的期望 MOD 版本；未自动应用。`;
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
    message.value = "已删除未被使用的主机 MOD 版本";
    await refresh();
  } catch (cause) {
    mutationError.value = describeError(cause).message;
  } finally {
    deleteBusy.value = false;
  }
}

watch(task, async (next, previous) => {
  if (!next || next.status === "queued" || next.status === "running") return;
  const pending = pendingUpdateSync.value;
  if (next.status !== "succeeded") {
    pendingUpdateSync.value = null;
    return;
  }
  if (previous?.taskId === next.taskId && previous.status === next.status) return;
  message.value = "主机 MOD 库已更新";
  addOpen.value = false;
  candidates.value = [];
  await refresh();
  if (!pending) return;
  pendingUpdateSync.value = null;
  const prepared = next.result?.preparedMods?.find(item => item.workshopId === pending.workshopId);
  if (!prepared) return;
  const updated = items.value.find(item => item.workshopId === prepared.workshopId
    && item.contentDigest === prepared.contentDigest);
  if (updated) void previewSync(updated);
}, { flush: "sync" });

function openAdd() {
  workshopInput.value = "";
  candidates.value = [];
  queryError.value = "";
  addOpen.value = true;
}

watch([() => server.value?.serverId, () => server.value?.recordVersion, () => workspace.preview.value], () => {
  message.value = "";
  mutationError.value = "";
  cancelMessage.value = "";
  deleteTarget.value = null;
}, { immediate: true });
watch(workshopInput, () => { candidates.value = []; queryError.value = ""; });
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="主机公共资源" title="MOD 库">
      <template #actions>
        <button class="button primary" :disabled="!target || workspace.preview.value" @click="openAdd"><Plus :size="16"/>添加 MOD</button>
      </template>
    </PageHeader>
    <p v-if="message" class="success-message" role="status">{{ message }}</p>
    <SummaryBar v-if="items.length" :items="modLibrarySummary" label="主机 MOD 摘要"/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>
    <div v-if="mutationError" class="environment-error" role="alert"><strong>{{ mutationError }}</strong></div>

    <div v-if="loading && !items.length" class="empty-state" role="status"><h2>正在读取主机 MOD 库</h2></div>
    <div v-else-if="!items.length" class="empty-state">
      <h2>主机 MOD 库为空</h2>
      <p class="muted-text">添加 Workshop MOD 后，新建专服可直接选择，不需要再次下载。</p>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>MOD</th><th>作者</th><th>大小</th><th>版本</th><th class="table-action">操作</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="`${item.workshopId}:${item.contentDigest}`">
            <td>
              <div class="host-mod-main">
                <img v-if="item.previewUrl" class="host-mod-thumb" :src="item.previewUrl" :alt="item.name ?? item.workshopId" width="40" height="40" loading="lazy"/>
                <span v-else class="host-mod-thumb host-mod-placeholder"><Package :size="17"/></span>
                <span class="host-mod-copy">
                  <strong>{{ item.name ?? item.workshopId }}</strong>
                  <small class="mono">{{ item.workshopId }}</small>
                </span>
              </div>
            </td>
            <td>{{ item.author ?? "未知" }}</td>
            <td>{{ item.sizeBytes === null ? "未知" : formatBytes(item.sizeBytes) }}</td>
            <td class="mono">{{ item.version ?? "—" }}</td>
            <td class="table-action">
              <div class="host-mod-actions">
                <button class="text-button" type="button" title="同步该版本到使用此 MOD 的专服" :disabled="workspace.preview.value || syncLoading || addingWorkshopId !== null || deleteBusy"
                  @click="previewSync(item)">同步</button>
                <button class="text-button" type="button" title="更新到 Workshop 最新版本" :disabled="workspace.preview.value || addingWorkshopId !== null || deleteBusy"
                  @click="updateHostMod(item)">{{ addingWorkshopId === item.workshopId ? "更新中…" : "更新" }}</button>
                <button class="text-button host-mod-delete" type="button" title="删除该版本" :disabled="workspace.preview.value || deleteBusy || addingWorkshopId !== null"
                  @click="deleteTarget = item">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
<FloatingTask v-if="task" label="主机 MOD">
    <TaskPanel v-if="task" title="准备主机 MOD" :status-label="taskStatusLabel" :stage-label="task.stage" :tone="taskTone"
      :queue-position="task.queuePosition" :error="task.error"
      :cancellable="!!task.cancelAllowed && ['queued','running'].includes(task.status)" :busy="cancelBusy" @cancel="cancelPrepare">
      <span v-if="task.status === 'succeeded'" class="muted-text">内容已固定，可供所有专服选择</span>
      <span v-if="cancelMessage" class="muted-text">{{ cancelMessage }}</span>
    </TaskPanel>
</FloatingTask>
    <AppDialog v-model:open="addOpen" title="添加主机 MOD">
      <div class="form-field">
        <label for="host-mod-input">Workshop ID 或链接</label>
        <textarea id="host-mod-input" v-model="workshopInput" rows="3" placeholder="每行一个 ID 或链接" @keydown.ctrl.enter="query" />
      </div>
      <div v-if="queryError" class="error-strip">{{ queryError }}</div>
      <div v-if="queryBusy" class="muted-text" role="status">正在查询 Workshop…</div>
      <div v-else-if="candidates.length" class="table-wrap">
        <table>
          <thead><tr><th>图片</th><th>条目</th><th>作者</th><th>状态</th><th class="table-action">操作</th></tr></thead>
          <tbody>
            <tr v-for="item in candidates" :key="item.workshopId">
              <td><img v-if="item.previewUrl" class="workshop-thumbnail" :src="item.previewUrl" :alt="item.title ?? item.workshopId" loading="lazy" /><span v-else class="muted-text">无图</span></td>
              <td><strong>{{ item.title ?? item.workshopId }}</strong><small class="table-sub mono">{{ item.workshopId }}</small></td>
              <td>{{ item.author ?? item.creatorSteamId ?? "未知" }}</td>
              <td><StatusLabel :tone="item.result === 1 ? 'success' : 'danger'" :label="item.result === 1 ? '可用' : '不可用'" /></td>
              <td class="table-action">
                <button class="button button-compact primary" :disabled="addingWorkshopId !== null || item.result !== 1" @click="addCandidate(item)">
                  {{ addingWorkshopId === item.workshopId ? "添加中…" : "添加到主机库" }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <TaskPanel class="dialog-task-panel" v-if="task" title="准备主机 MOD" :status-label="taskStatusLabel" :stage-label="task.stage" :tone="taskTone" :queue-position="task.queuePosition" :error="task.error" :cancellable="!!task.cancelAllowed && ['queued','running'].includes(task.status)" :busy="cancelBusy" @cancel="cancelPrepare"/>
      <template #footer>
        <button class="button" type="button" @click="addOpen = false">关闭</button>
        <button class="button primary" type="button" :disabled="queryBusy" @click="query">{{ queryBusy ? "查询中…" : "查询" }}</button>
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
.host-mod-delete { color: var(--danger); }
</style>
