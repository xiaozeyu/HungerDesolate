import { computed } from "vue";
import { useRoute } from "vue-router";
import { useWorkspace } from "./useWorkspace";
import type { InstanceTarget } from "../types/contracts";
export function usePageTarget() {
    const route = useRoute();
    const workspace = useWorkspace();
    const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId));
    const instance = computed(() => server.value?.hostId ? workspace.instances(server.value.hostId).find(item => item.instanceId === route.params.instanceId) : undefined);
    const target = computed<InstanceTarget | null>(() => {
        const currentServer = server.value, currentInstance = instance.value;
        if (!currentServer?.hostId || !currentInstance) return null;
        return { serverId: currentServer.serverId, hostId: currentServer.hostId, instanceId: currentInstance.instanceId };
    });
    return { server, instance, target, hostName: computed(() => server.value?.displayName ?? "主机"), instanceName: computed(() => instance.value?.displayName ?? "专服") };
}
