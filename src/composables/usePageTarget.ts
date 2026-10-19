import { computed } from "vue";
import { useRoute } from "vue-router";
import { useWorkspace } from "./useWorkspace";
export function usePageTarget() {
    const route = useRoute();
    const workspace = useWorkspace();
    const server = computed(() => workspace.servers.value.find(item => item.serverId === route.params.serverId));
    const instance = computed(() => server.value?.hostId ? workspace.instances(server.value.hostId).find(item => item.instanceId === route.params.instanceId) : undefined);
    return { hostName: computed(() => server.value?.displayName ?? "主机"), instanceName: computed(() => instance.value?.displayName ?? "专服") };
}
