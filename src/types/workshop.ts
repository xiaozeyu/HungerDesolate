export interface WorkshopItem {
  workshopId: string; result: number | null; title: string | null; description: string | null;
  creatorAppId: number | null; consumerAppId: number | null; timeCreated: number | null; timeUpdated: number | null;
  fileSize: number | null; previewUrl: string | null; contentHandle: string | null;
}
export interface WorkshopMetadataResponse {
  schemaVersion: 1; requestId: string; serverId: string; recordVersion: number; items: WorkshopItem[];
}
