export const sessionStartedAt = Date.now();

export function inSession(createdAt: string): boolean {
  return (Date.parse(createdAt) || 0) >= sessionStartedAt;
}
