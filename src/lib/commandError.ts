export interface UserError {
  code: string; message: string; recoverySteps: string[]; field: string | null; details?: Record<string, unknown>;
}
export function describeError(error: unknown): UserError {
  if (typeof error === "object" && error !== null && "code" in error && "message" in error
    && typeof error.code === "string" && typeof error.message === "string") {
    const details = "details" in error && typeof error.details === "object" && error.details !== null
      ? error.details as Record<string, unknown> : null;
    const remoteCode = details && "remoteCode" in details && typeof details.remoteCode === "string"
      && /^[A-Z0-9_]{1,96}$/.test(details.remoteCode) ? details.remoteCode : null;
    const remoteFailureType = details && "remoteFailureType" in details && typeof details.remoteFailureType === "string"
      && /^(OSError|PermissionError|ValueError|TypeError|KeyError|AttributeError)$/.test(details.remoteFailureType)
      ? details.remoteFailureType : null;
    const remoteFailureKey = details && "remoteFailureKey" in details && typeof details.remoteFailureKey === "string"
      && /^[A-Za-z0-9_.-]{1,64}$/.test(details.remoteFailureKey) ? details.remoteFailureKey : null;
    const responseBytes = details && "responseBytes" in details && Number.isSafeInteger(details.responseBytes)
      && (details.responseBytes as number) >= 0 && (details.responseBytes as number) <= 262144
      ? details.responseBytes as number : null;
    const exitStatus = details && "exitStatus" in details && Number.isSafeInteger(details.exitStatus)
      && (details.exitStatus as number) >= -1 && (details.exitStatus as number) <= 255
      ? details.exitStatus as number : null;
    const portList = (key: string) => {
      const value = details?.[key];
      return Array.isArray(value) && value.length <= 64
        && value.every(port => Number.isInteger(port) && port >= 1 && port <= 65535)
        ? value as number[] : null;
    };
    const missingMaster = portList("masterMissingPorts");
    const missingCaves = portList("cavesMissingPorts");
    const observedMaster = portList("masterObservedPorts");
    const observedCaves = portList("cavesObservedPorts");
    const masterRunning = details?.masterProcessRunning;
    const cavesRunning = details?.cavesProcessRunning;
    const showCaves = error.code === "RELEASE_RUNTIME_LISTENERS_NOT_READY"
      || missingCaves !== null || observedCaves !== null || typeof cavesRunning === "boolean";
    const portLabel = (ports: number[] | null) => ports === null ? "未知" : ports.join("、") || "无";
    const listenerDetails = (error.code === "RELEASE_RUNTIME_LISTENERS_NOT_READY" || error.code === "INSTANCE_START_RUNTIME_NOT_READY")
      && ([missingMaster, missingCaves, observedMaster, observedCaves].some(value => value !== null)
        || typeof masterRunning === "boolean" || typeof cavesRunning === "boolean")
      ? `（Master 缺少：${portLabel(missingMaster)}；${showCaves ? `Caves 缺少：${portLabel(missingCaves)}；` : ""}`
        + `Master 已监听：${portLabel(observedMaster)}；${showCaves ? `Caves 已监听：${portLabel(observedCaves)}；` : ""}`
        + `进程：Master ${masterRunning === true ? "运行中" : masterRunning === false ? "未运行" : "未知"}`
        + `${showCaves ? `，Caves ${cavesRunning === true ? "运行中" : cavesRunning === false ? "未运行" : "未知"}` : ""}）`
      : "";
    const runtimeFailureTypes = ["OSError", "PermissionError", "FileNotFoundError", "NotADirectoryError",
      "ValueError", "TypeError", "KeyError", "AttributeError", "RuntimeError", "UnicodeError", "BadZipFile", "Error", "Exception"];
    const runtimeStages: Record<string, string> = {
      initialize: "初始化", validateRequest: "校验请求", readHostState: "读取主机身份",
      readReleaseCatalog: "读取 Release catalog", readReleaseMetadata: "读取 Release 元数据",
      prepareConfiguration: "准备配置包", verifyImage: "检查镜像", createNetwork: "创建隔离网络",
      startMaster: "启动 Master", startCaves: "启动 Caves", waitForListeners: "等待端口监听",
      cleanup: "清理验证资源", publishEvidence: "保存验证证据",
    };
    const runtimeFailureType = details?.failureType;
    const runtimeFailureStage = details?.failureStage;
    const runtimeFunction = details?.failureFunction;
    const runtimeLine = details?.failureLine;
    const runtimeLocation = typeof runtimeFunction === "string" && /^[A-Za-z_][A-Za-z0-9_]{0,47}$/.test(runtimeFunction)
      && Number.isInteger(runtimeLine) && (runtimeLine as number) >= 1 && (runtimeLine as number) <= 1000
      ? `${runtimeFunction}:${runtimeLine}` : null;
    const runtimeFailureDetails = error.code === "RELEASE_RUNTIME_VERIFY_FAILED"
      && (typeof runtimeFailureType === "string" && runtimeFailureTypes.includes(runtimeFailureType)
        || typeof runtimeFailureStage === "string" && runtimeFailureStage in runtimeStages
        || runtimeLocation !== null)
      ? `（${typeof runtimeFailureStage === "string" && runtimeFailureStage in runtimeStages
        ? `阶段：${runtimeStages[runtimeFailureStage]}` : ""}`
        + `${typeof runtimeFailureType === "string" && runtimeFailureTypes.includes(runtimeFailureType)
          ? `${typeof runtimeFailureStage === "string" && runtimeFailureStage in runtimeStages ? "；" : ""}异常：${runtimeFailureType}` : ""}`
        + `${runtimeLocation ? `${typeof runtimeFailureStage === "string" || typeof runtimeFailureType === "string" ? "；" : ""}位置：${runtimeLocation}` : ""}）`
      : "";
    const showWorldConfigDiagnostic = error.code === "WORLD_CONFIG_FAILED"
      || error.code === "WORLD_CONFIG_PROTOCOL_INVALID" || error.code === "WORLD_DOCKER_RESPONSE_INVALID";
    const showCreationDiagnostic = error.code === "CREATE_TASK_FAILED" || error.code === "RELEASE_CATALOG_CHANGED"
      || error.code === "CREATE_INPUT_INVALID" || error.code === "CREATE_PORT_STATE_INVALID"
      || error.code === "CREATE_TASK_PATH_UNSAFE" || error.code === "CREATE_REGISTRY_INVALID"
      || error.code === "CREATE_OPERATION_STATE_INVALID" || error.code === "CREATE_TASK_STATE_INVALID";
    const showInstanceStartDiagnostic = error.code.startsWith("INSTANCE_START_")
      || error.code === "WORLD_TOKEN_MISSING" || error.code === "WORLD_RELEASE_INVALID"
      || error.code === "WORLD_DEPLOYMENT_MISSING";
    const startStages: Record<string, string> = {
      validateRequest: "校验启动请求", readHostState: "读取主机状态", acquireInstanceLock: "取得专服运行锁",
      validateManifest: "校验实例身份", validateAppliedConfig: "校验已应用配置", validateActiveRelease: "校验活动 Release",
      validateToken: "校验 Klei Token", validateShardConfig: "校验分片端口", validateContainers: "校验容器身份",
      startServices: "启动 Docker 分片", waitForRuntime: "等待 DST 进程和 UDP 监听",
      repairRuntimeLayout: "修复专服工作目录与写入权限",
    };
    const startStage = typeof details?.failureStage === "string" && details.failureStage in startStages
      ? startStages[details.failureStage] : null;
    const startType = typeof details?.failureType === "string"
      && /^(OSError|PermissionError|FileNotFoundError|NotADirectoryError|ValueError|TypeError|KeyError|AttributeError|RuntimeError|Error|Exception)$/.test(details.failureType)
      ? details.failureType : null;
    const startExitParts: string[] = [];
    if (error.code === "INSTANCE_START_CONTAINER_EXITED") {
      if (details?.shard === "master" || details?.shard === "caves") startExitParts.push(`分片：${details.shard === "master" ? "Master" : "Caves"}`);
      if (Number.isInteger(details?.exitCode) && (details?.exitCode as number) >= 0 && (details?.exitCode as number) <= 255) startExitParts.push(`退出码：${details?.exitCode}`);
      if (Number.isSafeInteger(details?.restartCount) && (details?.restartCount as number) >= 0) startExitParts.push(`重启次数：${details?.restartCount}`);
      if (details?.oomKilled === true) startExitParts.push("内存不足被终止");
    }
    const startParts = [remoteCode ? `远端错误码：${remoteCode}` : null, startStage ? `阶段：${startStage}` : null,
      startType ? `异常：${startType}` : null, ...startExitParts].filter(Boolean);
    const releaseProtocolMessage = error.code === "CREATE_RELEASE_PROTOCOL_INVALID" && responseBytes !== null && exitStatus !== null
      ? responseBytes === 0 && exitStatus === 127
        ? "远端找不到 /usr/bin/python3（退出码 127）；请安装 Python 3 后重试"
        : responseBytes === 0 && exitStatus === 126
          ? "远端无权执行 /usr/bin/python3（退出码 126）；请检查文件权限"
          : `${error.message}（退出码 ${exitStatus}，响应 ${responseBytes} 字节）`
      : error.message;
    return {
      code: error.code,
      message: listenerDetails ? `${error.message}${showInstanceStartDiagnostic && startParts.length ? `（${startParts.join("；")}）` : ""}${listenerDetails}`
        : runtimeFailureDetails ? `${error.message}${runtimeFailureDetails}`
        : releaseProtocolMessage !== error.message ? releaseProtocolMessage
        : showInstanceStartDiagnostic && startParts.length
        ? `${error.message}（${startParts.join("；")}）`
        : remoteCode && (error.code === "RELEASE_RUNTIME_VERIFY_FAILED" || showWorldConfigDiagnostic || showCreationDiagnostic)
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
