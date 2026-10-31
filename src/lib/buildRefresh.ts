import { getBuildStatus } from "../api/instances";
import { useBuildTasks } from "../composables/useBuildTasks";
import { describeError } from "./commandError";
import type { ServerSummary } from "../types/contracts";

const inFlight = new Set<string>();

export async function refreshBuildTasks(servers: ServerSummary[]): Promise<void> {
  const buildTasks = useBuildTasks();
  await Promise.all(buildTasks.all.value
    .filter(entry => entry.status.status === "building" && !inFlight.has(entry.hostId))
    .map(async entry => {
      const server = servers.find(item => item.serverId === entry.serverId);
      if (!server) return;
      inFlight.add(entry.hostId);
      try {
        const response = await getBuildStatus({
          schemaVersion: 1,
          requestId: crypto.randomUUID(),
          target: { serverId: entry.serverId, hostId: entry.hostId },
          expectedRecordVersion: server.recordVersion,
          releaseId: entry.releaseId,
        });
        buildTasks.receive(entry.serverId, entry.hostId, server.recordVersion, response);
      } catch (cause) {
        buildTasks.error(entry.hostId, describeError(cause).message);
      } finally {
        inFlight.delete(entry.hostId);
      }
    }));
}
