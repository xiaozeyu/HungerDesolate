import assert from "node:assert/strict";
import test from "node:test";
import type { ModSchema } from "../types/mods.ts";
import {
  fieldControl,
  groupModFields,
  mergeChangedConfiguration,
  optionIndex,
  schemaFieldIssue,
  validateChangedConfiguration,
} from "./modConfiguration.ts";

const schema: ModSchema = {
  status: "available", folderName: "workshop-123", locale: "zh",
  metadata: { name: "Test", author: null, description: null, version: "1", apiVersion: null },
  fields: [
    { name: "mode", label: "Mode", hover: null, defaultValue: 2, options: [
      { label: "Slow", value: 1, hover: null }, { label: "Fast", value: 2, hover: null },
    ], editable: true, reason: null },
    { name: "enabled", label: "Enabled", hover: null, defaultValue: false, options: [], editable: true, reason: null },
    { name: "count", label: "Count", hover: null, defaultValue: 3, options: [], editable: true, reason: null },
    { name: "name", label: "Name", hover: null, defaultValue: "Default", options: [], editable: true, reason: null },
    { name: "complex", label: "Complex", hover: null, defaultValue: { nested: true }, options: [], editable: false, reason: "complex" },
  ],
  diagnostics: [],
};

test("selects controls from schema and rejects unsupported values", () => {
  assert.equal(fieldControl(schema.fields[0]!, 2, true), "choice");
  assert.equal(fieldControl(schema.fields[1]!, false, true), "boolean");
  assert.equal(fieldControl(schema.fields[2]!, 3, true), "number");
  assert.equal(fieldControl(schema.fields[3]!, "Default", true), "text");
  assert.equal(fieldControl(schema.fields[4]!, null, false), "unsupported");
});

test("maps exact option values and isolates duplicate schema keys", () => {
  assert.equal(optionIndex(schema.fields[0]!, 2), "1");
  assert.equal(optionIndex(schema.fields[0]!, "2"), "");
  assert.equal(schemaFieldIssue(schema, schema.fields[0]!), null);
  const duplicate = { ...schema, fields: [...schema.fields, schema.fields[0]!] };
  assert.equal(schemaFieldIssue(duplicate, duplicate.fields[0]!), null);
  assert.match(schemaFieldIssue(schema, { ...schema.fields[0]!, name: "" }) ?? "", /名称无效/);
});

test("groups same-name fields only when they share an editable value", () => {
  const repeated = { ...schema.fields[0]!, label: "Fast mode", options: [
    { label: "Fast", value: 2, hover: null }, { label: "Turbo", value: 3, hover: null },
  ] };
  const duplicate = { ...schema, fields: [schema.fields[0]!, repeated] };
  const [group] = groupModFields(duplicate, {});
  assert.equal(group?.shared, true);
  assert.equal(group?.control, "choice");
  assert.deepEqual(group?.options.map(option => option.value), [2]);
  assert.equal(validateChangedConfiguration(duplicate, {}, { mode: 2 }, ["mode"]), null);
  assert.match(validateChangedConfiguration(duplicate, {}, { mode: 3 }, ["mode"]) ?? "", /所有同名/);

  const incompatible = { ...schema, fields: [schema.fields[0]!, {
    ...repeated, options: [{ label: "Turbo", value: 3, hover: null }],
  }] };
  assert.equal(groupModFields(incompatible, {})[0]?.control, "unsupported");
});

test("validates edited values against declared options and scalar types", () => {
  const base = { mode: 2, enabled: false, count: 3, name: "Default", legacy: "keep" };
  assert.equal(validateChangedConfiguration(schema, base,
    { ...base, mode: 1, enabled: true, count: 4, name: "Custom" },
    ["mode", "enabled", "count", "name"]), null);
  assert.match(validateChangedConfiguration(schema, base, { ...base, mode: "1" }, ["mode"]) ?? "", /选项/);
  assert.match(validateChangedConfiguration(schema, base, { ...base, count: Number.NaN }, ["count"]) ?? "", /类型/);
  assert.match(validateChangedConfiguration(schema, base, { ...base, complex: {} }, ["complex"]) ?? "", /不支持/);
});

test("merges only edited keys and keeps legacy values untouched", () => {
  const base = { mode: 2, legacy: "keep", resettable: true };
  const draft = { ...base, mode: 1 };
  delete (draft as Record<string, unknown>).resettable;
  const result = mergeChangedConfiguration(base, draft, ["mode", "resettable"]);
  assert.deepEqual(result, { mode: 1, legacy: "keep" });
  assert.deepEqual(base, { mode: 2, legacy: "keep", resettable: true });
});
