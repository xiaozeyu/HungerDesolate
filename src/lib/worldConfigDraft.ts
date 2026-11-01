import type { WorldSettings } from "../types/world";

export function copyWorldSettings(value: WorldSettings): WorldSettings {
  // WorldSettings is a JSON DTO. Serialization also unwraps nested Vue proxies
  // introduced when an edited shard is replaced with a spread of reactive data.
  return JSON.parse(JSON.stringify(value));
}
