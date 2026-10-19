export interface ConnectionTicket {serverId: string; recordVersion: number; epoch: number; sequence: number; requestId: string}
export function currentConnection(ticket: ConnectionTicket, current: ConnectionTicket): boolean {
  return ticket.serverId === current.serverId && ticket.recordVersion === current.recordVersion
    && ticket.epoch === current.epoch && ticket.sequence === current.sequence && ticket.requestId === current.requestId;
}
export function matchesConnection(ticket: ConnectionTicket, response: {schemaVersion: number; requestId: string; serverId: string; recordVersion: number}, mutation = false): boolean {
  return response.schemaVersion === 1 && response.requestId === ticket.requestId && ticket.serverId === response.serverId && response.recordVersion === ticket.recordVersion + (mutation ? 1 : 0);
}
