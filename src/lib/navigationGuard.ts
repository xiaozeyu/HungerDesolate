import type { ConnectionTaskTarget, HostTarget, InstanceTarget, TaskTarget } from "../types/contracts";
export function sameTarget(left: InstanceTarget | null, right: InstanceTarget | null): boolean {
    if (left === null || right === null || left.serverId !== right.serverId || left.hostId !== right.hostId)
        return false;
    return left.instanceId === right.instanceId;
}
export function sameTaskTarget(left: TaskTarget, right: TaskTarget): boolean {
    if ("connectionVersion" in left || "connectionVersion" in right) {
        return "connectionVersion" in left && "connectionVersion" in right
            && sameConnectionTaskTarget(left,right);
    }
    if ("instanceId" in left || "instanceId" in right) {
        return "instanceId" in left && "instanceId" in right && sameTarget(left,right);
    }
    return sameHostTarget(left,right);
}
function sameHostTarget(left: HostTarget, right: HostTarget): boolean {
    return left.serverId === right.serverId && left.hostId === right.hostId;
}
function sameConnectionTaskTarget(left: ConnectionTaskTarget, right: ConnectionTaskTarget): boolean {
    return left.serverId === right.serverId && left.connectionVersion === right.connectionVersion
        && left.verifiedFingerprint === right.verifiedFingerprint;
}
export function isUuid(value: string): boolean {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
}
