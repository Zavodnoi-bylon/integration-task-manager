import assert from "node:assert/strict";
import test from "node:test";
import { backlogChart, flowChart } from "../src/charts.js";
import { createRequestGate, escapeHtml, formatNumber, presetPeriod, queryString, safeSourceUrl, validatePeriod } from "../src/utils.js";
import { calendarDaysMarkup, calendarMonth, moscowToday, shiftPhase, workingOn } from "../src/schedule.js";
import { LAYOUT_KEY, moveWidget, normalizeLayout, readLayout, resizedWidget, widgetDefinitions, writeLayout } from "../src/layout-model.js";

const teamSchedule = [
  {name:"AIex911",anchor:"2026-10-23",color:"alex"},
  {name:"stanedgart",anchor:"2026-10-22",color:"stan"},
  {name:"Custodes112",anchor:"2026-10-25",color:"custodes"},
  {name:"Dmee911",anchor:"2026-10-24",color:"dmee"},
];

test("saved layout cannot duplicate/drop widgets or inject unknown blocks and CSS values", () => {
  const layout = normalizeLayout({ version: 1, widgets: [
    { id: "calendar", span: 99, height: -200 },
    { id: "calendar", span: 3 }, { id: "foreign", span: 12 },
    { id: "flow", span: "4;display:none", height: "100vh" }, null,
  ] });
  assert.equal(layout.widgets.length, widgetDefinitions.length);
  assert.equal(new Set(layout.widgets.map((row) => row.id)).size, widgetDefinitions.length);
  assert.deepEqual(layout.widgets[0], { id: "calendar", span: 12, height: 372 });
  assert.deepEqual(layout.widgets[1], { id: "flow", span: 7, height: null });
  assert.deepEqual(normalizeLayout({version: 99,widgets:[]}), normalizeLayout(null));
});

test("moving a block preserves all widgets and their sizes without mutating the saved snapshot", () => {
  const saved = normalizeLayout(null);
  const next = moveWidget(saved, "calendar", "metrics");
  assert.equal(saved.widgets[0].id, "metrics");
  assert.equal(next.widgets[0].id, "calendar");
  assert.deepEqual([...next.widgets].sort((a,b)=>a.id.localeCompare(b.id)), [...saved.widgets].sort((a,b)=>a.id.localeCompare(b.id)));
  assert.equal(moveWidget(next,"calendar","registry",true).widgets.at(-1).id,"calendar");
});

test("resizing snaps to columns and bounds width/height so data stays readable", () => {
  assert.deepEqual(resizedWidget("calendar",{span:3,height:372},200,108,100),{id:"calendar",span:5,height:480});
  assert.deepEqual(resizedWidget("registry",{span:12,height:600},-5000,-5000,100),{id:"registry",span:6,height:320});
  assert.deepEqual(resizedWidget("flow",{span:7,height:300},5000,5000,100),{id:"flow",span:12,height:1200});
});

test("layout reloads after saving, recovers corrupt storage, and reports storage failures", () => {
  const memory = new Map();
  const storage = {getItem:(key)=>memory.get(key),setItem:(key,value)=>memory.set(key,value)};
  const next = moveWidget(normalizeLayout(null),"calendar","metrics");
  assert.equal(writeLayout(storage,next),true);
  assert.deepEqual(readLayout(storage),next);
  memory.set(LAYOUT_KEY,"broken JSON");
  assert.deepEqual(readLayout(storage),normalizeLayout(null));
  assert.equal(writeLayout({setItem(){throw new Error("Storage blocked");}},next),false);
  assert.deepEqual(readLayout(null),normalizeLayout(null));
});

test("confirmed 2/2 roster matches working and resting dates for every employee", () => {
  const expected = {
    "2026-10-22": ["stanedgart", "Custodes112"],
    "2026-10-23": ["AIex911", "stanedgart"],
    "2026-10-24": ["AIex911", "Dmee911"],
    "2026-10-25": ["Custodes112", "Dmee911"],
    "2026-10-26": ["stanedgart", "Custodes112"],
    "2026-10-27": ["AIex911", "stanedgart"],
    "2026-10-28": ["AIex911", "Dmee911"],
  };
  for (const [date, names] of Object.entries(expected)) assert.deepEqual(workingOn(date, teamSchedule).map((p) => p.name), names);
  for (let day = 1; day <= 31; day++) assert.equal(workingOn(`2026-10-${String(day).padStart(2, "0")}`, teamSchedule).length, 2);
});

test("2/2 repeats across month/year boundaries, leap dates and dates before anchors", () => {
  for (const person of teamSchedule) {
    for (const date of ["2024-02-28", "2026-09-29", "2026-10-30", "2026-12-30"]) {
      const start = shiftPhase(date, person.anchor);
      for (let day = 0; day < 12; day++) {
        const next = new Date(`${date}T00:00:00Z`);
        next.setUTCDate(next.getUTCDate() + day);
        assert.equal(shiftPhase(next.toISOString().slice(0, 10), person.anchor), (start + day) % 4);
      }
    }
  }
  assert.throws(() => workingOn("2026-02-29"));
});

test("calendar weeks start Monday and cover the whole month including leap February", () => {
  const october = calendarMonth("2026-10");
  assert.equal(october[0], "2026-09-28");
  assert.equal(october.at(-1), "2026-11-01");
  assert.equal(october.length, 35);
  assert.ok(calendarMonth("2024-02").includes("2024-02-29"));
  assert.equal(calendarMonth("2026-11").length, 42);
  assert.throws(() => calendarMonth("2026-13"));
});

test("calendar today follows Moscow midnight independent of browser timezone", () => {
  assert.equal(moscowToday(new Date("2026-10-23T20:59:59Z")), "2026-10-23");
  assert.equal(moscowToday(new Date("2026-10-23T21:00:00Z")), "2026-10-24");
});

test("calendar exposes names in accessible day labels and hover tooltips", () => {
  const markup = calendarDaysMarkup("2026-10", "2026-10-23", "2026-10-24", teamSchedule);
  assert.match(markup, /aria-label="суббота, 24 октября\. На смене: AIex911, Dmee911\./);
  assert.equal((markup.match(/aria-pressed="true"/g) || []).length, 1);
  assert.equal((markup.match(/aria-current="date"/g) || []).length, 1);
  assert.match(markup, /aria-describedby="shift-tip-2026-10-24"/);
  const initial = calendarDaysMarkup("2026-10", "2026-10-23", null, teamSchedule);
  assert.doesNotMatch(initial, /aria-pressed="true"|class="[^"]*is-open|shift-day-people|shift-day-dots/);
});

test("unknown measurements stay unknown and observed zero stays zero", () => {
  for (const missing of [null, undefined, "", NaN, Infinity]) assert.equal(formatNumber(missing), "—");
  assert.equal(formatNumber(0), "0");
  assert.equal(formatNumber(2.5, 1), "2,5");
});

test("quick periods depend on the server's Moscow date, including month boundaries", () => {
  assert.deepEqual(presetPeriod("yesterday", "2026-03-01"), { from: "2026-02-28", to: "2026-02-28" });
  assert.deepEqual(presetPeriod("7d", "2026-01-03"), { from: "2025-12-28", to: "2026-01-03" });
  assert.deepEqual(presetPeriod("month", "2026-10-22"), { from: "2026-10-01", to: "2026-10-22" });
});

test("date validation catches invalid dates, reversed periods and range limits", () => {
  assert.ok(validatePeriod("2026-02-29", "2026-03-01"));
  assert.ok(validatePeriod("2026-10-22", "2026-10-01"));
  assert.equal(validatePeriod("2024-01-01", "2024-12-31"), null);
  assert.ok(validatePeriod("2024-01-01", "2025-01-01"));
});

test("a late response cannot commit after a newer response or explicit cancellation", async () => {
  const gate = createRequestGate();
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let result = null;
  const first = gate.start();
  const oldResponse = (async () => { await pending; if (first.isCurrent()) result = "old"; })();
  const second = gate.start();
  assert.equal(first.signal.aborted, true);
  if (second.isCurrent()) result = "new";
  release(); await oldResponse;
  assert.equal(result, "new");
  gate.abort();
  assert.equal(second.signal.aborted, true);
  assert.equal(second.isCurrent(), false);
});

test("chart snapshots remain individual values and are not summed", () => {
  const rows = [
    { period_start: "2026-10-01", period_end: "2026-10-01", observed_backlog: 10 },
    { period_start: "2026-10-02", period_end: "2026-10-02", observed_backlog: 12 },
  ];
  const chart = backlogChart(rows);
  assert.match(chart, /Известно открытых: 10/);
  assert.match(chart, /Известно открытых: 12/);
  assert.doesNotMatch(chart, /Известно открытых: 22/);
  assert.equal((chart.match(/data-metric="backlog"/g) || []).length, 2);
});

test("missing chart measurements do not become clickable zero measurements", () => {
  const row = { period_start: "2026-10-01", period_end: "2026-10-01", incoming: null, completed: 0, observed_backlog: null };
  assert.doesNotMatch(flowChart([row]), /data-metric="incoming"/);
  assert.match(flowChart([row]), /data-metric="completed"/);
  assert.doesNotMatch(backlogChart([row]), /data-metric="backlog"/);
});

test("source URLs reject executable schemes and display text is HTML escaped", () => {
  assert.equal(safeSourceUrl("javascript:alert(1)"), null);
  assert.equal(safeSourceUrl("data:text/html,<script>alert(1)</script>"), null);
  assert.equal(safeSourceUrl("https://t.me/c/123/456"), "https://t.me/c/123/456");
  assert.equal(escapeHtml('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
});

test("query serialization preserves string IDs, explicit zero and unknown assignees", () => {
  const query = new URLSearchParams(queryString({ client: "000123", assignee: "__unknown__", q: "A & B", page: 0, empty: "" }));
  assert.equal(query.get("client"), "000123");
  assert.equal(query.get("assignee"), "__unknown__");
  assert.equal(query.get("q"), "A & B");
  assert.equal(query.get("page"), "0");
  assert.equal(query.has("empty"), false);
});
