export interface WorldConfig {
  revision: number;
  desired: WorldSettings;
  deployed: WorldSettings | null;
  desiredDigest: string;
  deployedDigest: string | null;
  tokenStatus: "configured" | "missing" | "unknown";
  shardKeyStatus: "configured" | "missing" | "unknown";
  validationWarnings: string[];
}

export interface WorldSettings {
  roomName: string;
  description: string;
  gameMode: "survival" | "endless" | "wilderness";
  maxPlayers: number;
  pvp: boolean;
  cavesEnabled: boolean; // 只读，容器创建时确定
  masterRules: WorldRules;
  cavesRules: WorldRules | null;
}

export interface WorldRules {
  worldSize: "small" | "medium" | "large" | "huge";
  seasonStart: "autumn" | "winter" | "spring" | "summer";
  dayLength: "default" | "long" | "short";
}

export type TokenChange =
  | { action: "replace"; value: string }
  | { action: "keep" };

export interface WorldConfigDraft extends WorldSettings {
  tokenChange: TokenChange | null;
}
