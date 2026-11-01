import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { reactive } from "vue";
import { copyWorldSettings } from "./worldConfigDraft.ts";
import { defaultWorldSettings, optionValue, updateShardOption, visibleWorldOptions } from "./dstWorldOptions.ts";

describe("world config draft copying", () => {
  it("copies Vue reactive settings into an independent plain snapshot", () => {
    const draft = reactive(defaultWorldSettings(true));
    draft.worldgen.master.worldgenOverrides.grass = "often";

    const snapshot = copyWorldSettings(draft);
    snapshot.worldgen.master.worldgenOverrides.grass = "rare";

    assert.equal(draft.worldgen.master.worldgenOverrides.grass, "often");
    assert.equal(snapshot.worldgen.master.worldgenOverrides.grass, "rare");
  });
  for (const shard of ["master", "caves"] as const) {
    for (const category of ["worldSettings", "worldgen"] as const) {
      it(`copies a ${shard} ${category} draft after repeated option edits`, () => {
        const draft = reactive(defaultWorldSettings(true));
        const items = visibleWorldOptions(shard, category).filter(item => item.values.length > 1).slice(0, 2);
        assert.equal(items.length, 2);

        for (const item of items) {
          const value = item.values.find(value => value.value !== optionValue(draft.worldgen[shard], item, shard))!.value;
          draft.worldgen[shard] = updateShardOption(draft.worldgen[shard], item, value, shard);
          // The page snapshots its draft after every edit and again before saving.
          const snapshot = copyWorldSettings(draft);
          assert.equal(optionValue(snapshot.worldgen[shard], item, shard), value);
          assert.deepEqual(structuredClone(snapshot), snapshot);
          snapshot.worldgen[shard].worldgenOverrides.test = "snapshot";
          snapshot.worldgen[shard].worldSettingsOverrides.test = "snapshot";
          snapshot.advanced.adminList.push("KU_snapshot");
          assert.equal(draft.worldgen[shard].worldgenOverrides.test, undefined);
          assert.equal(draft.worldgen[shard].worldSettingsOverrides.test, undefined);
          assert.deepEqual(draft.advanced.adminList, []);
        }
      });
    }
  }
});
