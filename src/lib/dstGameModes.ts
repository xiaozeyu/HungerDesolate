import type { WorldSettings } from "../types/world";

export const dstGameModeOptions: ReadonlyArray<{ label: string; value: WorldSettings["gameMode"] }> = [
  { label: "轻松", value: "relaxed" },
  { label: "生存", value: "survival" },
  { label: "无尽", value: "endless" },
  { label: "荒野", value: "wilderness" },
  { label: "永夜", value: "lightsout" },
];

export const languageOptions = [
  { label: "中文", value: "zh" },
  { label: "英文", value: "en" },
] as const;
