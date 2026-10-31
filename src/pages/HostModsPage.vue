<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { Plus, Trash2 } from "@lucide/vue";
import { useRoute } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { deleteHostMod, getHostModTask, listHostMods, prepareHostMods, type HostModItem } from "../api/hostMods";
import { getWorkshopMetadata } from "../api/workshop";
import type { WorkshopItem } from "../types/workshop";
import { normalizeWorkshopIds } from "../lib/workshopInput";
import { describeError, type UserError } from "../lib/commandError";
import { formatBytes } from "../lib/environmentView";
import type { TaskSnapshot } from "../types/contracts";

const route = useRoute();
const workspace = useWorkspace();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const target = computed(() => server.value?.hostId ? { serverId: server.value.serverId, hostId: server.value.hostId } : null);
const items = ref<HostModItem[]>([]);
const loading = ref(false);
const stale = ref(false);
const error = ref<UserError | null>(null);
const message = ref("");
const addOpen = ref(false);
const workshopInput = ref("");
const queryBusy = ref(false);
const queryError = ref("");
const candidates = ref<WorkshopItem[]>([]);
const addingWorkshopId = ref<string | null>(null);
const task = ref<TaskSnapshot | null>(null);
const deleteTarget = ref<HostModItem | null>(null);
const deleteBusy = ref(false);
let epoch = 0;
let loadSequence = 0;
let pollTimer: ReturnType<typeof setTimeout> | undefined;

function current(sequence: number, capturedEpoch: number) {
  return sequence === loadSequence && capturedEpoch === epoch && target.value !== null;
}

async function refresh() {
  const connection = server.value;
  const hostTarget = target.value;
  if (!connection || !hostTarget) return;
  const capturedEpoch = epoch;
  const sequence = ++loadSequence;
  loading.value = true;
  error.value = null;
  try {
    const requestId = crypto.randomUUID();
    const response = await listHostMods({ schemaVersion: 1, requestId, target: hostTarget, expectedRecordVersion: connection.recordVersion });
    if (!current(sequence, capturedEpoch)) return;
    if (response.requestId !== requestId || response.recordVersion !== connection.recordVersion
        || response.target.serverId !== hostTarget.serverId || response.target.hostId !== hostTarget.hostId) {
      throw new Error("主机 MOD 库响应身份或版本不匹配");
    }
    items.value = response.items;
    stale.value = false;
  } catch (cause) {
    if (current(sequence, capturedEpoch)) {
      error.value = describeError(cause);
      stale.value = items.value.length > 0;
    }
  } finally {
    if (current(sequence, capturedEpoch)) loading.value = false;
  }
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = undefined;
}

function scheduleTaskPoll(operationId: string, capturedEpoch: number) {
  stopPolling();
  pollTimer = setTimeout(async () => {
    const connection = server.value;
    const hostTarget = target.value;
    if (!connection || !hostTarget || capturedEpoch !== epoch) return;
    try {
      const requestId = crypto.randomUUID();
      const response = await getHostModTask({ schemaVersion: 1, requestId, target: hostTarget,
        expectedRecordVersion: connection.recordVersion, operationId });
      if (capturedEpoch !== epoch || !target.value) return;
      task.value = response.task;
      if (["queued", "running"].includes(response.task.status)) scheduleTaskPoll(operationId, capturedEpoch);
      else if (response.task.status === "succeeded") {
        message.value = "主机 MOD 库已更新";
        addOpen.value = false;
        candidates.value = [];
        await refresh();
      }
    } catch (cause) {
      if (capturedEpoch === epoch) error.value = describeError(cause);
    }
  }, 1500);
}

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
  task.value = null;
  try {
    const requestId = crypto.randomUUID();
    const response = await prepareHostMods({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, operationId, workshopIds: [item.workshopId] });
    if (response.requestId !== requestId || response.task.operationId !== operationId) throw new Error("主机 MOD 准备响应身份不匹配");
    task.value = response.task;
    scheduleTaskPoll(operationId, epoch);
  } catch (cause) {
    queryError.value = describeError(cause).message;
  } finally {
    addingWorkshopId.value = null;
  }
}

async function remove() {
  const connection = server.value;
  const hostTarget = target.value;
  const item = deleteTarget.value;
  if (!connection || !hostTarget || !item || deleteBusy.value) return;
  deleteBusy.value = true;
  error.value = null;
  try {
    const requestId = crypto.randomUUID();
    const response = await deleteHostMod({ schemaVersion: 1, requestId, target: hostTarget,
      expectedRecordVersion: connection.recordVersion, workshopId: item.workshopId, contentDigest: item.contentDigest });
    if (response.requestId !== requestId) throw new Error("主机 MOD 删除响应身份不匹配");
    deleteTarget.value = null;
    message.value = "已删除未被使用的主机 MOD 版本";
    await refresh();
  } catch (cause) {
    error.value = describeError(cause);
  } finally {
    deleteBusy.value = false;
  }
}

function openAdd() {
  workshopInput.value = "";
  candidates.value = [];
  queryError.value = "";
  addOpen.value = true;
}

watch([() => server.value?.serverId, () => server.value?.recordVersion, () => workspace.preview.value], () => {
  epoch++;
  stopPolling();
  items.value = [];
  task.value = null;
  message.value = "";
  error.value = null;
  deleteTarget.value = null;
  if (server.value?.hostId) void refresh();
}, { immediate: true });
watch(workshopInput, () => { candidates.value = []; queryError.value = ""; });
onBeforeUnmount(() => { epoch++; stopPolling(); });
</script>

<template>
  <div class="page">
    <PageHeader eyebrow="主机公共资源" title="MOD 库">
      <template #actions>
        <button class="button primary" :disabled="!target || workspace.preview.value" @click="openAdd"><Plus :size="16"/>添加 MOD</button>
      </template>
    </PageHeader>
    <p v-if="message" class="success-message" role="status">{{ message }}</p>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>
    <div v-if="task" class="creation-task-state" :class="task.status">
      <StatusLabel :tone="task.status === 'failed' || task.status === 'interrupted' ? 'danger' : task.status === 'succeeded' ? 'success' : 'warning'"
        :label="task.status === 'failed' ? '准备失败' : task.status === 'interrupted' ? '准备中断' : task.status === 'succeeded' ? '已加入主机库' : '准备 MOD 中'" />
      <span>{{ task.status === 'failed' ? task.error?.message : task.status === 'interrupted' ? '任务未继续运行，请重试添加' : task.status === 'succeeded' ? '内容已固定，可供所有专服选择' : task.stage }}</span>
    </div>
    <div v-if="loading && !items.length" class="empty-state" role="status"><h2>正在读取主机 MOD 库</h2></div>
    <div v-else-if="!items.length" class="empty-state">
      <h2>主机 MOD 库为空</h2>
      <p class="muted-text">添加 Workshop MOD 后，新建专服可直接选择，不需要再次下载。</p>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead><tr><th>Workshop ID</th><th>内容摘要</th><th>大小</th><th>使用实例</th><th>准备时间</th><th class="table-action">操作</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="`${item.workshopId}:${item.contentDigest}`">
            <td class="mono">{{ item.workshopId }}</td>
            <td class="mono">{{ item.contentDigest.slice(0, 12) }}</td>
            <td>{{ item.sizeBytes === null ? "未知" : formatBytes(item.sizeBytes) }}</td>
            <td>{{ item.usedBy.length ? item.usedBy.map(entry => entry.displayName ?? entry.instanceId).join("、") : "未使用" }}</td>
            <td class="mono">{{ item.preparedAt ?? "—" }}</td>
            <td class="table-action">
              <button class="icon-button" type="button" title="删除主机 MOD" aria-label="删除主机 MOD"
                :disabled="workspace.preview.value || deleteBusy" @click="deleteTarget = item"><Trash2 :size="15"/></button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
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
      <div v-if="task" class="creation-task-state" :class="task.status">
        <StatusLabel :tone="task.status === 'failed' || task.status === 'interrupted' ? 'danger' : task.status === 'succeeded' ? 'success' : 'warning'"
          :label="task.status === 'failed' ? '准备失败' : task.status === 'interrupted' ? '准备中断' : task.status === 'succeeded' ? '已加入主机库' : '准备 MOD 中'" />
        <span>{{ task.status === 'failed' ? task.error?.message : task.status === 'interrupted' ? '任务未继续运行，请重试添加' : task.status === 'succeeded' ? '内容已固定，可供所有专服选择' : task.stage }}</span>
      </div>
      <template #footer>
        <button class="button" type="button" @click="addOpen = false">关闭</button>
        <button class="button primary" type="button" :disabled="queryBusy" @click="query">{{ queryBusy ? "查询中…" : "查询" }}</button>
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
