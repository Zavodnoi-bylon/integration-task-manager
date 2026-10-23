import { dateLabel, escapeHtml, formatNumber, numeric } from "./utils.js";

const COLORS = { incoming: "#7c9bff", completed: "#5ed4ae", backlog: "#e8b879" };

function layout(series, keys, width = 760, height = 240) {
  const left = 48, right = 16, top = 16, bottom = 34;
  const values = series.flatMap((row) => keys.map((key) => numeric(row[key])).filter((value) => value !== null));
  const maximum = values.length ? Math.max(...values, 0) : 0;
  const step = Math.max(1, Math.ceil(maximum / 4));
  const ceiling = step * 4;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  return {
    width, height, left, right, top, bottom, plotWidth, plotHeight, step, ceiling,
    x: (index) => left + (index + 0.5) * plotWidth / Math.max(series.length, 1),
    y: (value) => top + plotHeight - (value / ceiling) * plotHeight,
    base: top + plotHeight,
  };
}

function axes(series, box) {
  let result = "";
  for (let index = 0; index <= 4; index++) {
    const value = index * box.step;
    const y = box.y(value);
    result += `<line class="chart-grid-line" x1="${box.left}" y1="${y}" x2="${box.width - box.right}" y2="${y}" />`;
    result += `<text class="chart-axis-label" x="${box.left - 12}" y="${y + 4}" text-anchor="end">${formatNumber(value)}</text>`;
  }
  const stride = Math.max(1, Math.ceil(series.length / 6));
  series.forEach((row, index) => {
    if (index % stride !== 0 && index !== series.length - 1) return;
    // Keep both endpoints; omit an interior label if it would crowd the last one.
    if (index !== 0 && index !== series.length - 1 && box.x(series.length - 1) - box.x(index) < 56) return;
    result += `<text class="chart-axis-label" x="${box.x(index)}" y="${box.height - 9}" text-anchor="middle">${escapeHtml(dateLabel(row.period_start))}</text>`;
  });
  return result;
}

function bucketTitle(row) {
  const label = row.period_start === row.period_end ? dateLabel(row.period_start) : `${dateLabel(row.period_start)} — ${dateLabel(row.period_end)}`;
  return `${label}${row.partial ? " · неполная неделя" : ""}`;
}

export function flowChart(series) {
  if (!series.length) return '<div class="chart-placeholder">Нет наблюдаемых данных за период</div>';
  const box = layout(series, ["incoming", "completed"]);
  const slot = box.plotWidth / series.length;
  const bar = Math.max(0.5, Math.min(18, slot * 0.3));
  let marks = "";
  series.forEach((row, index) => {
    for (const [metric, direction, label] of [["incoming", -1, "Поступило"], ["completed", 1, "Завершено"]]) {
      const value = numeric(row[metric]);
      if (value === null) continue;
      const x = box.x(index) + direction * bar * 0.58 - bar / 2;
      const y = box.y(value);
      const description = `${bucketTitle(row)}. ${label}: ${formatNumber(value)}. Открыть задачи.`;
      marks += `<g class="chart-interactive" role="button" tabindex="0" data-bucket-index="${index}" data-metric="${metric}" data-tip="${escapeHtml(`${bucketTitle(row)}\n${label}: ${formatNumber(value)}`)}" aria-label="${escapeHtml(description)}">`;
      marks += `<rect class="chart-hit-area" x="${x - 1}" y="${box.top}" width="${bar + 2}" height="${box.plotHeight}" fill="transparent" />`;
      marks += `<rect class="chart-bar" x="${x}" y="${y}" width="${bar}" height="${Math.max(0, box.base - y)}" rx="${Math.min(3, bar / 2)}" fill="${COLORS[metric]}" />`;
      if (value === 0) marks += `<line x1="${x}" x2="${x + bar}" y1="${box.base}" y2="${box.base}" stroke="${COLORS[metric]}" stroke-width="2" />`;
      marks += `</g>`;
    }
  });
  return `<svg class="activity-chart" viewBox="0 0 ${box.width} ${box.height}" role="group" aria-label="Поступившие и завершённые задачи по периодам">${axes(series, box)}${marks}</svg>`;
}

export function backlogChart(series) {
  if (!series.length) return '<div class="chart-placeholder">Нет наблюдаемых данных за период</div>';
  const box = layout(series, ["observed_backlog"], 560);
  const segments = [];
  let segment = [];
  series.forEach((row, index) => {
    const value = numeric(row.observed_backlog);
    if (value === null) {
      if (segment.length) segments.push(segment);
      segment = [];
    } else segment.push({ x: box.x(index), y: box.y(value) });
  });
  if (segment.length) segments.push(segment);
  const paths = segments.map((points) => {
    const path = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
    const area = `${path} L${points.at(-1).x},${box.base} L${points[0].x},${box.base} Z`;
    return `<path d="${area}" fill="url(#backlog-area)" /><path d="${path}" fill="none" stroke="${COLORS.backlog}" stroke-width="2.5" stroke-linejoin="round" />`;
  }).join("");
  const points = series.map((row, index) => {
    const value = numeric(row.observed_backlog);
    if (value === null) return "";
    const description = `${bucketTitle(row)}. Известно открытых: ${formatNumber(value)}. Открыть задачи.`;
    return `<g class="chart-interactive" role="button" tabindex="0" data-bucket-index="${index}" data-metric="backlog" data-tip="${escapeHtml(`${bucketTitle(row)}\nИзвестно открытых: ${formatNumber(value)}`)}" aria-label="${escapeHtml(description)}"><circle class="chart-hit-area" cx="${box.x(index)}" cy="${box.y(value)}" r="10" fill="transparent" /><circle class="chart-point" cx="${box.x(index)}" cy="${box.y(value)}" r="3.5" fill="${COLORS.backlog}" stroke="#171e2a" stroke-width="2" /></g>`;
  }).join("");
  return `<svg class="activity-chart" viewBox="0 0 ${box.width} ${box.height}" role="group" aria-label="Известные открытые задачи на конец каждого периода"><defs><linearGradient id="backlog-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${COLORS.backlog}" stop-opacity="0.2" /><stop offset="100%" stop-color="${COLORS.backlog}" stop-opacity="0.015" /></linearGradient></defs>${axes(series, box)}${paths}${points}</svg>`;
}
