export const LAYOUT_KEY = "team-activity-layout-v1";
export const widgetDefinitions = Object.freeze([
  { id: "metrics", title: "Основные показатели", span: 12, minSpan: 6, minHeight: 140 },
  { id: "flow", title: "Поток задач", span: 7, minSpan: 4, minHeight: 300 },
  { id: "backlog", title: "Известно открытых", span: 5, minSpan: 4, minHeight: 300 },
  { id: "categories", title: "Структура работы", span: 12, minSpan: 4, minHeight: 140 },
  { id: "registry", title: "Реестр и предложения", span: 12, minSpan: 6, minHeight: 320 },
  { id: "calendar", title: "График смен", span: 3, minSpan: 3, minHeight: 372 },
].map(Object.freeze));

export function normalizeLayout(value) {
  const rows = value?.version === 1 && Array.isArray(value.widgets) ? value.widgets : [];
  const seen = new Set();
  const widgets = [];
  for (const row of rows) {
    const definition = widgetDefinitions.find((item) => item.id === row?.id);
    if (!definition || seen.has(row.id)) continue;
    seen.add(row.id);
    widgets.push({
      id: row.id,
      span: Number.isInteger(row.span) ? Math.max(definition.minSpan, Math.min(12, row.span)) : definition.span,
      height: Number.isFinite(row.height) ? Math.max(definition.minHeight, Math.min(1200, Math.round(row.height))) : null,
    });
  }
  for (const item of widgetDefinitions) {
    if (!seen.has(item.id)) widgets.push({ id: item.id, span: item.span, height: null });
  }
  return { version: 1, widgets };
}

export function moveWidget(layout, sourceId, targetId, after = false) {
  const result = normalizeLayout(layout);
  if (sourceId === targetId) return result;
  const source = result.widgets.find((item) => item.id === sourceId);
  if (!source || !result.widgets.some((item) => item.id === targetId)) return result;
  result.widgets = result.widgets.filter((item) => item.id !== sourceId);
  const target = result.widgets.findIndex((item) => item.id === targetId);
  result.widgets.splice(target + Number(after), 0, source);
  return result;
}

export function resizedWidget(id, initial, deltaX, deltaY, columnWidth) {
  const definition = widgetDefinitions.find((item) => item.id === id);
  if (!definition || !Number.isFinite(columnWidth) || columnWidth <= 0) throw new Error("Некорректный размер блока.");
  const span = Math.max(definition.minSpan, Math.min(12, initial.span + Math.round(deltaX / columnWidth)));
  const height = Math.max(definition.minHeight, Math.min(1200, Math.round((initial.height + deltaY) / 12) * 12));
  return { id, span, height };
}

export function readLayout(storage) {
  try { return normalizeLayout(JSON.parse(storage.getItem(LAYOUT_KEY))); } catch { return normalizeLayout(null); }
}

export function writeLayout(storage, layout) {
  try { storage.setItem(LAYOUT_KEY, JSON.stringify(normalizeLayout(layout))); return true; } catch { return false; }
}
