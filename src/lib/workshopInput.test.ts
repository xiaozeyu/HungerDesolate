import test from "node:test";
import assert from "node:assert/strict";
import { normalizeWorkshopCollection, normalizeWorkshopIds } from "./workshopInput.ts";

test("normalizes Workshop IDs and links", () => {
  assert.deepEqual(normalizeWorkshopIds("123, workshop-456 https://steamcommunity.com/sharedfiles/filedetails/?id=789"), {
    ids: ["123", "456", "789"], error: null,
  });
});

test("rejects invalid Workshop input", () => {
  assert.equal(normalizeWorkshopIds("abc").error, "Workshop ID 必须是数字");
  assert.equal(normalizeWorkshopIds("").error, "请输入 Workshop ID 或链接");
});

test("normalizes a single Workshop collection link", () => {
  assert.deepEqual(normalizeWorkshopCollection("https://steamcommunity.com/sharedfiles/filedetails/?id=123456789"), {
    id: "123456789", error: null,
  });
  assert.equal(normalizeWorkshopCollection("123 456").error, "一次只能导入一个 Steam 合集");
});
