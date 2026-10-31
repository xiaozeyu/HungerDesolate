import { computed, type Ref } from "vue";
import type { ServerSummary } from "../types/contracts";
import { listInstanceReleases } from "../api/instances";
import { useQuery } from "../lib/queryClient";

export function useReleaseList(server: Ref<ServerSummary | null>) {
  const key = computed(() => {
    const target = server.value;
    return target?.hostId
      ? `releases:${target.serverId}:${target.hostId}:${target.recordVersion}`
      : null;
  });

  return useQuery(key, async () => {
    const target = server.value;
    if (!target?.hostId) throw new Error("缺少主机身份，无法读取 Release");
    const requestId = crypto.randomUUID();
    const response = await listInstanceReleases({
      schemaVersion: 1,
      requestId,
      serverId: target.serverId,
      expectedRecordVersion: target.recordVersion,
    });
    if (server.value?.serverId !== target.serverId
        || server.value.hostId !== target.hostId
        || server.value.recordVersion !== target.recordVersion) {
      throw new Error("Release 列表请求上下文已失效");
    }
    if (response.hostId !== target.hostId) throw new Error("Release 列表主机身份不匹配");
    return response;
  });
}
