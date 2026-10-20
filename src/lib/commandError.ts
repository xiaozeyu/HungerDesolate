export interface UserError {
  code: string; message: string; recoverySteps: string[]; field: string | null; details?: Record<string, unknown>;
}
export function describeError(error: unknown): UserError {
  if (typeof error === "object" && error !== null && "code" in error && "message" in error
    && typeof error.code === "string" && typeof error.message === "string") {
    const details = "details" in error && typeof error.details === "object" && error.details !== null ? error.details : null;
    const remoteCode = details && "remoteCode" in details && typeof details.remoteCode === "string"
      && /^[A-Z0-9_]{1,96}$/.test(details.remoteCode) ? details.remoteCode : null;
    const remoteFailureType = details && "remoteFailureType" in details && typeof details.remoteFailureType === "string"
      && /^(OSError|PermissionError|ValueError|TypeError|KeyError|AttributeError)$/.test(details.remoteFailureType)
      ? details.remoteFailureType : null;
    const remoteFailureKey = details && "remoteFailureKey" in details && typeof details.remoteFailureKey === "string"
      && /^[A-Za-z0-9_]{1,64}$/.test(details.remoteFailureKey) ? details.remoteFailureKey : null;
    const showWorldConfigDiagnostic = error.code === "WORLD_CONFIG_FAILED" || error.code === "WORLD_CONFIG_PROTOCOL_INVALID";
    return {
      code: error.code,
      message: remoteCode && (error.code === "RELEASE_RUNTIME_VERIFY_FAILED" || showWorldConfigDiagnostic)
        ? `${error.message}（远端错误码：${remoteCode}${remoteFailureType ? `，异常类型：${remoteFailureType}` : ""}${remoteFailureKey ? `，缺失字段：${remoteFailureKey}` : ""}）` : error.message,
      recoverySteps: error.code === "WORLD_CONFIG_FAILED" && remoteFailureType
        ? ["请将错误码和异常类型提供给开发人员；未修改已保存配置"]
        : "recoverySteps" in error && Array.isArray(error.recoverySteps)
          ? error.recoverySteps.filter((item): item is string => typeof item === "string") : [],
      field: details && "field" in details && typeof details.field === "string" ? details.field : null,
      details: details ? details as Record<string, unknown> : {},
    };
  }
  return { code: "CLIENT_ERROR", message: error instanceof Error ? error.message : "操作未完成，请刷新后核实结果", recoverySteps: [], field: null, details: {} };
}
