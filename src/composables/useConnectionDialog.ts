import { ref } from "vue";

const serverId = ref<string | null>(null);

export function useConnectionDialog() {
  return {
    serverId,
    openConnection(id: string) { serverId.value = id; },
    closeConnection() { serverId.value = null; },
  };
}
