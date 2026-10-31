import type { InstanceModSummary } from "../types/mods";

export interface ModApplyChange {
  workshopId: string;
  action: "add" | "update" | "configure" | "disable" | "remove";
  actionLabel: string;
  contentDigest: string | null;
  configurationKeys: string[];
}

export interface ModApplyPreview {
  changes: ModApplyChange[];
  shards: Array<"master" | "caves">;
  blockers: string[];
  warnings: string[];
  canApply: boolean;
}

const serverBlockerLabels: Record<string, string> = {
  MOD_ARTIFACT_REQUIRED: "启用的 MOD 缺少主机库内容版本",
};

export function mergeModApplyBlockers(preview: ModApplyPreview, serverBlockers: string[]): string[] {
  const blockers = [...preview.blockers];
  for (const code of serverBlockers) blockers.push(serverBlockerLabels[code] ?? `服务端阻断发布：${code}`);
  return [...new Set(blockers)];
}

export function buildModApplyPreview(input: {
  mods: InstanceModSummary[];
  runtimeStatus: string;
  cavesEnabled: boolean;
  worldDesiredVsDeployed?: string;
}): ModApplyPreview {
  const changes: ModApplyChange[] = [];
  const blockers: string[] = [];
  const warnings: string[] = [];

  for (const mod of input.mods) {
    const desired = mod.desiredEnabled;
    const deployed = mod.deployedEnabled;
    if (desired === null && deployed !== true) continue;
    if (desired === null) {
      changes.push({ workshopId: mod.workshopId, action: "remove", actionLabel: "移除", contentDigest: null, configurationKeys: [] });
      continue;
    }
    if (desired === false) {
      if (deployed === true) changes.push({ workshopId: mod.workshopId, action: "disable", actionLabel: "禁用", contentDigest: null, configurationKeys: [] });
      continue;
    }

    const digest = mod.desiredArtifactDigest ?? mod.preparedDigest;
    const deployedDigest = mod.deployedArtifactDigest;
    const enabled = deployed === true;
    const hasDeployedConfig = mod.deployedConfiguration !== null;
    const configChanged = enabled && hasDeployedConfig
      && stableJson(mod.desiredConfiguration ?? {}) !== stableJson(mod.deployedConfiguration ?? {});
    const configurationKeys = configChanged
      ? [...new Set([...Object.keys(mod.desiredConfiguration ?? {}), ...Object.keys(mod.deployedConfiguration ?? {})])]
        .filter(key => stableJson(mod.desiredConfiguration?.[key]) !== stableJson(mod.deployedConfiguration?.[key]))
        .sort()
      : [];
    const contentChanged = enabled && !!digest && !!deployedDigest && digest !== deployedDigest;
    const unknownDeployedContent = enabled && !deployedDigest && !!digest;

    if (enabled && !configChanged && !contentChanged && !unknownDeployedContent) continue;
    if (!digest || mod.preparedDigest !== digest) {
      blockers.push(`${mod.workshopId} 缺少与期望配置绑定的固定 MOD 内容`);
    }
    if (unknownDeployedContent) warnings.push(`${mod.workshopId} 的已部署内容摘要未知，将按内容更新处理`);
    const action = !enabled ? "add" : contentChanged || unknownDeployedContent ? "update" : "configure";
    changes.push({
      workshopId: mod.workshopId,
      action,
      actionLabel: action === "add" ? "新增" : action === "update" ? "更新内容" : "更新配置",
      contentDigest: digest,
      configurationKeys,
    });
  }

  if (changes.length === 0) {
    return { changes, shards: [], blockers, warnings, canApply: false };
  }
  if (input.runtimeStatus === "unknown" || input.runtimeStatus === "starting") {
    blockers.push("无法确认专服运行状态，暂不能生成可执行预览");
  }
  if (input.worldDesiredVsDeployed === "pending") {
    warnings.push("世界配置有待应用；MOD 单独发布会沿用当前已部署的世界配置");
  }
  return {
    changes,
    shards: input.cavesEnabled ? ["master", "caves"] : ["master"],
    blockers,
    warnings,
    canApply: blockers.length === 0,
  };
}

export function modNeedsApply(mod: InstanceModSummary): boolean {
  if (mod.desiredEnabled === null) return mod.deployedEnabled === true;
  if (mod.desiredEnabled !== mod.deployedEnabled) return true;
  if (mod.desiredEnabled !== true) return false;
  if (mod.deployedConfiguration === null) return true;
  if (stableJson(mod.desiredConfiguration ?? {}) !== stableJson(mod.deployedConfiguration ?? {})) return true;
  const desiredDigest = mod.desiredArtifactDigest ?? mod.preparedDigest;
  return !!desiredDigest && desiredDigest !== mod.deployedArtifactDigest;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
