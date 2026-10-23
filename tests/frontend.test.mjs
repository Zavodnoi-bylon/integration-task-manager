import assert from "node:assert/strict";
import test from "node:test";
import { backlogChart, flowChart } from "../src/charts.js";
import { createRequestGate, escapeHtml, formatNumber, presetPeriod, queryString, safeSourceUrl, validatePeriod } from "../src/utils.js";

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
