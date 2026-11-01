import { call } from "./invoke";
import type { ConnectionRequest } from "../types/contracts";
import type { WorkshopMetadataResponse } from "../types/workshop";

export function getWorkshopMetadata(request: ConnectionRequest & { workshopIds: string[] }): Promise<WorkshopMetadataResponse> {
  return call("get_workshop_metadata", request);
}

export interface WorkshopCollectionResponse {
  schemaVersion: 1;
  requestId: string;
  collectionId: string;
  children: string[];
}

export function getWorkshopCollection(request: {
  schemaVersion: 1; requestId: string; collectionId: string;
}): Promise<WorkshopCollectionResponse> {
  return call("get_workshop_collection", request);
}
