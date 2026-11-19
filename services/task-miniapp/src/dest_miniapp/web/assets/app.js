(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const state = {
    csrf: sessionStorage.getItem("miniapp_csrf") || "",
    me: null,
    scope: "mine",
    status: "",
    issuedToBuying: false,
    ticketPage: 1,
    ticketSearch: "",
    ticketRequest: 0,
    currentTicket: null,
    page: "tickets",
    advertisers: [],
    advertiserLoading: false,
    advertiserRefreshRunning: false,
    currentDraft: null,
    draftAdvertisers: [],
    draftAdvertiserLoading: false,
    importantComments: [],
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
      if (!isKalisto()) await loadImportantComments();
      showWorkspace();
      await loadTickets();
    } catch (error) {
      showFatal(error);
    }
  }

  async function authenticate() {
    if (state.csrf) {
      try {
        await api("/api/me");
        return;
      } catch (error) {
        if (error.status !== 401 && error.status !== 403) throw error;
      }
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

    $$("#bottom-nav button[data-page]").forEach((button) => button.addEventListener("click", () => {
      navigate(button.dataset.page);
    }));
    $("#analytics-button").addEventListener("click", openAnalytics);
    $$("#scope-switch button").forEach((button) => button.addEventListener("click", async () => {
      state.scope = button.dataset.scope;
      state.ticketPage = 1;
      $$("#scope-switch button").forEach((item) => item.classList.toggle("active", item === button));
      await loadTickets();
    }));
    $$("#status-filters button").forEach((button) => button.addEventListener("click", async () => {
      state.issuedToBuying = button.dataset.buying === "true";
      state.ticketPage = 1;
      state.status = state.issuedToBuying ? "" : button.dataset.status;
      $$("#status-filters button").forEach((item) => item.classList.toggle("active", item === button));
      await loadTickets();
    }));
    $("#ticket-search").addEventListener("input", () => {
      clearTimeout(state.ticketSearchTimer);
      state.ticketRequest += 1;
      state.ticketSearch = $("#ticket-search").value.trim();
      state.ticketPage = 1;
      $("#tickets-prev").disabled = true;
      $("#tickets-next").disabled = true;
      state.ticketSearchTimer = setTimeout(loadTickets, 300);
    });
    $("#tickets-prev").addEventListener("click", () => {
      if (state.ticketPage > 1) { state.ticketPage -= 1; loadTickets(); }
    });
    $("#tickets-next").addEventListener("click", () => {
      state.ticketPage += 1;
      loadTickets();
    });
    $$("input[name='ticket-type']").forEach((input) => input.addEventListener("change", toggleCreateType));
    $$("input[name='draft-ticket-type']").forEach((input) => input.addEventListener("change", toggleDraftType));
    $("#advertiser-select").addEventListener("change", () => {
      $("#new-advertiser").classList.toggle("hidden", $("#advertiser-select").value !== "__new__");
    });
    $("#advertiser-search").addEventListener("input", () => {
      clearTimeout(state.advertiserSearchTimer);
      state.advertiserSearchTimer = setTimeout(() => loadAdvertisers(true), 300);
    });
    $("#advertiser-refresh").addEventListener("click", () => refreshAdvertisers("create"));
    $$("input[name='cap-mode']").forEach((input) => input.addEventListener("change", () => syncCapInput("#cap-input", "cap-mode")));
    $("#create-form").addEventListener("submit", createTicket);
    $("#create-form").addEventListener("input", clearFieldError);
    $("#create-form").addEventListener("change", clearFieldError);
    $("#draft-advertiser-select").addEventListener("change", () => {
      $("#draft-new-advertiser").classList.toggle("hidden", $("#draft-advertiser-select").value !== "__new__");
    });
    $("#draft-advertiser-search").addEventListener("input", () => {
      clearTimeout(state.draftAdvertiserSearchTimer);
      state.draftAdvertiserSearchTimer = setTimeout(() => loadDraftAdvertisers(true), 300);
    });
    $("#draft-advertiser-refresh").addEventListener("click", () => refreshAdvertisers("draft"));
    $$("input[name='draft-cap-mode']").forEach((input) => input.addEventListener("change", () => syncCapInput("#draft-cap", "draft-cap-mode")));
    $("#draft-save").addEventListener("click", saveCurrentDraft);
    $("#draft-submit").addEventListener("click", submitCurrentDraft);
    $("#draft-delete").addEventListener("click", deleteCurrentDraft);
    $("#draft-close").addEventListener("click", closeDraft);
    $("#important-comment-history").addEventListener("change", (event) => useImportantComment(event, "#important-comment"));
    $("#draft-important-comment-history").addEventListener("change", (event) => useImportantComment(event, "#draft-important-comment"));
    $("#draft-form").addEventListener("input", clearFieldError);
    $("#draft-form").addEventListener("change", clearFieldError);
  }

  function renderIdentity() {
    $("#profile-name").textContent = state.me.profile.name;
    const analyticsUrl = state.me.links?.analytics || "";
    $("#analytics-button").classList.toggle("hidden", !analyticsUrl);
    $("#bottom-nav").classList.toggle("analytics-enabled", Boolean(analyticsUrl));
    $("#scope-switch").classList.toggle("hidden", !state.me.capabilities.team_queue);
    $("#buying-filter").classList.toggle("hidden", !state.me.capabilities.buying_flag);
    $$("#scope-switch button").forEach((button) => button.classList.toggle("active", button.dataset.scope === state.scope));
  }

  async function openAnalytics() {
    if (!state.me?.links?.analytics) return;
    const button = $("#analytics-button");
    button.disabled = true;
    try {
      const handoff = await api("/api/analytics/handoff", {
        method: "POST",
        body: {},
      });
      const url = handoff.url;
      if (tg?.openLink) {
        tg.openLink(url);
        return;
      }
      window.location.assign(url);
    } catch (error) {
      toast(error.message || "Не удалось открыть аналитику.");
    } finally {
      button.disabled = false;
    }
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
    $("#bottom-nav").classList.remove("hidden");
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
    if (page === "create") {
      loadAdvertisers(true);
      loadDrafts();
    }
    if (page === "tools") {
      loadHelp();
      loadPostbacks();
    }
  }

  function goBack() {
    if (state.page === "detail") navigate("tickets");
    else navigate("tickets");
  }

  async function loadTickets() {
    clearTimeout(state.ticketSearchTimer);
    const request = ++state.ticketRequest;
    const list = $("#ticket-list");
    list.innerHTML = '<div class="empty">Загрузка…</div>';
    $("#tickets-prev").disabled = true;
    $("#tickets-next").disabled = true;
    const params = new URLSearchParams({ scope: state.scope, page_size: "50", page: String(state.ticketPage) });
    if (state.ticketSearch) params.set("search", state.ticketSearch);
    if (state.status) params.set("status", state.status);
    if (state.issuedToBuying) params.set("issued_to_buying", "true");
    try {
      const data = await api(`/api/tickets?${params}`);
      if (request !== state.ticketRequest) return;
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      if (state.ticketPage > pages) {
        state.ticketPage = pages;
        return loadTickets();
      }
      $("#ticket-pagination").classList.toggle("hidden", !data.total);
      $("#tickets-page-info").textContent = `${data.page} / ${pages} · Всего: ${data.total}`;
      $("#tickets-prev").disabled = data.page <= 1;
      $("#tickets-next").disabled = !data.has_next;
      if (!data.items.length) {
        list.innerHTML = `<div class="empty">${state.ticketSearch ? "По этому названию тикетов не найдено." : "Здесь пока нет тикетов."}</div>`;
        return;
      }
      list.innerHTML = data.items.map(ticketCard).join("");
      $$(".ticket-card").forEach((card) => card.addEventListener("click", () => openTicket(Number(card.dataset.id))));
    } catch (error) {
      if (request !== state.ticketRequest) return;
      $("#ticket-pagination").classList.add("hidden");
      list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
  }

  function publicTicketNumber(ticket) {
    return ticket.ticket_number || ticket.ticket_id;
  }

  function ticketCard(ticket) {
    const issued = state.me.capabilities.buying_flag && ticket.issued_to_buying;
    return `<button class="ticket-card${issued ? " issued-to-buying" : ""}" data-id="${ticket.ticket_id}">
      <span class="ticket-id">#${publicTicketNumber(ticket)}</span>
      <span class="ticket-type">${escapeHtml(typeLabel(ticket.ticket_type))}</span>
      <span class="ticket-title">${escapeHtml(ticket.title)}</span>
      <span class="ticket-side">${issued ? '<span class="buying-badge">✓ Выдано</span>' : ""}${statusBadge(ticket.status)}<time>${formatDate(ticket.updated_at)}</time></span>
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
      $("#page-title").innerHTML = `Тикет #${publicTicketNumber(ticket)}<span>.</span>`;
      $("#page-subtitle").textContent = typeLabel(ticket.ticket_type);
      renderTicketDetail(ticket);
    } catch (error) { toast(error.message); }
  }

  function renderTicketDetail(ticket) {
    const publicMessages = ticket.conversation?.map((item) => messageHtml(item, false)).join("") || '<div class="empty">Переписки пока нет.</div>';
    const internal = ticket.internal_history?.length ? `<section class="internal-box"><h3>Внутренняя история</h3>${ticket.internal_history.map((item) => messageHtml(item, true)).join("")}</section>` : "";
    const destination = ticket.ticket_type === "destination" && ticket.destination_results?.length
      ? `<div class="destination-results">${ticket.destination_results.map((item, index) => `<section class="destination-result"><h2>${escapeHtml(item.naming)}</h2><h3>Destination:</h3><p class="destination-url">${escapeHtml(item.url)}</p>${index === 0 ? importantCommentMarkup(ticket.important_comment) : ""}</section>`).join("")}</div>`
      : `<p class="detail-description">${escapeHtml(ticket.description)}</p>`;
    $("#ticket-detail").innerHTML = `
      <header class="detail-head">
        <div class="ticket-meta"><span>#${publicTicketNumber(ticket)}</span>${statusBadge(ticket.status)}</div>
        <div class="action-row detail-action-row">${buyingFlagButton(ticket)}${actionButtons(ticket)}</div>
        ${destination}
      </header>
      <section class="timeline"><h3>Переписка</h3>${publicMessages}</section>
      ${internal}
      <form class="composer" id="message-form">
        <textarea id="message-body" rows="2" maxlength="16384" placeholder="Написать сообщение…" required></textarea>
        <div class="composer-row">
          <button class="primary-button" type="submit">Отправить</button>
          ${state.me.capabilities.internal_history ? '<button class="secondary-button" type="button" id="internal-message">Внутренняя запись</button>' : ""}
        </div>
      </form>`;
    $("#message-form").addEventListener("submit", (event) => sendMessage(event, "public"));
    $("#internal-message")?.addEventListener("click", (event) => sendMessage(event, "internal"));
    $$("[data-action]").forEach((button) => button.addEventListener("click", () => runAction(button.dataset.action)));
    $$("[data-buying-flag]").forEach((button) => button.addEventListener("click", () => setBuyingFlag(button.dataset.buyingFlag === "true")));
  }

  function importantCommentMarkup(comment) {
    if (!comment) return "";
    return `<div class="important-comment"><h3>Важно:</h3><p>${escapeHtml(comment)}</p></div>`;
  }

  function buyingFlagButton(ticket) {
    if (!state.me.capabilities.buying_flag || ticket.ticket_type !== "destination") return "";
    if (ticket.issued_to_buying) {
      return '<button class="secondary-button buying-toggle" data-buying-flag="false">✓ Выдано баингу — снять</button>';
    }
    if (ticket.status === "completed") {
      return '<button class="primary-button buying-toggle" data-buying-flag="true">Выдать баингу</button>';
    }
    return "";
  }

  function actionButtons(ticket) {
    const staff = state.me.capabilities.technical_actions;
    if (ticket.status === "open" && staff && ticket.ticket_type === "general") return '<button class="primary-button" data-action="submit-review">Отправить на проверку</button>';
    if (ticket.status === "open" && staff && ticket.ticket_type === "destination") return '<button class="primary-button" data-action="complete">Завершить Destination</button>';
    if (ticket.status === "review" && ticket.is_author) return '<button class="primary-button" data-action="accept">Принять работу</button><button class="secondary-button" data-action="return">Вернуть на доработку</button>';
    if (ticket.status === "completed" && ticket.is_author) return '<button class="secondary-button" data-action="reopen">Возобновить задачу</button>';
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

  async function setBuyingFlag(issued) {
    try {
      await api(`/api/tickets/${state.currentTicket.ticket_id}/buying-flag`, {
        method: "POST",
        body: { issued },
      });
      toast(issued ? "Отмечено: выдано баингу" : "Отметка снята");
      await openTicket(state.currentTicket.ticket_id);
    } catch (error) { toast(error.message); }
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
    clearFormErrors($("#create-form"));
    const type = $("input[name='ticket-type']:checked").value;
    const body = $("#ticket-body").value.trim();
    let payload = { type, body };
    if (type === "destination") {
      const advertiser = $("#advertiser-select").value;
      const capSelection = readCap("#cap-input", "cap-mode");
      const missing = [];
      if (!advertiser) missing.push(requiredField($("#advertiser-select"), "рекламодатель"));
      if (!capSelection.cap_mode) {
        missing.push(requiredField($(".cap-mode-fieldset"), "категория CAP"));
      } else if (!capSelection.complete) {
        missing.push(requiredField($("#cap-input"), "CAP"));
      }
      payload.advertiser = advertiser === "__new__" ? { kind: "new", name: $("#new-advertiser").value.trim() } : { kind: "catalog", key: advertiser };
      payload.cap = capSelection.cap;
      payload.cap_mode = capSelection.cap_mode;
      if (!payload.advertiser.name && advertiser === "__new__") {
        missing.push(requiredField($("#new-advertiser"), "название рекламодателя"));
      }
      if (isKalisto()) {
        payload.body = "";
        payload.details = kalistoDetails("destination-");
        missing.push(...missingKalistoFields("destination-", payload.details));
      } else {
        payload.important_comment = $("#important-comment").value.trim();
      }
      if (missing.length) return showRequiredFields(missing);
    }
    const button = event.submitter;
    button.disabled = true;
    try {
      const result = await api("/api/tickets", { method: "POST", body: payload });
      event.target.reset();
      syncCapInput("#cap-input", "cap-mode");
      toggleCreateType();
      toast(`Тикет #${publicTicketNumber(result)} создан`);
      await openTicket(result.ticket_id);
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }

  function isKalisto() {
    return state.me?.profile?.key === "kalisto";
  }

  function kalistoDetails(prefix) {
    const value = (name) => $(`#${prefix}${name}`).value.trim();
    const details = {
      naming: value("naming"),
      geo: value("geo"),
      rate: Number(value("rate")),
      currency: value("currency"),
      landing_url: value("landing"),
      clickid_subid: value("clickid"),
      additional: value("additional"),
    };
    return details;
  }

  function missingKalistoFields(prefix, details) {
    return [
      [requiredField($(`#${prefix}naming`), "нейминг"), !details.naming],
      [requiredField($(`#${prefix}geo`), "GEO"), !details.geo],
      [requiredField($(`#${prefix}rate`), "ставка"), !Number.isFinite(details.rate) || details.rate <= 0],
      [requiredField($(`#${prefix}currency`), "валюта"), !details.currency],
      [requiredField($(`#${prefix}landing`), "основной ленд"), !details.landing_url],
    ].filter(([, missing]) => missing).map(([field]) => field);
  }

  async function loadDrafts() {
    const panel = $("#drafts-panel");
    const list = $("#draft-list");
    try {
      const drafts = await api("/api/drafts");
      panel.classList.toggle("hidden", !drafts.length);
      $("#draft-count").textContent = drafts.length ? String(drafts.length) : "";
      list.innerHTML = drafts.map((draft) => `<button class="draft-card" data-draft-id="${draft.draft_id}"><strong>${escapeHtml(draft.source_preview)}</strong><span>${escapeHtml(draft.client_comment || "Без комментария")} · ${formatDate(draft.updated_at)}</span></button>`).join("");
      $$("[data-draft-id]").forEach((button) => button.addEventListener("click", () => openDraft(Number(button.dataset.draftId))));
    } catch (error) {
      panel.classList.remove("hidden");
      list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
  }

  async function openDraft(draftId) {
    try {
      const draft = await api(`/api/drafts/${draftId}`);
      state.currentDraft = draft;
      $("#draft-title").textContent = `Черновик #${draft.draft_id}`;
      $("#draft-comment").value = draft.client_comment || "";
      $("#draft-important-comment").value = draft.important_comment || "";
      $("#draft-body").value = draft.edited_body;
      $$("input[name='draft-ticket-type']").forEach((input) => {
        input.checked = input.value === draft.ticket_type;
      });
      setCapMode("draft-cap-mode", draft.cap_mode || (draft.cap === "uncap" ? "unlim" : null));
      $("#draft-cap").value = typeof draft.cap === "number" ? String(draft.cap) : "";
      syncCapInput("#draft-cap", "draft-cap-mode");
      $("#draft-advertiser-search").value = "";
      await loadDraftAdvertisers(true);
      if (draft.advertiser_kind === "catalog" && draft.advertiser_key) {
        const select = $("#draft-advertiser-select");
        if (![...select.options].some((option) => option.value === draft.advertiser_key)) {
          select.add(new Option(draft.advertiser_key, draft.advertiser_key), 1);
        }
        select.value = draft.advertiser_key;
      } else if (draft.advertiser_kind === "new") {
        $("#draft-advertiser-select").value = "__new__";
        $("#draft-new-advertiser").value = draft.advertiser_name || "";
      }
      $("#draft-new-advertiser").classList.toggle("hidden", $("#draft-advertiser-select").value !== "__new__");
      const details = draft.details || {};
      $("#draft-naming").value = details.naming || "";
      $("#draft-geo").value = details.geo || "";
      $("#draft-rate").value = details.rate || "";
      $("#draft-currency").value = details.currency || "";
      $("#draft-landing").value = details.landing_url || "";
      $("#draft-clickid").value = details.clickid_subid || "";
      $("#draft-additional").value = details.additional || "";
      toggleDraftType();
      $("#draft-dialog").showModal();
    } catch (error) { toast(error.message); }
  }

  function closeDraft() {
    $("#draft-dialog").close();
    state.currentDraft = null;
  }

  function missingDraftSubmitFields() {
    const missing = [];
    const type = $("input[name='draft-ticket-type']:checked")?.value || null;
    if (!$("#draft-body").value.trim()) missing.push(requiredField($("#draft-body"), "текст заявки"));
    if (!type) missing.push(requiredField($(".draft-type-switch"), "тип задачи"));
    if (type !== "destination") return missing;
    const advertiser = $("#draft-advertiser-select").value;
    if (!advertiser) missing.push(requiredField($("#draft-advertiser-select"), "рекламодатель"));
    if (advertiser === "__new__" && !$("#draft-new-advertiser").value.trim()) {
      missing.push(requiredField($("#draft-new-advertiser"), "название рекламодателя"));
    }
    const capSelection = readCap("#draft-cap", "draft-cap-mode");
    if (!capSelection.cap_mode) {
      missing.push(requiredField($("#draft-cap-fields .cap-mode-fieldset"), "категория CAP"));
    } else if (!capSelection.complete) {
      missing.push(requiredField($("#draft-cap"), "CAP"));
    }
    if (isKalisto()) {
      missing.push(...missingKalistoFields("draft-", kalistoDetails("draft-")));
    }
    return missing;
  }

  function draftPayload(requireComplete) {
    const type = $("input[name='draft-ticket-type']:checked")?.value || null;
    const body = $("#draft-body").value.trim();
    const clientComment = $("#draft-comment").value.trim();
    if (!body) throw friendlyError(422, "Укажите текст заявки");
    if (requireComplete && !type) throw friendlyError(422, "Выберите тип задачи");
    if (type !== "destination") {
      return { type, body, client_comment: clientComment };
    }
    const selected = $("#draft-advertiser-select").value;
    const capSelection = readCap("#draft-cap", "draft-cap-mode");
    let advertiser = null;
    if (selected === "__new__") {
      const name = $("#draft-new-advertiser").value.trim();
      if (name) advertiser = { kind: "new", name };
    } else if (selected) {
      advertiser = { kind: "catalog", key: selected };
    }
    const payload = {
      type,
      advertiser,
      cap: capSelection.cap,
      cap_mode: capSelection.cap_mode,
      body,
      client_comment: clientComment,
      important_comment: $("#draft-important-comment").value.trim(),
    };
    if (requireComplete && !payload.advertiser) throw friendlyError(422, "Выберите рекламодателя");
    if (requireComplete && !capSelection.complete) throw friendlyError(422, "Укажите CAP и его категорию");
    if (isKalisto()) {
      if (payload.advertiser && payload.advertiser.kind !== "catalog") {
        throw friendlyError(422, "Выберите рекламодателя из списка");
      }
      const text = (id) => $(id).value.trim();
      const rate = Number(text("#draft-rate"));
      payload.details = {
        naming: text("#draft-naming") || null,
        geo: text("#draft-geo") || null,
        rate: rate || null,
        currency: text("#draft-currency") || null,
        landing_url: text("#draft-landing") || null,
        clickid_subid: text("#draft-clickid"),
        additional: text("#draft-additional"),
      };
      if (requireComplete && (!payload.details.naming || !payload.details.geo || !payload.details.rate || !payload.details.currency || !payload.details.landing_url)) {
        throw friendlyError(422, "Заполните обязательные поля");
      }
    }
    return payload;
  }

  async function loadImportantComments() {
    if (isKalisto()) return;
    try {
      state.importantComments = await api("/api/destination-comments");
    } catch (_) {
      state.importantComments = [];
    }
    renderImportantCommentHistory("#important-comment-history");
    renderImportantCommentHistory("#draft-important-comment-history");
  }

  function renderImportantCommentHistory(selector) {
    const select = $(selector);
    select.classList.toggle("hidden", !state.importantComments.length);
    select.innerHTML = '<option value="">Использовать прежний комментарий…</option>'
      + state.importantComments.map((comment) => `<option value="${escapeAttr(comment)}">${escapeHtml(comment)}</option>`).join("");
  }

  function useImportantComment(event, targetSelector) {
    if (!event.target.value) return;
    $(targetSelector).value = event.target.value;
    event.target.value = "";
    $(targetSelector).focus();
  }

  async function saveCurrentDraft() {
    if (!state.currentDraft) return;
    try {
      const draft = await api(`/api/drafts/${state.currentDraft.draft_id}/save`, { method: "POST", body: draftPayload(false) });
      state.currentDraft = draft;
      toast("Черновик сохранён");
      await loadDrafts();
    } catch (error) { toast(error.message); }
  }

  async function submitCurrentDraft() {
    if (!state.currentDraft) return;
    clearFormErrors($("#draft-form"));
    const missing = missingDraftSubmitFields();
    if (missing.length) return showRequiredFields(missing);
    const button = $("#draft-submit");
    button.disabled = true;
    try {
      const result = await api(`/api/drafts/${state.currentDraft.draft_id}/submit`, { method: "POST", body: draftPayload(true) });
      closeDraft();
      toast(`Тикет #${publicTicketNumber(result)} создан`);
      await openTicket(result.ticket_id);
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  }

  async function deleteCurrentDraft() {
    if (!state.currentDraft || !window.confirm("Удалить этот черновик?")) return;
    try {
      await api(`/api/drafts/${state.currentDraft.draft_id}`, { method: "DELETE" });
      closeDraft();
      toast("Черновик удалён");
      await loadDrafts();
    } catch (error) { toast(error.message); }
  }

  async function loadDraftAdvertisers(reset = true) {
    if (state.draftAdvertiserLoading) return;
    state.draftAdvertiserLoading = true;
    const select = $("#draft-advertiser-select");
    const previous = select.value;
    const query = $("#draft-advertiser-search").value.trim();
    if (reset) {
      state.draftAdvertisers = [];
      select.innerHTML = '<option value="">Загрузка…</option>';
    }
    try {
      const params = new URLSearchParams(query ? {
        mode: "search", q: query, page_size: "100",
      } : {
        mode: "page", page: "1", page_size: "100",
      });
      const data = await api(`/api/advertisers?${params}`);
      const known = new Set(state.draftAdvertisers.map((item) => item.key));
      state.draftAdvertisers.push(...data.items.filter((item) => !known.has(item.key)));
      const newOption = isKalisto() ? "" : '<option value="__new__">+ Новый рекламодатель</option>';
      select.innerHTML = '<option value="">Выбрать…</option>' + state.draftAdvertisers.map((item) => `<option value="${escapeAttr(item.key)}">${escapeHtml(item.name)}</option>`).join("") + newOption;
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    } catch (error) {
      select.innerHTML = isKalisto() ? '<option value="">Недоступно</option>' : '<option value="__new__">Новый рекламодатель</option>';
    } finally {
      state.draftAdvertiserLoading = false;
    }
  }

  async function loadAdvertisers(reset = true) {
    if (state.advertiserLoading) return;
    state.advertiserLoading = true;
    const select = $("#advertiser-select");
    const previous = select.value;
    const query = $("#advertiser-search").value.trim();
    if (reset) {
      state.advertisers = [];
      select.innerHTML = '<option value="">Загрузка…</option>';
    }
    try {
      const params = new URLSearchParams(query ? {
        mode: "search", q: query, page_size: "100",
      } : {
        mode: "page", page: "1", page_size: "100",
      });
      const data = await api(`/api/advertisers?${params}`);
      const known = new Set(state.advertisers.map((item) => item.key));
      state.advertisers.push(...data.items.filter((item) => !known.has(item.key)));
      const newOption = isKalisto() ? "" : '<option value="__new__">+ Новый рекламодатель</option>';
      select.innerHTML = '<option value="">Выбрать…</option>' + state.advertisers.map((item) => `<option value="${escapeAttr(item.key)}">${escapeHtml(item.name)}</option>`).join("") + newOption;
      if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    } catch (error) {
      if (!state.advertisers.length) select.innerHTML = isKalisto() ? '<option value="">Недоступно</option>' : '<option value="__new__">Новый рекламодатель</option>';
    } finally {
      state.advertiserLoading = false;
    }
  }

  async function refreshAdvertisers(target) {
    if (state.advertiserRefreshRunning) return;
    state.advertiserRefreshRunning = true;
    setAdvertiserRefreshBusy(true);
    try {
      let result = await api("/api/advertisers/refresh", {
        method: "POST",
        body: {},
      });
      for (let attempt = 0; attempt < 300 && ["pending", "running"].includes(result.status); attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        result = await api("/api/advertisers/refresh");
      }
      if (result.status === "succeeded") {
        if (target === "draft") await loadDraftAdvertisers(true);
        else await loadAdvertisers(true);
        toast("Список рекламодателей обновлён");
      } else if (result.status === "failed") {
        toast("Не удалось обновить список. Повторите позже.");
      } else {
        toast("Обновление продолжается. Проверьте список позже.");
      }
    } catch (error) {
      toast(error.message || "Не удалось запустить обновление.");
    } finally {
      state.advertiserRefreshRunning = false;
      setAdvertiserRefreshBusy(false);
    }
  }

  function setAdvertiserRefreshBusy(busy) {
    [$("#advertiser-refresh"), $("#draft-advertiser-refresh")].forEach((button) => {
      button.disabled = busy;
      button.textContent = busy ? "Обновление…" : "Обновить список рекламодателей";
    });
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

  async function loadHelp() {
    const target = $("#help-content");
    target.innerHTML = '<div class="empty">Загрузка…</div>';
    try {
      const help = await api("/api/help");
      target.innerHTML = helpMarkup(help.markdown);
    } catch (error) {
      target.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
  }

  function helpMarkup(markdown) {
    const sections = String(markdown || "").split(/^##\s+/m).map((part) => part.trim()).filter(Boolean);
    if (!sections.length) return '<div class="empty">Справка пока не заполнена.</div>';
    return sections.map((section) => {
      const [title, ...body] = section.split("\n");
      return `<details class="faq-item"><summary>${inlineHelp(title)}</summary><div class="faq-body">${helpBody(body)}</div></details>`;
    }).join("");
  }

  function helpBody(lines) {
    const output = [];
    let paragraph = [];
    let list = null;
    let items = [];
    const flushParagraph = () => {
      if (paragraph.length) output.push(`<p>${inlineHelp(paragraph.join(" "))}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (list && items.length) output.push(`<${list}>${items.map((item) => `<li>${inlineHelp(item)}</li>`).join("")}</${list}>`);
      list = null;
      items = [];
    };
    lines.forEach((raw) => {
      const line = raw.trim();
      if (!line) { flushParagraph(); flushList(); return; }
      const bullet = line.match(/^[-*]\s+(.+)$/);
      const ordered = line.match(/^\d+\.\s+(.+)$/);
      const match = bullet || ordered;
      if (match) {
        flushParagraph();
        const nextList = ordered ? "ol" : "ul";
        if (list && list !== nextList) flushList();
        list = nextList;
        items.push(match[1]);
        return;
      }
      if (list && /^\s+/.test(raw) && items.length) {
        items[items.length - 1] += ` ${line}`;
        return;
      }
      flushList();
      paragraph.push(line);
    });
    flushParagraph();
    flushList();
    return output.join("");
  }

  function inlineHelp(value) {
    return escapeHtml(value).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
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
    const structured = destination && isKalisto();
    $("#kalisto-destination-fields").classList.toggle("hidden", !structured);
    if (structured) $("#destination-cap-anchor").after($("#cap-fields"));
    else {
      $("#destination-fields").append($("#cap-fields"));
      $("#destination-fields").append($("#important-comment-field"));
    }
    $("#important-comment-field").classList.toggle("hidden", !destination || isKalisto());
    syncCapInput("#cap-input", "cap-mode");
    $("#ticket-body-field").classList.toggle("hidden", structured);
    $("#ticket-body").required = !structured;
    if (structured) $("#new-advertiser").classList.add("hidden");
    if (destination) loadAdvertisers(true);
  }

  function toggleDraftType() {
    const type = $("input[name='draft-ticket-type']:checked")?.value || null;
    const destination = type === "destination";
    $("#draft-destination-fields").classList.toggle("hidden", !destination);
    $("#draft-kalisto-destination-fields").classList.toggle("hidden", !(destination && isKalisto()));
    if (destination && isKalisto()) $("#draft-cap-anchor").after($("#draft-cap-fields"));
    else {
      $("#draft-destination-fields").append($("#draft-cap-fields"));
      $("#draft-destination-fields").append($("#draft-important-comment-field"));
    }
    $("#draft-important-comment-field").classList.toggle("hidden", !destination || isKalisto());
    syncCapInput("#draft-cap", "draft-cap-mode");
    if (destination && !state.draftAdvertisers.length) loadDraftAdvertisers(true);
  }

  function readCap(inputSelector, modeName) {
    const mode = $(`input[name='${modeName}']:checked`)?.value || null;
    if (mode === "unlim") return { cap: "uncap", cap_mode: "unlim", complete: true };
    const raw = $(inputSelector).value.trim();
    const value = raw ? Number(raw) : null;
    const validValue = Number.isInteger(value) && value >= 1 && value <= 1000000;
    const validMode = ["daily", "monthly", "lifetime"].includes(mode);
    return {
      cap: validValue ? value : null,
      cap_mode: mode,
      complete: validValue && validMode,
    };
  }

  function setCapMode(modeName, mode) {
    $$(`input[name='${modeName}']`).forEach((input) => {
      input.checked = input.value === mode;
    });
  }

  function syncCapInput(inputSelector, modeName) {
    const input = $(inputSelector);
    const mode = $(`input[name='${modeName}']:checked`)?.value;
    input.disabled = mode === "unlim";
    if (mode === "unlim") input.value = "";
  }

  function showRequiredFields(fields) {
    const unique = [...new Map(
      fields.filter((item) => item.field).map((item) => [item.field, item]),
    ).values()];
    unique.forEach((item) => item.field.setAttribute("aria-invalid", "true"));
    const first = unique[0];
    first?.field.scrollIntoView({ behavior: "smooth", block: "center" });
    const focusTarget = first?.field.matches("input, select, textarea, button")
      ? first.field
      : first?.field.querySelector("input, select, textarea, button");
    focusTarget?.focus({ preventScroll: true });
    const labels = unique.map((item) => item.label).join(", ");
    toast(`${unique.length === 1 ? "Не заполнено поле" : "Не заполнены поля"}: ${labels}`);
  }

  function requiredField(field, label) {
    return { field, label };
  }

  function clearFieldError(event) {
    event.target.closest('[aria-invalid="true"]')?.removeAttribute("aria-invalid");
  }

  function clearFormErrors(form) {
    form.querySelectorAll('[aria-invalid="true"]').forEach((field) => {
      field.removeAttribute("aria-invalid");
    });
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
  function formatDate(value) { return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
  function escapeHtml(value = "") { const div = document.createElement("div"); div.textContent = String(value); return div.innerHTML; }
  function escapeAttr(value = "") { return escapeHtml(value).replaceAll('"', "&quot;"); }
  function toast(message) { const node = $("#toast"); node.textContent = message; node.classList.remove("hidden"); clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.add("hidden"), 3200); }
})();
