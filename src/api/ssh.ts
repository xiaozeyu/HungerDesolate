import { call } from "./invoke";
import type { CleanupStatus, ConnectionRequest, CredentialInput, FingerprintChallenge, HostConnection, SshConnectionState } from "../types/contracts";
export function getSshState(request: ConnectionRequest): Promise<SshConnectionState> { return call("get_ssh_connection_state", request); }
export function scanFingerprint(request: ConnectionRequest): Promise<FingerprintChallenge> { return call("scan_server_fingerprint", request); }
export function confirmFingerprint(request: ConnectionRequest & {challengeId: string; fingerprint: string}): Promise<SshConnectionState> { return call("confirm_server_fingerprint", request); }
export function saveCredentials(request: ConnectionRequest & {operationId: string; credential: CredentialInput}): Promise<SshConnectionState> { return call("save_server_credentials", request); }
export function connectServer(request: ConnectionRequest): Promise<HostConnection> { return call("connect_server", request); }
export function cleanupCredentials(): Promise<CleanupStatus> { return call("cleanup_credentials"); }
export function getCleanupStatus(): Promise<CleanupStatus> { return call("get_credential_cleanup_status"); }
export function pickPrivateKeyFile(): Promise<string | null> { return call("pick_private_key_file"); }
