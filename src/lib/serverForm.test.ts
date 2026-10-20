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

  it("shows safe remote diagnostics for world config preview failures", () => {
    const error = describeError({code: "WORLD_CONFIG_FAILED", message: "操作失败",
      details: {remoteCode: "WORLD_CONFIG_FAILED", remoteFailureType: "KeyError", remoteFailureKey: "configRevision"}});
    assert.equal(error.message, "操作失败（远端错误码：WORLD_CONFIG_FAILED，异常类型：KeyError，缺失字段：configRevision）");
    assert.deepEqual(error.recoverySteps, ["请将错误码和异常类型提供给开发人员；未修改已保存配置"]);
    const unsafe = describeError({code: "WORLD_CONFIG_FAILED", message: "操作失败",
      details: {remoteCode: "secret-value", remoteFailureType: "secret-value"}});
    assert.equal(unsafe.message, "操作失败");
  });
});
