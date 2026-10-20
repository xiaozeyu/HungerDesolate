import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  changedWorldOptionCount,
  defaultWorldSettings,
  dstWorldOptions,
  optionDefaultValue,
  optionValue,
  updateShardOption,
  visibleWorldOptions,
} from "./dstWorldOptions.ts";

describe("DST per-shard world option model", () => {
  it("keeps option keys unique and restricts each option to its supported shards", () => {
    const keys = dstWorldOptions.map((item) => `${item.category}:${item.key}`);
    assert.equal(new Set(keys).size, keys.length);
    assert.ok(dstWorldOptions.every((item) => item.worlds.length > 0));
    assert.ok(dstWorldOptions.some((item) => item.worlds.includes("caves")));
    assert.ok(dstWorldOptions.some((item) => item.category === "worldgen"));
    assert.ok(dstWorldOptions.some((item) => item.category === "worldSettings"));
  });

  it("uses shard defaults and removes explicit overrides when reset to default", () => {
    const cavesTaskSet = dstWorldOptions.find((item) => item.key === "task_set" && item.worlds.includes("caves"));
    assert.ok(cavesTaskSet);
    assert.equal(optionDefaultValue(cavesTaskSet, "caves"), "cave_default");
    const config = defaultWorldSettings(true).worldgen.caves;
    assert.equal(optionValue(config, cavesTaskSet, "caves"), "cave_default");
    const overridden = updateShardOption(config, cavesTaskSet, "default", "caves");
    assert.equal(optionValue(overridden, cavesTaskSet, "caves"), "default");
    const reset = updateShardOption(overridden, cavesTaskSet, "cave_default", "caves");
    assert.equal(reset.worldgenOverrides.task_set, undefined);
  });

  it("defaults shard configs and counts only enabled shard overrides", () => {
    const defaults = defaultWorldSettings(true);
    assert.equal(defaults.worldgen.master.preset, "SURVIVAL_TOGETHER");
    assert.equal(defaults.worldgen.caves.preset, "DST_CAVE");
    const option = dstWorldOptions.find((item) => item.category === "worldgen")!;
    defaults.worldgen.master = updateShardOption(defaults.worldgen.master, option, "always", "master");
    defaults.worldgen.caves = updateShardOption(defaults.worldgen.caves, option, "rare", "caves");
    assert.equal(changedWorldOptionCount(defaults.worldgen, false).total, 1);
    assert.equal(changedWorldOptionCount(defaults.worldgen, true).total, 2);
  });

  it("filters groups using shard and category metadata", () => {
    assert.ok(visibleWorldOptions("caves", "worldgen").every((item) => item.worlds.includes("caves") && item.category === "worldgen"));
    assert.ok(visibleWorldOptions("master", "worldSettings").every((item) => item.worlds.includes("master") && item.category === "worldSettings"));
  });
});
