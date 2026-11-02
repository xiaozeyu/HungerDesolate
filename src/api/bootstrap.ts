import { call } from "./invoke";
import type { BootstrapSnapshot } from "../types/contracts";

export function getBootstrapSnapshot(): Promise<BootstrapSnapshot> {
  return call("get_bootstrap_snapshot");
}
