import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { reactive } from "vue";
import { copyWorldSettings } from "./worldConfigDraft.ts";
import { defaultWorldSettings } from "./dstWorldOptions.ts";

describe("world config draft copying", () => {
  it("copies Vue reactive settings into an independent plain snapshot", () => {
    const draft = reactive(defaultWorldSettings(true));
    draft.worldgen.master.worldgenOverrides.grass = "often";

    const snapshot = copyWorldSettings(draft);
    snapshot.worldgen.master.worldgenOverrides.grass = "rare";

    assert.equal(draft.worldgen.master.worldgenOverrides.grass, "often");
    assert.equal(snapshot.worldgen.master.worldgenOverrides.grass, "rare");
  });
});
