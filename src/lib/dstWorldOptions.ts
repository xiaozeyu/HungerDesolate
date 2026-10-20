import catalog from "./dst-world-options.json" with { type: "json" };
import type { DstWorldCategory, DstWorldOption, DstWorldShard, ShardWorldConfig, WorldgenConfig, WorldSettings } from "../types/world";

export const dstWorldOptions = catalog as DstWorldOption[];

export function defaultShardWorldConfig(shard: DstWorldShard): ShardWorldConfig {
  return { preset: shard === "master" ? "SURVIVAL_TOGETHER" : "DST_CAVE", worldgenOverrides: {}, worldSettingsOverrides: {} };
}

export function defaultWorldgenConfig(): WorldgenConfig {
  return { master: defaultShardWorldConfig("master"), caves: defaultShardWorldConfig("caves") };
}

export function defaultClusterAdvancedConfig() {
  return { voteEnabled: true, voteKickPercentage: 50, adminList: [] as string[], whitelist: [] as string[], whitelistSlots: 0, blocklist: [] as string[] };
}

export function defaultWorldSettings(cavesEnabled: boolean): WorldSettings {
  return {
    roomName: "",
    description: "",
    gameMode: "survival",
    maxPlayers: 6,
    pvp: false,
    language: "zh",
    pauseWhenEmpty: true,
    cavesEnabled,
    advanced: defaultClusterAdvancedConfig(),
    worldgen: defaultWorldgenConfig(),
  };
}

export function visibleWorldOptions(shard: DstWorldShard, category?: DstWorldCategory): DstWorldOption[] {
  return dstWorldOptions.filter((item) => item.worlds.includes(shard) && (!category || item.category === category));
}

export function optionValue(config: ShardWorldConfig, item: DstWorldOption, shard?: DstWorldShard): string {
  const source = item.category === "worldgen" ? config.worldgenOverrides : config.worldSettingsOverrides;
  return source[item.key] ?? (shard ? item.shardDefaultValues?.[shard] : undefined) ?? item.defaultValue;
}

export function optionDefaultValue(item: DstWorldOption, shard: DstWorldShard): string {
  return item.shardDefaultValues?.[shard] ?? item.defaultValue;
}

export function optionValuesForShard(item: DstWorldOption, shard: DstWorldShard) {
  return item.shardValues?.[shard] ?? item.values;
}

export function updateShardOption(config: ShardWorldConfig, item: DstWorldOption, value: string, shard?: DstWorldShard): ShardWorldConfig {
  const sourceKey = item.category === "worldgen" ? "worldgenOverrides" : "worldSettingsOverrides";
  const nextSource = { ...config[sourceKey] };
  const defaultValue = shard ? optionDefaultValue(item, shard) : item.defaultValue;
  const cleanValue = value.trim();
  if (!cleanValue || cleanValue === defaultValue) delete nextSource[item.key];
  else nextSource[item.key] = cleanValue;
  return { ...config, [sourceKey]: nextSource };
}

export function resetShardGroup(config: ShardWorldConfig, shard: DstWorldShard, group: string, category: DstWorldCategory): ShardWorldConfig {
  return visibleWorldOptions(shard, category).filter((item) => item.group === group)
    .reduce((next, item) => updateShardOption(next, item, optionDefaultValue(item, shard), shard), { ...config });
}

export function changedWorldOptionCount(config: WorldgenConfig, includeCaves: boolean) {
  const count = (item: ShardWorldConfig) => Object.keys(item.worldgenOverrides).length + Object.keys(item.worldSettingsOverrides).length;
  const masterCount = count(config.master);
  const cavesCount = includeCaves ? count(config.caves) : 0;
  return { masterCount, cavesCount, total: masterCount + cavesCount };
}
