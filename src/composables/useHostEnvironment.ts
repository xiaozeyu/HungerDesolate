import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from "vue";
import { getHostEnvironment, getHostInitialization, previewHostInitialization, initializeHost, previewDockerInstallation, startDockerInstallation, getDockerInstallationStatus, type DockerInstallTask, type DockerInstallationPreviewResponse, type HostInitializationPlan } from "../api/hosts";
import { demoEnvironment } from "../fixtures/environment";
import { currentConnection, type ConnectionTicket } from "../lib/connectionGuard";
import { matchesEnvironment } from "../lib/environmentView";
import { matchesInitialization } from "../lib/hostInitialization";
import { matchesDockerInstallationPreview, matchesDockerInstallStatus } from "../lib/dockerInstallation";
import { describeError, type UserError } from "../lib/commandError";
import type { HostEnvironment, ServerSummary, TaskSnapshot } from "../types/contracts";
import { useTasks } from "./useTasks";
import { useDockerTasks } from "./useDockerTasks";
import { useQuery } from "../lib/queryClient";

export function useHostEnvironment(server: Ref<ServerSummary | null>, preview: Readonly<Ref<boolean>>, onHostBound?: (hostId: string, recordVersion: number) => void) {
  const plan = shallowRef<HostInitializationPlan | null>(null);
  const planOpen = ref(false), initializing = ref(false), outcomeUnknown = ref(false);
  const initializationError = shallowRef<UserError | null>(null);
  const dockerPreview = shallowRef<DockerInstallationPreviewResponse | null>(null);
  const dockerPreviewOpen = ref(false), dockerPreviewLoading = ref(false);
  const dockerPreviewError = shallowRef<UserError | null>(null);
  const dockerTasks = useDockerTasks();
  const dockerTask = computed<DockerInstallTask | null>(() => dockerTasks.forHost(server.value?.hostId)?.task ?? null);
  const dockerStarting = ref(false);
  const dockerTaskLoading = ref(false), dockerTaskError = shallowRef<UserError | null>(null);
  const taskStore = useTasks();
  let epoch = 0, sequence = 0, requestId = "";
  let dockerEpoch = 0, dockerSequence = 0, dockerRequestId = "";
  let taskEpoch = 0, taskSequence = 0, taskRequestId = "";

  const key = computed(() => {
    const target = server.value;
    return target
      ? `hostenv:${target.serverId}:${target.hostId ?? "none"}:${target.recordVersion}:${preview.value ? "preview" : "live"}`
      : null;
  });

  function toTaskSnapshot(task: DockerInstallTask, target: ServerSummary): TaskSnapshot | null {
    if (!target.hostId) return null;
    const startedAt = new Date(task.startedAtUnixSeconds * 1000).toISOString();
    const updatedAt = new Date(task.updatedAtUnixSeconds * 1000).toISOString();
    const terminal = ["succeeded", "failed", "interrupted"].includes(task.status);
    return { schemaVersion: 1, taskId: task.taskId, operationId: task.taskId, target: { serverId: target.serverId, hostId: target.hostId }, operation: "dockerInstall", status: task.status, stage: task.phase, lastCompletedStage: null, progress: { kind: "indeterminate" }, sequence: task.sequence, createdAt: startedAt, startedAt, finishedAt: terminal ? updatedAt : null, cancelAllowed: false, observation: "live" };
  }

  function current(ticket: ConnectionTicket) {
    return !!server.value && currentConnection(ticket, { serverId: server.value.serverId, recordVersion: server.value.recordVersion, epoch, sequence, requestId });
  }

  const query = useQuery<HostEnvironment>(key, async () => {
    const target = server.value;
    if (!target) throw new Error("缺少主机连接");
    const previewMode = preview.value;
    const expectedHostId = target.hostId;
    const requestId = crypto.randomUUID();
    const request = { schemaVersion: 1 as const, requestId, serverId: target.serverId, expectedRecordVersion: target.recordVersion };
    const result = previewMode ? await Promise.resolve(demoEnvironment(request, expectedHostId)) : await getHostEnvironment(request);
    if (server.value?.serverId !== target.serverId || server.value.recordVersion !== target.recordVersion
        || preview.value !== previewMode) throw new Error("环境检查请求上下文已失效");
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch: 0, sequence: 0, requestId };
    if (!matchesEnvironment(ticket, expectedHostId, result)) throw new Error("环境检查响应身份或版本不匹配");
    if (result.hostId) onHostBound?.(result.hostId, target.recordVersion);
    if (previewMode) return result;
    const operation = await getHostInitialization(request);
    if (server.value?.serverId !== target.serverId || server.value.recordVersion !== target.recordVersion
        || preview.value !== previewMode) throw new Error("初始化检查请求上下文已失效");
    if (!matchesInitialization(ticket, operation, result.hostId)) throw new Error("初始化响应身份或版本不匹配");
    plan.value = operation.plan;
    if (operation.plan?.status !== "succeeded") return result;
    const checked = await getHostEnvironment(request);
    if (server.value?.serverId !== target.serverId || server.value.recordVersion !== target.recordVersion
        || preview.value !== previewMode) throw new Error("初始化后环境检查上下文已失效");
    if (!matchesEnvironment(ticket, expectedHostId, checked) || checked.hostId !== operation.plan.hostId) throw new Error("初始化后主机身份不匹配");
    onHostBound?.(checked.hostId, target.recordVersion);
    return checked;
  });

  const data = query.data;
  const loading = query.loading;
  const stale = query.stale;
  const error = computed(() => initializationError.value ?? query.error.value);

  async function refresh() {
    initializationError.value = null;
    await query.refresh();
  }

  async function prepareInitialization() {
    const target = server.value;
    if (!target || preview.value || loading.value || initializing.value) return;
    if (plan.value?.status === "incomplete") { planOpen.value = true; return; }
    requestId = crypto.randomUUID();
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch, sequence: ++sequence, requestId };
    initializing.value = true; initializationError.value = null;
    try {
      const result = await previewHostInitialization({ schemaVersion: 1, requestId: ticket.requestId, serverId: target.serverId, expectedRecordVersion: target.recordVersion });
      if (!current(ticket)) return;
      if (!matchesInitialization(ticket, result, target.hostId) || result.plan?.status !== "preview") throw new Error("初始化预览响应无效");
      plan.value = result.plan; outcomeUnknown.value = false; planOpen.value = true;
    } catch (cause) { if (current(ticket)) initializationError.value = describeError(cause); }
    finally { if (current(ticket)) initializing.value = false; }
  }

  async function confirmInitialization() {
    const target = server.value, selected = plan.value;
    if (!target || !selected || preview.value || initializing.value || loading.value || outcomeUnknown.value || selected.status === "succeeded") return;
    requestId = crypto.randomUUID();
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch, sequence: ++sequence, requestId };
    initializing.value = true; initializationError.value = null;
    try {
      const result = await initializeHost({ schemaVersion: 1, requestId: ticket.requestId, serverId: target.serverId, expectedRecordVersion: target.recordVersion, planId: selected.planId });
      if (!current(ticket)) return;
      if (!matchesInitialization(ticket, result, target.hostId) || result.plan?.status !== "succeeded" || result.plan.operationId !== selected.operationId || result.plan.planId !== selected.planId || result.plan.hostId !== selected.hostId) throw new Error("初始化结果身份不匹配，请重查原操作");
      planOpen.value = false; initializing.value = false;
      await refresh();
    } catch (cause) {
      if (current(ticket)) { initializationError.value = describeError(cause); outcomeUnknown.value = true; }
    } finally { if (current(ticket)) initializing.value = false; }
  }

  async function previewDockerInstall() {
    const target = server.value;
    if (!target || preview.value || loading.value || initializing.value || dockerPreviewLoading.value || dockerTaskLoading.value || dockerTask.value && ["queued", "running"].includes(dockerTask.value.status)) return;
    dockerRequestId = crypto.randomUUID();
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch: dockerEpoch, sequence: ++dockerSequence, requestId: dockerRequestId };
    const currentDocker = () => !!server.value && currentConnection(ticket, { serverId: server.value.serverId, recordVersion: server.value.recordVersion, epoch: dockerEpoch, sequence: dockerSequence, requestId: dockerRequestId });
    dockerPreviewLoading.value = true; dockerPreviewError.value = null;
    try {
      const result = await previewDockerInstallation({ schemaVersion: 1, requestId: ticket.requestId, serverId: target.serverId, expectedRecordVersion: target.recordVersion });
      if (!currentDocker()) return;
      if (!matchesDockerInstallationPreview(ticket, target.hostId, result)) throw new Error("Docker 安装预检响应身份或内容无效");
      dockerTaskError.value = null; dockerPreview.value = result; dockerPreviewOpen.value = true;
    } catch (cause) { if (currentDocker()) dockerPreviewError.value = describeError(cause); }
    finally { if (currentDocker()) dockerPreviewLoading.value = false; }
  }

  async function refreshDockerTask(preserveError = false, operationId = dockerTask.value?.taskId) {
    const target = server.value;
    if (!target?.hostId || preview.value || dockerStarting.value) return;
    taskRequestId = crypto.randomUUID();
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch: taskEpoch, sequence: ++taskSequence, requestId: taskRequestId };
    const currentTask = () => !!server.value && currentConnection(ticket, { serverId: server.value.serverId, recordVersion: server.value.recordVersion, epoch: taskEpoch, sequence: taskSequence, requestId: taskRequestId });
    dockerTaskLoading.value = true;
    if (!preserveError) dockerTaskError.value = null;
    try {
      const result = await getDockerInstallationStatus({ schemaVersion: 1, requestId: ticket.requestId, serverId: target.serverId, expectedRecordVersion: target.recordVersion, ...(operationId ? { operationId } : {}) });
      if (!currentTask() || server.value?.hostId !== target.hostId) return;
      if (result.hostId !== target.hostId) return;
      if (!matchesDockerInstallStatus(ticket, target.hostId, result) || (operationId && result.task && result.task.taskId !== operationId)) throw new Error("Docker 安装任务响应身份或状态无效");
      const previousTask = dockerTask.value, nextTask = result.task;
      if (!nextTask && (!operationId || previousTask?.taskId === operationId)) dockerTasks.clear(target.hostId);
      if (previousTask && nextTask && previousTask.taskId === nextTask.taskId && nextTask.sequence < previousTask.sequence) return;
      if (previousTask && nextTask && previousTask.taskId === nextTask.taskId && ["succeeded", "failed", "interrupted"].includes(previousTask.status) && nextTask.status !== previousTask.status) return;
      if (nextTask) {
        dockerTasks.receive(target.serverId, target.hostId, nextTask);
        const snapshot = toTaskSnapshot(nextTask, target); if (snapshot) taskStore.receive(snapshot);
      }
      if (preserveError && operationId && nextTask?.taskId === operationId) dockerTaskError.value = null;
    } catch (cause) { if (currentTask() && (!preserveError || !dockerTaskError.value)) dockerTaskError.value = describeError(cause); }
    finally { if (currentTask()) dockerTaskLoading.value = false; }
  }

  watch(() => dockerTask.value?.status, (next, previous) => {
    if (next === "succeeded" && previous !== "succeeded" && !loading.value) void refresh();
  }, { flush: "sync" });

  function clearDockerTask() {
    const hostId = server.value?.hostId;
    if (hostId) dockerTasks.clear(hostId);
  }

  async function startDockerInstall() {
    const target = server.value, planValue = dockerPreview.value;
    if (!target?.hostId || !planValue || planValue.preview.status !== "ready" || !planValue.planId || !planValue.operationId || dockerStarting.value || dockerPreviewLoading.value || dockerTaskLoading.value || initializing.value || loading.value) return;
    taskRequestId = crypto.randomUUID();
    const ticket = { serverId: target.serverId, recordVersion: target.recordVersion, epoch: taskEpoch, sequence: ++taskSequence, requestId: taskRequestId };
    const currentTask = () => !!server.value && currentConnection(ticket, { serverId: server.value.serverId, recordVersion: server.value.recordVersion, epoch: taskEpoch, sequence: taskSequence, requestId: taskRequestId });
    dockerStarting.value = true; dockerTaskLoading.value = true; dockerTaskError.value = null;
    try {
      const result = await startDockerInstallation({ schemaVersion: 1, requestId: ticket.requestId, serverId: target.serverId, expectedRecordVersion: target.recordVersion, planId: planValue.planId, operationId: planValue.operationId, snapshotDigest: planValue.preview.snapshotDigest, expiresAtUnixSeconds: planValue.preview.expiresAtUnixSeconds });
      if (!matchesDockerInstallStatus(ticket, target.hostId, result) || result.task?.taskId !== planValue.operationId) throw new Error("Docker 安装任务响应身份不匹配");
      if (result.task) {
        dockerTasks.receive(target.serverId, target.hostId, result.task);
        const snapshot = toTaskSnapshot(result.task, target); if (snapshot) taskStore.receive(snapshot);
      }
      if (!currentTask()) return;
    } catch (cause) {
      if (currentTask()) dockerTaskError.value = describeError(cause);
    } finally { if (currentTask()) { dockerStarting.value = false; dockerTaskLoading.value = false; } }
    if (currentTask() && dockerTaskError.value) await refreshDockerTask(true, planValue.operationId);
  }

  watch([() => server.value?.serverId, () => server.value?.recordVersion, () => server.value?.hostId, () => preview.value], () => {
    epoch++; sequence++;
    initializationError.value = null;
    plan.value = null; planOpen.value = false; initializing.value = false; outcomeUnknown.value = false;
    dockerEpoch++; dockerSequence++; dockerPreview.value = null; dockerPreviewOpen.value = false; dockerPreviewLoading.value = false; dockerPreviewError.value = null;
    taskEpoch++; taskSequence++; dockerStarting.value = false; dockerTaskLoading.value = false; dockerTaskError.value = null;
    void refreshDockerTask();
  }, { immediate: true });

  onBeforeUnmount(() => { epoch++; sequence++; dockerEpoch++; dockerSequence++; taskEpoch++; taskSequence++; });

  return { data, loading, error, stale, refresh, plan, planOpen, initializing, outcomeUnknown, prepareInitialization, confirmInitialization, dockerPreview, dockerPreviewOpen, dockerPreviewLoading, dockerPreviewError, previewDockerInstall, dockerTask, dockerStarting, dockerTaskLoading, dockerTaskError, startDockerInstall, refreshDockerTask, clearDockerTask };
}
