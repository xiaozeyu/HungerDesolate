import type { InstanceCreateInput } from "../api/instances.ts";

export function validateCreationInput(input: InstanceCreateInput): string | null {
  const name=input.displayName.trim();
  if (!name || [...name].length>64 || /[\u0000-\u001f\u007f-\u009f]/.test(name)) return "名称需为 1–64 个字符，且不能含控制字符";
  if (!input.autostart && input.startAfterCreate) return "不自动启动时不能设置创建后启动";
  if (input.portPolicy !== "auto") return "端口策略无效";
  if (typeof input.cavesEnabled !== "boolean") return "容器拓扑设置无效";
  return null;
}
const blockers: Record<string,string>={
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
