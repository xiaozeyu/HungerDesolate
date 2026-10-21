import type { InstanceStartResponse } from "../api/instances.ts";
import type { InstanceRuntimeSnapshot, InstanceTarget } from "../types/contracts.ts";

export function hasReadyShardListeners(
  result: Pick<InstanceStartResponse, "runningShards" | "listeners">,
  cavesEnabled: boolean,
): boolean {
  const expected: Array<"master" | "caves"> = cavesEnabled ? ["master", "caves"] : ["master"];
  return result.runningShards.length === expected.length
    && new Set(result.runningShards).size === expected.length
    && Object.keys(result.listeners).length === expected.length
    && expected.every(shard => {
      const ports = result.listeners[shard];
      const count = shard === "master" && cavesEnabled ? 3 : 2;
      return result.runningShards.includes(shard) && Array.isArray(ports)
        && ports.length === count && new Set(ports).size === count
        && ports.every(port => Number.isInteger(port) && port >= 1 && port <= 65535);
    });
}

export function matchesRuntimeSnapshot(value: InstanceRuntimeSnapshot | undefined, target: InstanceTarget, cavesEnabled: boolean): value is InstanceRuntimeSnapshot {
  if (!value || value.hostId !== target.hostId || value.instanceId !== target.instanceId
    || !Number.isSafeInteger(value.configRevision) || value.configRevision < 0
    || !Number.isSafeInteger(value.capturedAtUnixSeconds) || value.capturedAtUnixSeconds <= 0
    || typeof value.capturedAt !== "string" || Date.parse(value.capturedAt) !== value.capturedAtUnixSeconds * 1000
    || !["unknown","stopped","starting","running","degraded","stopping","failed"].includes(value.status)
    || !["unverified","processOnly","localVerified"].includes(value.verificationLevel)
    || value.publicConnectivityVerified !== false || !Array.isArray(value.shards) || value.shards.length > 2
    || !Array.isArray(value.ports) || value.ports.length > 7 || !Array.isArray(value.warnings) || value.warnings.length > 16
    || !["unknown","containerFile","releaseMetadata"].includes(value.buildSource)
    || (value.installedBuild !== null && !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/.test(value.installedBuild))) return false;
  const roles = cavesEnabled ? ["master","caves"] : ["master"];
  if (new Set(value.shards.map(shard=>shard.shard)).size !== value.shards.length
    || value.shards.some(shard=>!roles.includes(shard.shard)
      || !["created","running","paused","restarting","removing","exited","dead"].includes(shard.containerStatus)
      || (shard.processRunning !== null && typeof shard.processRunning !== "boolean")
      || typeof shard.ready !== "boolean" || !Array.isArray(shard.missingPorts) || shard.missingPorts.length > 3
      || shard.missingPorts.some(port=>!Number.isInteger(port)||port<1||port>65535)
      || (shard.ready && (shard.processRunning !== true || shard.containerStatus !== "running" || shard.missingPorts.length)))
    || new Set(value.ports.map(port=>`${port.shard}:${port.purpose}`)).size !== value.ports.length
    || value.ports.some(port=>!roles.includes(port.shard)||!["game","steamMaster","steamAuth","shard"].includes(port.purpose)
      || port.protocol !== "udp" || !Number.isInteger(port.containerPort) || port.containerPort<1 || port.containerPort>65535
      || (port.hostPort!==null && (!Number.isInteger(port.hostPort)||port.hostPort<1||port.hostPort>65535))
      || (port.published!==null && typeof port.published!=="boolean") || (port.listening!==null && typeof port.listening!=="boolean")
      || typeof port.required!=="boolean" || (port.purpose==="shard" && (port.hostPort!==null||port.published!==false)))) return false;
  return value.status !== "running" || (value.verificationLevel === "localVerified" && value.shards.length === roles.length
    && value.shards.every(shard=>shard.ready) && hasReadyShardListeners({runningShards:value.shards.map(shard=>shard.shard),
      listeners:Object.fromEntries(roles.map(shard=>[shard,value.ports.filter(port=>port.shard===shard&&port.required).map(port=>port.containerPort)]))},cavesEnabled)
    && value.ports.filter(port=>port.required).every(port=>port.listening===true&&(port.hostPort===null||port.published===true)));
}

export function firewallUdpPorts(value: InstanceRuntimeSnapshot | undefined): number[] {
  return [...new Set((value?.ports ?? []).filter(port=>port.hostPort!==null && port.published===true)
    .map(port=>port.hostPort!))].sort((a,b)=>a-b);
}
