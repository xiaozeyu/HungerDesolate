<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { AlertCircle, CheckCircle, Hammer, Package, RefreshCw, Trash2 } from "@lucide/vue";
import { useRoute } from "vue-router";
import PageHeader from "../components/PageHeader.vue";
import StatusLabel from "../components/StatusLabel.vue";
import AppDialog from "../components/AppDialog.vue";
import { useWorkspace } from "../composables/useWorkspace";
import { useReleaseList } from "../composables/useReleaseList";
import { useBuildTasks } from "../composables/useBuildTasks";
import { queryClient } from "../lib/queryClient";
import {
  buildDstImage,
  deleteInstanceRelease,
  importInstanceRelease,
  previewInstanceRelease,
  previewInstanceReleaseDelete,
  verifyInstanceReleaseBase,
  verifyInstanceReleaseRuntime,
  type BuildStatusResponse,
  type InstanceReleaseSummary,
  type InstanceReleaseDeletePreview,
} from "../api/instances";
import { describeError } from "../lib/commandError";
import { cleanupBuildCache, previewBuildCache, type BuildCachePreview } from "../api/buildCache";
import { formatBytes } from "../lib/environmentView";

const route = useRoute();
const workspace = useWorkspace();
const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId) ?? null);
const { data, error, loading, stale, refresh } = useReleaseList(server);

const releases = computed(() => data.value?.releases ?? []);
const creationReadyCount = computed(() => releases.value.filter(item => item.verified).length);
const buildTasks = useBuildTasks();
const buildStarting = ref(false);
const buildStartError = ref("");
const buildEntry = computed(() => buildTasks.forHost(server.value?.hostId));
const building = computed(() => buildStarting.value || buildEntry.value?.status.status === "building");
const buildStatus = computed<BuildStatusResponse | null>(() => buildEntry.value?.status ?? null);
const buildMessage = computed(() => buildEntry.value?.message ?? "");
const buildObservationError = computed(() => buildEntry.value?.observationError ?? "");
const buildError = computed(() => buildEntry.value?.status.status === "failed"
  ? buildEntry.value.status.logTail.at(-1) ?? "构建失败，请查看主机构建日志"
  : buildEntry.value?.status.status === "notFound" ? "构建任务不存在，请重新构建"
  : buildStartError.value);
const pendingReleaseId = ref("");
const pendingStage = ref<"base" | "runtime" | "import" | "delete" | "" >("");
const actionError = ref<{ releaseId: string; message: string } | null>(null);
const actionMessage = ref<{ releaseId: string; message: string } | null>(null);
const bundleReleaseId = ref("");
const bundleBase64 = ref("");
const bundleName = ref("");
const bundleError = ref<{ releaseId: string; message: string } | null>(null);
const deletePreview = ref<InstanceReleaseDeletePreview|null>(null);
const deleteTypedName = ref("");
const deleteBusy = ref(false);
const deleteError = ref("");
const cachePreview = ref<BuildCachePreview | null>(null);
const cacheBusy = ref(false);
const cacheError = ref("");
const cacheMessage = ref("");
const bundleInputs = new Map<string, HTMLInputElement>();
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

function statusTone(release: InstanceReleaseSummary): "success" | "warning" | "danger" {
  if (release.verified) return "success";
  return "danger";
}

function statusLabel(release: InstanceReleaseSummary) {
  if (release.verified) return release.runtimeVerified ? "构建验证通过 · 已有运行证据" : "构建验证通过，可创建";
  return "待镜像验证";
}

function shortDigest(value: string) {
  return value.length > 24 ? `${value.slice(0, 19)}…` : value;
}

function clearBundle() {
  bundleReleaseId.value = "";
  bundleBase64.value = "";
  bundleName.value = "";
  for (const input of bundleInputs.values()) input.value = "";
}

function rememberInput(releaseId: string, element: unknown) {
  if (element instanceof HTMLInputElement) bundleInputs.set(releaseId, element);
  else bundleInputs.delete(releaseId);
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
      message: !result.stagingRemoved ? "Release 已删除，但 staging 文件清理未完成"
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

function encodeBase64(bytes: Uint8Array) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

async function readBundle(release: InstanceReleaseSummary, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  const target = targetSnapshot();
  const sequence = actionSequence;
  clearBundle();
  bundleError.value = null;
  if (!file) return;
  if (file.size > 1024 * 1024) {
    bundleError.value = { releaseId: release.releaseId, message: "配置 ZIP 不能超过 1 MiB" };
    input.value = "";
    return;
  }
  try {
    const encoded = encodeBase64(new Uint8Array(await file.arrayBuffer()));
    if (!target || !isCurrent(target, sequence) || !releases.value.some(item => item.releaseId === release.releaseId)) return;
    bundleBase64.value = encoded;
    bundleName.value = file.name;
    bundleReleaseId.value = release.releaseId;
  } catch {
    bundleError.value = { releaseId: release.releaseId, message: "无法读取配置 ZIP" };
    input.value = "";
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

async function verifyRuntime(release: InstanceReleaseSummary) {
  const target = targetSnapshot();
  const catalogDigest = data.value?.catalogDigest;
  const sequence = ++actionSequence;
  if (!target || !catalogDigest || !bundleBase64.value || bundleReleaseId.value !== release.releaseId || pendingReleaseId.value) return;
  const encodedBundle = bundleBase64.value;
  pendingReleaseId.value = release.releaseId;
  pendingStage.value = "runtime";
  actionError.value = null;
  actionMessage.value = null;
  clearBundle();
  try {
    await verifyInstanceReleaseRuntime({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
      releaseId: release.releaseId,
      catalogDigest,
      operationId: crypto.randomUUID(),
      settingsBundleBase64: encodedBundle,
    });
    if (!isCurrent(target, sequence)) return;
    pendingStage.value = "import";
    const preview = await previewInstanceRelease({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
      releaseId: release.releaseId,
      catalogDigest,
    });
    if (!isCurrent(target, sequence)) return;
    if (preview.status !== "ready") {
      const message = preview.blockers.join("、") || "运行验证证据尚未准备好";
      throw new Error(message);
    }
    const imported = await importInstanceRelease({
      schemaVersion: 1,
      requestId: crypto.randomUUID(),
      target: { serverId: target.serverId, hostId: target.hostId },
      expectedRecordVersion: target.recordVersion,
      releaseId: release.releaseId,
      catalogDigest,
      operationId: crypto.randomUUID(),
    });
    if (!isCurrent(target, sequence)) return;
    const task = imported.task as { status?: string; error?: { code?: string } } | null;
    if (task?.status !== "succeeded") {
      throw new Error(task?.error?.code ?? "Release 运行验证未能登记");
    }
    actionMessage.value = { releaseId: release.releaseId, message: "隔离运行验证通过" };
    await refresh();
  } catch (cause) {
    if (isCurrent(target, sequence)) {
      actionError.value = { releaseId: release.releaseId, message: describeError(cause).message };
    }
  } finally {
    if (isCurrent(target, sequence)) {
      clearBundle();
      pendingReleaseId.value = "";
      pendingStage.value = "";
    }
  }
}

async function startBuild() {
  const target = targetSnapshot();
  if (!target || building.value) return;
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

watch(() => buildEntry.value?.status.status, (next, previous) => {
  if (next === "completed" && previous !== "completed") {
    queryClient.invalidate("releases:");
    void refresh();
  }
}, { flush: "sync" });

watch(() => [server.value?.serverId, server.value?.hostId, server.value?.recordVersion], () => {
  actionSequence++;
  pendingReleaseId.value = "";
  pendingStage.value = "";
  actionError.value = null;
  actionMessage.value = null;
  bundleError.value = null;
  deletePreview.value = null;
  deleteTypedName.value = "";
  deleteBusy.value = false;
  deleteError.value = "";
  cachePreview.value = null;
  cacheError.value = "";
  cacheMessage.value = "";
  cacheBusy.value = false;
  clearBundle();
  buildStarting.value = false;
  buildStartError.value = "";
});

onUnmounted(() => {
  actionSequence++;
  clearBundle();
});

async function openCacheCleanup() {
  const target = targetSnapshot();
  if (!target || cacheBusy.value) return;
  const sequence = ++actionSequence;
  cacheBusy.value = true;
  cacheError.value = "";
  cacheMessage.value = "";
  try {
    const response = await previewBuildCache({ schemaVersion: 1, requestId: crypto.randomUUID(), action: "preview",
      serverId: target.serverId, hostId: target.hostId, expectedRecordVersion: target.recordVersion });
    if (!isCurrent(target, sequence)) return;
    if (response.serverId !== target.serverId || response.hostId !== target.hostId) throw new Error("构建缓存预览身份不匹配");
    cachePreview.value = response;
  } catch (cause) {
    if (isCurrent(target, sequence)) cacheError.value = describeError(cause).message;
  } finally {
    if (isCurrent(target, sequence)) cacheBusy.value = false;
  }
}

async function confirmCacheCleanup() {
  const preview = cachePreview.value;
  const target = targetSnapshot();
  if (!preview || !target || cacheBusy.value || !preview.allowed) return;
  const sequence = ++actionSequence;
  cacheBusy.value = true;
  cacheError.value = "";
  try {
    const response = await cleanupBuildCache({ schemaVersion: 1, requestId: crypto.randomUUID(), action: "cleanup",
      serverId: target.serverId, hostId: target.hostId, expectedRecordVersion: target.recordVersion });
    if (!isCurrent(target, sequence)) return;
    if (response.serverId !== target.serverId || response.hostId !== target.hostId) throw new Error("构建缓存清理身份不匹配");
    cachePreview.value = null;
    cacheMessage.value = `构建缓存已清理${response.contextBytes ? `，移除 ${formatBytes(response.contextBytes)} 构建目录` : ""}`;
  } catch (cause) {
    if (isCurrent(target, sequence)) cacheError.value = describeError(cause).message;
  } finally {
    if (isCurrent(target, sequence)) cacheBusy.value = false;
  }
}
</script>

<template>
  <div class="page release-page">
    <PageHeader :eyebrow="server?.displayName" title="DST Release 管理">
      <template #actions>
        <button class="button" :disabled="building || loading || !server || cacheBusy" @click="openCacheCleanup">
          <Trash2 :size="16" />清理构建缓存
        </button>
        <button class="button" :disabled="building || loading || !server" @click="startBuild">
          <Hammer :size="16" />{{ building ? "构建中…" : "构建镜像" }}
        </button>
        <button class="button icon-button" type="button" aria-label="刷新 Release" title="刷新 Release" :disabled="loading || !server" @click="refresh">
          <RefreshCw :size="16" />
        </button>
      </template>
    </PageHeader>
    <p v-if="cacheMessage" class="success-message" role="status">{{ cacheMessage }}</p>

    <div v-if="building || buildStatus?.status === 'failed' || buildMessage || buildError" class="build-state" role="status">
      <div class="build-state-main">
        <span v-if="building" class="state-indicator busy" aria-hidden="true" />
        <CheckCircle v-else-if="buildMessage && !buildError" :size="16" class="state-success-icon" />
        <AlertCircle v-else :size="16" class="state-error-icon" />
        <div class="build-state-copy">
          <strong v-if="building">{{ buildStatus?.progress?.currentStep || "正在构建 DST 镜像…" }}</strong>
          <strong v-else-if="buildMessage && !buildError">{{ buildMessage }}</strong>
          <strong v-else>构建失败</strong>
          <span v-if="building && buildStatus?.progress?.percent != null">{{ buildStatus.progress.percent }}%</span>
          <span v-if="building && buildObservationError">{{ buildObservationError }}</span>
          <span v-else-if="buildError">{{ buildError }}</span>
        </div>
        <progress v-if="building && buildStatus?.progress?.percent != null" :value="buildStatus.progress.percent" max="100" />
      </div>
      <details v-if="buildStatus?.logTail.length" class="build-log">
        <summary>构建日志</summary>
        <pre>{{ buildStatus.logTail.join("\n") }}</pre>
      </details>
    </div>

    <div v-if="error" class="environment-error release-feedback" role="alert">
      <strong>{{ error.message }}</strong>
      <ul v-if="error.recoverySteps.length"><li v-for="step in error.recoverySteps" :key="step">{{ step }}</li></ul>
    </div>

    <div v-if="loading && !data" class="empty-state" role="status">读取 Release…</div>
    <template v-else-if="data">
      <section class="summary-line" aria-label="Release 概览">
        <div><span class="metric-number">{{ releases.length }}</span><span>Release</span></div>
        <span class="summary-divider"/>
        <div><span class="metric-number">{{ creationReadyCount }}</span><span>可用于创建</span></div>
        <span class="summary-divider"/>
        <StatusLabel v-if="building" tone="warning" label="构建中"/>
        <StatusLabel v-else-if="stale" tone="warning" label="结果已过期"/>
        <span v-else class="muted-text">构建完成后自动刷新</span>
      </section>

      <div v-if="!releases.length" class="release-empty">
        <Package :size="20" />
        <div><strong>暂无 DST Release</strong><span>构建镜像后，Release 会显示在此处。</span></div>
        <button class="button" :disabled="building || !server" @click="startBuild"><Hammer :size="16" />构建镜像</button>
      </div>

      <div v-else class="release-table-wrap">
        <div class="release-table release-table-head" aria-hidden="true">
          <span>版本</span><span>固定镜像</span><span>验证状态</span><span>操作</span>
        </div>
        <article v-for="release in releases" :key="release.releaseId" class="release-table release-row">
          <div class="release-version-cell">
            <strong>{{ release.dstBuild }}</strong>
            <span class="mono release-id">{{ release.releaseId }}</span>
            <span class="release-platform">{{ release.platform }} · {{ release.scriptVersion }}</span>
          </div>
          <div class="release-digest-cell">
            <span class="mono" :title="release.imageDigest">{{ release.image }}</span>
            <span class="mono digest-value" :title="release.imageDigest">{{ shortDigest(release.imageDigest) }}</span>
          </div>
          <div class="release-status-cell">
            <StatusLabel :tone="statusTone(release)" :label="statusLabel(release)" />
            <div class="verification-steps">
              <span :class="{ passed: release.verified }"><CheckCircle :size="14" />镜像</span>
              <span :class="{ passed: release.runtimeVerified }"><CheckCircle :size="14" />可选运行验证</span>
            </div>
          </div>
          <div class="release-actions">
            <template v-if="pendingReleaseId === release.releaseId">
              <span class="release-action-progress" role="status"><span class="state-indicator busy" aria-hidden="true" />{{ pendingStage === "base" ? "检查镜像…" : pendingStage === "runtime" ? "启动隔离分片…" : pendingStage === "delete" ? "核对/删除 Release…" : "登记验证结果…" }}</span>
            </template>
            <template v-else-if="!release.verified">
              <button class="button button-compact" :disabled="!!pendingReleaseId || building" @click="verifyBase(release)"><CheckCircle :size="15" />验证镜像</button>
            </template>
            <template v-else-if="!release.runtimeVerified">
              <label class="runtime-bundle-picker">
                <span>{{ bundleReleaseId === release.releaseId ? bundleName : "选择 ZIP 文件" }}</span>
                <input :ref="element => rememberInput(release.releaseId, element)" type="file" accept=".zip,application/zip" :aria-label="`为 ${release.dstBuild} 选择 Klei 配置 ZIP`" :disabled="!!pendingReleaseId || building" @change="readBundle(release, $event)" />
              </label>
              <button class="button button-compact" :disabled="!!pendingReleaseId || building || bundleReleaseId !== release.releaseId || !bundleBase64" @click="verifyRuntime(release)"><CheckCircle :size="15" />运行验证</button>
              <small v-if="bundleError?.releaseId === release.releaseId" class="release-action-error" role="alert">{{ bundleError.message }}</small>
            </template>
            <span v-else class="release-ready"><CheckCircle :size="15" />运行验证通过</span>
            <button class="icon-button" type="button" aria-label="删除 DST Release" title="删除 DST Release" :disabled="!!pendingReleaseId || building || stale" @click="previewDeleteRelease(release)"><Trash2 :size="15"/></button>
            <small v-if="actionError?.releaseId === release.releaseId" class="release-action-error" role="alert">{{ actionError.message }}</small>
            <small v-if="actionMessage?.releaseId === release.releaseId" class="release-action-success" role="status">{{ actionMessage.message }}</small>
          </div>
        </article>
      </div>
    </template>
    <div v-else-if="!error" class="empty-state">尚未读取 Release</div>
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
    <AppDialog :open="!!cachePreview" title="清理构建缓存" description="删除构建上下文并清理 Docker 未使用的构建缓存；不会删除 Release 镜像。" @update:open="value => { if (!value && !cacheBusy) { cachePreview = null; cacheError = ''; } }">
      <div v-if="cachePreview" class="form-stack">
        <dl class="creation-facts">
          <div><dt>构建目录</dt><dd>{{ formatBytes(cachePreview.contextBytes) }}</dd></div>
          <div><dt>Docker 构建缓存</dt><dd>{{ cachePreview.dockerCache.size ?? "不可用" }}</dd></div>
          <div><dt>可回收</dt><dd>{{ cachePreview.dockerCache.reclaimable ?? "—" }}</dd></div>
          <div><dt>Docker</dt><dd>{{ cachePreview.dockerAvailable ? "可用" : "不可用" }}</dd></div>
          <div><dt>构建状态</dt><dd>{{ cachePreview.buildActive ? "正在构建，暂不可清理" : "空闲" }}</dd></div>
        </dl>
        <p v-if="!cachePreview.allowed" class="environment-error">{{ cachePreview.buildActive ? "当前有镜像构建任务，不能清理缓存。" : "Docker 不可用，不能清理构建缓存。" }}</p>
        <p v-if="cacheError" class="error-strip" role="alert">{{ cacheError }}</p>
      </div>
      <template #footer>
        <button class="button" :disabled="cacheBusy" @click="cachePreview = null; cacheError = ''">取消</button>
        <button class="button danger" :disabled="cacheBusy || !cachePreview?.allowed" @click="confirmCacheCleanup">{{ cacheBusy ? "清理中…" : "清理缓存" }}</button>
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.release-page { min-width: 0; }
.build-state { margin-bottom: var(--space-4); padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.build-state-main { display: flex; align-items: center; gap: var(--space-3); min-height: 32px; }
.build-state-copy { display: flex; flex: 1; min-width: 0; align-items: baseline; gap: var(--space-3); flex-wrap: wrap; }
.build-state-copy strong { font-size: 13px; font-weight: 600; }
.build-state-copy span { color: var(--text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.build-state-main progress { width: 96px; height: 6px; accent-color: var(--success); }
.state-success-icon { color: var(--success); }
.state-error-icon { color: var(--danger); }
.state-indicator { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.state-indicator.busy { background: var(--warning); animation: release-pulse 1.2s ease-in-out infinite; }
.build-log { margin: var(--space-2) 0 0 24px; color: var(--text-secondary); font-size: 12px; }
.build-log summary { width: fit-content; cursor: pointer; }
.build-log pre { max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: var(--space-3); border: 1px solid var(--border); background: var(--surface-muted); }
.release-feedback { margin-bottom: var(--space-3); }
.release-feedback-success { display: flex; align-items: center; gap: var(--space-2); color: var(--success); font-size: 13px; }
.release-summary { display: flex; align-items: center; gap: var(--space-5); flex-wrap: wrap; padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.release-summary > div { display: flex; align-items: baseline; gap: var(--space-2); }
.release-summary > div strong { font-size: 18px; font-variant-numeric: tabular-nums; }
.release-summary > div span { color: var(--text-secondary); font-size: 12px; }
.release-table-wrap { min-width: 0; }
.release-table { display: grid; grid-template-columns: minmax(160px, 1.1fr) minmax(150px, 1fr) minmax(140px, .9fr) minmax(240px, 1.35fr); gap: var(--space-4); align-items: center; }
.release-table-head { min-height: 36px; color: var(--text-secondary); font-size: 11px; border-bottom: 1px solid var(--border); }
.release-row { min-height: 92px; padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.release-version-cell, .release-digest-cell, .release-status-cell, .release-actions { display: flex; min-width: 0; flex-direction: column; gap: 5px; }
.release-version-cell strong { font-size: 14px; font-variant-numeric: tabular-nums; }
.release-id, .release-platform, .release-digest-cell { color: var(--text-secondary); font-size: 11px; }
.release-id, .digest-value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.release-digest-cell > span:first-child { color: var(--text); font-size: 12px; overflow-wrap: anywhere; }
.verification-steps { display: flex; flex-wrap: wrap; gap: var(--space-3); color: var(--text-secondary); font-size: 11px; }
.verification-steps span { display: inline-flex; align-items: center; gap: 4px; }
.verification-steps svg { opacity: .55; }
.verification-steps .passed { color: var(--success); }
.verification-steps .passed svg { opacity: 1; }
.release-actions { align-items: flex-start; }
.button-compact { min-height: 30px; padding: 0 var(--space-2); font-size: 12px; }
.runtime-bundle-picker { display: inline-flex; position: relative; align-items: center; max-width: 100%; min-height: 30px; padding: 0 var(--space-2); border: 1px solid var(--border-strong); border-radius: var(--radius-control); color: var(--text-secondary); font-size: 11px; cursor: pointer; }
.runtime-bundle-picker span { display: block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.runtime-bundle-picker:focus-within { outline: 2px solid var(--focus); outline-offset: 2px; }
.runtime-bundle-picker input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
.release-action-progress, .release-ready { display: inline-flex; align-items: center; gap: var(--space-2); font-size: 12px; }
.release-ready { color: var(--success); }
.release-action-error, .release-action-success { font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
.release-action-error { color: var(--danger); }
.release-action-success { color: var(--success); }
.release-empty { display: flex; min-height: 88px; align-items: center; gap: var(--space-3); border-bottom: 1px solid var(--border); }
.release-empty > div { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 4px; }
.release-empty > div span { color: var(--text-secondary); font-size: 12px; }
.release-empty .button { flex: 0 0 auto; }
.release-feedback :deep(ul) { margin: var(--space-2) 0 0; }
@keyframes release-pulse { 50% { opacity: .35; } }
@media (max-width: 760px) {
  .release-table-head { display: none; }
  .release-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; row-gap: var(--space-3); }
  .release-status-cell, .release-actions { border-top: 1px solid var(--border); padding-top: var(--space-2); }
}
@media (prefers-reduced-motion: reduce) { .state-indicator.busy { animation: none; } }
</style>
