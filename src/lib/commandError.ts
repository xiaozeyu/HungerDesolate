export interface UserError {
  code: string; message: string; recoverySteps: string[]; field: string | null;
}
export function describeError(error: unknown): UserError {
  if (typeof error === "object" && error !== null && "code" in error && "message" in error
    && typeof error.code === "string" && typeof error.message === "string") {
    const details = "details" in error && typeof error.details === "object" && error.details !== null ? error.details : null;
    return {
      code: error.code, message: error.message,
      recoverySteps: "recoverySteps" in error && Array.isArray(error.recoverySteps)
        ? error.recoverySteps.filter((item): item is string => typeof item === "string") : [],
      field: details && "field" in details && typeof details.field === "string" ? details.field : null,
    };
  }
  return { code: "CLIENT_ERROR", message: error instanceof Error ? error.message : "操作未完成，请刷新后核实结果", recoverySteps: [], field: null };
}
