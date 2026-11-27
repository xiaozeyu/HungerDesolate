<script setup lang="ts">
import { computed, h, onBeforeUnmount, ref, watch } from "vue";
import { AlertCircle, CheckCircle, Hammer, Package, RefreshCw, Trash2 } from "@lucide/vue";
import { useRoute } from "vue-router";
import { NAlert, NButton, NDataTable, NInput, NLog, NProgress, NSpin, type DataTableColumns } from "naive-ui";
import PageHeader from "../components/PageHeader.vue";
import PreviewNotice from "../components/PreviewNotice.vue";
import FloatingTask from "../components/FloatingTask.vue";
import StatusLabel from "../components/StatusLabel.vue";
import IconButton from "../components/IconButton.vue";
import AppDialog from "../components/AppDialog.vue";
import FingerprintTrustDialog from "../components/FingerprintTrustDialog.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
import { notification, notifyError, notifySuccess } from "../naive/notify";
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
import { describeError, type UserError } from "../lib/commandError";
import { sessionStartedAt } from "../lib/session";
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
  : "");
const buildTone = computed<"running" | "danger" | "success" | "muted">(() => building.value ? "running"
  : buildError.value ? "danger" : buildMessage.value ? "success" : "muted");
const dismissedBuildKey = ref<string | null>(null);
const buildDockKey = computed(() => {
  const state = building.value ? "building" : buildError.value ? "error" : buildMessage.value ? "success" : "";
  return state ? `${state}:${buildEntry.value?.startedAt ?? 0}` : "";
});
const buildDockVisible = computed(() => {
  if (!buildDockKey.value || buildDockKey.value === dismissedBuildKey.value) return false;
  return building.value || (buildEntry.value?.startedAt ?? 0) >= sessionStartedAt;
});
const buildDismissible = computed(() => !building.value && !!buildDockKey.value);
function dismissBuild() {
  dismissedBuildKey.value = buildDockKey.value;
}
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
const deletePreview = ref<InstanceReleaseDeletePreview|null>(null);
const deleteTypedName = ref("");
const deleteBusy = ref(false);
const cachePreview = ref<BuildCachePreview | null>(null);
const updateStatus = ref<HostDstUpdateStatus | null>(null);
const updateCheckBusy = ref(false);
let updatePollTimer: number | undefined;
let updatePollStartedAt = 0;
let updateDisposed = false;
let contextEpoch = 0, updateStatusSequence = 0;
const reclaimableReleases = computed(() => releases.value.filter(release => release.reclaimable));
const cleanupOpen = ref(false);
const cleanupBusy = ref(false);
const cleanupError = ref<UserError | null>(null);
let actionSequence = 0;

function targetSnapshot() {
  const value = server.value;
  if (!value?.hostId) return null;
  return { serverId: value.serverId, hostId: value.hostId, recordVersion: value.recordVersion };
}

function isCurrent(target: { serverId: string; hostId: string; recordVersion: number }, sequence?: number) {
  const value = server.value;
  return !updateDisposed && !!value && value.serverId === target.serverId && value.hostId === target.hostId
    && value.recordVersion === target.recordVersion && (sequence === undefined || actionSequence === sequence);
}

const updateNotifyPending = ref(false);

function notifyUpdateResult(status: HostDstUpdateStatus | null) {
  if (!updateNotifyPending.value) return;
  updateNotifyPending.value = false;
  if (!status) {
    notifyError("DST 更新检查失败", "未能读取检查结果", 0);
    return;
  }
  if (status.lastCheckError) {
    const detail = status.lastCheckError.detail
      ?.split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean)
      .slice(-3)
      .map(line => line.slice(0, 160))
      .join(" · ");
    notifyError("DST 更新检查失败", detail ? `${status.lastCheckError.message} · ${detail}` : status.lastCheckError.message, 0);
    return;
  }
  if (status.latest) {
    notification.success({
      title: "DST 更新检查完成",
      content: `最新 Steam build ${status.latest.build} · 检查于 ${formatReleaseDate(status.latest.checkedAt)}${status.candidateReleases.length ? ` · 可用 Release ${status.candidateReleases.length} 个` : " · 没有匹配的 Release"}`,
      duration: 5000,
      closable: true,
    });
    return;
  }
  notification.info({ title: "DST 更新检查完成", content: "未发现可用 Release", duration: 5000, closable: true });
}

function notifyUpdateError(message: string | UserError) {
  if (!updateNotifyPending.value) return;
  updateNotifyPending.value = false;
  notifyError("DST 更新检查失败", message, 0);
}

const buildAlert = computed(() => {
  if (building.value) {
    return {
      type: "info" as const,
      title: buildStatus.value?.progress?.currentStep || "正在构建 DST 镜像…",
    };
  }
  if (buildError.value) {
    return { type: "error" as const, title: "构建失败" };
  }
  if (buildMessage.value) {
    return { type: "success" as const, title: buildMessage.value };
  }
  return { type: "default" as const, title: "镜像构建" };
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
  if (Date.now() - updatePollStartedAt > 75 * 60 * 1000) {
    if (updateStatus.value) updateStatus.value = { ...updateStatus.value, checking: false, stage: null };
    notifyUpdateError("检查超时，请稍后刷新或重试");
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
  const target = targetSnapshot();
  if (!target || updateDisposed || workspace.preview.value) return;
  const epoch = contextEpoch, sequence = ++updateStatusSequence;
  const current = () => isCurrent(target) && contextEpoch === epoch && sequence === updateStatusSequence;
  const requestId = crypto.randomUUID();
  try {
    const status = await getDstUpdateStatus({ schemaVersion: 1, requestId, serverId: target.serverId,
      hostId: target.hostId, expectedRecordVersion: target.recordVersion });
    if (!current()) return;
    if (status.requestId !== requestId || status.target.serverId !== target.serverId || status.target.hostId !== target.hostId) {
      throw new Error("DST 更新状态响应身份不匹配");
    }
    updateStatus.value = status;
    if (status.checking || updateCheckBusy.value) scheduleUpdatePoll();
    else {
      stopUpdatePoll();
      notifyUpdateResult(status);
    }
  } catch (cause) {
    if (!current()) return;
    if (!updateCheckBusy.value) notifyUpdateError(describeError(cause));
    if (updateStatus.value?.checking || updateCheckBusy.value) scheduleUpdatePoll();
    else {
      updateStatus.value = null;
      stopUpdatePoll();
    }
  }
}

async function checkUpdates() {
  const target = targetSnapshot();
  if (!target || updateCheckBusy.value || workspace.preview.value) return;
  if (updateStatus.value?.checking) {
    updateNotifyPending.value = true;
    scheduleUpdatePoll();
    return;
  }
  const epoch = contextEpoch;
  const current = () => isCurrent(target) && contextEpoch === epoch;
  updateCheckBusy.value = true;
  updateNotifyPending.value = true;
  scheduleUpdatePoll();
  const requestId = crypto.randomUUID();
  try {
    const status = await checkDstUpdates({ schemaVersion: 1, requestId, serverId: target.serverId,
      hostId: target.hostId, expectedRecordVersion: target.recordVersion });
    if (!current()) return;
    if (status.requestId !== requestId || status.target.serverId !== target.serverId || status.target.hostId !== target.hostId) {
      throw new Error("DST 更新检查响应身份不匹配");
    }
    updateStatusSequence++;
    updateStatus.value = status;
    if (status.checking) scheduleUpdatePoll();
    else {
      stopUpdatePoll();
      notifyUpdateResult(status);
    }
  } catch (cause) {
    if (!current()) return;
    notifyUpdateError(describeError(cause));
    await loadUpdateStatus();
  } finally {
    if (current()) updateCheckBusy.value = false;
  }
}

async function openDiskCleanup() {
  const target = targetSnapshot();
  if (!target || cleanupBusy.value || pendingReleaseId.value) return;
  cleanupOpen.value = true;
  cleanupError.value = null;
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
    if (isCurrent(target, sequence)) cleanupError.value = describeError(cause);
  } finally {
    if (isCurrent(target, sequence)) cleanupBusy.value = false;
  }
}

async function confirmDiskCleanup() {
  const target = targetSnapshot();
  if (!target || cleanupBusy.value || pendingReleaseId.value) return;
  const sequence = ++actionSequence;
  const current = () => isCurrent(target, sequence);
  const candidates = reclaimableReleases.value;
  const cache = cachePreview.value;
  cleanupBusy.value = true;
  cleanupError.value = null;
  let deleted = 0;
  let skipped = 0;
  let cacheCleaned = false;
  try {
    for (const release of candidates) {
      try {
        const preview = await previewInstanceReleaseDelete({
          schemaVersion: 1, requestId: crypto.randomUUID(), target: { serverId: target.serverId, hostId: target.hostId },
          expectedRecordVersion: target.recordVersion, releaseId: release.releaseId,
        });
        if (!current()) return;
        if (!preview.allowed) {
          skipped++;
          continue;
        }
        await deleteInstanceRelease({
          schemaVersion: 1, requestId: crypto.randomUUID(), target: { serverId: target.serverId, hostId: target.hostId },
          expectedRecordVersion: target.recordVersion, releaseId: release.releaseId,
          catalogDigest: preview.catalogDigest, typedName: release.dstBuild,
        });
        if (!current()) return;
        deleted++;
      } catch (cause) {
        if (!current()) return;
        cleanupError.value = describeError(cause);
        break;
      }
    }
    if (!cleanupError.value && cache?.allowed) {
      const before = cache.dockerCache.size;
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
      if (!current()) return;
      if (outcome.error) cleanupError.value = outcome.error;
      else cacheCleaned = true;
    }
    if (!cleanupError.value) {
      notifySuccess("磁盘清理完成", `已清理 ${deleted} 个 Release${skipped ? `，跳过 ${skipped} 个` : ""}${cacheCleaned ? "，构建缓存已清理" : ""}`);
      cleanupOpen.value = false;
    }
    await refresh();
  } finally {
    if (current()) cleanupBusy.value = false;
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

function closeDeleteDialog(open: boolean) {
  if (!open && !deleteBusy.value) {
    deletePreview.value = null;
    deleteTypedName.value = "";
  }
}

async function previewDeleteRelease(release: InstanceReleaseSummary) {
  const target = targetSnapshot();
  if (!target || pendingReleaseId.value || building.value || cleanupBusy.value) return;
  const sequence = ++actionSequence;
  pendingReleaseId.value = release.releaseId;
  pendingStage.value = "delete";
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
    if (isCurrent(target, sequence)) notifyError("读取 Release 删除预览失败", describeError(cause));
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
      || deleteTypedName.value !== preview.dstBuild || pendingReleaseId.value || cleanupBusy.value) return;
  const sequence = ++actionSequence;
  deleteBusy.value = true;
  pendingReleaseId.value = preview.releaseId;
  pendingStage.value = "delete";
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
    notifySuccess("Release 已删除", !result.stagingRemoved || !result.dstRemoved ? "文件清理未完成"
      : result.imageShared ? "共用镜像仍被其他 Release 使用"
        : result.imageRemoved ? "Release 与镜像已删除" : "镜像本已不存在");
    await refresh();
  } catch (cause) {
    if (isCurrent(target, sequence)) notifyError("删除 Release 失败", describeError(cause));
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
  if (!target || pendingReleaseId.value || cleanupBusy.value) return;
  const sequence = ++actionSequence;
  pendingReleaseId.value = release.releaseId;
  pendingStage.value = "base";
  try {
    await verifyInstanceReleaseBase({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
      releaseId: release.releaseId,
    });
    if (!isCurrent(target, sequence)) return;
    notifySuccess("镜像验证通过", release.dstBuild);
    await refresh();
  } catch (cause) {
    if (isCurrent(target, sequence)) notifyError("验证镜像失败", describeError(cause));
  } finally {
    if (isCurrent(target, sequence)) {
      pendingReleaseId.value = "";
      pendingStage.value = "";
    }
  }
}

async function requestBuild(target: {serverId: string; hostId: string; recordVersion: number}) {
  const epoch = contextEpoch;
  const current = () => isCurrent(target) && contextEpoch === epoch;
  const requestId = crypto.randomUUID();
  buildStarting.value = true;
  try {
    const response = await buildDstImage({
      schemaVersion: 1,
      requestId,
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
    });
    if (response.requestId !== requestId || response.serverId !== target.serverId || response.recordVersion !== target.recordVersion) {
      throw new Error("镜像构建回执身份或版本不匹配");
    }
    buildTasks.start(target.serverId, target.hostId, target.recordVersion, response.releaseId, response.message);
  } catch (cause) {
    if (current()) notifyError("启动镜像构建失败", describeError(cause));
  } finally {
    if (current()) buildStarting.value = false;
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
    if (updateDisposed || server.value?.serverId !== value.serverId || workspace.preview.value) return;
    if (!connected.hostId) {
      notifyError("主机尚未初始化", "请先在主机环境完成初始化");
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

watch([() => server.value?.serverId, () => server.value?.hostId, () => server.value?.recordVersion, workspace.preview], () => {
  contextEpoch++;
  stopUpdatePoll();
  actionSequence++;
  pendingReleaseId.value = "";
  pendingStage.value = "";
  deletePreview.value = null;
  deleteTypedName.value = "";
  deleteBusy.value = false;
  cachePreview.value = null;
  cleanupOpen.value = false;
  cleanupBusy.value = false;
  cleanupError.value = null;
  updateStatus.value = null;
  updateCheckBusy.value = false;
  updateNotifyPending.value = false;
  buildStarting.value = false;
  void loadUpdateStatus();
}, { immediate: true, flush: "sync" });

onBeforeUnmount(() => {
  updateDisposed = true;
  stopUpdatePoll();
  actionSequence++;
});

const releaseColumns = computed<DataTableColumns<InstanceReleaseSummary>>(() => {
  void pendingReleaseId.value;
  void pendingStage.value;
  void building.value;
  void stale.value;
  return [
  {
    title: "版本",
    key: "dstBuild",
    render: release => h("div", { class: "release-version-cell" }, [
      h("strong", release.dstBuild),
      h("span", { class: "release-id", title: release.releaseId }, `Release ${release.releaseId.slice(0, 8)}`),
      release.steamBuildId ? h("span", { class: "release-id" }, `Steam build ${release.steamBuildId}`) : null,
    ]),
  },
  {
    title: "状态",
    key: "status",
    render: release => h("div", { class: "release-status-cell" }, [
      h(StatusLabel, { tone: statusTone(release), label: statusLabel(release) }),
      release.inUse ? h("span", { class: "release-retention" }, "使用中")
        : release.reclaimable ? h("span", { class: "release-retention" }, "可回收") : null,
    ]),
  },
  {
    title: "镜像",
    key: "image",
    render: release => h("div", { class: "release-digest-cell" }, [
      h("span", { class: "mono", title: release.image }, release.image),
      h("span", null, release.imageSize != null ? formatBytes(release.imageSize) : "镜像大小未知"),
    ]),
  },
  { title: "创建时间", key: "createdAt", render: release => formatReleaseDate(release.createdAt) },
  {
    title: "",
    key: "actions",
    align: "center",
    render: release => {
      const pending = pendingReleaseId.value === release.releaseId;
      const actions = [];
      if (pending) {
        actions.push(h("span", { class: "release-action-progress", role: "status" }, [
          h("span", { class: "state-indicator busy", "aria-hidden": "true" }),
          pendingStage.value === "base" ? "检查镜像…" : "核对/删除 Release…",
        ]));
      } else if (!release.verified) {
        actions.push(h(IconButton, {
          label: "验证镜像",
          disabled: !!pendingReleaseId.value || building.value || cleanupBusy.value,
          onClick: () => void verifyBase(release),
        }, () => h(CheckCircle, { size: 15 })));
      }
      actions.push(h(IconButton, {
        label: "删除 DST Release",
        disabled: !!pendingReleaseId.value || building.value || cleanupBusy.value || stale.value,
        tone: "danger",
        onClick: () => void previewDeleteRelease(release),
      }, () => h(Trash2, { size: 15 })));
      return h("div", { class: "row-actions" }, actions);
    },
  },
  ];
});
const releaseRowKey = (release: InstanceReleaseSummary) => release.releaseId;</script>

<template>
  <div class="page release-page">
    <PageHeader :eyebrow="server?.displayName" title="DST Release 管理">
      <template #status><StatusLabel v-if="dockerNotice" tone="warning" :label="dockerNotice.title"/></template>
      <template #actions>
        <NButton :disabled="loading || !server || updateCheckBusy || updateStatus?.checking || building" @click="checkUpdates">
          <template #icon><RefreshCw :size="16"/></template>{{ updateCheckBusy || updateStatus?.checking ? "检查中…" : "检查 DST 更新" }}
        </NButton>
        <NButton :disabled="loading || !server || cleanupBusy || !!pendingReleaseId || building" @click="openDiskCleanup">
          <template #icon><Trash2 :size="16"/></template>{{ cleanupBusy ? "清理中…" : "清理磁盘" }}
        </NButton>
        <NButton :disabled="building || !server || !!dockerNotice || hostConnection.busyServerId.value === server?.serverId" @click="startBuild">
          <template #icon><Hammer :size="16"/></template>{{ hostConnection.busyServerId.value === server?.serverId ? "连接中…" : building ? "构建中…" : "构建镜像" }}
        </NButton>
        <IconButton label="刷新 Release" :disabled="loading || !server" @click="refresh"><RefreshCw :size="16"/></IconButton>
      </template>
    </PageHeader>
    <PreviewNotice/>

    <ErrorNotice :key="server?.serverId" title="读取 DST Release 失败" :error="error" :pending="loading"/>
    <ErrorNotice :title="dockerNotice?.title" :error="dockerNotice?.steps.join('；')"/>
    <ErrorNotice :key="buildStatus?.releaseId" title="镜像构建未完成" :error="buildDockVisible && !building ? buildError : null"/>
    <ErrorNotice title="读取构建进度失败" :error="building ? buildObservationError : null"/>

    <template v-if="loading || data">
      <div v-if="stale" class="stale-note"><StatusLabel tone="warning" label="结果已过期"/></div>

      <div class="table-wrap">
        <NDataTable :columns="releaseColumns" :data="releases" :bordered="false" :single-line="true" size="medium" :row-key="releaseRowKey" :loading="loading">
          <template #empty><div class="table-empty"><Package :size="20"/><strong>暂无 DST Release</strong></div></template>
        </NDataTable>
      </div>
    </template>
    <div v-else-if="!error" class="empty-state">尚未读取 Release</div>
<FloatingTask v-if="buildDockVisible" label="镜像构建" :tone="buildTone" :summary="buildAlert.title" :dismissible="buildDismissible" @dismiss="dismissBuild">
    <NAlert :type="buildAlert.type" :title="buildAlert.title" :bordered="false" :show-icon="true" class="build-alert">
      <template #icon>
        <NSpin v-if="building" size="small"/>
        <CheckCircle v-else-if="buildMessage && !buildError" :size="16" class="state-success-icon"/>
        <AlertCircle v-else :size="16" class="state-error-icon"/>
      </template>
    </NAlert>
    <div v-if="building && buildPercent != null" class="build-progress">
      <div class="build-progress-head"><span>构建阶段</span><span>{{ buildPercent }}%</span></div>
      <NProgress type="line" :percentage="buildPercent" :show-indicator="false" :height="6" status="success"/>
      <div v-if="buildStepPercent != null" class="build-progress-detail">
        <div class="build-progress-head"><span>当前步骤下载</span><span>{{ buildStepPercent }}%</span></div>
        <NProgress type="line" :percentage="buildStepPercent" :show-indicator="false" :height="4" status="info"/>
      </div>
    </div>
    <NLog v-if="buildStatus?.logTail.length" :log="buildStatus.logTail.join('\n')" :rows="8" class="build-log"/>
</FloatingTask>

    <AppDialog :open="cleanupOpen" title="清理磁盘"  @update:open="value => { if (!value && !cleanupBusy) cleanupOpen = false; }">
      <div class="form-stack">
        <ErrorNotice title="磁盘清理未完成" :error="cleanupError" :pending="cleanupBusy"/>
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
          <p v-if="!reclaimableReleases.length">无可回收 Release</p>
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
        <NButton :disabled="cleanupBusy" @click="cleanupOpen = false">取消</NButton>
        <NButton type="error" ghost :disabled="cleanupBusy || (!reclaimableReleases.length && !cachePreview?.allowed)" @click="confirmDiskCleanup">{{ cleanupBusy ? "清理中…" : "清理" }}</NButton>
      </template>
    </AppDialog>
    <AppDialog :open="!!deletePreview" title="删除 DST Release"  @update:open="closeDeleteDialog">
      <div v-if="deletePreview" class="form-stack">
        <dl class="environment-facts">
          <div><dt>DST 版本</dt><dd>{{ deletePreview.dstBuild }}</dd></div>
<!--          <div><dt>镜像摘要</dt><dd class="mono">{{ shortDigest(deletePreview.imageDigest) }}</dd></div>-->
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
        <label v-else>输入 DST 版本确认<NInput v-model:value="deleteTypedName" placeholder="" :input-props="{ autocomplete: 'off' }" :disabled="deleteBusy"/></label>
        </div>
      <template #footer>
        <NButton :disabled="deleteBusy" @click="closeDeleteDialog(false)">取消</NButton>
        <NButton type="error" ghost :disabled="deleteBusy || !deletePreview?.allowed || deleteTypedName!==deletePreview?.dstBuild" @click="confirmDeleteRelease">{{ deleteBusy ? "正在删除…" : "删除 Release" }}</NButton>
      </template>
    </AppDialog>
    <FingerprintTrustDialog :open="hostConnection.trustOpen.value" :display-name="hostConnection.challengeServer.value?.displayName ?? ''" :host="hostConnection.challengeServer.value?.host ?? ''" :ssh-port="hostConnection.challengeServer.value?.sshPort ?? 22" :challenge="hostConnection.challenge.value" :busy="hostConnection.busyServerId.value === hostConnection.challengeServer.value?.serverId" @update:open="value => { if (!value) hostConnection.closeTrust() }" @confirm="hostConnection.confirmTrust"/>
  </div>
</template>

<style scoped>
.release-page { min-width: 0; }
.build-alert { margin-bottom: 8px; }
.build-progress { display: grid; gap: 8px; margin: 8px 0 0; }
.build-progress-detail { display: grid; gap: 6px; margin-top: 2px; }
.build-progress-head { display: flex; justify-content: space-between; gap: 12px; color: var(--text-secondary); font-size: 11px; }
.state-success-icon { color: var(--success); }
.state-error-icon { color: var(--danger); }
.state-indicator { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.state-indicator.busy { background: var(--warning); animation: release-pulse 1.2s ease-in-out infinite; }
.build-log { margin-top: 8px; }
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
