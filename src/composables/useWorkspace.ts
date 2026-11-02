import { computed, readonly, ref, shallowRef } from "vue";
import { listServers, saveServer, deleteServer } from "../api/servers";
import { demoServers, demoInstances } from "../fixtures/workspace";
import { sameTarget } from "../lib/navigationGuard";
import { describeError } from "../lib/commandError";
import type { DeleteServerRequest, InstanceTarget, SaveServerRequest, ServerSummary } from "../types/contracts";
import type { InstanceListResponse } from "../api/instances";
import type { InstanceSummary } from "../types/contracts";
const servers = shallowRef<ServerSummary[]>([]);
const preview = ref(false);
const loading = ref(false);
const error = ref<string | null>(null);
const grant = shallowRef<InstanceTarget | null>(null);
const instanceCache = shallowRef<Record<string, {serverId:string;recordVersion:number;revision:number;items:InstanceSummary[]}>>({});
let grantRecordVersion: number | null = null;
let refreshSequence = 0;
let mutationEpoch = 0;
let initialization: Promise<void> | null = null;
let initialized = false;

export function useWorkspace() {
  const visibleServers = computed(() => preview.value ? [...demoServers] : servers.value);
  async function refresh() {
    const sequence = ++refreshSequence;
    const epoch = mutationEpoch;
    loading.value = true; error.value = null;
    try {
      const result = await listServers();
      if (sequence === refreshSequence && epoch === mutationEpoch) servers.value = result;
    } catch (cause) {
      if (sequence === refreshSequence && epoch === mutationEpoch) error.value = describeError(cause).message;
    } finally {
      if (sequence === refreshSequence && epoch === mutationEpoch) loading.value = false;
    }
  }
  function ensureLoaded() {
    if (initialized) return Promise.resolve();
    initialization ??= refresh().finally(() => { initialized = !error.value; initialization = null; });
    return initialization;
  }
  function invalidatePendingRead() { mutationEpoch++; refreshSequence++; loading.value = false; error.value = null; }
  async function save(request: SaveServerRequest) {
    if (preview.value) throw new Error("请先退出界面预览，再保存真实连接");
    const saved = await saveServer(request);
    invalidatePendingRead();
    // Refresh the authoritative list; an idempotent result may describe an older revision.
    await refresh();
    grant.value = null;
    if (error.value) throw new Error("连接信息已写入，但列表重读失败。请刷新核实；重试会关联原保存操作。");
    return saved;
  }
  async function remove(request: DeleteServerRequest) {
    if (preview.value) throw new Error("界面预览中的示例主机不能删除");
    await deleteServer(request);
    invalidatePendingRead();
    servers.value = servers.value.filter(server => server.serverId !== request.serverId);
    grant.value = null;
    await refresh();
  }
  function setPreview(enabled: boolean) { invalidatePendingRead(); preview.value = enabled; grant.value = null; instanceCache.value = {}; }
  function select(target: InstanceTarget) {
    grant.value = { ...target };
    grantRecordVersion = visibleServers.value.find(server=>server.serverId===target.serverId)?.recordVersion ?? null;
  }
  function clearSelection() { grant.value = null; }
  function invalidateSelection() { grant.value = null; }
  function commitInstances(response: InstanceListResponse) {
    const server=servers.value.find(item=>item.serverId===response.serverId);
    if (!preview.value && (!server || server.recordVersion!==response.recordVersion)) throw new Error("主机连接记录已变化");
    const previous=instanceCache.value[response.hostId];
    if (previous && response.registryRevision<previous.revision) throw new Error("注册表版本已回退，请核对远端数据");
    if (!preview.value) {
      if (server?.hostId && server.hostId!==response.hostId) throw new Error("主机身份与列表响应不一致");
      servers.value=servers.value.map(item=>item.serverId===response.serverId?{...item,hostId:response.hostId}:item);
      instanceCache.value={...instanceCache.value,[response.hostId]:{serverId:response.serverId,recordVersion:response.recordVersion,revision:response.registryRevision,items:response.instances}};
    }
    // A background refresh may finish after the user already selected an instance.
    // Keep that selection; clearing it here causes the first instance-page navigation to be redirected.
  }
  function commitInstanceOverview(serverId: string, recordVersion: number, identity: InstanceSummary) {
    const server = servers.value.find(item => item.serverId === serverId);
    const cached = instanceCache.value[identity.hostId];
    if (!server || server.hostId !== identity.hostId || server.recordVersion !== recordVersion
      || !cached || cached.serverId !== serverId || cached.recordVersion !== recordVersion) return;
    instanceCache.value = {...instanceCache.value, [identity.hostId]: {...cached,
      items: cached.items.map(item => item.instanceId === identity.instanceId ? {...identity} : item)}};
  }
  function bindHost(serverId: string, recordVersion: number, hostId: string) {
    servers.value = servers.value.map(server => server.serverId === serverId && server.recordVersion === recordVersion
      ? { ...server, hostId }
      : server);
  }
  function canEnter(target: InstanceTarget) {
    return sameTarget(grant.value,target) && grantRecordVersion===visibleServers.value.find(server=>server.serverId===target.serverId)?.recordVersion;
  }
  function instances(hostId: string) {
    if (preview.value) return demoInstances(hostId);
    const cached=instanceCache.value[hostId];
    return cached && servers.value.some(server=>server.serverId===cached.serverId && server.hostId===hostId && server.recordVersion===cached.recordVersion)?cached.items:[];
  }
  return { servers: visibleServers, preview: readonly(preview), loading: readonly(loading), error: readonly(error),
    refresh, ensureLoaded, save, remove, setPreview, select, clearSelection, invalidateSelection, commitInstances, commitInstanceOverview, bindHost, canEnter, instances };
}
