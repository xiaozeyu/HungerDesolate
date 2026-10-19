import type { CredentialInput } from "../types/contracts";
export interface CredentialFields {
  password: string; privateKeyPath: string; privateKey: string; passphrase: string;
  keySource: "file" | "text";
}
export function emptyCredentials(): CredentialFields {
  return {password: "", privateKeyPath: "", privateKey: "", passphrase: "", keySource: "file"};
}
export function credentialError(authKind: "password" | "key", fields: CredentialFields): string | null {
  if (authKind === "password") return fields.password.length ? null : "请输入 SSH 密码";
  return (fields.keySource === "file" ? fields.privateKeyPath.trim() : fields.privateKey.trim())
    ? null : fields.keySource === "file" ? "请输入或选择本机 SSH 私钥文件路径" : "请粘贴完整 SSH 私钥";
}
export function credentialInput(authKind: "password" | "key", fields: CredentialFields): CredentialInput {
  if (authKind === "password") return {kind: "password", password: fields.password};
  if (fields.keySource === "file") return {kind: "privateKeyFile", path: fields.privateKeyPath, passphrase: fields.passphrase || null};
  return {kind: "privateKey", privateKey: fields.privateKey, passphrase: fields.passphrase || null};
}
