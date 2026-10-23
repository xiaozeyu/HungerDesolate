import assert from "node:assert/strict";
import test from "node:test";
import { compareWorkshopRevision } from "./modUpdateCheck.ts";

test("compares opaque Workshop revision handles and never treats timestamps as versions", () => {
  assert.equal(compareWorkshopRevision("file-a", "file-a"), "unchanged");
  assert.equal(compareWorkshopRevision("file-b", "file-a"), "candidate");
  assert.equal(compareWorkshopRevision("file-a", null), "candidate");
  assert.equal(compareWorkshopRevision(null, "file-a"), "unknown");
  assert.equal(compareWorkshopRevision(null, null), "unknown");
});
