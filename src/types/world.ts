export interface WorldConfig {
  revision: number;
  desired: WorldSettings;
  deployed: WorldSettings | null;
  desiredDigest: string;
  deployedDigest: string | null;
  deployedRevision: number | null;
  tokenStatus: "configured" | "missing" | "unknown";
  serverPasswordStatus: "configured" | "missing" | "unknown";
  validationWarnings: string[];
}

export interface ClusterAdvancedConfig {
  voteEnabled: boolean;
  voteKickPercentage: number;
  adminList: string[];
  whitelist: string[];
  whitelistSlots: number;
  blocklist: string[];
}

export type DstWorldCategory = "worldgen" | "worldSettings";
export type DstWorldShard = "master" | "caves";
export type DstWorldEffectScope = "newWorld" | "restart" | "runtime";

export interface ShardWorldConfig {
  preset: string;
  worldgenOverrides: Record<string, string>;
  worldSettingsOverrides: Record<string, string>;
}

export interface WorldgenConfig {
  master: ShardWorldConfig;
  caves: ShardWorldConfig;
}

export interface DstWorldOptionValue {
  label: string;
  value: string;
}

export interface DstWorldOption {
  key: string;
  label: string;
  category: DstWorldCategory;
  group: string;
  groupLabel: string;
  worlds: DstWorldShard[];
  defaultValue: string;
  shardDefaultValues?: Partial<Record<DstWorldShard, string>>;
  valueKind: string;
  values: DstWorldOptionValue[];
  shardValues?: Partial<Record<DstWorldShard, DstWorldOptionValue[]>>;
  effectScope: DstWorldEffectScope;
}

export interface WorldSettings {
  roomName: string;
  description: string;
  gameMode: "relaxed" | "survival" | "endless" | "wilderness" | "lightsout";
  maxPlayers: number;
  pvp: boolean;
  language: "zh" | "en";
  pauseWhenEmpty: boolean;
  cavesEnabled: boolean;
  advanced: ClusterAdvancedConfig;
  worldgen: WorldgenConfig;
}

export type TokenChange =
  | { action: "replace"; value: string }
  | { action: "keep" };

export type ServerPasswordChange = TokenChange;
