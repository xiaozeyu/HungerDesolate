<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { AlertCircle, CheckCircle, Hammer, Package, RefreshCw, Trash2 } from "@lucide/vue";
import { useRoute } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import AppDialog from "../components/AppDialog.vue";
import FingerprintTrustDialog from "../components/FingerprintTrustDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useReleaseList } from "../composables/useReleaseList";
import { useBuildTasks } from "../composables/useBuildTasks";
import { useHostConnection } from "../composables/useHostConnection";
import { useHostEnvironment } from "../composables/useHostEnvironment";
import { queryClient } from "../lib/queryClient";
import { useOperations } from "../composables/useOperations";
import {
  buildDstImage,
  deleteInstanceRelease,
  previewInstanceReleaseDelete,
  verifyInstanceReleaseBase,
  type BuildStatusResponse,
  type InstanceReleaseSummary,
  type InstanceReleaseDeletePreview,
} from "../api/instances";
import { describeError } from "../lib/commandError";
import { cleanupBuildCache, previewBuildCache, type BuildCachePreview } from "../api/buildCache";
import { checkDstUpdates, getDstUpdateStatus, type HostDstUpdateStatus } from "../api/updates";
import { formatBytes } from "../lib/environmentView";

const route = useRoute();
const workspace = useWorkspace();
const operations = useOperations();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const { data, error, loading, stale, refresh } = useReleaseList(server);

const releases = computed(() => data.value?.releases ?? []);
const buildTasks = useBuildTasks();
const hostConnection = useHostConnection();
const hostEnvironment = useHostEnvironment(server, workspace.preview, (hostId, recordVersion) => workspace.bindHost(server.value?.serverId ?? "", recordVersion, hostId));
const buildStarting = ref(false);
const buildStartError = ref("");
const buildEntry = computed(() => buildTasks.forHost(server.value?.hostId));
const building = computed(() => buildStarting.value || buildEntry.value?.status.status === "building");
const buildStatus = computed<BuildStatusResponse | null>(() => buildEntry.value?.status ?? null);
const buildPercent = computed(() => buildStatus.value?.progress?.percent ?? null);
const buildStepPercent = computed(() => buildStatus.value?.progress?.stepPercent ?? null);
const buildMessage = computed(() => buildEntry.value?.message ?? "");
const buildObservationError = computed(() => buildEntry.value?.observationError ?? "");
const buildError = computed(() => buildEntry.value?.status.status === "failed"
  ? buildEntry.value.status.logTail.at(-1) ?? "构建失败，请查看主机构建日志"
  : buildEntry.value?.status.status === "notFound" ? "构建任务不存在，请重新构建"
  : buildStartError.value);
const dockerNotice = computed(() => {
  const docker = hostEnvironment.data.value?.docker;
  if (!docker) return null;
  if (docker.engineState === "notInstalled") return { title: "Docker 未安装", steps: ["前往主机环境安装 Docker 后再构建镜像"] };
  if (docker.engineState === "permissionDenied") return { title: "Docker 权限不足", steps: ["检查管理账户的 Docker 访问权限后重新检查主机环境"] };
  if (docker.engineState === "unavailable") return { title: "Docker 不可用", steps: ["检查 Docker 服务状态后重新检查主机环境"] };
  return null;
});
const pendingReleaseId = ref("");
const pendingStage = ref<"base" | "delete" | "" >("");
const actionError = ref<{ releaseId: string; message: string } | null>(null);
const actionMessage = ref<{ releaseId: string; message: string } | null>(null);
const deletePreview = ref<InstanceReleaseDeletePreview|null>(null);
const deleteTypedName = ref("");
const deleteBusy = ref(false);
const deleteError = ref("");
const cachePreview = ref<BuildCachePreview | null>(null);
const updateStatus = ref<HostDstUpdateStatus | null>(null);
const updateCheckBusy = ref(false);
const updateCheckError = ref("");
let updatePollTimer: number | undefined;
let updatePollStartedAt = 0;
let updateDisposed = false;
const reclaimableReleases = computed(() => releases.value.filter(release => release.reclaimable));
const cleanupOpen = ref(false);
const cleanupBusy = ref(false);
const cleanupError = ref("");
const cleanupResult = ref("");
let actionSequence = 0;

function targetSnapshot() {
  const value = server.value;
  if (!value?.hostId) return null;
  return { serverId: value.serverId, hostId: value.hostId, recordVersion: value.recordVersion };
}

function isCurrent(target: { serverId: string; hostId: string; recordVersion: number }, sequence?: number) {
  const value = server.value;
  return !!value && value.serverId === target.serverId && value.hostId === target.hostId
    && value.recordVersion === target.recordVersion && (sequence === undefined || actionSequence === sequence);
}

const updateStageLabel = computed(() => {
  const stage = updateStatus.value?.stage;
  if (stage === "image") return "正在准备检查镜像…";
  if (stage === "steamcmd") return "正在通过 SteamCMD 查询最新 build…";
  if (stage === "finalize") return "正在整理检查结果…";
  return "正在检查最新 DST build…";
});

function stopUpdatePoll() {
  if (updatePollTimer !== undefined) {
    window.clearTimeout(updatePollTimer);
    updatePollTimer = undefined;
  }
  updatePollStartedAt = 0;
}

function scheduleUpdatePoll() {
  if (updateDisposed) return;
  const value = server.value;
  if (!value?.hostId) return;
  if (updatePollStartedAt === 0) updatePollStartedAt = Date.now();
  if (Date.now() - updatePollStartedAt > 13 * 60 * 1000) {
    if (updateStatus.value) updateStatus.value = { ...updateStatus.value, checking: false, stage: null };
    updateCheckError.value = "检查超时，请稍后刷新或重试";
    stopUpdatePoll();
    return;
  }
  const serverId = value.serverId;
  const hostId = value.hostId;
  const expectedRecordVersion = value.recordVersion;
  if (updatePollTimer !== undefined) window.clearTimeout(updatePollTimer);
  updatePollTimer = window.setTimeout(async () => {
    updatePollTimer = undefined;
    if (server.value?.serverId !== serverId || server.value.hostId !== hostId
      || server.value.recordVersion !== expectedRecordVersion) return;
    await loadUpdateStatus();
  }, 3000);
}

async function loadUpdateStatus() {
  if (updateDisposed) return;
  const value = server.value;
  if (!value?.hostId) return;
  const expectedRecordVersion = value.recordVersion;
  const requestId = crypto.randomUUID();
  try {
    const status = await getDstUpdateStatus({ schemaVersion: 1, requestId, serverId: value.serverId,
      hostId: value.hostId, expectedRecordVersion });
    if (updateDisposed || server.value?.serverId !== value.serverId || server.value.hostId !== value.hostId
      || server.value.recordVersion !== expectedRecordVersion || status.requestId !== requestId
      || status.target.serverId !== value.serverId || status.target.hostId !== value.hostId) return;
    updateStatus.value = status;
    updateCheckError.value = "";
    if (status.checking || updateCheckBusy.value) scheduleUpdatePoll();
    else stopUpdatePoll();
  } catch (cause) {
    if (!updateDisposed && server.value?.serverId === value.serverId && server.value.hostId === value.hostId) {
      updateCheckError.value = describeError(cause).message;
      if (updateStatus.value?.checking || updateCheckBusy.value) scheduleUpdatePoll();
      else {
        updateStatus.value = null;
        stopUpdatePoll();
      }
    }
  }
}

async function checkUpdates() {
  const value = server.value;
  if (!value?.hostId || updateCheckBusy.value) return;
  if (updateStatus.value?.checking) {
    scheduleUpdatePoll();
    return;
  }
  updateCheckBusy.value = true;
  updateCheckError.value = "";
  scheduleUpdatePoll();
  const expectedRecordVersion = value.recordVersion;
  const requestId = crypto.randomUUID();
  try {
    const status = await checkDstUpdates({ schemaVersion: 1, requestId, serverId: value.serverId,
      hostId: value.hostId, expectedRecordVersion });
    if (updateDisposed || server.value?.serverId !== value.serverId || server.value.hostId !== value.hostId
      || server.value.recordVersion !== expectedRecordVersion || status.requestId !== requestId
      || status.target.serverId !== value.serverId || status.target.hostId !== value.hostId) return;
    updateStatus.value = status;
    if (status.checking) scheduleUpdatePoll();
    else stopUpdatePoll();
  } catch (cause) {
    if (!updateDisposed && server.value?.serverId === value.serverId && server.value.hostId === value.hostId) {
      updateCheckError.value = describeError(cause).message;
      await loadUpdateStatus();
    }
  } finally {
    updateCheckBusy.value = false;
  }
}

async function openDiskCleanup() {
  const target = targetSnapshot();
  if (!target || cleanupBusy.value) return;
  cleanupOpen.value = true;
  cleanupError.value = "";
  cleanupResult.value = "";
  cachePreview.value = null;
  cleanupBusy.value = true;
  const sequence = ++actionSequence;
  try {
    const response = await previewBuildCache({ schemaVersion: 1, requestId: crypto.randomUUID(), action: "preview",
      serverId: target.serverId, hostId: target.hostId, expectedRecordVersion: target.recordVersion });
    if (!isCurrent(target, sequence)) return;
    if (response.serverId !== target.serverId || response.hostId !== target.hostId) throw new Error("构建缓存预览身份不匹配");
    cachePreview.value = response;
  } catch (cause) {
    if (isCurrent(target, sequence)) cleanupError.value = describeError(cause).message;
  } finally {
    if (isCurrent(target, sequence)) cleanupBusy.value = false;
  }
}

async function confirmDiskCleanup() {
  const target = targetSnapshot();
  if (!target || cleanupBusy.value) return;
  cleanupBusy.value = true;
  cleanupError.value = "";
  cleanupResult.value = "";
  let deleted = 0;
  let skipped = 0;
  let cacheCleaned = false;
  try {
    for (const release of reclaimableReleases.value) {
      try {
        const preview = await previewInstanceReleaseDelete({
          schemaVersion: 1, requestId: crypto.randomUUID(), target: { serverId: target.serverId, hostId: target.hostId },
          expectedRecordVersion: target.recordVersion, releaseId: release.releaseId,
        });
        if (!preview.allowed) {
          skipped++;
          continue;
        }
        await deleteInstanceRelease({
          schemaVersion: 1, requestId: crypto.randomUUID(), target: { serverId: target.serverId, hostId: target.hostId },
          expectedRecordVersion: target.recordVersion, releaseId: release.releaseId,
          catalogDigest: preview.catalogDigest, typedName: release.dstBuild,
        });
        deleted++;
      } catch (cause) {
        cleanupError.value = describeError(cause).message;
        break;
      }
    }
    if (!cleanupError.value && cachePreview.value?.allowed) {
      const before = cachePreview.value.dockerCache.size;
      const outcome = await operations.run({
        kind: "buildCacheCleanup",
        target: { serverId: target.serverId, hostId: target.hostId },
        title: "清理构建缓存",
        stage: "清理构建缓存",
        work: async setStage => {
          setStage("清理 Docker 构建缓存");
          const response = await cleanupBuildCache({ schemaVersion: 1, requestId: crypto.randomUUID(), action: "cleanup",
            serverId: target.serverId, hostId: target.hostId, expectedRecordVersion: target.recordVersion });
          if (response.serverId !== target.serverId || response.hostId !== target.hostId) throw new Error("构建缓存清理身份不匹配");
          return response;
        },
        completeStage: response => before
          ? `Docker 缓存 ${before} → ${response.dockerCache.size ?? "不可用"}`
          : `Docker 缓存 ${response.dockerCache.size ?? "已清理"}`,
      });
      if (outcome.error) cleanupError.value = outcome.error.message;
      else cacheCleaned = true;
    }
    if (!cleanupError.value) {
      cleanupResult.value = `已清理 ${deleted} 个 Release${skipped ? `，跳过 ${skipped} 个` : ""}${cacheCleaned ? "，构建缓存已清理" : ""}`;
      cleanupOpen.value = false;
    }
    await refresh();
  } finally {
    cleanupBusy.value = false;
  }
}

function statusTone(release: InstanceReleaseSummary): "success" | "warning" | "danger" {
  return release.verified ? "success" : "warning";
}

function statusLabel(release: InstanceReleaseSummary) {
  return release.verified ? "已通过" : "待验证";
}
function formatReleaseDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function shortDigest(value: string) {
  return value.length > 24 ? `${value.slice(0, 19)}…` : value;
}

function closeDeleteDialog(open: boolean) {
  if (!open && !deleteBusy.value) {
    deletePreview.value = null;
    deleteTypedName.value = "";
    deleteError.value = "";
  }
}

async function previewDeleteRelease(release: InstanceReleaseSummary) {
  const target = targetSnapshot();
  const sequence = ++actionSequence;
  if (!target || pendingReleaseId.value || building.value) return;
  pendingReleaseId.value = release.releaseId;
  pendingStage.value = "delete";
  deleteError.value = "";
  try {
    const result = await previewInstanceReleaseDelete({
      schemaVersion: 1, requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion, releaseId: release.releaseId,
    });
    if (!isCurrent(target, sequence)) return;
    deletePreview.value = result;
    deleteTypedName.value = "";
  } catch (cause) {
    if (isCurrent(target, sequence)) {
      deleteError.value = describeError(cause).message;
      actionError.value = { releaseId: release.releaseId, message: deleteError.value };
    }
  } finally {
    if (isCurrent(target, sequence)) {
      pendingReleaseId.value = "";
      pendingStage.value = "";
    }
  }
}

async function confirmDeleteRelease() {
  const preview = deletePreview.value;
  const target = targetSnapshot();
  if (!preview || !target || deleteBusy.value || !preview.allowed
      || deleteTypedName.value !== preview.dstBuild || pendingReleaseId.value) return;
  const sequence = ++actionSequence;
  deleteBusy.value = true;
  pendingReleaseId.value = preview.releaseId;
  pendingStage.value = "delete";
  deleteError.value = "";
  try {
    const result = await deleteInstanceRelease({
      schemaVersion: 1, requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion, releaseId: preview.releaseId,
      catalogDigest: preview.catalogDigest, typedName: deleteTypedName.value,
    });
    if (!isCurrent(target, sequence)) return;
    deletePreview.value = null;
    deleteTypedName.value = "";
    actionMessage.value = { releaseId: result.releaseId,
      message: !result.stagingRemoved || !result.dstRemoved ? "Release 已删除，但文件清理未完成"
        : result.imageShared ? "Release 已删除，共用镜像仍被其他 Release 使用"
          : result.imageRemoved ? "Release 与镜像已删除" : "Release 已删除，镜像本已不存在" };
    await refresh();
  } catch (cause) {
    if (isCurrent(target, sequence)) {
      deleteError.value = describeError(cause).message;
      actionError.value = { releaseId: preview.releaseId, message: deleteError.value };
    }
  } finally {
    if (isCurrent(target, sequence)) {
      deleteBusy.value = false;
      pendingReleaseId.value = "";
      pendingStage.value = "";
    }
  }
}

async function verifyBase(release: InstanceReleaseSummary) {
  const target = targetSnapshot();
  const sequence = ++actionSequence;
  if (!target || pendingReleaseId.value) return;
  pendingReleaseId.value = release.releaseId;
  pendingStage.value = "base";
  actionError.value = null;
  actionMessage.value = null;
  try {
    await verifyInstanceReleaseBase({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
      releaseId: release.releaseId,
    });
    if (!isCurrent(target, sequence)) return;
    actionMessage.value = { releaseId: release.releaseId, message: "镜像验证通过" };
    await refresh();
  } catch (cause) {
    if (isCurrent(target, sequence)) actionError.value = { releaseId: release.releaseId, message: describeError(cause).message };
  } finally {
    if (isCurrent(target, sequence)) {
      pendingReleaseId.value = "";
      pendingStage.value = "";
    }
  }
}

async function requestBuild(target: {serverId: string; hostId: string; recordVersion: number}) {
  buildStarting.value = true;
  buildStartError.value = "";
  try {
    const response = await buildDstImage({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
    });
    buildTasks.start(target.serverId, target.hostId, target.recordVersion, response.releaseId, response.message);
    if (!isCurrent(target)) return;
  } catch (cause) {
    if (isCurrent(target)) buildStartError.value = describeError(cause).message;
  } finally {
    buildStarting.value = false;
  }
}
async function startBuild() {
  const value = server.value;
  if (!value || building.value) return;
  const target = targetSnapshot();
  if (target) {
    await requestBuild(target);
    return;
  }
  await hostConnection.connect(value, async (_result, connected) => {
    if (server.value?.serverId !== value.serverId) return;
    if (!connected.hostId) {
      buildStartError.value = "主机尚未初始化，请先在主机环境完成初始化";
      return;
    }
    await requestBuild({ serverId: connected.serverId, hostId: connected.hostId, recordVersion: connected.recordVersion });
  });
}

watch(() => buildEntry.value?.status.status, (next, previous) => {
  if (next === "completed" && previous !== "completed") {
    queryClient.invalidate("releases:");
    void refresh();
  }
}, { flush: "sync" });

watch(() => [server.value?.serverId, server.value?.hostId, server.value?.recordVersion], () => {
  stopUpdatePoll();
  actionSequence++;
  pendingReleaseId.value = "";
  pendingStage.value = "";
  actionError.value = null;
  actionMessage.value = null;
  deletePreview.value = null;
  deleteTypedName.value = "";
  deleteBusy.value = false;
  deleteError.value = "";
  cachePreview.value = null;
  cleanupOpen.value = false;
  cleanupBusy.value = false;
  cleanupError.value = "";
  cleanupResult.value = "";
  updateStatus.value = null;
  updateCheckBusy.value = false;
  updateCheckError.value = "";
  void loadUpdateStatus();
  buildStarting.value = false;
  buildStartError.value = "";
});

onUnmounted(() => {
  updateDisposed = true;
  stopUpdatePoll();
  actionSequence++;
});</script>

<template>
  <div class="page release-page">
    <PageHeader :eyebrow="server?.displayName" title="DST Release 管理">
      <template #actions>
        <button class="button" :disabled="loading || !server || updateCheckBusy || updateStatus?.checking || building" @click="checkUpdates">
          <RefreshCw :size="16" />{{ updateCheckBusy || updateStatus?.checking ? "检查中…" : "检查 DST 更新" }}
        </button>
        <button class="button" :disabled="loading || !server || cleanupBusy || building" @click="openDiskCleanup">
          <Trash2 :size="16" />{{ cleanupBusy ? "清理中…" : "清理磁盘" }}
        </button>
        <button class="button" :disabled="building || !server || !!dockerNotice || hostConnection.busyServerId.value === server?.serverId" @click="startBuild">
          <Hammer :size="16" />{{ hostConnection.busyServerId.value === server?.serverId ? "连接中…" : building ? "构建中…" : "构建镜像" }}
        </button>
        <button class="icon-button" type="button" aria-label="刷新 Release" title="刷新 Release" :disabled="loading || !server" @click="refresh">
          <RefreshCw :size="16" />
        </button>
      </template>
    </PageHeader>

    <div class="release-update-status" role="status">
      <span v-if="updateCheckBusy || updateStatus?.checking">{{ updateStatus?.checking ? updateStageLabel : "正在启动检查…" }}</span>
      <span v-else-if="updateStatus?.latest">最新 DST build {{ updateStatus.latest.build }} · 检查于 {{ formatReleaseDate(updateStatus.latest.checkedAt) }}</span>
      <span v-else-if="updateStatus?.lastCheckError">尚未成功检查 DST 更新</span>
      <span v-else>尚未检查 DST 更新</span>
      <span v-if="updateStatus?.candidateReleases.length">可用 Release {{ updateStatus.candidateReleases.length }} 个</span>
      <span v-if="updateStatus?.lastCheckError">{{ updateStatus.lastCheckError.message }}</span>
      <span v-if="updateCheckError">{{ updateCheckError }}</span>
      <span v-if="cleanupResult">{{ cleanupResult }}</span>
    </div>

    <div v-if="error" class="environment-error release-feedback" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>
    <div v-if="hostConnection.error.value && hostConnection.errorServerId.value === server?.serverId" class="environment-error release-feedback" role="alert">
      <strong>{{ hostConnection.error.value.message }}</strong>
      <ul v-if="hostConnection.error.value.recoverySteps.length"><li v-for="step in hostConnection.error.value.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>
    <div v-if="dockerNotice" class="environment-error release-feedback" role="alert">
      <strong>{{ dockerNotice.title }}</strong>
      <ul><li v-for="step in dockerNotice.steps" :key="step">{{ step }}</li></ul>
    </div>

    <div v-if="loading && !data" class="empty-state" role="status">读取 Release…</div>
    <template v-else-if="data">
      <div v-if="stale" class="stale-note"><StatusLabel tone="warning" label="结果已过期"/></div>

      <div v-if="!releases.length" class="release-empty">
        <Package :size="20" />
        <div><strong>暂无 DST Release</strong></div>
      </div>

      <div v-else class="release-table-wrap">
        <div class="release-table release-table-head" aria-hidden="true">
          <span>版本</span><span>状态</span><span>镜像</span><span>创建时间</span><span>操作</span>
        </div>
        <article v-for="release in releases" :key="release.releaseId" class="release-table release-row">
          <div class="release-version-cell">
            <strong>{{ release.dstBuild }}</strong>
            <span class="release-id" :title="release.releaseId">Release {{ release.releaseId.slice(0, 8) }}</span>
          </div>
          <div class="release-status-cell">
            <StatusLabel :tone="statusTone(release)" :label="statusLabel(release)" />
            <span v-if="release.inUse" class="release-retention">使用中</span>
            <span v-else-if="release.reclaimable" class="release-retention">可回收</span>
          </div>
          <div class="release-digest-cell">
            <span class="mono" :title="release.image">{{ release.image }}</span>
            <span>{{ release.imageSize != null ? formatBytes(release.imageSize) : "镜像大小未知" }}</span>
          </div>
          <div class="release-created-cell">{{ formatReleaseDate(release.createdAt) }}</div>
          <div class="release-actions">
            <div class="release-actions-main">
            <template v-if="pendingReleaseId === release.releaseId">
              <span class="release-action-progress" role="status"><span class="state-indicator busy" aria-hidden="true" />{{ pendingStage === "base" ? "检查镜像…" : "核对/删除 Release…" }}</span>
            </template>
            <template v-else-if="!release.verified">
              <button class="button button-compact" :disabled="!!pendingReleaseId || building" @click="verifyBase(release)"><CheckCircle :size="15" />验证镜像</button>
            </template>
            <small v-if="actionError?.releaseId === release.releaseId" class="release-action-error" role="alert">{{ actionError.message }}</small>
            <small v-if="actionMessage?.releaseId === release.releaseId" class="release-action-success" role="status">{{ actionMessage.message }}</small>
            </div>
            <button class="icon-button release-delete" type="button" aria-label="删除 DST Release" title="删除 DST Release" :disabled="!!pendingReleaseId || building || stale" @click="previewDeleteRelease(release)"><Trash2 :size="15"/></button>
          </div>
        </article>
      </div>
    </template>
    <div v-else-if="!error" class="empty-state">尚未读取 Release</div>
<FloatingTask v-if="building || buildStatus?.status === 'failed' || buildMessage || buildError" label="镜像构建">
    <div v-if="building || buildStatus?.status === 'failed' || buildMessage || buildError" class="build-state" role="status">
      <div class="build-state-main">
        <span v-if="building" class="state-indicator busy" aria-hidden="true" />
        <CheckCircle v-else-if="buildMessage && !buildError" :size="16" class="state-success-icon" />
        <AlertCircle v-else :size="16" class="state-error-icon" />
        <div class="build-state-copy">
          <strong v-if="building">{{ buildStatus?.progress?.currentStep || "正在构建 DST 镜像…" }}</strong>
          <strong v-else-if="buildMessage && !buildError">{{ buildMessage }}</strong>
          <strong v-else>构建失败</strong>
          <span v-if="building && buildObservationError">{{ buildObservationError }}</span>
          <span v-else-if="buildError">{{ buildError }}</span>
        </div>
      </div>
      <div v-if="building && buildPercent != null" class="build-progress">
        <div class="build-progress-head"><span>构建阶段</span><span>{{ buildPercent }}%</span></div>
        <div class="build-progress-track" role="progressbar" aria-label="构建阶段" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="buildPercent"><div class="build-progress-fill stage" :style="{ width: `${buildPercent}%` }"></div></div>
        <div v-if="buildStepPercent != null" class="build-progress-detail">
          <div class="build-progress-head"><span>当前步骤下载</span><span>{{ buildStepPercent }}%</span></div>
          <div class="build-progress-track thin" role="progressbar" aria-label="当前步骤下载" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="buildStepPercent"><div class="build-progress-fill download" :style="{ width: `${buildStepPercent}%` }"></div></div>
        </div>
      </div>
      <details v-if="buildStatus?.logTail.length" class="build-log">
        <summary>构建日志</summary>
        <pre>{{ buildStatus.logTail.join("\n") }}</pre>
      </details>
    </div>
</FloatingTask>

    <AppDialog :open="cleanupOpen" title="清理磁盘" description="清理构建临时文件、未被引用的 Docker 镜像和可回收 Release；使用中的 Release 会跳过。" @update:open="value => { if (!value && !cleanupBusy) cleanupOpen = false; }">
      <div class="form-stack">
        <p v-if="cleanupError" class="environment-error" role="alert">{{ cleanupError }}</p>
        <section class="disk-cleanup-section">
          <strong>构建缓存</strong>
          <dl v-if="cachePreview" class="creation-facts">
            <div><dt>构建目录</dt><dd>{{ formatBytes(cachePreview.contextBytes) }}</dd></div>
            <div><dt>Docker 构建缓存</dt><dd>{{ cachePreview.dockerCache.size ?? "不可用" }}</dd></div>
            <div><dt>Docker 可回收</dt><dd>{{ cachePreview.dockerCache.reclaimable ?? "—" }}</dd></div>
            <div><dt>构建状态</dt><dd>{{ cachePreview.buildActive ? "正在构建，暂不可清理" : cachePreview.dockerAvailable ? "空闲" : "Docker 不可用" }}</dd></div>
          </dl>
          <p v-else>{{ cleanupError ? "构建缓存不可用。" : "读取构建缓存…" }}</p>
        </section>
        <section class="disk-cleanup-section">
          <strong>可回收 Release</strong>
          <p v-if="!reclaimableReleases.length">无可回收 Release。</p>
          <div v-else class="release-cleanup-list">
            <ul>
              <li v-for="release in reclaimableReleases" :key="release.releaseId">
                build {{ release.dstBuild }} · {{ release.releaseId.slice(0, 8) }} · {{ release.imageSize != null ? formatBytes(release.imageSize) : "大小未知" }}
              </li>
            </ul>
          </div>
        </section>
      </div>
      <template #footer>
        <button class="button" :disabled="cleanupBusy" @click="cleanupOpen = false">取消</button>
        <button class="button danger" :disabled="cleanupBusy || (!reclaimableReleases.length && !cachePreview?.allowed)" @click="confirmDiskCleanup">{{ cleanupBusy ? "清理中…" : "清理" }}</button>
      </template>
    </AppDialog>
    <AppDialog :open="!!deletePreview" title="删除 DST Release" description="删除 Release 登记及独占镜像；共用镜像保留。已归档专服的数据保留，但恢复时可能需要重新导入原镜像。" @update:open="closeDeleteDialog">
      <div v-if="deletePreview" class="form-stack">
        <dl class="environment-facts">
          <div><dt>DST 版本</dt><dd>{{ deletePreview.dstBuild }}</dd></div>
          <div><dt>镜像摘要</dt><dd class="mono">{{ shortDigest(deletePreview.imageDigest) }}</dd></div>
          <div><dt>实例引用</dt><dd>{{ deletePreview.instanceReferences.length }}</dd></div>
          <div><dt>任务引用</dt><dd>{{ deletePreview.taskReferences.length }}</dd></div>
          <div><dt>容器引用</dt><dd>{{ deletePreview.containerReferences.length }}</dd></div>
          <div><dt>镜像状态</dt><dd>{{ deletePreview.sharedImage ? "其他 Release 共用" : "仅此 Release 引用" }}</dd></div>
        </dl>
        <div v-if="!deletePreview.allowed" class="environment-error" role="status">
          <strong>该 Release 仍有以下引用，不能删除：</strong>
          <ul>
            <li v-if="deletePreview.buildActive">正在构建 Release，暂不能删除</li>
            <li v-for="id in deletePreview.instanceReferences" :key="`instance:${id}`">活动实例：<span class="mono">{{ id }}</span></li>
            <li v-for="id in deletePreview.taskReferences" :key="`task:${id}`">未结束或待核实任务：<span class="mono">{{ id }}</span></li>
            <template v-if="!deletePreview.sharedImage">
              <li v-for="id in deletePreview.containerReferences" :key="`container:${id}`">镜像关联容器：<span class="mono">{{ id }}</span>（含已停止容器）</li>
            </template>
          </ul>
        </div>
        <label v-else>输入 DST 版本确认<input v-model="deleteTypedName" autocomplete="off" :disabled="deleteBusy"/></label>
        <div v-if="deleteError" class="environment-error" role="alert">{{ deleteError }}</div>
      </div>
      <template #footer>
        <button class="button" :disabled="deleteBusy" @click="closeDeleteDialog(false)">取消</button>
        <button class="button danger" :disabled="deleteBusy || !deletePreview?.allowed || deleteTypedName!==deletePreview?.dstBuild" @click="confirmDeleteRelease">{{ deleteBusy ? "正在删除…" : "删除 Release" }}</button>
      </template>
    </AppDialog>
    <FingerprintTrustDialog :open="hostConnection.trustOpen.value" :display-name="hostConnection.challengeServer.value?.displayName ?? ''" :host="hostConnection.challengeServer.value?.host ?? ''" :ssh-port="hostConnection.challengeServer.value?.sshPort ?? 22" :challenge="hostConnection.challenge.value" :busy="hostConnection.busyServerId.value === hostConnection.challengeServer.value?.serverId" :error="hostConnection.trustError.value" @update:open="value => { if (!value) hostConnection.closeTrust() }" @confirm="hostConnection.confirmTrust"/>
  </div>
</template>

<style scoped>
.release-page { min-width: 0; }
.build-state { margin-bottom: var(--space-4); padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.build-state-main { display: flex; align-items: center; gap: var(--space-3); min-height: 32px; }
.build-state-copy { display: flex; flex: 1; min-width: 0; align-items: baseline; gap: var(--space-3); flex-wrap: wrap; }
.build-state-copy strong { font-size: 13px; font-weight: 600; }
.build-state-copy span { color: var(--text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.build-progress { display: grid; gap: 8px; margin: 8px 0 0 24px; }
.build-progress-detail { display: grid; gap: 6px; margin-top: 2px; }
.build-progress-head { display: flex; justify-content: space-between; gap: 12px; color: var(--text-secondary); font-size: 11px; }
.build-progress-track { height: 8px; overflow: hidden; border-radius: 999px; border: 1px solid var(--border); background: var(--surface-muted); }
.build-progress-track.thin { height: 5px; }
.build-progress-fill { height: 100%; border-radius: inherit; background-color: var(--success); background-image: linear-gradient(45deg, rgba(255, 255, 255, .22) 25%, transparent 25%, transparent 50%, rgba(255, 255, 255, .22) 50%, rgba(255, 255, 255, .22) 75%, transparent 75%, transparent); background-size: 12px 12px; animation: build-stripes 1s linear infinite; transition: width 240ms ease; }
.build-progress-fill.download { background-color: var(--accent); }
@keyframes build-stripes { from { background-position: 0 0; } to { background-position: 12px 0; } }
.state-success-icon { color: var(--success); }
.state-error-icon { color: var(--danger); }
.state-indicator { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.state-indicator.busy { background: var(--warning); animation: release-pulse 1.2s ease-in-out infinite; }
.build-log { margin: var(--space-2) 0 0 24px; color: var(--text-secondary); font-size: 12px; }
.build-log summary { width: fit-content; cursor: pointer; }
.build-log pre { max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: var(--space-3); border: 1px solid var(--border); background: var(--surface-muted); }
.release-feedback { margin-bottom: var(--space-3); }
.release-update-status { display: flex; flex-wrap: wrap; gap: var(--space-3); margin-bottom: var(--space-3); color: var(--text-secondary); font-size: 12px; }
.release-retention { color: var(--text-secondary); font-size: 11px; }
.disk-cleanup-section { display: grid; gap: var(--space-2); }
.disk-cleanup-section > strong { font-size: 12px; font-weight: 600; }
.disk-cleanup-section > p { color: var(--text-secondary); font-size: 12px; }
.release-cleanup-list ul { margin: var(--space-2) 0 0; padding-left: var(--space-4); color: var(--text-secondary); font-size: 12px; }
.release-table-wrap { min-width: 0; }
.release-table { display: grid; grid-template-columns: minmax(140px, .85fr) minmax(84px, .5fr) minmax(220px, 1.5fr) minmax(110px, .65fr) minmax(150px, .8fr); gap: var(--space-4); align-items: center; }
.release-table-head { min-height: 36px; color: var(--text-secondary); font-size: 11px; border-bottom: 1px solid var(--border); }
.release-row { min-height: 92px; padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.release-version-cell, .release-digest-cell, .release-status-cell, .release-actions { display: flex; min-width: 0; flex-direction: column; gap: 5px; }
.release-version-cell strong { font-size: 14px; font-variant-numeric: tabular-nums; }
.release-id, .release-digest-cell { color: var(--text-secondary); font-size: 11px; }
.release-id { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.release-digest-cell > span:first-child { display: block; overflow: hidden; color: var(--text); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.release-actions { flex-direction: row; align-items: center; gap: 10px; }
.release-actions-main { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; min-width: 0; }
.release-delete { flex: 0 0 auto; width: 30px; }
.release-created-cell { color: var(--text-secondary); font-size: 11px; }
.button-compact { min-height: 30px; padding: 0 var(--space-2); font-size: 12px; }
.release-action-progress { display: inline-flex; align-items: center; gap: var(--space-2); font-size: 12px; }
.release-action-error, .release-action-success { font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
.release-action-error { color: var(--danger); }
.release-action-success { color: var(--success); }
.release-empty { display: flex; min-height: 88px; align-items: center; gap: var(--space-3); border-bottom: 1px solid var(--border); }
.release-empty > div { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 4px; }
.release-feedback :deep(ul) { margin: var(--space-2) 0 0; }
@keyframes release-pulse { 50% { opacity: .35; } }
@media (max-width: 1099px) {
  .release-table-head { display: none; }
  .release-row {
    grid-template-columns: minmax(0, 1fr) minmax(132px, 168px);
    grid-template-areas:
      "version status"
      "image actions"
      "created actions";
    align-items: start;
    gap: 6px var(--space-4);
    padding: var(--space-4) 0;
    min-height: 0;
  }
  .release-version-cell { grid-area: version; }
  .release-status-cell { grid-area: status; align-items: flex-end; }
  .release-digest-cell { grid-area: image; }
  .release-created-cell { grid-area: created; }
  .release-actions { grid-area: actions; align-self: start; justify-content: flex-end; }
  .release-actions-main { gap: 4px; justify-content: flex-end; }
}
@media (prefers-reduced-motion: reduce) { .state-indicator.busy { animation: none; } }
</style>
