import { matchesConnection, type ConnectionTicket } from "./connectionGuard.ts";
import type { HostInitializationResponse } from "../api/hosts.ts";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const root = "/var/lib/famine-hub";
export function matchesInitialization(ticket: ConnectionTicket, response: HostInitializationResponse, expectedHostId: string | null): boolean {
  if (!matchesConnection(ticket,response)) return false;
  const plan = response.plan;
  if (plan === null) return true;
  return uuid.test(plan.planId) && uuid.test(plan.operationId) && uuid.test(plan.hostId)
    && (!expectedHostId || expectedHostId === plan.hostId)
    && ["preview","incomplete","succeeded"].includes(plan.status)
    && Number.isSafeInteger(plan.expiresAtUnixSeconds) && plan.expiresAtUnixSeconds > 0
    && plan.managementRoot === root && Array.isArray(plan.paths)
    && plan.paths.length === 4 && plan.paths.every((path,index)=>path===[root,`${root}/host.json`,`${root}/registry.json`,`${root}.bootstrap`][index]);
}
