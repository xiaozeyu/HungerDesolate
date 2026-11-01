const ID_PATTERN = /^\d{1,20}$/;

export function normalizeWorkshopIds(input: string): { ids: string[]; error: string | null } {
  const values = input
    .split(/[\s,;]+/)
    .map(value => value.trim())
    .filter(Boolean)
    .map(value => {
      const match = value.match(/(?:id=|filedetails\/\?id=|workshop[-/])([0-9]{1,20})/i);
      return match?.[1] ?? value;
    });
  const ids = [...new Set(values)];
  if (!ids.length) return { ids: [], error: "请输入 Workshop ID 或链接" };
  if (ids.length > 100) return { ids: [], error: "一次最多查询 100 个 Workshop 条目" };
  if (ids.some(value => !ID_PATTERN.test(value))) return { ids: [], error: "Workshop ID 必须是数字" };
  return { ids, error: null };
}

export function normalizeWorkshopCollection(input: string): { id: string | null; error: string | null } {
  const parsed = normalizeWorkshopIds(input);
  if (parsed.error) return { id: null, error: parsed.error };
  if (parsed.ids.length !== 1) return { id: null, error: "一次只能导入一个 Steam 合集" };
  return { id: parsed.ids[0]!, error: null };
}
