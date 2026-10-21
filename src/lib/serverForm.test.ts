import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { validateServerForm, serverInput, type ServerFields } from "./serverForm.ts";
import { describeError } from "./commandError.ts";
const fields: ServerFields = {displayName: "上海", host: "EXAMPLE.COM", sshPort: "22", username: "dst-admin", authKind: "key"};
describe("server form", () => {
  it("normalizes metadata without credential input", () => {
    assert.deepEqual(serverInput(fields), {displayName: "上海", host: "example.com", sshPort: 22, username: "dst-admin", authKind: "key"});
    assert.deepEqual(validateServerForm(fields), {});
  });
  it("shows errors for empty values and out of range/fractional ports", () => {
    assert.ok(validateServerForm({...fields, displayName: " "}).displayName);
    for (const sshPort of ["0", "65536", "1.5", "NaN", ""]) assert.ok(validateServerForm({...fields, sshPort}).sshPort);
  });
  it("accepts IPv6 but rejects URLs and unsafe usernames", () => {
    assert.equal(validateServerForm({...fields, host: "2001:db8::1"}).host, undefined);
    assert.ok(validateServerForm({...fields, host: "ssh://host/path"}).host);
    assert.ok(validateServerForm({...fields, username: "-option"}).username);
  });
  it("maps structured field errors without classifying message strings", () => {
    const error = describeError({code: "INPUT_INVALID", message: "地址无效", details: {field: "host"}, recoverySteps: ["修正后重试"]});
    assert.equal(error.field, "host"); assert.equal(error.code, "INPUT_INVALID");
    assert.equal(describeError("INPUT_INVALID").code, "CLIENT_ERROR");
  });
  it("shows a safe remote code for otherwise unknown Release verification failures", () => {
    const error = describeError({code: "RELEASE_RUNTIME_VERIFY_FAILED", message: "验证失败", details: {remoteCode: "NEW_RUNTIME_FAILURE"}});
    assert.match(error.message, /NEW_RUNTIME_FAILURE/);
    const unsafe = describeError({code: "RELEASE_RUNTIME_VERIFY_FAILED", message: "验证失败", details: {remoteCode: "token-secret"}});
    assert.equal(unsafe.message, "验证失败");
  });

  it("shows bounded release protocol diagnostics without remote output", () => {
    const missingPython = describeError({code: "CREATE_RELEASE_PROTOCOL_INVALID", message: "响应无效",
      details: {responseBytes: 0, exitStatus: 127}});
    assert.equal(missingPython.message, "远端找不到 /usr/bin/python3（退出码 127）；请安装 Python 3 后重试");
    const otherFailure = describeError({code: "CREATE_RELEASE_PROTOCOL_INVALID", message: "响应无效",
      details: {responseBytes: 8, exitStatus: 1, output: "sensitive"}});
    assert.equal(otherFailure.message, "响应无效（退出码 1，响应 8 字节）");
    assert.doesNotMatch(otherFailure.message, /sensitive/);
  });

  it("shows only validated listener port diagnostics", () => {
    const error = describeError({code: "RELEASE_RUNTIME_LISTENERS_NOT_READY", message: "未监听配置端口",
      details: {masterMissingPorts: [11000], cavesMissingPorts: [], masterObservedPorts: [8768],
        cavesObservedPorts: [11001, 8769], masterProcessRunning: true, cavesProcessRunning: false,
        token: "secret"}});
    assert.equal(error.message, "未监听配置端口（Master 缺少：11000；Caves 缺少：无；Master 已监听：8768；Caves 已监听：11001、8769；进程：Master 运行中，Caves 未运行）");
    const unsafe = describeError({code: "RELEASE_RUNTIME_LISTENERS_NOT_READY", message: "未监听配置端口",
      details: {masterMissingPorts: [0], cavesMissingPorts: [70000]}});
    assert.equal(unsafe.message, "未监听配置端口");
  });

  it("shows current instance readiness evidence without inventing unavailable ports", () => {
    const failure = describeError({code: "INSTANCE_START_RUNTIME_NOT_READY", message: "分片未就绪",
      details: {masterProcessRunning:true, cavesProcessRunning:true,
        masterObservedPorts:[10888,27016],masterMissingPorts:[11000],
        cavesObservedPorts:[11001,27017],cavesMissingPorts:[],token:"private"}});
    assert.match(failure.message,/Master 缺少：11000/);
    assert.match(failure.message,/Master 运行中，Caves 运行中/);
    assert.doesNotMatch(failure.message,/private/);
    const unavailable = describeError({code: "INSTANCE_START_RUNTIME_NOT_READY", message: "分片未就绪",
      details: {masterProcessRunning:true}});
    assert.match(unavailable.message,/Master 缺少：未知/);
    assert.doesNotMatch(unavailable.message,/Caves/);
  });

  it("shows safe Release runtime failure stage and exception type", () => {
    const error = describeError({code: "RELEASE_RUNTIME_VERIFY_FAILED", message: "Release 检查未完成",
      details: {remoteCode: "RELEASE_RUNTIME_VERIFY_FAILED", failureType: "PermissionError",
        failureStage: "prepareConfiguration", failureFunction: "verify", failureLine: 414,
        exception: "private path"}});
    assert.equal(error.message, "Release 检查未完成（阶段：准备配置包；异常：PermissionError；位置：verify:414）");
  });

  it("shows safe remote diagnostics for world config preview failures", () => {
    const error = describeError({code: "WORLD_CONFIG_FAILED", message: "操作失败",
      details: {remoteCode: "WORLD_CONFIG_FAILED", remoteFailureType: "KeyError", remoteFailureKey: "configRevision"}});
    assert.equal(error.message, "操作失败（远端错误码：WORLD_CONFIG_FAILED，异常类型：KeyError，缺失字段：configRevision）");
    assert.deepEqual(error.recoverySteps, ["请将错误码和异常类型提供给开发人员；未修改已保存配置"]);
    const unsafe = describeError({code: "WORLD_CONFIG_FAILED", message: "操作失败",
      details: {remoteCode: "secret-value", remoteFailureType: "secret-value"}});
    assert.equal(unsafe.message, "操作失败");
  });

  it("does not hide creation request rejection codes", () => {
    const failure = describeError({code: "CREATE_TASK_FAILED", message: "创建容器请求失败",
      details: {remoteCode: "CREATE_NEW_FAILURE"}});
    assert.equal(failure.message, "创建容器请求失败（远端错误码：CREATE_NEW_FAILURE）");
    const unsafe = describeError({code: "CREATE_TASK_FAILED", message: "创建容器请求失败",
      details: {remoteCode: "private token /path"}});
    assert.equal(unsafe.message, "创建容器请求失败");
  });

  it("shows safe start failure code, stage and exception type", () => {
    const error = describeError({code: "INSTANCE_START_FAILED", message: "远端启动检查失败",
      details: {remoteCode: "INSTANCE_START_COMPOSE_FAILED", failureStage: "startServices",
        failureType: "PermissionError", stderr: "sensitive output"}});
    assert.equal(error.message, "远端启动检查失败（远端错误码：INSTANCE_START_COMPOSE_FAILED；阶段：启动 Docker 分片；异常：PermissionError）");
    assert.doesNotMatch(error.message, /sensitive output/);
    const unsafe = describeError({code: "INSTANCE_START_FAILED", message: "启动失败",
      details: {remoteCode: "bad code", failureStage: "privatePath", failureType: "SecretError"}});
    assert.equal(unsafe.message, "启动失败");
  });

  it("identifies a crashing shard without exposing container log text", () => {
    const error = describeError({code: "INSTANCE_START_CONTAINER_EXITED", message: "DST 分片已退出",
      details: {shard: "master", exitCode: 1, restartCount: 7, oomKilled: false,
        containerError: "private-token", logs: "private-password"}});
    assert.equal(error.message, "DST 分片已退出（分片：Master；退出码：1；重启次数：7）");
    const invalid = describeError({code: "INSTANCE_START_CONTAINER_EXITED", message: "DST 分片已退出",
      details: {shard: "private-token", exitCode: 999, restartCount: -1}});
    assert.equal(invalid.message, "DST 分片已退出");
  });

  it("shows only sanitized Docker service-name diagnostics", () => {
    const error = describeError({code: "WORLD_DOCKER_RESPONSE_INVALID", message: "Docker 响应无效",
      details: {remoteCode: "WORLD_DOCKER_RESPONSE_INVALID", remoteFailureKey: "dedicated-server"}});
    assert.equal(error.message, "Docker 响应无效（远端错误码：WORLD_DOCKER_RESPONSE_INVALID，缺失字段：dedicated-server）");
    const unsafe = describeError({code: "WORLD_DOCKER_RESPONSE_INVALID", message: "Docker 响应无效",
      details: {remoteCode: "WORLD_DOCKER_RESPONSE_INVALID", remoteFailureKey: "../../secret"}});
    assert.equal(unsafe.message, "Docker 响应无效（远端错误码：WORLD_DOCKER_RESPONSE_INVALID）");
  });
});
