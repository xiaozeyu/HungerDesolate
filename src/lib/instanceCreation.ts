import type { InstanceCreateInput, InstanceCreatePreview, InstanceCreatePreviewRequest } from "../api/instances.ts";

export function validateCreationInput(input: InstanceCreateInput): string | null {
  const name=input.displayName.trim();
  if (!name || [...name].length>64 || /[\u0000-\u001f\u007f-\u009f]/.test(name)) return "名称需为 1–64 个字符，且不能含控制字符";
  if (!input.autostart && input.startAfterCreate) return "不自动启动时不能设置创建后启动";
  if (input.portPolicy !== "auto") return "端口策略无效";
  if (typeof input.cavesEnabled !== "boolean") return "容器拓扑设置无效";
  return null;
}
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const blockers: Record<string,string>={
  DEPLOYMENT_VALIDATION_REQUIRED:"游戏镜像与部署兼容性尚未完成验证，暂不可创建",
  CREATE_RELEASE_REQUIRED:"没有已验证的 DST release，暂不可创建",
  CREATE_RELEASE_INVALID:"DST release 元数据或摘要无效",
  CREATE_COMPOSE_CONFIG_FAILED:"Compose 配置校验失败，创建现场已保留",
  CREATE_COMPOSE_CONFIG_UNAVAILABLE:"远端 Docker Compose 不可用",
  CREATE_COMPOSE_CONFIG_TIMEOUT:"Compose 配置校验超时",
  CREATE_DOCKER_RUNTIME_REQUIRED:"Docker runtime 和容器验证尚未完成",
  CREATE_DOCKER_UNAVAILABLE:"远端 Docker 引擎不可用",
  CREATE_DOCKER_TIMEOUT:"Docker 操作超时",
  CREATE_NETWORK_CONFLICT:"实例 network 标签与现有资源冲突",
  CREATE_NETWORK_FAILED:"实例 network 创建失败",
  CREATE_CONTAINERS_FAILED:"实例容器创建失败，现场已保留",
  CREATE_CONTAINER_VERIFICATION_REQUIRED:"容器已创建，挂载、端口和进程仍待验证",
  PORT_RESERVATIONS_MISSING:"注册表缺少端口预留信息",
  EXISTING_PORT_PLAN_INCOMPLETE:"已有专服的完整端口计划缺失",
  PORT_EVIDENCE_UNAVAILABLE:"无法核对主机端口占用",
  DOCKER_UNAVAILABLE:"Docker 引擎不可访问",
  DOCKER_PLATFORM_UNSUPPORTED:"Docker 引擎需要 Linux amd64",
  COMPOSE_UNAVAILABLE:"Docker Compose v2 不可用",
  MANAGEMENT_ROOT_UNWRITABLE:"主机管理目录不可写",
  PORT_PLAN_EXHAUSTED:"可用端口范围不足",
};
export function creationBlockerLabel(code:string):string {return Object.hasOwn(blockers,code) ? blockers[code]! : "创建条件未满足";}
export function matchesCreationPreview(request: InstanceCreatePreviewRequest, response: InstanceCreatePreview):boolean {
  if (!response?.target || !response.input || !Array.isArray(response.ports) || !Array.isArray(response.blockers)) return false;
  const input=request.input, result=response.input;
  const used=new Set<number>(), purposes=new Set<string>();
  const validPorts=response.ports.every(port=>{
    if (!port || typeof port!=="object") return false;
    const lower=port.purpose==="game" ? 11000 : port.purpose==="steamAuth"?8766:27016;
    const upper=port.purpose==="game" ? 19999 : port.purpose==="steamAuth"?9765:28015;
    const key=`${port.shard}:${port.purpose}`;
    const valid=["master","caves"].includes(port.shard) && (input.cavesEnabled || port.shard!=="caves")
      && ["game","steamAuth","steamMaster"].includes(port.purpose)
      && port.protocol==="udp" && port.bindAddress==="0.0.0.0" && Number.isInteger(port.hostPort)
      && port.hostPort>=lower && port.hostPort<=upper && port.containerPort===port.hostPort
      && !used.has(port.hostPort) && !purposes.has(key);
    used.add(port.hostPort);purposes.add(key);return valid;
  });
  return response.schemaVersion===1 && response.requestId===request.requestId
    && response.target.serverId===request.target.serverId && response.target.hostId===request.target.hostId
    && response.recordVersion===request.expectedRecordVersion && response.registryRevision===request.expectedRegistryRevision
    && uuid.test(response.previewId) && uuid.test(response.proposedInstanceId)
    && response.composeProject===`fh_${response.proposedInstanceId.replaceAll("-","")}`
    && response.storagePath===`/var/lib/famine-hub/instances/${response.proposedInstanceId}`
    && response.allowed===false && response.portReservation==="unreserved"
    && Number.isSafeInteger(response.availableBytes) && response.availableBytes>=0
    && Number.isSafeInteger(response.capturedAtUnixSeconds) && response.capturedAtUnixSeconds>0
    && response.expiresAtUnixSeconds===response.capturedAtUnixSeconds+600
    && result.displayName===input.displayName && result.autostart===input.autostart && result.startAfterCreate===input.startAfterCreate && result.portPolicy===input.portPolicy
    && result.cavesEnabled===input.cavesEnabled
    && response.blockers.every(item=>Object.hasOwn(blockers,item))
    && [response.dockerVersion,response.composeVersion].every(value=>value===null || typeof value==="string" && /^[a-zA-Z0-9.+_-]{1,64}$/.test(value))
    && validPorts && (response.ports.length===0 || response.ports.length===(input.cavesEnabled?6:3));
}
