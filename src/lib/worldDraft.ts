import { copyWorldSettings } from "./worldConfigDraft";
import type { InstanceTarget } from "../types/contracts";
import type { WorldSettings } from "../types/world";

interface StoredDraft {
  settings: WorldSettings;
  updatedAt: number;
}

const drafts = new Map<string, StoredDraft>();

function key(target: InstanceTarget, revision: number): string {
  return `${target.serverId}:${target.hostId}:${target.instanceId}@${revision}`;
}

function targetPrefix(target: InstanceTarget): string {
  return `${target.serverId}:${target.hostId}:${target.instanceId}@`;
}

export function readWorldDraft(target: InstanceTarget, revision: number): WorldSettings | null {
  const entry = drafts.get(key(target, revision));
  return entry ? copyWorldSettings(entry.settings) : null;
}

export function writeWorldDraft(target: InstanceTarget, revision: number, settings: WorldSettings): void {
  drafts.set(key(target, revision), { settings: copyWorldSettings(settings), updatedAt: Date.now() });
}

export function clearWorldDraft(target: InstanceTarget): void {
  const prefix = targetPrefix(target);
  for (const draftKey of drafts.keys()) {
    if (draftKey.startsWith(prefix)) drafts.delete(draftKey);
  }
}
