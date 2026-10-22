import test from "node:test";
import assert from "node:assert/strict";
import { normalizeWorkshopIds } from "./workshopInput.ts";

test("normalizes Workshop IDs and links", () => {
  assert.deepEqual(normalizeWorkshopIds("123, workshop-456 https://steamcommunity.com/sharedfiles/filedetails/?id=789"), {
    ids: ["123", "456", "789"], error: null,
  });
});

test("rejects invalid Workshop input", () => {
  assert.equal(normalizeWorkshopIds("abc").error, "Workshop ID 必须是数字");
  assert.equal(normalizeWorkshopIds("").error, "请输入 Workshop ID 或链接");
});
