# Ticket search and pagination — 19 November 2026

Base: production Mini App source/image `20261117-comment-2c71768`.
This is an isolated checkout because the historical task-manager checkout contains
uncommitted changes and predates the running release. No Git remote push performed.

`GET /api/tickets` accepts optional `search` (maximum 200 characters, trimmed).
Search is a case-insensitive literal substring of the visible list title: first
Destination result naming, otherwise ticket title, otherwise description preview.
The same predicate applies to count and page queries, after profile/actor/status
restrictions. `%`, `_`, quotes and backslashes are literal input, bound as SQL parameters.

UI uses existing page/page_size/total/has_next API fields, 50 tickets per page.
Search debounces for 300 ms; stale responses are ignored. Search/scope/status changes
reset page 1. Returning from a detail preserves the page and search. If concurrent
changes remove the last page, reload the last valid page. Pager uses bounded grid
columns for narrow screens.

Validation: 114 Linux unit/API tests; 3 Node behavior tests; read-only canonical
gateway smoke against both real profiles (16 Kalisto and 14 Mephisto page checks).
Final title-projection SQL change additionally passed the 2 gateway query tests
and repeated real-data smoke. No migration or data modification is required.

Image: `dest-bot-miniapp:20261119-search-pages`, built on the existing production
image with unchanged dependencies. Source:
`/home/teamactivity/dest-bot-miniapp/releases/20261119Tsearch-pages`.
Deployment helper stores previous runtime configuration and source pointer before
cutover and restores the previous image automatically on failed health/HTTP checks.
