import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { matchesDockerInstallationPreview, matchesDockerInstallStatus, dockerInstallBlockerLabel, dockerInstallTaskErrorLabel, dockerInstallPhaseLabel } from "./dockerInstallation.ts";
const ticket={serverId:"server-a",recordVersion:3,epoch:4,sequence:5,requestId:"request-a"};
const response={schemaVersion:1 as const,requestId:ticket.requestId,serverId:ticket.serverId,recordVersion:3,planId:"11111111-1111-4111-8111-111111111111",operationId:"22222222-2222-4222-8222-222222222222",preview:{
  status:"ready" as const,blockers:[],snapshotDigest:"a".repeat(64),expiresAtUnixSeconds:1800000000,
  distribution:{id:"ubuntu",version:"26.04",codename:"resolute",architecture:"x86_64"},hostId:"33333333-3333-4333-8333-333333333333",uid:0,
  rootAvailableBytes:8*1024**3,sourceState:"missing" as const,repositoryReachable:true,installedDockerPackages:{},aptRunning:false,dpkgClean:true,
  packages:["docker-ce","docker-ce-cli","containerd.io","docker-buildx-plugin","docker-compose-plugin"],
  repository:"https://download.docker.com/linux/ubuntu",sourceFile:"/etc/apt/sources.list.d/docker.sources",keyFile:"/etc/apt/keyrings/docker.asc",
}};
describe("Docker installation preflight response",()=>{
  it("shows safe worker reasons and the actual failed stage",()=>{
    assert.equal(dockerInstallTaskErrorLabel("INSTALL_FAILED"),"Docker 安装脚本执行异常");
    assert.equal(dockerInstallTaskErrorLabel("INSTALL_KEY_TIMEOUT"),"Docker 签名密钥下载超时");
    assert.equal(dockerInstallTaskErrorLabel("INSTALL_KEY_HTTP_503"),"Docker 签名密钥下载失败（HTTP 503）");
    assert.equal(dockerInstallTaskErrorLabel("INSTALL_KEY_HTTP_503 secret"),"Docker 安装任务失败");
    assert.equal(dockerInstallTaskErrorLabel("INSTALL_DISK_FULL"),"安装所需磁盘空间或配额不足");
    assert.equal(dockerInstallPhaseLabel("configuringRepository"),"配置 Docker 软件源");
    assert.equal(dockerInstallPhaseLabel("verifyingRuntime"),"验证 Docker 与 Compose");
  });
  it("accepts only the current connection and fixed install plan",()=>{
    assert.equal(matchesDockerInstallationPreview(ticket,null,response),true);
    assert.equal(matchesDockerInstallationPreview(ticket,"44444444-4444-4444-8444-444444444444",response),false);
    assert.equal(matchesDockerInstallationPreview(ticket,null,{...response,serverId:"server-b"}),false);
    assert.equal(matchesDockerInstallationPreview(ticket,null,{...response,recordVersion:2}),false);
    assert.equal(matchesDockerInstallationPreview(ticket,null,{...response,preview:{...response.preview,repository:"https://example.invalid"}}),false);
    assert.equal(matchesDockerInstallationPreview(ticket,null,{...response,preview:{...response.preview,packages:["apt-get"]}}),false);
    assert.equal(matchesDockerInstallationPreview(ticket,null,{...response,preview:{...response.preview,snapshotDigest:"bad"}}),false);
  });
  it("maps known blockers without echoing remote text",()=>{
    assert.equal(dockerInstallBlockerLabel("ROOT_REQUIRED"),"需要 root SSH 账户");
    assert.equal(dockerInstallBlockerLabel("remote-controlled-label"),"安装条件未满足");
    assert.equal(dockerInstallBlockerLabel("DOCKER_REPOSITORY_TIMEOUT"),"Docker 仓库连接或读取超时");
    assert.equal(dockerInstallBlockerLabel("DOCKER_REPOSITORY_DNS_FAILED"),"Docker 仓库域名解析失败");
    assert.equal(dockerInstallBlockerLabel("DOCKER_REPOSITORY_HTTP_503"),"Docker 仓库请求失败（HTTP 503）");
    for (const code of ["DOCKER_REPOSITORY_HTTP_999", "DOCKER_REPOSITORY_HTTP_403 token=secret", "DOCKER_REPOSITORY_HTTP_200\n"]) {
      assert.equal(dockerInstallBlockerLabel(code),"安装条件未满足");
    }
  });
  it("accepts task updates only for the current connection and a coherent sequence",()=>{
    const hostId="33333333-3333-4333-8333-333333333333";
    const response={schemaVersion:1 as const,requestId:ticket.requestId,serverId:ticket.serverId,recordVersion:3,hostId,task:{schemaVersion:1 as const,taskId:"22222222-2222-4222-8222-222222222222",hostId,sequence:2,status:"running" as const,phase:"installingPackages",messageCode:null,startedAtUnixSeconds:1700000000,updatedAtUnixSeconds:1700000010,installedVersions:{}}};
    assert.equal(matchesDockerInstallStatus(ticket,hostId,response),true);
    assert.equal(matchesDockerInstallStatus(ticket,hostId,{...response,serverId:"server-b"}),false);
    assert.equal(matchesDockerInstallStatus(ticket,hostId,{...response,task:{...response.task,sequence:0}}),false);
    assert.equal(matchesDockerInstallStatus(ticket,hostId,{...response,task:{...response.task,updatedAtUnixSeconds:1}}),false);
    assert.equal(matchesDockerInstallStatus(ticket,hostId,{...response,task:null}),true);
  });
});
