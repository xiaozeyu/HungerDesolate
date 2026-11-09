import type { InstanceOverview, InstanceSummary, ServerSummary } from "../types/contracts";
export const demoServers: readonly ServerSummary[] = [
    { serverId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", hostId: "11111111-1111-4111-8111-111111111111", displayName: "上海服务器", host: "192.0.2.24", sshPort: 22, username: "dst-admin", authKind: "key", recordVersion: 1, connectionState: "disconnected" },
    { serverId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", hostId: "33333333-3333-4333-8333-333333333333", displayName: "家用主机", host: "192.0.2.18", sshPort: 22, username: "dst-admin", authKind: "key", recordVersion: 1, connectionState: "disconnected" },
];
export function demoInstances(hostId: string): InstanceSummary[] {
    if (!demoServers.some(server => server.hostId === hostId))
        return [];
    const secondary = hostId === demoServers[1]?.hostId;
    return [
        { instanceId: secondary ? "66666666-6666-4666-8666-666666666666" : "22222222-2222-4222-8222-222222222222", displayName: "生存一服", runtimeStatus: "running", lifecycle: "ready", players: 3, installedBuild: "示例 build" },
        { instanceId: secondary ? "77777777-7777-4777-8777-777777777777" : "44444444-4444-4444-8444-444444444444", displayName: "测试服", runtimeStatus: "stopped", lifecycle: "ready", players: null, installedBuild: "示例 build" },
        { instanceId: secondary ? "88888888-8888-4888-8888-888888888888" : "55555555-5555-4555-8555-555555555555", displayName: "新建中的专服", runtimeStatus: "unknown", lifecycle: "createFailed", players: null, installedBuild: null },
    ].map(item => ({ ...item, hostId, maxPlayers: 6, gameMode: "survival", cavesEnabled: item.lifecycle !== "createFailed", lastSyncedAt: null, activeTaskId: null, manifestAvailable: item.lifecycle !== "createFailed", configRevision: item.lifecycle !== "createFailed" ? 1 : null } as InstanceSummary));
}
export function demoOverview(instance: InstanceSummary): InstanceOverview {
    return { identity: instance, verificationLevel: "processOnly", blockers: ["演示数据没有远端运行证据"], desiredVsDeployed: "unknown" };
}
