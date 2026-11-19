const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function harness() {
  const nodes = new Map();
  const requests = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {innerHTML: '', textContent: '', disabled: false,
      classList: {toggle() {}, add() {}, remove() {}}});
    return nodes.get(id);
  };
  const context = {window: {}, sessionStorage: {getItem() {return '';}},
    document: {addEventListener() {}, querySelector: node, querySelectorAll() {return [];}},
    URLSearchParams, clearTimeout, setTimeout};
  let source = fs.readFileSync(path.join(__dirname, '../src/dest_miniapp/web/assets/app.js'), 'utf8');
  source = source.replace(/\}\)\(\);\s*$/, 'window.test = {state, loadTickets, setApi: fn => api = fn};})();');
  vm.runInNewContext(source, context);
  const app = context.window.test;
  app.setApi(url => new Promise((resolve, reject) => requests.push({url, resolve, reject})));
  return {app, requests, node};
}
const page = (number, total) => ({items: [], page: number, page_size: 50, total, has_next: number * 50 < total});

test('search and page are sent together with scope and status', async () => {
  const {app, requests, node} = harness();
  Object.assign(app.state, {ticketPage: 2, ticketSearch: 'Lucky %_', scope: 'team', status: 'completed'});
  const pending = app.loadTickets();
  const params = new URL(requests[0].url, 'https://example.test').searchParams;
  assert.equal(params.get('search'), 'Lucky %_');
  assert.equal(params.get('page'), '2');
  assert.equal(params.get('scope'), 'team');
  assert.equal(params.get('status'), 'completed');
  requests[0].resolve(page(2, 73)); await pending;
  assert.equal(node('#tickets-prev').disabled, false);
  assert.equal(node('#tickets-next').disabled, true);
  assert.equal(node('#tickets-page-info').textContent, '2 / 2 · Всего: 73');
});

test('late response cannot overwrite a newer search', async () => {
  const {app, requests, node} = harness();
  const old = app.loadTickets();
  app.state.ticketSearch = 'new';
  const latest = app.loadTickets();
  requests[1].resolve(page(1, 0)); await latest;
  requests[0].resolve(page(1, 200)); await old;
  assert.equal(node('#tickets-page-info').textContent, '1 / 1 · Всего: 0');
  assert.match(node('#ticket-list').innerHTML, /не найдено/);
});

test('empty last page after data changes goes back to last existing page', async () => {
  const {app, requests} = harness();
  app.state.ticketPage = 3;
  const pending = app.loadTickets();
  requests[0].resolve(page(3, 51));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.state.ticketPage, 2);
  assert.match(requests[1].url, /page=2/);
  requests[1].resolve(page(2, 51)); await pending;
});
