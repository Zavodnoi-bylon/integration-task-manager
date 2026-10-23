import "./base.css";
import "./style.css";
import { backlogChart, flowChart } from "./charts.js";
import { mountSchedule } from "./schedule.js";
import { mountLayout } from "./layout.js";
import {
  categoryLabels, createRequestGate, dateLabel, escapeHtml, eventLabels, formatNumber,
  formatTimestamp, numeric, periodLabel, presetPeriod, queryString, readPreference,
  safeSourceUrl, savePreference, statusLabels, timeBasisLabels, validatePeriod,
} from "./utils.js";

const $ = (selector) => document.querySelector(selector);
const gates = Object.fromEntries(["meta", "report", "tasks", "detail", "proposals", "export"].map((key) => [key, createRequestGate()]));
const savedBucket = readPreference("team-activity-bucket-v1", "day");
const savedPageSize = readPreference("team-activity-page-size-v1", 25);
const state = {
  meta: null, demo: false, bucket: ["day", "week"].includes(savedBucket) ? savedBucket : "day",
  report: null, reportParams: null, registryParams: null, drill: null,
  page: 1, pageSize: [25, 50, 100].includes(savedPageSize) ? savedPageSize : 25,
  tasks: null, proposals: [], reportLoading: false, tasksLoading: false, exportLoading: false,
  activeTab: "registry",
  access: null,
};
const fields = { from: $("#date-from"), to: $("#date-to"), category: $("#category"), client: $("#client"), assignee: $("#assignee"), q: $("#search") };
const dialog = $("#detail-dialog");
let schedule = null;
const allowed = panel => Boolean(state.access?.panels.includes(panel));

async function initializeAccess(token) {
  const access = await apiJson("/api/access", token);
  if (!Array.isArray(access.panels)) throw new Error("Не удалось проверить права доступа.");
  const settings = await apiJson("/api/layout", token);
  let revision = settings.revision;
  state.access = access;
  mountLayout($("#dashboard-layout"), $("#layout-toolbar"), {
    layout: settings.layout, canEdit: access.can_edit_layout,
    async onSave(layout) {
      const response = await fetch("/api/layout", {
        method: "PUT", headers: {"Content-Type":"application/json", "X-Activity-Action":"save-layout"},
        body: JSON.stringify({layout, revision}),
      });
      const value = await response.json();
      if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Не удалось сохранить общую раскладку.");
      revision = value.revision;
    },
  });
  for (const node of document.querySelectorAll("[data-layout-id]")) node.hidden = !allowed(node.dataset.layoutId);
  $("#dashboard-layout").hidden = false;
  for (const selector of ['a[href="#schedule-section"]', 'a[href="#registry-section"]', '#open-proposals']) {
    $(selector).hidden = !allowed(selector.includes("schedule") ? "calendar" : "registry");
  }
  if (allowed("calendar")) {
    const roster = await apiJson("/api/schedule", token);
    schedule = mountSchedule($("#schedule-section"), roster.people);
    schedule.setToday(access.today);
  }
  const hasReport = access.panels.some(p => ["metrics","flow","backlog","categories","registry"].includes(p));
  $("#filters").hidden = !hasReport;
  $(".report-heading").hidden = !hasReport;
  $(".freshness").hidden = !hasReport;
  if (!allowed("registry")) {
    for (const name of ["category", "client", "assignee", "q"]) fields[name].closest("label").hidden = true;
  }
  $("#access-status").textContent = access.panels.length
    ? `Доступ: ${access.role === "admin" ? "Администратор" : access.role === "viewer" ? "Просмотр всех панелей" : "Tech lead"}`
    : "Панели ещё не назначены. Доступ появится после выбора панелей администратором.";
  return hasReport;
}

async function apiResponse(path, token, accept = "application/json") {
  const response = await fetch(path, { signal: token.signal, headers: { Accept: accept }, cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = typeof payload?.detail === "string" ? payload.detail : null;
    throw new Error(detail || `Не удалось получить данные (HTTP ${response.status}).`);
  }
  return response;
}

async function apiJson(path, token) {
  const response = await apiResponse(path, token);
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("Сервер вернул неожиданный ответ. Проверьте доступность API.");
  }
  const payload = await response.json();
  return payload;
}

function observeDemo(payload) {
  if (payload?.demo === true) state.demo = true;
  $("#demo-banner").hidden = !state.demo;
}

function showError(message) {
  $("#report-error").textContent = message;
  $("#report-error").hidden = false;
}

function renderCoverage(coverage) {
  const complete = coverage?.status === "complete";
  const notice = $("#coverage-notice");
  notice.classList.toggle("complete", complete);
  const title = complete ? "Охват подтверждён." : "Частичный охват данных.";
  const defaultNote = complete
    ? "Показатели рассчитаны по принятому набору источников."
    : "Показаны только известные задачи. Полный объём открытых задач отдела может быть больше.";
  notice.innerHTML = `<strong>${title}</strong><span>${escapeHtml(defaultNote)}${coverage?.note ? ` ${escapeHtml(coverage.note)}` : ""}</span>`;
  notice.hidden = false;
}

function renderFreshness() {
  const loaded = Boolean(state.meta);
  $(".freshness").classList.toggle("loaded", loaded);
  $("#freshness-text").textContent = state.meta?.last_import_at
    ? `Последняя загрузка: ${formatTimestamp(state.meta.last_import_at)}`
    : loaded ? "Дата последней загрузки не указана" : "Источники недоступны";
}

function updateProposalCounts(value) {
  for (const selector of ["#nav-proposal-count", "#proposal-count"]) $(selector).textContent = formatNumber(value);
}

function readFilters() {
  return {
    from: fields.from.value, to: fields.to.value, bucket: state.bucket,
    category: fields.category.value, client: fields.client.value,
    assignee: fields.assignee.value, q: fields.q.value.trim(),
  };
}

function syncBucket() {
  document.querySelectorAll("[data-bucket]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.bucket === state.bucket));
  });
}

function markDirty() {
  const dirty = state.reportParams && queryString(readFilters()) !== queryString(state.reportParams);
  $("#filter-dirty").hidden = !dirty;
}

function clearOverview(message = "Загрузка данных…") {
  state.report = null;
  state.reportParams = null;
  state.registryParams = null;
  state.tasks = null;
  $("#kpis").innerHTML = "";
  $("#category-breakdown").innerHTML = "";
  $("#report-empty").hidden = true;
  $("#report-error").hidden = true;
  $("#chart-tooltip").hidden = true;
  for (const selector of ["#flow-chart", "#backlog-chart"]) {
    $(selector).innerHTML = `<div class="chart-placeholder">${escapeHtml(message)}</div>`;
    $(selector).setAttribute("aria-busy", "true");
  }
  $("#registry-table").innerHTML = `<div class="table-empty">${escapeHtml(message)}</div>`;
  $("#task-count").textContent = "—";
  $("#pagination-label").textContent = "—";
  $("#page-label").textContent = "1";
  $("#page-prev").disabled = true;
  $("#page-next").disabled = true;
  $("#export-tasks").disabled = true;
  $("#export-status").hidden = true;
}

function renderKpis(summary) {
  const completions = numeric(summary.completion_events);
  const distinct = numeric(summary.completed);
  const completedNote = completions !== null && distinct !== null && completions !== distinct
    ? `${formatNumber(completions)} событий завершения, включая повторные`
    : "Уникальные задачи за период";
  const cards = [
    { kind: "incoming", label: "Поступило", value: summary.incoming, note: "Первый подтверждённый запрос", icon: "↘" },
    { kind: "completed", label: "Завершено", value: summary.completed, note: completedNote, icon: "✓" },
    { kind: "backlog", label: "Известно открытых", value: summary.observed_backlog, note: "На конец выбранного периода", icon: "◷" },
    { kind: "duration", label: "Медиана срока решения", value: summary.resolution_median_hours, note: `Календарные часы · выборка: ${formatNumber(summary.resolution_sample_size)}`, icon: "↔" },
  ];
  $("#kpis").innerHTML = cards.map((card) => {
    const interactive = card.kind !== "duration";
    const tag = interactive ? "button" : "article";
    const attributes = interactive ? `type="button" data-registry-metric="${card.kind}" title="Открыть соответствующие задачи"` : 'title="От первого запроса до последнего завершения задачи в выбранном периоде. Это календарный срок, не трудозатраты."';
    const value = formatNumber(card.value, card.kind === "duration" ? 1 : 0);
    const unit = card.kind === "duration" && numeric(card.value) !== null ? "<small>ч</small>" : "";
    return `<${tag} class="kpi-card ${card.kind}" ${attributes}><span class="kpi-label">${card.label}<span class="kpi-icon" aria-hidden="true">${card.icon}</span></span><strong class="kpi-value">${value}${unit}</strong><span class="kpi-note">${escapeHtml(card.note)}</span></${tag}>`;
  }).join("");
}

function renderBreakdown(rows) {
  if (!rows.length) {
    $("#category-breakdown").innerHTML = '<div class="category-empty">Нет классифицированных задач в выбранном срезе.</div>';
    return;
  }
  const max = Math.max(1, ...rows.map((row) => numeric(row.tasks) ?? 0));
  $("#category-breakdown").innerHTML = rows.map((row) => {
    const count = numeric(row.tasks);
    const width = count === null ? 0 : Math.max(0, Math.min(100, count / max * 100));
    return `<button type="button" class="category-card" data-category="${escapeHtml(row.category)}" aria-label="Показать тип ${escapeHtml(row.label || categoryLabels[row.category] || row.category)}"><span class="category-name">${escapeHtml(row.label || categoryLabels[row.category] || row.category)}<span aria-hidden="true">↗</span></span><span class="category-values"><strong>${formatNumber(count)}</strong><small>завершено: ${formatNumber(row.completed)}</small></span><span class="category-track"><span style="width:${width}%"></span></span></button>`;
  }).join("");
}

function renderReport(payload, params) {
  observeDemo(payload);
  renderCoverage(payload.coverage || state.meta?.coverage);
  $("#report-period").textContent = periodLabel(payload.from, payload.to);
  const partial = payload.bucket === "week" && payload.series.some((row) => row.partial);
  $("#report-status").textContent = `${payload.bucket === "week" ? "По неделям" : "По дням"} · МСК${partial ? " · крайние недели неполные" : ""}`;
  $("#report-status").classList.remove("error");
  $("#flow-bucket-label").textContent = payload.bucket === "week" ? "По неделям" : "По дням";
  renderKpis(payload.summary);
  $("#flow-chart").innerHTML = flowChart(payload.series);
  $("#backlog-chart").innerHTML = backlogChart(payload.series);
  for (const selector of ["#flow-chart", "#backlog-chart"]) $(selector).setAttribute("aria-busy", "false");
  renderBreakdown(payload.breakdown);
  updateProposalCounts(payload.summary.pending_proposals);
  $("#report-empty").hidden = !payload.empty;
  if (payload.empty) $("#report-empty").innerHTML = '<strong>Пока нет принятых задач для этого отчёта</strong>Загрузите источники и проверьте предложения или выберите другой период. Отсутствие данных не означает нулевую нагрузку.';
  const missing = numeric(payload.summary.missing_request_count);
  $("#registry-note").textContent = `Тип, клиент и ответственный — текущие значения карточки. Задачи без подтверждённого имени входят в общую нагрузку отдела.${missing ? ` За весь период отчёта для ${formatNumber(missing)} завершённых задач неизвестно начало запроса: срок решения по ним не рассчитывается.` : ""}`;
  history.replaceState(null, "", `${location.pathname}?${queryString(params)}${location.hash}`);
}

async function loadReport() {
  if (!state.meta) return;
  if (!state.access?.panels.some(p => ["metrics","flow","backlog","categories"].includes(p))) {
    state.reportParams = Object.freeze({...readFilters()});
    if (allowed("registry")) void loadTasks();
    return;
  }
  const params = Object.freeze({ ...readFilters() });
  const token = gates.report.start();
  gates.tasks.abort(); gates.export.abort(); gates.detail.abort();
  if (dialog.open) dialog.close();
  state.drill = null; state.page = 1; state.reportLoading = true; state.exportLoading = false;
  state.tasksLoading = false;
  clearOverview();
  renderDrilldownLabel();
  $("#report-status").textContent = "Загрузка…";
  $("#report-period").textContent = "Обновляем отчёт";
  $("#filter-dirty").hidden = true;
  $("#refresh").disabled = true;
  $("#apply-filters").disabled = true;
  const validationError = validatePeriod(params.from, params.to);
  try {
    if (validationError) throw new Error(validationError);
    const payload = await apiJson(`/api/report?${queryString(params)}`, token);
    if (!token.isCurrent()) return;
    if (!Array.isArray(payload.series) || !Array.isArray(payload.breakdown) || !payload.summary) throw new Error("Ответ отчёта имеет неожиданный формат.");
    state.report = payload;
    state.reportParams = params;
    renderReport(payload, params);
    markDirty();
    if (allowed("registry")) void loadTasks();
    if (allowed("registry") && state.activeTab === "proposals") void loadProposals();
  } catch (error) {
    if (!token.isCurrent() || error.name === "AbortError") return;
    clearOverview("Отчёт не загружен");
    $("#report-period").textContent = "Отчёт недоступен";
    $("#report-status").textContent = "Ошибка загрузки";
    $("#report-status").classList.add("error");
    for (const selector of ["#flow-chart", "#backlog-chart"]) $(selector).setAttribute("aria-busy", "false");
    showError(error.message || "Не удалось загрузить отчёт.");
  } finally {
    if (token.isCurrent()) {
      state.reportLoading = false;
      $("#refresh").disabled = false;
      $("#apply-filters").disabled = false;
      updateExportButton();
    }
  }
}

function registryFilters() {
  if (!state.reportParams) return null;
  const { bucket, ...filters } = state.reportParams;
  return {
    ...filters,
    ...(state.drill ? { from: state.drill.from, to: state.drill.to } : {}),
    metric: state.drill?.metric || "all", status: $("#task-status").value,
  };
}

function renderDrilldownLabel() {
  const label = $("#drilldown-label");
  label.classList.toggle("active", Boolean(state.drill));
  if (state.drill) {
    const metric = { incoming: "Поступившие", completed: "Завершённые", backlog: "Известно открытых" }[state.drill.metric];
    label.textContent = `${metric} · ${periodLabel(state.drill.from, state.drill.to)}`;
  } else label.textContent = "Все задачи выбранного периода";
  $("#clear-drilldown").hidden = !state.drill;
}

function renderTaskRows(payload) {
  const rows = payload.rows;
  if (!rows.length) {
    $("#registry-table").innerHTML = '<div class="table-empty">В этом срезе задач не найдено. Попробуйте другой период или статус.</div>';
  } else {
    const body = rows.map((task) => {
      const status = Object.hasOwn(statusLabels, task.status) ? task.status : "unknown";
      return `<tr><td class="title-cell"><button type="button" class="task-title" data-task-id="${escapeHtml(task.task_id)}">${escapeHtml(task.title)}</button><span class="task-id">${escapeHtml(task.task_id)}</span></td><td>${escapeHtml(categoryLabels[task.category] || task.category || "Не указан")}</td><td${task.client ? "" : ' class="cell-muted"'}>${escapeHtml(task.client || "Не указан")}</td><td${task.assignee ? "" : ' class="cell-muted"'}>${escapeHtml(task.assignee || "Команда / не определён")}</td><td><span class="status-badge ${status}">${statusLabels[status]}</span></td><td class="date-cell">${escapeHtml(formatTimestamp(task.requested_at))}</td><td class="date-cell">${escapeHtml(formatTimestamp(task.completed_at))}</td><td><span class="evidence-count" title="Количество подтверждений">${formatNumber(task.evidence_count)}</span></td></tr>`;
    }).join("");
    $("#registry-table").innerHTML = `<table class="task-table"><caption class="sr-only">Задачи выбранного среза. Статус на конец выбранного периода.</caption><thead><tr><th scope="col">Задача</th><th scope="col">Тип</th><th scope="col">Клиент</th><th scope="col">Ответственный</th><th scope="col">Статус на конец периода</th><th scope="col">Первый запрос, МСК</th><th scope="col">Завершение, МСК</th><th scope="col">Подтверждения</th></tr></thead><tbody>${body}</tbody></table>`;
  }
  $("#task-count").textContent = formatNumber(payload.total);
  const first = rows.length ? (payload.page - 1) * payload.page_size + 1 : 0;
  const last = rows.length ? first + rows.length - 1 : 0;
  $("#pagination-label").textContent = `${formatNumber(first)}–${formatNumber(last)} из ${formatNumber(payload.total)} задач`;
  $("#page-label").textContent = String(payload.page);
  $("#page-prev").disabled = payload.page <= 1;
  $("#page-next").disabled = !payload.has_more;
}

async function loadTasks() {
  if (!allowed("registry")) return;
  const filters = registryFilters();
  if (!filters) return;
  const token = gates.tasks.start();
  const params = { ...filters, page: state.page, page_size: state.pageSize };
  state.tasksLoading = true;
  state.registryParams = null;
  state.tasks = null;
  updateExportButton();
  $("#page-prev").disabled = true; $("#page-next").disabled = true;
  $("#registry-table").innerHTML = '<div class="table-empty">Загрузка задач…</div>';
  renderDrilldownLabel();
  try {
    const payload = await apiJson(`/api/tasks?${queryString(params)}`, token);
    if (!token.isCurrent()) return;
    if (!Array.isArray(payload.rows)) throw new Error("Ответ реестра имеет неожиданный формат.");
    observeDemo(payload);
    state.tasks = payload;
    state.page = payload.page;
    state.registryParams = Object.freeze({ ...filters });
    renderTaskRows(payload);
  } catch (error) {
    if (!token.isCurrent() || error.name === "AbortError") return;
    $("#registry-table").innerHTML = `<div class="table-empty error">${escapeHtml(error.message || "Не удалось загрузить задачи.")}</div>`;
    $("#task-count").textContent = "—";
    $("#pagination-label").textContent = "Реестр не загружен";
  } finally {
    if (token.isCurrent()) { state.tasksLoading = false; updateExportButton(); }
  }
}

function chooseDrilldown(metric, row = null) {
  if (!allowed("registry")) return;
  if (!state.reportParams || !state.report) return;
  const base = state.reportParams;
  state.drill = {
    from: row ? (row.period_start < base.from ? base.from : row.period_start) : base.from,
    to: row ? (row.period_end > base.to ? base.to : row.period_end) : base.to,
    metric,
  };
  state.page = 1;
  $("#task-status").value = "all";
  setActiveTab("registry");
  void loadTasks();
  $("#registry-section").scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function updateExportButton() {
  $("#export-tasks").disabled = state.reportLoading || state.tasksLoading || state.exportLoading || !state.registryParams || !state.tasks?.total || state.activeTab !== "registry";
}

async function exportTasks() {
  updateExportButton();
  if ($("#export-tasks").disabled) return;
  const params = { ...state.registryParams };
  const token = gates.export.start();
  state.exportLoading = true;
  updateExportButton();
  const status = $("#export-status");
  status.hidden = false; status.classList.remove("error");
  status.textContent = `Готовим полный реестр: ${periodLabel(params.from, params.to)}…`;
  try {
    const response = await apiResponse(`/api/export/tasks?${queryString(params)}`, token, "text/csv");
    if (!response.headers.get("content-type")?.includes("text/csv")) throw new Error("Сервер не вернул CSV. Файл не скачан.");
    const header = response.headers.get("X-Export-Count");
    const count = header === null ? NaN : Number(header);
    if (!Number.isSafeInteger(count) || count < 0) throw new Error("Не удалось проверить количество строк выгрузки.");
    const blob = await response.blob();
    if (!token.isCurrent()) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `team_activity_${params.from}_${params.to}.csv`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    status.textContent = `CSV готов: ${formatNumber(count)} задач · ${periodLabel(params.from, params.to)}. Выгружены все страницы выбранного среза.`;
  } catch (error) {
    if (!token.isCurrent() || error.name === "AbortError") return;
    status.textContent = error.message || "Не удалось скачать CSV.";
    status.classList.add("error");
  } finally {
    if (token.isCurrent()) { state.exportLoading = false; updateExportButton(); }
  }
}

function lifecycleMarkup(events = []) {
  if (!events.length) return '<p class="detail-no-data">События не указаны.</p>';
  return `<ol class="timeline">${events.map((event) => {
    const kind = Object.hasOwn(eventLabels, event.kind) ? event.kind : "unknown";
    return `<li class="event-${kind}"><strong>${escapeHtml(eventLabels[event.kind] || event.kind)}</strong><time datetime="${escapeHtml(event.occurred_at)}">${escapeHtml(formatTimestamp(event.occurred_at))}</time><small>${escapeHtml(timeBasisLabels[event.time_basis] || "Основание времени не указано")}</small></li>`;
  }).join("")}</ol>`;
}

function evidenceMarkup(evidence = []) {
  if (!evidence.length) return '<p class="detail-no-data">Подтверждения не указаны.</p>';
  return evidence.map((item) => {
    const url = safeSourceUrl(item.source_url);
    const flags = [item.has_conflict ? "Конфликт версий сообщения" : null, item.media_missing ? "Вложение не загружено" : null].filter(Boolean);
    const text = item.text_plain ?? "Текст сообщения не включён в эту запись. Доступна ссылка на сохранённое подтверждение.";
    return `<article class="evidence-card"><div class="evidence-heading"><span>${escapeHtml(item.source_name || "Исходное сообщение")}</span>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Открыть источник ↗</a>` : ""}</div><p class="evidence-meta">${escapeHtml(item.sender_name || "Автор не указан")} · ${escapeHtml(formatTimestamp(item.sent_at))}<br>${escapeHtml(item.message_key || "ID не указан")}${item.edited_at ? `<br>Изменено: ${escapeHtml(formatTimestamp(item.edited_at))}` : ""}${item.revision_hash ? `<br>Версия: ${escapeHtml(item.revision_hash)}` : ""}</p><pre class="evidence-text">${escapeHtml(text)}</pre>${flags.length ? `<div class="evidence-flags">${flags.map((flag) => `<span>${flag}</span>`).join("")}</div>` : ""}</article>`;
  }).join("");
}

function detailSummary(task, extra = "") {
  return `<dl class="detail-summary"><div><dt>Тип работы</dt><dd>${escapeHtml(categoryLabels[task.category] || task.category || "Не указан")}</dd></div><div><dt>Клиент / рекламодатель</dt><dd>${escapeHtml(task.client || "Не указан")}</dd></div><div><dt>Ответственный</dt><dd>${escapeHtml(task.assignee || "Команда / не определён")}</dd></div><div><dt>${extra ? "Версия" : "ID записи"}</dt><dd>${escapeHtml(extra || task.task_id)}</dd></div></dl>`;
}

async function openTask(taskId) {
  const token = gates.detail.start();
  $("#detail-kicker").textContent = "Карточка задачи";
  $("#detail-content").innerHTML = '<h2 id="detail-title">Загрузка…</h2><p class="detail-no-data">Получаем хронологию и подтверждения.</p>';
  if (!dialog.open) dialog.showModal();
  try {
    const payload = await apiJson(`/api/tasks/${encodeURIComponent(taskId)}`, token);
    if (!token.isCurrent()) return;
    observeDemo(payload);
    const task = payload.task;
    if (!task) throw new Error("Карточка задачи имеет неожиданный формат.");
    $("#detail-content").innerHTML = `<h2 id="detail-title">${escapeHtml(task.title)}</h2><p class="detail-id">${escapeHtml(task.task_id)}</p>${detailSummary(task, task.revision ? String(task.revision) : "")}<div class="detail-columns"><section><h3>Полная хронология · МСК</h3>${lifecycleMarkup(payload.events)}</section><section><h3>Подтверждения · ${formatNumber(payload.evidence?.length || 0)}</h3>${evidenceMarkup(payload.evidence)}</section></div>`;
  } catch (error) {
    if (!token.isCurrent() || error.name === "AbortError") return;
    $("#detail-content").innerHTML = `<h2 id="detail-title">Не удалось открыть задачу</h2><p class="detail-no-data">${escapeHtml(error.message)}</p>`;
  }
}

async function loadProposals() {
  const token = gates.proposals.start();
  $("#proposals-list").innerHTML = '<div class="table-empty">Загрузка предложений…</div>';
  try {
    const payload = await apiJson("/api/proposals", token);
    if (!token.isCurrent()) return;
    observeDemo(payload);
    if (!Array.isArray(payload.rows)) throw new Error("Ответ очереди имеет неожиданный формат.");
    state.proposals = payload.rows;
    updateProposalCounts(payload.total);
    $("#proposals-list").innerHTML = payload.rows.length ? payload.rows.map((proposal, index) => `<article class="proposal-row"><div><h3>${escapeHtml(proposal.title || proposal.task_id || "Предложение")}</h3><p>${escapeHtml(categoryLabels[proposal.category] || proposal.category || "Тип не указан")} · ${escapeHtml(proposal.client || "Клиент не указан")} · версия ${escapeHtml(proposal.version ?? "—")}${proposal.context_missing ? " · не хватает контекста" : ""}</p></div><button type="button" class="secondary" data-proposal-index="${index}">Посмотреть</button></article>`).join("") : '<div class="table-empty">Новых предложений на проверку нет.</div>';
  } catch (error) {
    if (!token.isCurrent() || error.name === "AbortError") return;
    state.proposals = [];
    $("#proposals-list").innerHTML = `<div class="table-empty error">${escapeHtml(error.message || "Не удалось загрузить предложения.")}</div>`;
  }
}

function openProposal(index) {
  const proposal = state.proposals[index];
  if (!proposal) return;
  gates.detail.abort();
  $("#detail-kicker").textContent = "Предложение · не входит в отчёт";
  const evidence = Array.isArray(proposal.evidence_details) ? proposal.evidence_details : proposal.evidence || [];
  $("#detail-content").innerHTML = `<h2 id="detail-title">${escapeHtml(proposal.title || "Предложение")}</h2><p class="detail-id">${escapeHtml(proposal.proposal_id || proposal.task_id || "")}</p><div class="detail-note">Это предложение ещё не принято и не включено в показатели.${proposal.context_missing ? " Для решения не хватает контекста." : ""}${proposal.note ? `<p>${escapeHtml(proposal.note)}</p>` : ""}</div>${detailSummary(proposal, String(proposal.version ?? "—"))}<div class="detail-columns"><section><h3>Предлагаемые события · МСК</h3>${lifecycleMarkup(proposal.events)}</section><section><h3>Подтверждения · ${formatNumber(evidence.length)}</h3>${evidenceMarkup(evidence)}</section></div>`;
  if (!dialog.open) dialog.showModal();
}

function setActiveTab(tab) {
  state.activeTab = tab;
  for (const name of ["registry", "proposals"]) {
    const active = name === tab;
    $(`#${name}-tab`).classList.toggle("active", active);
    $(`#${name}-tab`).setAttribute("aria-selected", String(active));
    $(`#${name}-tab`).tabIndex = active ? 0 : -1;
    $(`#${name}-view`).hidden = !active;
  }
  updateExportButton();
}

function selectOption(select, value) {
  if (value && ![...select.options].some((option) => option.value === value)) {
    select.add(new Option(`Неизвестное значение: ${value}`, value));
  }
  select.value = value || "";
}

function fillOptions(meta) {
  fields.category.replaceChildren(new Option("Все типы", ""));
  for (const item of meta.categories || []) fields.category.add(new Option(item.label, item.value));
  fields.client.replaceChildren(new Option("Все клиенты", ""));
  for (const value of meta.clients || []) fields.client.add(new Option(value, value));
  fields.assignee.replaceChildren(new Option("Вся команда", ""), new Option("Команда / не определён", "__unknown__"));
  for (const value of meta.assignees || []) fields.assignee.add(new Option(value, value));
}

function enableFilters(enabled) {
  $("#filters").querySelectorAll("input,select,button").forEach((element) => { element.disabled = !enabled; });
}

async function initialize() {
  const token = gates.meta.start();
  enableFilters(false);
  $("#refresh").disabled = true;
  clearOverview();
  try {
    if (!state.access && !await initializeAccess(token)) { $("#refresh").disabled = false; return; }
    if (!state.access.panels.some(p => ["metrics","flow","backlog","categories","registry"].includes(p))) { $("#refresh").disabled = false; return; }
    const meta = await apiJson("/api/meta", token);
    if (!token.isCurrent()) return;
    if (meta.timezone !== "Europe/Moscow") throw new Error("Ожидается отчёт в часовом поясе Europe/Moscow.");
    state.meta = meta;
    schedule?.setToday(meta.today);
    observeDemo(meta); renderCoverage(meta.coverage); renderFreshness(); updateProposalCounts(meta.pending_proposals);
    fillOptions(meta);
    const query = new URLSearchParams(location.search);
    fields.from.value = query.get("from") || meta.default_from;
    fields.to.value = query.get("to") || meta.default_to;
    for (const name of ["category", "client", "assignee"]) selectOption(fields[name], query.get(name));
    fields.q.value = query.get("q") || "";
    if (!allowed("registry")) for (const name of ["category", "client", "assignee", "q"]) fields[name].value = "";
    const bucket = query.get("bucket");
    if (["day", "week"].includes(bucket)) state.bucket = bucket;
    $("#page-size").value = String(state.pageSize);
    syncBucket(); enableFilters(true);
    await loadReport();
  } catch (error) {
    if (!token.isCurrent() || error.name === "AbortError") return;
    state.meta = null;
    renderFreshness(); clearOverview("Источники недоступны");
    $("#report-period").textContent = "Не удалось подключиться";
    $("#report-status").textContent = "Нет данных";
    showError(error.message || "Не удалось подключиться к API. Данные не подменяются демонстрацией.");
    if (!state.access) $("#access-status").textContent = "Не удалось проверить доступ. Обновите страницу.";
    $("#refresh").disabled = false;
  }
}

$("#filters").addEventListener("submit", (event) => { event.preventDefault(); void loadReport(); });
for (const field of Object.values(fields)) {
  field.addEventListener("input", markDirty);
  field.addEventListener("change", markDirty);
}
document.querySelectorAll("[data-period]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!state.meta) return;
    const period = presetPeriod(button.dataset.period, state.meta.today);
    fields.from.value = period.from; fields.to.value = period.to;
    document.querySelectorAll("[data-period]").forEach((item) => item.classList.toggle("active", item === button));
    void loadReport();
  });
});
document.querySelectorAll("[data-bucket]").forEach((button) => {
  button.addEventListener("click", () => {
    state.bucket = button.dataset.bucket;
    savePreference("team-activity-bucket-v1", state.bucket); syncBucket(); void loadReport();
  });
});
$("#reset-filters").addEventListener("click", () => {
  if (!state.meta) return;
  fields.from.value = state.meta.default_from; fields.to.value = state.meta.default_to;
  for (const name of ["category", "client", "assignee", "q"]) fields[name].value = "";
  document.querySelectorAll("[data-period]").forEach((item) => item.classList.remove("active"));
  void loadReport();
});
$("#refresh").addEventListener("click", () => { if (state.meta) void loadReport(); else void initialize(); });
$("#kpis").addEventListener("click", (event) => {
  const button = event.target.closest("[data-registry-metric]");
  if (button) chooseDrilldown(button.dataset.registryMetric);
});
$("#category-breakdown").addEventListener("click", (event) => {
  if (!allowed("registry")) return;
  const button = event.target.closest("[data-category]");
  if (!button) return;
  selectOption(fields.category, button.dataset.category);
  void loadReport();
});

function activateChartTarget(target) {
  const index = Number(target?.dataset.bucketIndex);
  if (!target || !Number.isInteger(index) || !state.report?.series[index]) return;
  chooseDrilldown(target.dataset.metric, state.report.series[index]);
}

for (const chart of [$("#flow-chart"), $("#backlog-chart")]) {
  chart.addEventListener("click", (event) => activateChartTarget(event.target.closest("[data-bucket-index]")));
  chart.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    const target = event.target.closest("[data-bucket-index]");
    if (target) { event.preventDefault(); activateChartTarget(target); }
  });
  const showTooltip = (target, x, y) => {
    const tooltip = $("#chart-tooltip");
    if (!target) { tooltip.hidden = true; return; }
    tooltip.textContent = target.dataset.tip;
    tooltip.hidden = false;
    tooltip.style.left = `${Math.max(10, Math.min(x + 13, window.innerWidth - tooltip.offsetWidth - 12))}px`;
    tooltip.style.top = `${Math.max(10, Math.min(y + 13, window.innerHeight - tooltip.offsetHeight - 12))}px`;
  };
  chart.addEventListener("pointermove", (event) => showTooltip(event.target.closest("[data-tip]"), event.clientX, event.clientY));
  chart.addEventListener("pointerleave", () => { $("#chart-tooltip").hidden = true; });
  chart.addEventListener("focusin", (event) => {
    const target = event.target.closest("[data-tip]");
    if (target) { const rect = target.getBoundingClientRect(); showTooltip(target, rect.left + rect.width / 2, rect.top); }
  });
  chart.addEventListener("focusout", () => { $("#chart-tooltip").hidden = true; });
}

$("#clear-drilldown").addEventListener("click", () => { state.drill = null; state.page = 1; void loadTasks(); });
$("#task-status").addEventListener("change", () => { state.page = 1; void loadTasks(); });
$("#page-size").addEventListener("change", (event) => {
  state.pageSize = Number(event.target.value); state.page = 1;
  savePreference("team-activity-page-size-v1", state.pageSize); void loadTasks();
});
$("#page-prev").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; void loadTasks(); } });
$("#page-next").addEventListener("click", () => { if (state.tasks?.has_more) { state.page += 1; void loadTasks(); } });
$("#registry-table").addEventListener("click", (event) => { const button = event.target.closest("[data-task-id]"); if (button) void openTask(button.dataset.taskId); });
$("#export-tasks").addEventListener("click", () => void exportTasks());
$("#detail-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => gates.detail.abort());
$("#registry-tab").addEventListener("click", () => setActiveTab("registry"));
$("#proposals-tab").addEventListener("click", () => { setActiveTab("proposals"); void loadProposals(); });
$("#open-proposals").addEventListener("click", () => {
  setActiveTab("proposals"); void loadProposals(); $("#registry-section").scrollIntoView({ block: "start" });
});
$(".registry-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tab = event.key === "Home" ? "registry" : event.key === "End" ? "proposals" : state.activeTab === "registry" ? "proposals" : "registry";
  setActiveTab(tab); $(`#${tab}-tab`).focus(); if (tab === "proposals") void loadProposals();
});
$("#proposals-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-proposal-index]");
  if (button) openProposal(Number(button.dataset.proposalIndex));
});

await initialize();
