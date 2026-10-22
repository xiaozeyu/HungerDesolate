import { call } from "./invoke";
import type { ConnectionRequest } from "../types/contracts";
import type { WorkshopMetadataResponse } from "../types/workshop";

export function getWorkshopMetadata(request: ConnectionRequest & { workshopIds: string[] }): Promise<WorkshopMetadataResponse> {
  return call("get_workshop_metadata", request);
}
