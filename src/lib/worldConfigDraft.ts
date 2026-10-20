import { toRaw } from "vue";
import type { WorldSettings } from "../types/world";

export function copyWorldSettings(value: WorldSettings): WorldSettings {
  return structuredClone(toRaw(value));
}
