<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import { RefreshCw, Users } from "@lucide/vue";
import { NButton, NDataTable, type DataTableColumns } from "naive-ui";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import LoadingState from "../components/LoadingState.vue";
import AppDialog from "../components/AppDialog.vue";
import { useInstancePlayers } from "../composables/useInstancePlayers";
import { usePageTarget } from "../composables/usePageTarget";
import { useWorkspace } from "../composables/useWorkspace";
import { characterLabel } from "../lib/characters";
import { describeError } from "../lib/commandError";
import { notifyError, notifyInfo, notifySuccess } from "../naive/notify";
import {
  installInstancePlayerBridge, previewInstancePlayerBridge,
  type PlayerBridgeStatusResponse, type PlayerEntry,
} from "../api/players";

const { server, target, instanceName } = usePageTarget();
const workspace = useWorkspace();
const { data, error, loading, stale, refresh } = useInstancePlayers();
let refreshFailureNotified = false;
watch(data, () => { refreshFailureNotified = false; });
watch(() => data.value?.freshness, (freshness, previous) => {
  if (!freshness) return;
  if (freshness === "stale" && previous !== "stale") {
    notifyInfo("玩家状态可能已过期", "超过 30 秒未更新。");
  } else if (freshness === "fresh" && previous === "stale") {
    notifySuccess("玩家状态已更新");
  }
});
watch(stale, value => {
  if (!value || refreshFailureNotified) return;
  refreshFailureNotified = true;
  notifyError("玩家列表刷新失败", "当前显示上次结果。");
});
const enableOpen = ref(false);
const enableLoading = ref(false);
const enableBusy = ref(false);
const enableError = ref("");
const enablePreview = ref<PlayerBridgeStatusResponse | null>(null);
let epoch = 0;
let sequence = 0;
const targetKey = () => {
  const current = target.value;
  return current ? `${current.serverId}:${current.hostId}:${current.instanceId}:${server.value?.recordVersion ?? 0}` : "";
};
function isCurrent(ticket: number, requestEpoch: number, key: string) {
  return ticket === sequence && requestEpoch === epoch && key === targetKey();
}

const players = computed<PlayerEntry[]>(() => data.value?.data.players ?? []);
const bridgeDisabled = computed(() => data.value?.freshness === "disabled");
const canEnable = computed(() => !!data.value && bridgeDisabled.value && !!target.value
  && !!server.value && !workspace.preview.value && data.value.configRevision >= 0);
const shardLabel = (shard: PlayerEntry["shard"]) => shard === "Master" ? "主世界" : "洞穴";
const timeLabel = (value: number) => new Date(value * 1000).toLocaleTimeString();
const columns = computed<DataTableColumns<PlayerEntry>>(() => [
  { title: "玩家", key: "name", render: row => h("strong", null, row.name) },
  { title: "角色", key: "character", render: row => characterLabel(row.character) },
  { title: "分片", key: "shard", render: row => shardLabel(row.shard) },
  { title: "更新", key: "capturedAtUnixSeconds", render: row => h("span", { class: "mono muted-text" }, timeLabel(row.capturedAtUnixSeconds)) },
]);
const rowKey = (row: PlayerEntry) => row.userId;

async function openEnable() {
  const connection = server.value, current = target.value, response = data.value;
  if (!connection || !current || !response || workspace.preview.value) return;
  const ticket = ++sequence, requestEpoch = epoch, key = targetKey();
  enableOpen.value = true;
  enableLoading.value = true;
  enableError.value = "";
  enablePreview.value = null;
  const requestId = crypto.randomUUID();
  try {
    const preview = await previewInstancePlayerBridge({ schemaVersion: 1, requestId, target: current,
      expectedRecordVersion: connection.recordVersion, expectedRevision: response.configRevision });
    if (!isCurrent(ticket, requestEpoch, key)) return;
    if (preview.requestId !== requestId || preview.target.serverId !== current.serverId
        || preview.target.hostId !== current.hostId || preview.target.instanceId !== current.instanceId
        || preview.configRevision !== response.configRevision) throw new Error("启用预览响应不匹配");
    enablePreview.value = preview;
  } catch (cause) {
    if (isCurrent(ticket, requestEpoch, key)) enableError.value = describeError(cause).message;
  } finally {
    if (isCurrent(ticket, requestEpoch, key)) enableLoading.value = false;
  }
}

async function confirmEnable() {
  const connection = server.value, current = target.value, preview = enablePreview.value;
  if (!connection || !current || !preview || !preview.allowed || enableBusy.value) return;
  const ticket = ++sequence, requestEpoch = epoch, key = targetKey();
  enableBusy.value = true;
  enableError.value = "";
  const requestId = crypto.randomUUID();
  try {
    const response = await installInstancePlayerBridge({ schemaVersion: 1, requestId, target: current,
      expectedRecordVersion: connection.recordVersion, expectedRevision: preview.configRevision });
    if (!isCurrent(ticket, requestEpoch, key)) return;
    if (response.requestId !== requestId || response.target.serverId !== current.serverId
        || response.target.hostId !== current.hostId || response.target.instanceId !== current.instanceId
        || !response.bridge || !response.allowed) throw new Error("启用结果不匹配");
    enableOpen.value = false;
    notifySuccess("玩家列表已启用", "重启或启动专服后生效。");
    await refresh();
  } catch (cause) {
    if (isCurrent(ticket, requestEpoch, key)) enableError.value = describeError(cause).message;
  } finally {
    if (isCurrent(ticket, requestEpoch, key)) enableBusy.value = false;
  }
}

watch([() => target.value?.instanceId, () => server.value?.recordVersion], () => {
  epoch += 1;
  sequence += 1;
  enableOpen.value = false;
  enablePreview.value = null;
  enableError.value = "";
}, { flush: "sync" });
</script>

<template>
  <div class="page">
    <PageHeader :eyebrow="instanceName" title="玩家">
      <template #actions>
        <NButton v-if="canEnable" size="small" type="primary" @click="openEnable">
          <template #icon><Users :size="16"/></template>
          启用玩家列表
        </NButton>
        <NButton size="small" :disabled="loading || workspace.preview.value || !target" @click="refresh">
          <template #icon><RefreshCw :size="16" :class="{ spinning: loading }"/></template>
          {{ loading ? "读取中…" : "刷新" }}
        </NButton>
      </template>
    </PageHeader>
    <PreviewNotice/>
    <div v-if="error" class="environment-error" role="alert">
      <strong>{{ error }}</strong>
    </div>
    <LoadingState v-if="loading && !data" description="读取玩家列表…"/>
    <template v-else-if="data">
      <section v-if="bridgeDisabled" class="empty-state">
        <h2>玩家同步未启用</h2>
      </section>
      <section v-else-if="data.freshness === 'unavailable'" class="empty-state">
        <h2>暂无玩家数据</h2>
      </section>
      <section v-else class="section-block">
        <div class="section-heading">
          <div>
            <p class="muted-text">更新于 {{ new Date(data.capturedAt).toLocaleTimeString() }}</p>
          </div>
        </div>
        <div v-if="!players.length" class="empty-state">
          <h2>当前无人在线</h2>
        </div>
        <div v-else class="table-wrap">
          <NDataTable :columns="columns" :data="players" :row-key="rowKey" :bordered="false" :single-line="true" size="medium"/>
        </div>
        <div v-if="data.data.warnings.length" class="muted-text">
          {{ data.data.warnings.includes('PLAYER_STATE_INVALID') ? '部分分片的玩家状态无法读取，已跳过' : '部分玩家条目无法读取，已跳过' }}
        </div>
      </section>
    </template>
    <div v-else class="empty-state" role="alert">
      <h2>暂无玩家列表</h2>
    </div>

    <AppDialog v-model:open="enableOpen" title="启用玩家列表" size="sm">
      <LoadingState v-if="enableLoading" compact description="正在检查安装条件"/>
      <div v-else-if="enableError" class="inline-error">{{ enableError }}</div>
      <template v-else-if="enablePreview">
        <ul v-if="enablePreview.blockers.length" class="environment-error">
          <li v-for="blocker in enablePreview.blockers" :key="blocker">{{ blocker === 'PLAYER_BRIDGE_OVERRIDES_UNMANAGED'
            ? '现有 MOD 覆盖文件不是应用生成的格式，无法安全合并' : '当前实例布局不支持玩家桥接' }}</li>
        </ul>
        <p v-else class="muted-text">安装后重启或启动专服即可生效</p>
      </template>
      <template #footer>
        <NButton :disabled="enableBusy" @click="enableOpen = false">取消</NButton>
        <NButton type="primary" :loading="enableBusy" :disabled="enableLoading || !enablePreview?.allowed" @click="confirmEnable">
          确认启用
        </NButton>
      </template>
    </AppDialog>
  </div>
</template>
