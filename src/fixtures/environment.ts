import type { ConnectionRequest, HostEnvironment } from "../types/contracts";
export function demoEnvironment(request: ConnectionRequest, hostId: string | null): HostEnvironment {
  const denied = request.serverId.startsWith("bbbb");
  return {
    schemaVersion:1,requestId:request.requestId,serverId:request.serverId,recordVersion:request.expectedRecordVersion,
    hostId,environmentCheckId:crypto.randomUUID(),checkedAtUnixSeconds:Math.floor(Date.now()/1000),
    os:"Ubuntu 26.04 LTS",distributionId:"ubuntu",distributionVersion:"26.04",distributionCodename:"resolute",kernel:"Linux",architecture:"x86_64",uid:1000,
    memory:{totalBytes:8*1024**3,availableBytes:5*1024**3},
    disk:{path:"/var/lib",totalBytes:100*1024**3,availableBytes:72*1024**3},
    docker:{clientVersion:"示例 27.0.0",serverVersion:denied?null:"示例 27.0.0",composeVersion:"示例 2.29.0",
      engineState:denied?"permissionDenied":"available",dataRoot:denied?null:"/var/lib/docker",
      disk:denied?null:{path:"/var/lib/docker",totalBytes:100*1024**3,availableBytes:72*1024**3}},
    checks:[
      {code:"LINUX_REQUIRED",level:"passed",label:"操作系统",message:"通过",recoverySteps:[]},
      {code:"AMD64_REQUIRED",level:"passed",label:"系统架构",message:"通过",recoverySteps:[]},
      {code:denied?"DOCKER_PERMISSION_DENIED":"DOCKER_AVAILABLE",level:denied?"blocker":"passed",label:"Docker",message:denied?"权限不足":"可访问",recoverySteps:denied?["检查当前账户对本机 Docker socket 的访问权限"]:[]},
      {code:"MANAGEMENT_PATH_PERMISSION",level:"blocker",label:"目录权限",message:"管理目录不可写",recoverySteps:["为管理账户配置受控目录权限后重新检查"]},
      {code:"DEPLOYMENT_COMPATIBILITY_UNVERIFIED",level:"warning",label:"部署兼容性",message:"待验证",recoverySteps:["完成三专服联网、更新与恢复验证"]},
    ],deploymentVerified:false,
  };
}
