(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const state = {
    csrf: sessionStorage.getItem("miniapp_csrf") || "",
    me: null,
    scope: "mine",
    status: "",
    currentTicket: null,
    page: "tickets",
  };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  document.addEventListener("DOMContentLoaded", boot);

  async function boot() {
    tg?.ready();
    tg?.expand();
    tg?.setHeaderColor?.("#0d0e10");
    tg?.setBackgroundColor?.("#0d0e10");
    bindEvents();
    try {
      await authenticate();
      state.me = await api("/api/me");
      state.scope = state.me.capabilities.team_queue ? "team" : "mine";
      renderIdentity();
      showWorkspace();
      await loadTickets();
    } catch (error) {
      showFatal(error);
    }
  }

  async function authenticate() {
    try {
      await api("/api/me");
      return;
    } catch (error) {
      if (error.status !== 401 && error.status !== 403) throw error;
    }
    const initData = tg?.initData;
    if (!initData) {
      throw friendlyError(401, "Откройте Mini App из меню Telegram-бота.");
    }
    const session = await api("/api/session", {
      method: "POST",
      body: { init_data: initData },
      skipCsrf: true,
    });
    state.csrf = session.csrf_token;
    sessionStorage.setItem("miniapp_csrf", state.csrf);
  }

  function bindEvents() {
    $("#retry-button").addEventListener("click", () => location.reload());
    $("#back-button").addEventListener("click", goBack);
    tg?.BackButton?.onClick(goBack);

    $$("#bottom-nav button").forEach((button) => button.addEventListener("click", () => {
      navigate(button.dataset.page);
    }));
    $$("#scope-switch button").forEach((button) => button.addEventListener("click", async () => {
      state.scope = button.dataset.scope;
      $$("#scope-switch button").forEach((item) => item.classList.toggle("active", item === button));
      await loadTickets();
    }));
    $$("#status-filters button").forEach((button) => button.addEventListener("click", async () => {
      state.status = button.dataset.status;
      $$("#status-filters button").forEach((item) => item.classList.toggle("active", item === button));
      await loadTickets();
    }));
    $$("input[name='ticket-type']").forEach((input) => input.addEventListener("change", toggleCreateType));
    $("#advertiser-select").addEventListener("change", () => {
      $("#new-advertiser").classList.toggle("hidden", $("#advertiser-select").value !== "__new__");
    });
    $("#create-form").addEventListener("submit", createTicket);
  }

  function renderIdentity() {
    $("#profile-name").textContent = state.me.profile.name;
    $("#role-badge").textContent = roleLabel(state.me.role);
    $("#scope-switch").classList.toggle("hidden", !state.me.capabilities.team_queue);
    $$("#scope-switch button").forEach((button) => button.classList.toggle("active", button.dataset.scope === state.scope));
  }

  function showWorkspace() {
    $("#loading-view").classList.add("hidden");
    $("#error-view").classList.add("hidden");
    $("#workspace-view").classList.remove("hidden");
    $("#bottom-nav").classList.remove("hidden");
  }

  function showFatal(error) {
    $("#loading-view").classList.add("hidden");
    $("#workspace-view").classList.add("hidden");
    $("#bottom-nav").classList.add("hidden");
    $("#error-message").textContent = error.message || "Повторите позже.";
    $("#error-view").classList.remove("hidden");
  }

  function navigate(page) {
    state.page = page;
    state.currentTicket = null;
    ["tickets", "create", "tools", "detail"].forEach((name) => {
      $(`#${name}-page`).classList.toggle("hidden", name !== page);
    });
    $$("#bottom-nav button").forEach((button) => button.classList.toggle("active", button.dataset.page === page));
    $("#back-button").classList.add("hidden");
    tg?.BackButton?.hide();
    const copy = {
      tickets: ["Все задачи", "Тикеты, переписка и история работы"],
      create: ["Новый тикет", "Обычная задача или Destination"],
      tools: ["Инструменты", "Шаблоны и данны команды"],
    };
    if (copy[page]) {
      $("#page-title").innerHTML = `${escapeHtml(copy[page][0])}<span>.</span>`;
      $("#page-subtitle").textContent = copy[page][1];
    }
    if (page === "tickets") loadTickets();
    if (page === "create") loadAdvertisers();
    if (page === "tools") loadPostbacks();
  }

  function goBack() {
    if (state.page === "detail") navigate("tickets");
    else navigate("tickets");
  }

  async function loadTickets() {
    const list = $("#ticket-list");
    list.innerHTML = '<div class="empty">Загрузка…</div>';
    const params = new URLSearchParams({ scope: state.scope, page_size: "50" });
    if (state.status) params.set("status", state.status);
    try {
      const data = await api(`/api/tickets?${params}`);
      if (!data.items.length) {
        list.innerHTML = '<div class="empty">Здесь пока нет тикетов.</div>';
        return;
      }
      list.innerHTML = data.items.map(ticketCard).join("");
      $$(".ticket-card").forEach((card) => card.addEventListener("click", () => openTicket(Number(card.dataset.id))));
    } catch (error) {
      list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
  }

  function ticketCard(ticket) {
    return `<button class="ticket-card" data-id="${ticket.ticket_id}">
      <div class="ticket-meta"><span>#${ticket.ticket_id} · ${escapeHtml(typeLabel(ticket.ticket_type))}</span>${statusBadge(ticket.status)}</div>
      <h2>${escapeHtml(ticket.title)}</h2>
      <p>${escapeHtml(ticket.description_preview)}</p>
      <div class="ticket-footer"><span>${formatDate(ticket.updated_at)}</span><span class="chevron">›</span></div>
    </button>`;
  }

  async function openTicket(ticketId) {
    try {
      const ticket = await api(`/api/tickets/${ticketId}`);
      state.currentTicket = ticket;
      state.page = "detail";
      ["tickets", "create", "tools"].forEach((name) => $(`#${name}-page`).classList.add("hidden"));
      $("#detail-page").classList.remove("hidden");
      $("#bottom-nav").classList.add("hidden");
      $("#back-button").classList.remove("hidden");
      tg?.BackButton?.show();
      $("#page-title").innerHTML = `Тикет #${ticket.ticket_id}<span>.</span>`;
      $("#page-subtitle").textContent = typeLabel(ticket.ticket_type);
      renderTicketDetail(ticket);
    } catch (error) { toast(error.message); }
  }

  function renderTicketDetail(ticket) {
    const publicMessages = ticket.conversation?.map((item) => messageHtml(item, false)).join("") || '<div class="empty">Переписки пока нет.</div>';
    const internal = ticket.internal_history?.length ? `<section class="internal-box"><h3>Внутренняя история</h3>${ticket.internal_history.map((item) => messageHtml(item, true)).join("")}</section>` : "";
    $("#ticket-detail").innerHTML = `
      <header class="detail-head">
        <div class="ticket-meta"><span>#${ticket.ticket_id}</span>${statusBadge(ticket.status)}</div>
        <h2>${escapeHtml(ticket.title)}</h2>
        <p class="detail-description">${escapeHtml(ticket.description)}</p>
      </header>
      <section class="timeline"><h3>Переписка</h3>${publicMessages}</section>
      ${internal}
      <form class="composer" id="message-form">
        <textarea id="message-body" rows="2" maxlength="16384" placeholder="Написать сообщение…" required></textarea>
        <div class="composer-row">
          <button class="primary-button" type="submit">Отправить</button>
          ${state.me.capabilities.internal_history ? '<button class="secondary-button" type="button" id="internal-message">Внутренняя запись</button>' : ""}
        </div>
      </form>
      <div class="action-row">${actionButtons(ticket)}</div>`;
    $("#message-form").addEventListener("submit", (event) => sendMessage(event, "public"));
    $("#internal-message")?.addEventListener("click", (event) => sendMessage(event, "internal"));
    $$("[data-action]").forEach((button) => button.addEventListener("click", () => runAction(button.dataset.action)));
  }

  function actionButtons(ticket) {
    const staff = state.me.capabilities.team_queue;
    if (ticket.status === "open" && staff && ticket.ticket_type === "general") return '<button class="primary-button" data-action="submit-review">Отправить на проверку</button>';
    if (ticket.status === "open" && staff && ticket.ticket_type === "destination") return '<button class="primary-button" data-action="complete">Завершить Destination</button>';
    if (ticket.status === "review" && !staff) return '<button class="primary-button" data-action="accept">Принять работу</button><button class="secondary-button" data-action="return">Вернуть на доработку</button>';
    if (ticket.status === "completed" && !staff) return '<button class="secondary-button" data-action="reopen">Возобновить задачу</button>';
    return "";
  }

  async function sendMessage(event, visibility) {
    event.preventDefault();
    const body = $("#message-body").value.trim();
    if (!body) return;
    await mutate(`/api/tickets/${state.currentTicket.ticket_id}/messages`, { visibility, body }, "Сообщение добавлено");
  }

  async function runAction(action) {
    let body = {};
    if (action === "return" || action === "reopen") {
      const reason = await askReason(action === "return" ? "Почему нужна доработка?" : "Почему задачу нужно возобновить?");
      if (!reason) return;
      body = { reason };
    }
    await mutate(`/api/tickets/${state.currentTicket.ticket_id}/${action}`, body, "Статус обновлён");
  }

  async function mutate(path, body, success) {
    try {
      await api(path, { method: "POST", body });
      toast(success);
      await openTicket(state.currentTicket.ticket_id);
    } catch (error) { toast(error.message); }
  }

  async function createTicket(event) {
    event.preventDefault();
    const type = $("input[name='ticket-type']:checked").value;
    const body = $("#ticket-body").value.trim();
    let payload = { type, body };
    if (type === "destination") {
      const advertiser = $("#advertiser-select").value;
      const cap = Number($("#cap-input").value);
      if (!advertiser || !cap) return toast("Выберите рекламодателя и укажите cap");
      payload.advertiser = advertiser === "__new__" ? { kind: "new", name: $("#new-advertiser").value.trim() } : { kind: "catalog", key: advertiser };
      payload.cap = cap;
      if (!payload.advertiser.name && advertiser === "__new__") return toast("Укажите нового рекламодателя");
    }
    const button = event.submitter;
    button.disabled = true;
    try {
      const result = await api("/api/tickets", { method: "POST", body: payload });
      event.target.reset();
      toggleCreateType();
      toast(`Тикет #${result.ticket_id} создан`);
      await openTicket(result.ticket_id);
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }

  async function loadAdvertisers() {
    const select = $("#advertiser-select");
    try {
      const data = await api("/api/advertisers?mode=popular&page_size=100");
      select.innerHTML = '<option value="">Выбрать…</option>' + data.items.map((item) => `<option value="${escapeAttr(item.key)}">${escapeHtml(item.name)}</option>`).join("") + '<option value="__new__">+ Новый рекламодатель</option>';
    } catch (error) { select.innerHTML = '<option value="__new__">Новый рекламодатель</option>'; }
  }

  async function loadPostbacks() {
    const list = $("#postback-list");
    list.innerHTML = '<div class="empty">Загрузка…</div>';
    try {
      const items = await api("/api/postbacks");
      list.innerHTML = items.length ? items.map((item) => `<div class="tool-item"><button data-postback="${escapeAttr(item.key)}">${escapeHtml(item.name)} <span class="chevron">›</span></button><pre class="template hidden"></pre></div>`).join("") : '<div class="empty">Шаблонов пока нет.</div>';
      $$('[data-postback]').forEach((button) => button.addEventListener("click", () => showPostback(button)));
    } catch (error) { list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
  }

  async function showPostback(button) {
    const target = button.parentElement.querySelector(".template");
    if (target.textContent) return target.classList.toggle("hidden");
    try {
      const item = await api(`/api/postbacks/${encodeURIComponent(button.dataset.postback)}`);
      target.textContent = item.template;
      target.classList.remove("hidden");
    } catch (error) { toast(error.message); }
  }

  function toggleCreateType() {
    const destination = $("input[name='ticket-type']:checked").value === "destination";
    $("#destination-fields").classList.toggle("hidden", !destination);
    if (destination) loadAdvertisers();
  }

  function askReason(title) {
    const dialog = $("#reason-dialog");
    $("#reason-title").textContent = title;
    $("#reason-body").value = "";
    dialog.showModal();
    return new Promise((resolve) => dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "confirm" ? $("#reason-body").value.trim() : "");
    }, { once: true }));
  }

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (!options.skipCsrf && options.method && options.method !== "GET") {
      headers["X-CSRF-Token"] = state.csrf;
      headers["Idempotency-Key"] = crypto.randomUUID();
    }
    const response = await fetch(path, {
      method: options.method || "GET",
      credentials: "include",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (!response.ok) {
      let detail = "Ошибка запроса";
      try { detail = humanError((await response.json()).detail); } catch (_) { /* empty */ }
      throw friendlyError(response.status, detail);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function friendlyError(status, message) { const error = new Error(message); error.status = status; return error; }
  function humanError(detail) {
    const map = {
      "authentication required": "Нужно заново открыть Mini App из Telegram.",
      "session expired": "Сессия истекла. Откройте Mini App заново.",
      "operation forbidden": "Это действие недоступно для вашей роли.",
      "state or idempotency conflict": "Тикет уже изменился. Обновите карточку.",
      "public reason is required": "Нужно указать причину.",
      "resource not found": "Тикет не найден или недоступен.",
    };
    return map[detail] || detail;
  }

  function messageHtml(item, internal) {
    return `<div class="message${internal ? " internal" : ""}"><small>${escapeHtml(item.actor.display_name)} · ${formatDate(item.sent_at)}</small><p>${escapeHtml(item.body)}</p></div>`;
  }
  function statusBadge(status) { return `<span class="status status-${status}">${statusLabel(status)}</span>`; }
  function statusLabel(status) { return ({ open: "В работе", review: "На проверке", completed: "Завершена" })[status] || status; }
  function typeLabel(type) { return type === "destination" ? "Destination" : "Обычная задача"; }
  function roleLabel(role) { return ({ client: "Клиент", technician: "Технарь", admin: "Техлид" })[role] || role; }
  function formatDate(value) { return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
  function escapeHtml(value = "") { const div = document.createElement("div"); div.textContent = String(value); return div.innerHTML; }
  function escapeAttr(value = "") { return escapeHtml(value).replaceAll('"', "&quot;"); }
  function toast(message) { const node = $("#toast"); node.textContent = message; node.classList.remove("hidden"); clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.add("hidden"), 3200); }
})();
