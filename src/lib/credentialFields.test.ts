import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { emptyCredentials, credentialError, credentialInput } from "./credentialFields.ts";
describe("authentication inputs", () => {
  it("requires a password for password authentication and never trims it", () => {
    assert.ok(credentialError("password",emptyCredentials()));
    const fields = {...emptyCredentials(),password:" secret "};
    assert.equal(credentialError("password",fields),null);
    assert.deepEqual(credentialInput("password",fields),{kind:"password",password:" secret "});
  });
  it("requires a private-key path and submits it without reading file content in the UI", () => {
    const fields = {...emptyCredentials(),privateKeyPath:"/Users/test/My Keys/id_ed25519",passphrase:"phrase"};
    assert.equal(credentialError("key",fields),null);
    assert.deepEqual(credentialInput("key",fields),{kind:"privateKeyFile",path:fields.privateKeyPath,passphrase:"phrase"});
    assert.ok(credentialError("key",emptyCredentials()));
  });
  it("keeps pasted key compatibility while excluding the unused password and path", () => {
    const fields = {...emptyCredentials(),keySource:"text" as const,privateKey:"test-key",password:"unused",privateKeyPath:"unused"};
    assert.deepEqual(credentialInput("key",fields),{kind:"privateKey",privateKey:"test-key",passphrase:null});
  });
});
