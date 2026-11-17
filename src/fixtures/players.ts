import type { PlayersResponse } from "../api/players";
import type { InstanceTarget } from "../types/contracts";

export function demoPlayers(target: InstanceTarget): PlayersResponse {
  return {
    schemaVersion: 1,
    requestId: "preview-players",
    target,
    snapshotId: "preview-players",
    capturedAt: new Date().toISOString(),
    source: "remote",
    freshness: "fresh",
    configRevision: 1,
    data: {
      bridge: { enabled: true, version: "1.0.0", digest: "sha256:" + "0".repeat(64), installedAt: null },
      runtimeStatus: "running",
      onlineCount: 2,
      players: [
        { userId: "KU_preview_1", name: "棒打鲜橙", character: "wathgrithr", shard: "Master", capturedAtUnixSeconds: Math.floor(Date.now() / 1000) },
        { userId: "KU_preview_2", name: "Wilson", character: "wilson", shard: "Caves", capturedAtUnixSeconds: Math.floor(Date.now() / 1000) },
      ],
      warnings: [],
    },
  };
}
