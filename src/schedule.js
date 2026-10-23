import { escapeHtml, shiftDate, validIsoDate } from "./utils.js";

export function shiftPhase(date, anchor) {
  if (!validIsoDate(date) || !validIsoDate(anchor)) throw new Error("Некорректная дата графика.");
  const days = (Date.parse(`${date}T00:00:00Z`) - Date.parse(`${anchor}T00:00:00Z`)) / 86400000;
  return ((days % 4) + 4) % 4;
}

export function workingOn(date, people = []) {
  if (!validIsoDate(date)) throw new Error("Некорректная дата графика.");
  return people.filter((person) => shiftPhase(date, person.anchor) < 2);
}

export function moscowToday(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now).map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function calendarMonth(month) {
  if (!/^\d{4}-\d{2}$/.test(month) || !validIsoDate(`${month}-01`)) throw new Error("Некорректный месяц.");
  const first = `${month}-01`;
  const offset = (new Date(`${first}T12:00:00Z`).getUTCDay() + 6) % 7;
  const last = new Date(`${first}T12:00:00Z`);
  last.setUTCMonth(last.getUTCMonth() + 1);
  last.setUTCDate(0);
  return Array.from({ length: Math.ceil((offset + last.getUTCDate()) / 7) * 7 }, (_, i) => shiftDate(first, i - offset));
}

const monthTitle = (month) => new Intl.DateTimeFormat("ru-RU", {
  timeZone: "UTC", month: "long", year: "numeric",
}).format(new Date(`${month}-01T12:00:00Z`));
const fullDate = (date) => new Intl.DateTimeFormat("ru-RU", {
  timeZone: "UTC", weekday: "long", day: "numeric", month: "long",
}).format(new Date(`${date}T12:00:00Z`));

export function calendarDaysMarkup(month, today, selected = null, roster = []) {
  return calendarMonth(month).map((date) => {
    const people = workingOn(date, roster);
    const description = `${fullDate(date)}. На смене: ${people.map((p) => p.name).join(", ") || "никого"}.`;
    return `<button type="button" class="shift-day${date.slice(0, 7) !== month ? " outside-month" : ""}${date === today ? " is-today" : ""}${date === selected ? " is-open" : ""}" data-shift-date="${date}" aria-label="${escapeHtml(description)}" aria-pressed="${date === selected}"${date === today ? ' aria-current="date"' : ""} aria-describedby="shift-tip-${date}">
      <time datetime="${date}">${Number(date.slice(8))}</time>
      <span class="shift-hover" role="tooltip" id="shift-tip-${date}"><strong>${escapeHtml(fullDate(date))}</strong><span>На смене · ${people.length}</span>${people.map((person) => `<span class="shift-person ${person.color}"><i aria-hidden="true"></i>${escapeHtml(person.name)}</span>`).join("")}</span>
    </button>`;
  }).join("");
}

export function mountSchedule(root, roster) {
  let today = moscowToday();
  let month = today.slice(0, 7);
  let selected = null;
  root.innerHTML = `<div class="shift-heading"><h2 id="schedule-title">График смен</h2><span class="quiet-chip">2/2 · МСК</span></div>
    <div class="shift-toolbar"><h3 id="shift-month-title" aria-live="polite"></h3><div class="shift-month-actions"><button type="button" class="text-button" data-shift-today>Сегодня</button><button type="button" class="secondary" data-shift-month="-1" aria-label="Предыдущий месяц">←</button><button type="button" class="secondary" data-shift-month="1" aria-label="Следующий месяц">→</button></div></div>
    <div class="shift-weekdays" aria-hidden="true">${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((day) => `<span>${day}</span>`).join("")}</div>
    <div class="shift-days" role="group" aria-labelledby="shift-month-title"></div>
    <p class="shift-footnote">Наведите на дату или нажмите её, чтобы увидеть смену.</p>`;

  function render() {
    root.querySelector("#shift-month-title").textContent = monthTitle(month);
    root.querySelector(".shift-days").innerHTML = calendarDaysMarkup(month, today, selected, roster);
  }
  function close() {
    selected = null;
    root.querySelectorAll(".is-open").forEach((day) => {
      day.classList.remove("is-open");
      day.setAttribute("aria-pressed", "false");
    });
  }
  function select(date) {
    selected = date;
    month = date.slice(0, 7);
    render();
    root.querySelector(`[data-shift-date="${date}"]`)?.focus({ preventScroll: true });
  }
  root.addEventListener("click", (event) => {
    const day = event.target.closest("[data-shift-date]");
    if (day) {
      const wasOpen = selected === day.dataset.shiftDate;
      close();
      day.classList.toggle("tip-dismissed", wasOpen);
      if (!wasOpen) {
        selected = day.dataset.shiftDate;
        day.classList.add("is-open");
        day.setAttribute("aria-pressed", "true");
      }
      return;
    }
    if (event.target.closest("[data-shift-today]")) { selected = null; month = today.slice(0, 7); render(); return; }
    const button = event.target.closest("[data-shift-month]");
    if (button) {
      const date = new Date(`${month}-01T12:00:00Z`);
      date.setUTCMonth(date.getUTCMonth() + Number(button.dataset.shiftMonth));
      selected = null;
      month = date.toISOString().slice(0, 7);
      render();
    }
  });
  root.addEventListener("pointerover", (event) => {
    const day = event.target.closest("[data-shift-date]");
    if (day && !day.contains(event.relatedTarget)) {
      if (event.pointerType === "mouse") close();
      day.classList.remove("tip-dismissed");
    }
  });
  root.addEventListener("pointerleave", (event) => { if (event.pointerType === "mouse") close(); });
  document.addEventListener("pointerdown", (event) => { if (!root.contains(event.target)) close(); });
  root.addEventListener("keydown", (event) => {
    const day = event.target.closest("[data-shift-date]");
    if (event.key === "Escape") {
      close();
      if (day) day.classList.add("tip-dismissed");
      return;
    }
    const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    if (day && offset) { event.preventDefault(); select(shiftDate(day.dataset.shiftDate, offset)); }
  });
  render();
  return {
    setToday(date) {
      if (!validIsoDate(date)) return;
      if (month === today.slice(0, 7) && !selected) month = date.slice(0, 7);
      today = date;
      render();
    },
  };
}
