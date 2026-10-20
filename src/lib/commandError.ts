export interface UserError {
  code: string; message: string; recoverySteps: string[]; field: string | null; details?: Record<string, unknown>;
}
export function describeError(error: unknown): UserError {
  if (typeof error === "object" && error !== null && "code" in error && "message" in error
    && typeof error.code === "string" && typeof error.message === "string") {
    const details = "details" in error && typeof error.details === "object" && error.details !== null ? error.details : null;
    const remoteCode = details && "remoteCode" in details && typeof details.remoteCode === "string"
      && /^[A-Z0-9_]{1,96}$/.test(details.remoteCode) ? details.remoteCode : null;
    return {
      code: error.code,
      message: error.code === "RELEASE_RUNTIME_VERIFY_FAILED" && remoteCode
        ? `${error.message}（远端错误码：${remoteCode}）` : error.message,
      recoverySteps: "recoverySteps" in error && Array.isArray(error.recoverySteps)
        ? error.recoverySteps.filter((item): item is string => typeof item === "string") : [],
      field: details && "field" in details && typeof details.field === "string" ? details.field : null,
      details: details ? details as Record<string, unknown> : {},
    };
  }
  return { code: "CLIENT_ERROR", message: error instanceof Error ? error.message : "操作未完成，请刷新后核实结果", recoverySteps: [], field: null, details: {} };
}
