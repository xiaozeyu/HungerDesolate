import type { PlayersResponse } from "../api/players";
import type { InstanceTarget } from "../types/contracts";

export function validatePlayersResponse(response: PlayersResponse, requestId: string, target: InstanceTarget): boolean {
  if (response.schemaVersion !== 1 || response.requestId !== requestId
      || response.target.serverId !== target.serverId || response.target.hostId !== target.hostId
      || response.target.instanceId !== target.instanceId || response.configRevision < 0
      || !["fresh", "stale", "unavailable", "disabled"].includes(response.freshness)
      || !["running", "stopped", "degraded", "unknown"].includes(response.data.runtimeStatus)
      || response.data.onlineCount !== response.data.players.length
      || (response.freshness === "disabled") !== (response.data.bridge === null)) {
    return false;
  }
  const players = response.data.players;
  return players.length <= 128
    && new Set(players.map(player => player.userId)).size === players.length
    && players.every(player => !!player.userId && !!player.name
      && ["Master", "Caves"].includes(player.shard)
      && Number.isSafeInteger(player.capturedAtUnixSeconds));
}
