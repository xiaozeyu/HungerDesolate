export type ModUpdateHint = "candidate" | "fetching" | "unchanged" | "unknown";

export function compareWorkshopRevision(sourceHandle: string | null, preparedHandle: string | null): ModUpdateHint {
  if (!sourceHandle) return "unknown";
  if (!preparedHandle) return "candidate";
  return sourceHandle === preparedHandle ? "unchanged" : "candidate";
}
