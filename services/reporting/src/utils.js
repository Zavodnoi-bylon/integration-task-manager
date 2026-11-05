// escapeHtml body retained from TheRaw Explorer, src/main.js:79–86.
export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function numeric(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function formatNumber(value, digits = 0) {
  const number = numeric(value);
  if (number === null) return "—";
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(number);
}

export function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(date);
}

export function dateLabel(value, year = false) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "—";
  const date = new Date(`${value}T12:00:00+03:00`);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow", day: "numeric", month: "short", ...(year ? { year: "numeric" } : {}),
  }).format(date);
}

export function periodLabel(from, to) {
  return from === to ? dateLabel(from, true) : `${dateLabel(from)} — ${dateLabel(to, true)}`;
}

export function shiftDate(iso, days) {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function presetPeriod(kind, today) {
  if (!validIsoDate(today)) throw new Error("Сервер не вернул текущую дату по Москве.");
  if (kind === "yesterday") return { from: shiftDate(today, -1), to: shiftDate(today, -1) };
  if (kind === "7d") return { from: shiftDate(today, -6), to: today };
  if (kind === "month") return { from: `${today.slice(0, 7)}-01`, to: today };
  return { from: today, to: today };
}

export function validIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
  const date = new Date(`${value}T12:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function validatePeriod(from, to) {
  if (!validIsoDate(from) || !validIsoDate(to)) return "Укажите обе даты периода.";
  if (from > to) return "Дата начала должна быть не позже даты окончания.";
  const days = (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000 + 1;
  return days > 366 ? "Выберите период не длиннее 366 дней." : null;
}

export function queryString(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values).sort(([a], [b]) => a.localeCompare(b))) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function createRequestGate() {
  let sequence = 0;
  let controller = null;
  return {
    start() {
      controller?.abort();
      controller = new AbortController();
      const current = ++sequence;
      return { signal: controller.signal, isCurrent: () => current === sequence && !controller.signal.aborted };
    },
    abort() {
      sequence += 1;
      controller?.abort();
    },
  };
}

export function readPreference(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

export function savePreference(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Optional display preference. */ }
}

export const categoryLabels = { integration: "Интеграции", incident: "Инциденты", support: "Поддержка", change: "Изменения", other: "Другое" };
export const statusLabels = { unknown: "Не определён", open: "Открыта", waiting: "Ожидает", work_ready: "Ожидает подтверждения", completed: "Завершена", cancelled: "Отменена" };
export const eventLabels = { requested: "Поступил запрос", observed_open: "Обнаружена открытая задача", assigned: "Назначен ответственный", waiting: "Ожидание", resumed: "Работа продолжена", work_ready: "Результат готов, ждёт подтверждения", completed: "Завершение подтверждено", reopened: "Открыта повторно", cancelled: "Отменена" };
export const timeBasisLabels = { event: "Время события", message: "Время сообщения", notification: "Время уведомления", observed: "Время наблюдения" };
