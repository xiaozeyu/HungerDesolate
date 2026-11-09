import { computed, ref } from "vue";
import * as api from "../api/ssh";
import { useWorkspace } from "./useWorkspace";
import { useConnectionDialog } from "./useConnectionDialog";
import { describeError, type UserError } from "../lib/commandError";
import type { FingerprintChallenge, HostConnection, ServerSummary } from "../types/contracts";

const TRUST_CODES = new Set(["SSH_TRUST_REQUIRED", "SSH_FINGERPRINT_CHANGED"]);
const CREDENTIAL_CODES = new Set(["SECRET_REQUIRED", "SSH_AUTH_FAILED", "SSH_AUTH_KIND_MISMATCH"]);

const busyServerId = ref("");
const challenge = ref<FingerprintChallenge | null>(null);
const challengeServer = ref<ServerSummary | null>(null);
const error = ref<UserError | null>(null);
const errorServerId = ref("");
const trustError = ref("");
const verifiedServerVersions = ref<Record<string, number>>({});
let pending: {server: ServerSummary; onConnected: ConnectedHandler; ticket: number} | null = null;
let sequence = 0;

type ConnectedHandler = (connection: HostConnection, server: ServerSummary) => void;

function request(server: ServerSummary) {
  return {schemaVersion: 1 as const, requestId: crypto.randomUUID(), serverId: server.serverId,
    expectedRecordVersion: server.recordVersion};
}
function connectOnce(server: ServerSummary) {
  return api.connectServer(request(server));
}

export function useHostConnection() {
  const workspace = useWorkspace();
  const connectionDialog = useConnectionDialog();
  const trustOpen = computed(() => challenge.value !== null);

  function latest(server: ServerSummary) {
    return workspace.servers.value.find(item => item.serverId === server.serverId) ?? server;
  }
  function clearTrust() {
    challenge.value = null;
    challengeServer.value = null;
    trustError.value = "";
    pending = null;
  }
  function clearError() {
    error.value = null;
    errorServerId.value = "";
  }
  function markVerified(server: ServerSummary) {
    verifiedServerVersions.value = {...verifiedServerVersions.value, [server.serverId]: server.recordVersion};
  }
  function clearVerified(serverId: string) {
    const next = {...verifiedServerVersions.value};
    delete next[serverId];
    verifiedServerVersions.value = next;
  }
  function fail(cause: unknown, server: ServerSummary) {
    const failure = describeError(cause);
    error.value = failure;
    errorServerId.value = server.serverId;
    if (CREDENTIAL_CODES.has(failure.code)) connectionDialog.openConnection(server.serverId);
    return failure;
  }
  async function connect(server: ServerSummary, onConnected: ConnectedHandler) {
    if (workspace.preview.value) return;
    clearTrust();
    clearError();
    const ticket = ++sequence;
    clearVerified(server.serverId);
    busyServerId.value = server.serverId;
    try {
      const result = await connectOnce(server);
      if (ticket !== sequence) return;
      markVerified(server);
      await workspace.refresh();
      if (ticket !== sequence) return;
      onConnected(result, latest(server));
    } catch (cause) {
      if (ticket !== sequence) return;
      const failure = describeError(cause);
      if (!TRUST_CODES.has(failure.code)) {
        fail(cause, server);
        return;
      }
      try {
        const scanned = await api.scanFingerprint(request(server));
        if (ticket !== sequence) return;
        challenge.value = scanned;
        challengeServer.value = latest(server);
        pending = {server: latest(server), onConnected, ticket};
      } catch (scanCause) {
        if (ticket === sequence) fail(scanCause, server);
      }
    } finally {
      if (ticket === sequence) busyServerId.value = "";
    }
  }
  async function confirmTrust() {
    const current = pending;
    const target = challengeServer.value;
    const currentChallenge = challenge.value;
    if (!current || !target || !currentChallenge) return;
    const ticket = current.ticket;
    busyServerId.value = target.serverId;
    trustError.value = "";
    try {
      const confirmed = await api.confirmFingerprint({...request(target),
        challengeId: currentChallenge.challengeId, fingerprint: currentChallenge.fingerprint});
      if (ticket !== sequence) return;
      await workspace.refresh();
      if (ticket !== sequence) return;
      const refreshed = workspace.servers.value.find(item => item.serverId === target.serverId);
      const currentServer = refreshed ?? {...target, recordVersion: confirmed.recordVersion};
      const result = await connectOnce(currentServer);
      if (ticket !== sequence) return;
      markVerified(currentServer);
      await workspace.refresh();
      if (ticket !== sequence) return;
      const connectedServer = latest(currentServer);
      clearTrust();
      current.onConnected(result, connectedServer);
    } catch (cause) {
      if (ticket !== sequence) return;
      const failure = describeError(cause);
      if (CREDENTIAL_CODES.has(failure.code)) {
        clearTrust();
        fail(cause, target);
        return;
      }
      trustError.value = [failure.message, ...failure.recoverySteps].join("；");
    } finally {
      if (ticket === sequence) busyServerId.value = "";
    }
  }
  function closeTrust() {
    clearTrust();
  }
  return {busyServerId, challenge, challengeServer, error, errorServerId, trustError, trustOpen,
    verifiedServerVersions, markVerified, connect, confirmTrust, closeTrust, clearError};
}
