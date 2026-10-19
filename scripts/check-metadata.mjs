import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const config = JSON.parse(readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const cargo = readFileSync(new URL("../src-tauri/Cargo.toml", import.meta.url), "utf8");
const cargoVersion = cargo.match(/^version = "([^"]+)"/m)?.[1];
assert.equal(config.version, packageJson.version, "Tauri 与前端版本必须一致");
assert.equal(cargoVersion, packageJson.version, "Rust 与前端版本必须一致");
assert.equal(config.identifier, "app.hungerdesolate.hub", "应用数据必须使用新命名空间");
assert.notEqual(config.identifier, "app.famine.ecs");
console.log(`荒野中枢 ${packageJson.version}：版本和独立应用标识检查通过`);
