import { isTauri } from "@tauri-apps/api/core";
import { call } from "./invoke";
import type { DeleteServerRequest, SaveServerRequest, ServerSummary } from "../types/contracts";
export function listServers(): Promise<ServerSummary[]> {
    return isTauri() ? call<ServerSummary[]>("list_servers") : Promise.resolve([]);
}
export function saveServer(request: SaveServerRequest): Promise<ServerSummary> {
    return call("save_server", request);
}
export function deleteServer(request: DeleteServerRequest): Promise<{ serverId: string }> {
    return call("delete_server", request);
}
