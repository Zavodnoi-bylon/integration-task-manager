# Frontend provenance

`base.css` is a byte-for-byte copy of `theraw-analytics/src/style.css` from the
user-provided source archive. It is imported before `style.css`; new layout and
chart styles are isolated in that override file. The original source project was
not modified.

The `escapeHtml` function body in `utils.js` is retained from the original
TheRaw `src/main.js:79–86`. Other frontend logic was written for the task/event API
in `docs/IMPLEMENTATION_CONTRACT.md`.

No AIO metric aggregation, visits, conversions, campaign filters, original API
credentials, or estimated labour figures are used. Charts render server-provided
time series without summing backlog snapshots. No chart runtime dependency was
added. SVG chart marks support pointer and keyboard drilldown.

All normal requests use `/api`. Demo content is only provided by the explicitly
started backend demo mode and is visibly marked whenever a response has
`demo=true`. There is no client-side demo fallback after an API error.
