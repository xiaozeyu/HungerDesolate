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
});
