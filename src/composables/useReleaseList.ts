import { ref, computed, watch, onBeforeUnmount, type Ref } from "vue";
import type { ServerSummary } from "../types/contracts";
import { listInstanceReleases, type InstanceReleaseCatalogResponse } from "../api/instances";
import { currentConnection, type ConnectionTicket } from "../lib/connectionGuard";
import { describeError, type UserError } from "../lib/commandError";

export function useReleaseList(server: Ref<ServerSummary | null>) {
  const data = ref<InstanceReleaseCatalogResponse | null>(null);
  const error = ref<UserError | null>(null);
  const loading = ref(false);
  const stale = ref(false);

  let epoch = 0;
  let sequence = 0;
  let requestId = "";

  function current(ticket: ConnectionTicket) {
    return !!server.value && currentConnection(ticket, {
      serverId: server.value.serverId,
      recordVersion: server.value.recordVersion,
      epoch,
      sequence,
      requestId
    });
  }

  async function refresh() {
    const target = server.value;
    if (!target || !target.hostId) {
      data.value = null;
      error.value = null;
      return;
    }

    requestId = crypto.randomUUID();
    const ticket: ConnectionTicket = {
      serverId: target.serverId,
      recordVersion: target.recordVersion,
      epoch,
      sequence: ++sequence,
      requestId
    };

    loading.value = true;
    error.value = null;

    try {
      const response = await listInstanceReleases({
        schemaVersion: 1,
        requestId: ticket.requestId,
        serverId: target.serverId,
        expectedRecordVersion: target.recordVersion,
      });

      if (!current(ticket)) return;

      if (response.hostId !== target.hostId) {
        throw new Error("主机身份不匹配");
      }

      data.value = response;
      stale.value = false;
    } catch (cause) {
      if (current(ticket)) {
        error.value = describeError(cause);
        stale.value = data.value !== null;
      }
    } finally {
      if (current(ticket)) {
        loading.value = false;
      }
    }
  }

  watch(
    () => [server.value?.serverId, server.value?.hostId, server.value?.recordVersion],
    () => {
      epoch++;
      sequence++;
      data.value = null;
      error.value = null;
      loading.value = false;
      stale.value = false;
      void refresh();
    },
    { immediate: true }
  );

  onBeforeUnmount(() => {
    epoch++;
    sequence++;
  });

  return {
    data: computed(() => data.value),
    error: computed(() => error.value),
    loading: computed(() => loading.value),
    stale: computed(() => stale.value),
    refresh,
  };
}
