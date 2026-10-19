import type { CredentialInput, SaveServerRequest, ServerSummary, SshConnectionState, ConnectionRequest } from "../types/contracts";

// Only non-secret progress is retained for retry. The credential is transient input.
export interface NewServerProgress {
  metadataRequest: SaveServerRequest;
  server: ServerSummary | null;
  credentialOperationId: string;
}
export async function saveNewServer(
  progress: NewServerProgress,
  credential: CredentialInput,
  saveMetadata: (request: SaveServerRequest) => Promise<ServerSummary>,
  saveCredential: (request: ConnectionRequest & {operationId: string; credential: CredentialInput}) => Promise<SshConnectionState>,
  isActive: () => boolean,
): Promise<ServerSummary | null> {
  progress.server ??= await saveMetadata(progress.metadataRequest);
  // Closing/navigating after metadata creation must not silently continue saving secrets.
  if (!isActive()) return null;
  const requestId = crypto.randomUUID();
  const server = progress.server;
  const result = await saveCredential({schemaVersion:1, requestId, serverId:server.serverId,
    expectedRecordVersion:server.recordVersion, operationId:progress.credentialOperationId, credential});
  if (result.schemaVersion !== 1 || result.requestId !== requestId || result.serverId !== server.serverId
    || result.recordVersion !== server.recordVersion + 1) throw new Error("凭据响应与当前主机不匹配，请刷新核实");
  return {...server, recordVersion:result.recordVersion};
}
