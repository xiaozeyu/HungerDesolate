import type { InstanceTarget } from "../types/contracts";
import { sameTarget } from "./navigationGuard.ts";
export interface QueryTicket {
    target: InstanceTarget;
    contextEpoch: number;
    requestSeq: number;
    requestId: string;
}
export function isCurrentQuery(ticket: QueryTicket, current: QueryTicket | null, contextEpoch: number): boolean {
    return current !== null && ticket.contextEpoch === contextEpoch
        && current.contextEpoch === contextEpoch && current.requestId === ticket.requestId
        && current.requestSeq === ticket.requestSeq && sameTarget(current.target, ticket.target);
}
