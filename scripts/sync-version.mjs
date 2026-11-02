import { readFile, writeFile } from "node:fs/promises";

const pkg = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
if (typeof pkg.version !== "string" || !/^\d+\.\d+\.\d+(?:[-+].+)?$/.test(pkg.version)) {
  throw new Error("package.json version is not a valid semantic version");
}
const cargoUrl = new URL("../src-tauri/Cargo.toml", import.meta.url);
const cargo = await readFile(cargoUrl, "utf8");
const next = cargo.replace(/^version = "[^"]+"$/m, `version = "${pkg.version}"`);
if (next !== cargo) await writeFile(cargoUrl, next);
console.log(`version ${pkg.version} synced`);
