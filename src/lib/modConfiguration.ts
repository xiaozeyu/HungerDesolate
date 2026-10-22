import type { ModField, ModOption, ModSchema } from "../types/mods";

export type ModFieldControl = "choice" | "boolean" | "number" | "text" | "unsupported";
export interface ModFieldGroup {
  name: string;
  label: string;
  hover: string | null;
  fields: ModField[];
  options: ModOption[];
  defaultValue: unknown;
  hasDefault: boolean;
  control: ModFieldControl;
  issue: string | null;
  shared: boolean;
}

export function cloneConfiguration(value: Record<string, unknown> | null): Record<string, unknown> {
  if (!value) return {};
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

export function schemaFieldIssue(_schema: ModSchema, field: ModField): string | null {
  if (!field.name || field.name.length > 256 || [...field.name].some(char => /[\u0000-\u001f\u007f]/.test(char))) {
    return "字段名称无效，暂不支持编辑";
  }
  return null;
}

export function groupModFields(schema: ModSchema, configuration: Record<string, unknown>): ModFieldGroup[] {
  const groups = new Map<string, ModField[]>();
  for (const field of schema.fields) groups.set(field.name, [...(groups.get(field.name) ?? []), field]);
  return [...groups].map(([name, fields]) => {
    const first = fields[0]!;
    const shared = fields.length > 1;
    const names = [...new Set(fields.map(field => field.label))];
    const base = {
      name,
      label: names.join(" / "),
      hover: [...new Set(fields.flatMap(field => field.hover ? [field.hover] : []))].join("\n") || null,
      fields,
      shared,
    };
    const issue = fields.map(field => schemaFieldIssue(schema, field)).find(Boolean)
      ?? (!fields.every(field => field.editable) ? first.reason ?? "此配置项不支持编辑" : null);
    if (issue) return { ...base, options: [], defaultValue: null, hasDefault: false, control: "unsupported", issue };
    const current = configuration[name];
    const hasCurrent = Object.hasOwn(configuration, name);
    const controls = fields.map(field => fieldControl(field, current, hasCurrent));
    if (controls[0] === "unsupported" || controls.some(control => control !== controls[0])) {
      return { ...base, options: [], defaultValue: null, hasDefault: false, control: "unsupported", issue: shared
        ? "同名配置项的值类型不一致，无法安全合并" : first.reason ?? "此配置项的类型无法安全编辑" };
    }
    const control = controls[0]!;
    if (shared && hasCurrent === false && control !== "choice"
        && !fields.every(field => sameJsonValue(field.defaultValue, first.defaultValue))) {
      return { ...base, options: [], defaultValue: null, hasDefault: false, control: "unsupported",
        issue: "同名配置项的默认值不同，无法安全确定当前显示值" };
    }
    if (control === "choice") {
      const options = first.options.filter(option => fields.every(field =>
        field.options.some(candidate => sameJsonValue(candidate.value, option.value))));
      if (options.length === 0) {
        return { ...base, options: [], defaultValue: null, hasDefault: false, control: "unsupported",
          issue: shared ? "同名配置项没有共同候选值，无法安全合并" : "此配置项没有可用候选值" };
      }
      const defaults = fields.map(field => field.defaultValue);
      const hasDefault = defaults[0] !== null && defaults.every(value => sameJsonValue(value, defaults[0]));
      const defaultValue = hasDefault ? defaults[0] : null;
      return { ...base, options, defaultValue, hasDefault, control, issue: null };
    }
    const defaults = fields.map(field => field.defaultValue);
    const hasDefault = defaults[0] !== null && defaults.every(value => sameJsonValue(value, defaults[0]));
    if (shared && !hasCurrent && !hasDefault) {
      return { ...base, options: [], defaultValue: null, hasDefault: false, control: "unsupported",
        issue: "同名配置项的默认值不同，无法安全确定当前显示值" };
    }
    const defaultValue = hasDefault ? defaults[0] : null;
    return { ...base, options: [], defaultValue, hasDefault, control, issue: null };
  });
}

export function fieldControl(field: ModField, currentValue: unknown, hasCurrentValue: boolean): ModFieldControl {
  if (!field.editable || isObjectOrArray(field.defaultValue)) return "unsupported";
  if (field.options.length > 0) return "choice";
  const inferred = isScalar(field.defaultValue) ? field.defaultValue
    : hasCurrentValue && isScalar(currentValue) ? currentValue : null;
  if (typeof inferred === "boolean") return "boolean";
  if (typeof inferred === "number" && Number.isFinite(inferred)) return "number";
  if (typeof inferred === "string") return "text";
  return "unsupported";
}

export function optionIndex(field: ModField, value: unknown): string {
  const index = field.options.findIndex(option => sameJsonValue(option.value, value));
  return index < 0 ? "" : String(index);
}

export function validateChangedConfiguration(
  schema: ModSchema,
  base: Record<string, unknown>,
  draft: Record<string, unknown>,
  changedFields: Iterable<string>,
): string | null {
  for (const name of changedFields) {
    const fields = schema.fields.filter(field => field.name === name);
    const first = fields[0];
    if (!first || fields.some(field => schemaFieldIssue(schema, field) || !field.editable)) {
      return `配置项“${first?.label ?? name}”不支持编辑`;
    }
    if (!Object.hasOwn(draft, name)) continue;
    const value = draft[name];
    const controls = fields.map(field => fieldControl(field, base[name], Object.hasOwn(base, name)));
    if (controls.some(control => control !== controls[0] || control === "unsupported")) {
      return `配置项“${first.label}”存在不兼容的同名定义`;
    }
    if (controls[0] === "choice") {
      if (!fields.every(field => field.options.some(option => sameJsonValue(option.value, value)))) {
        return `配置项“${first.label}”的值不在所有同名 MOD 选项中`;
      }
      continue;
    }
    const validForField = (field: ModField) => {
      const control = fieldControl(field, base[name], Object.hasOwn(base, name));
      return control === "boolean" ? typeof value === "boolean"
        : control === "number" ? typeof value === "number" && Number.isFinite(value)
          : control === "text" ? typeof value === "string" : false;
    };
    if (!fields.every(validForField)) return `配置项“${first.label}”的值类型无效`;
  }
  return null;
}

export function mergeChangedConfiguration(
  base: Record<string, unknown>,
  draft: Record<string, unknown>,
  changedFields: Iterable<string>,
): Record<string, unknown> {
  const result = cloneConfiguration(base);
  for (const name of changedFields) {
    if (Object.hasOwn(draft, name)) result[name] = draft[name];
    else delete result[name];
  }
  return result;
}

export function sameJsonValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function isObjectOrArray(value: unknown): value is Record<string, unknown> | unknown[] {
  return value !== null && typeof value === "object";
}

function isScalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "boolean"
    || typeof value === "number" && Number.isFinite(value);
}
