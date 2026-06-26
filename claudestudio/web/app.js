/* ============================================================================
   ClaudeStudio — single-page app (vanilla JS, no build step)
   ========================================================================== */
'use strict';

// ---- tiny DOM + format helpers --------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const fmt = {
  num: (n) => Number(n || 0).toLocaleString('en-US'),
  compact(n) {
    n = Number(n || 0);
    if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k';
    return String(Math.round(n));
  },
  cost(n) {
    n = Number(n || 0);
    if (n === 0) return '$0';
    if (n < 0.01) return '$' + n.toFixed(4);
    if (n < 100) return '$' + n.toFixed(2);
    return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 });
  },
  dur(s) {
    s = Number(s || 0);
    if (s < 60) return Math.round(s) + 's';
    if (s < 3600) return Math.round(s / 60) + 'm';
    return (s / 3600).toFixed(1) + 'h';
  },
  rel(epoch) {
    if (!epoch) return '—';
    const d = epoch * 1000, now = Date.now(), diff = (now - d) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + 'd ago';
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: diff > 86400 * 300 ? 'numeric' : undefined });
  },
  dt(epoch) { return epoch ? new Date(epoch * 1000).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'; },
};

function family(model) {
  const m = (model || '').toLowerCase();
  if (m.includes('fable')) return 'Fable';
  if (m.includes('mythos')) return 'Mythos';
  if (m.includes('opus')) return 'Opus';
  if (m.includes('sonnet')) return 'Sonnet';
  if (m.includes('haiku')) return 'Haiku';
  return 'Other';
}
const shortModel = (m) => (m || '').replace('claude-', '');

// ---- API ------------------------------------------------------------------
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  },
  summary: () => API.get('/api/summary'),
  sessions: (q) => API.get('/api/sessions?' + new URLSearchParams(q)),
  session: (id) => API.get('/api/session/' + encodeURIComponent(id)),
  search: (q, limit = 30, filters = {}) => {
    const p = { q, limit };
    for (const k of ['kind', 'project', 'since', 'until', 'session']) if (filters[k]) p[k] = filters[k];
    return API.get('/api/search?' + new URLSearchParams(p));
  },
  analytics: () => API.get('/api/analytics'),
  projects: () => API.get('/api/projects'),
  wrapped: (year) => API.get('/api/wrapped' + (year ? '?year=' + year : '')),
  compare: (a, b) => API.get('/api/compare?' + new URLSearchParams({ a, b })),
  ask: (q, session) => API.get('/api/ask?' + new URLSearchParams(session ? { q, session } : { q })),
  state: (id, patch) => API.post('/api/state/' + encodeURIComponent(id), patch),
  reindex: () => API.post('/api/reindex', {}),
  exportUrl: (id, fmt) => '/api/session/' + encodeURIComponent(id) + '/export.' + fmt,
  saved: () => API.get('/api/saved'),
  addSaved: (b) => API.post('/api/saved', b),
  delSaved: (id) => fetch('/api/saved/' + encodeURIComponent(id), { method: 'DELETE' }).then((r) => r.json()),
  // v0.5.1
  toolLatency: () => API.get('/api/tools/latency'),
  patterns: (minCount) => API.get('/api/prompts/patterns' + (minCount ? '?min_count=' + minCount : '')),
  bookmarks: (session) => API.get('/api/bookmarks' + (session ? '?session=' + encodeURIComponent(session) : '')),
  addBookmark: (id, body) => API.post('/api/session/' + encodeURIComponent(id) + '/bookmark', body),
  delBookmark: (id) => fetch('/api/bookmark/' + encodeURIComponent(id), { method: 'DELETE' }).then((r) => r.json()),
  reportUrl: (since, until, fmt = 'html') => '/api/report.' + fmt + '?' + new URLSearchParams({ since, until }),
  analyticsCsvUrl: () => '/api/analytics.csv',
  sessionsCsvUrl: () => '/api/sessions.csv',
  // v0.5.2
  budget: () => API.get('/api/budget'),
  setBudget: (b) => API.post('/api/budget', b),
  clearBudget: () => fetch('/api/budget', { method: 'DELETE' }).then((r) => r.json()),
  efficiency: () => API.get('/api/analytics/efficiency'),
  prompts: (q) => API.get('/api/prompts' + (q ? '?' + new URLSearchParams(q) : '')),
  addPrompt: (b) => API.post('/api/prompts', b),
  delPrompt: (id) => fetch('/api/prompts/' + encodeURIComponent(id), { method: 'DELETE' }).then((r) => r.json()),
  extractPrompts: () => API.post('/api/prompts/extract', {}),
  annotations: (id) => API.get('/api/session/' + encodeURIComponent(id) + '/annotations'),
  saveAnnotation: (id, b) => API.post('/api/session/' + encodeURIComponent(id) + '/annotations', b),
  delAnnotation: (id, aid) => fetch('/api/session/' + encodeURIComponent(id) + '/annotations/' + encodeURIComponent(aid), { method: 'DELETE' }).then((r) => r.json()),
  claudeMd: (project) => API.get('/api/project/' + encodeURIComponent(project) + '/claude-md'),
  // v0.6.0
  patternWorkflows: () => API.get('/api/patterns/workflows'),
  patternDebugLoops: () => API.get('/api/patterns/debug-loops'),
  patternMomentum: () => API.get('/api/patterns/momentum'),
  crossRefs: () => API.get('/api/cross-refs'),
};

// trigger a browser download from a server endpoint that sets Content-Disposition
function downloadFrom(url) {
  const a = el('a', { href: url, download: '' });
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 0);
}

// ---- charts (hand-rolled SVG) ---------------------------------------------
function areaChart(series, { w = 600, h = 180, color = 'var(--accent)', key = 'value' } = {}) {
  const pts = series.map((d) => +d[key] || 0);
  const n = pts.length;
  if (!n) return el('div', { class: 'empty', text: 'No activity yet' });
  const max = Math.max(1, ...pts);
  const pad = 6;
  const X = (i) => pad + (i * (w - pad * 2)) / Math.max(1, n - 1);
  const Y = (v) => h - pad - (v / max) * (h - pad * 2 - 14);
  let line = '', area = `M ${X(0)} ${h - pad} `;
  pts.forEach((v, i) => { const cmd = i ? 'L' : 'M'; line += `${cmd} ${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; area += `L ${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; });
  area += `L ${X(n - 1)} ${h - pad} Z`;
  const gid = 'g' + Math.random().toString(36).slice(2, 7);
  const svg = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
    <defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity="0.35"/>
      <stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${X(n - 1)}" cy="${Y(pts[n - 1])}" r="3.5" fill="${color}"/>
  </svg>`;
  return el('div', { html: svg });
}

function donut(segments, { size = 132, stroke = 18 } = {}) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, cx = size / 2;
  let offset = 0, circles = '';
  for (const s of segments) {
    const frac = s.value / total, len = frac * c;
    circles += `<circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${s.color}" stroke-width="${stroke}"
      stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"
      transform="rotate(-90 ${cx} ${cx})" stroke-linecap="butt"/>`;
    offset += len;
  }
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">${circles}
    <text x="${cx}" y="${cx - 4}" text-anchor="middle" fill="var(--text)" font-size="20" font-weight="700">${segments.length}</text>
    <text x="${cx}" y="${cx + 14}" text-anchor="middle" fill="var(--text-3)" font-size="10">models</text></svg>`;
}

function spark(values, { w = 240, h = 38, color = 'var(--accent)' } = {}) {
  if (!values.length) return '';
  const max = Math.max(1, ...values), n = values.length;
  const X = (i) => (i * w) / Math.max(1, n - 1), Y = (v) => h - 2 - (v / max) * (h - 6);
  let p = '';
  values.forEach((v, i) => { p += `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; });
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none"><path d="${p}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
}

const FAM_COLOR = { Opus: '#ff8a5b', Sonnet: '#6aa6ff', Haiku: '#5ec98a', Fable: '#9a8cff', Mythos: '#9a8cff', Other: '#6f7585' };

// ---- toasts ---------------------------------------------------------------
function toast(msg, kind = 'ok') {
  const host = $('#toasts');
  const t = el('div', { class: 'toast ' + (kind === 'err' ? 'err' : '') }, [el('span', { class: 'dot' }), el('span', { text: msg })]);
  host.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 2600);
}

// ---- nav ------------------------------------------------------------------
const NAV = [
  { id: 'sessions', label: 'Sessions', icon: 'M4 6h16M4 12h16M4 18h10' },
  { id: 'ask', label: 'Ask', icon: 'M21 11.5a8.5 8.5 0 0 1-12.6 7.4L3 21l2.1-5.4A8.5 8.5 0 1 1 21 11.5z' },
  { id: 'bookmarks', label: 'Bookmarks', icon: 'M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z' },
  { id: 'timeline', label: 'Timeline', icon: 'M3 12h4l3-8 4 16 3-8h4' },
  { id: 'analytics', label: 'Analytics', icon: 'M4 19V5M4 19h16M8 16v-5M13 16V8M18 16v-9' },
  { id: 'efficiency', label: 'Efficiency', icon: 'M13 2L3 14h7l-1 8 10-12h-7l1-8z' },
  { id: 'prompts', label: 'Prompts', icon: 'M4 5h16M4 12h16M4 19h10M18 17l3 2-3 2' },
  { id: 'patterns', label: 'Patterns', icon: 'M5 5h5v5H5zM14 5h5v5h-5zM5 14h5v5H5zM14 14h5v5h-5z' },
  { id: 'projects', label: 'Projects', icon: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z' },
  { id: 'compare', label: 'Compare', icon: 'M9 3v18M4 7h5M4 12h5M4 17h5M15 3v18M15 8h5M15 13h5' },
  { id: 'wrapped', label: 'Wrapped', icon: 'M12 2l2.4 5 5.6.5-4.2 3.7 1.3 5.5L12 19l-5.1 2.7 1.3-5.5L4 12.5 9.6 12z' },
];

function renderNav(summary) {
  const nav = $('#nav');
  nav.innerHTML = '';
  for (const item of NAV) {
    const btn = el('button', { class: 'nav-item', 'data-route': item.id, onclick: () => go(item.id) }, [
      el('span', { html: `<svg viewBox="0 0 24 24" width="17" height="17"><path d="${item.icon}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
      el('span', { text: item.label }),
    ]);
    if (item.id === 'sessions' && summary) btn.appendChild(el('span', { class: 'count', text: fmt.num(summary.sessions) }));
    if (item.id === 'projects' && summary) btn.appendChild(el('span', { class: 'count', text: fmt.num(summary.projects) }));
    nav.appendChild(btn);
  }
}

function highlightNav(route) {
  $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.route === route));
}

function renderFootStats(s) {
  $('#foot-stats').innerHTML = `
    <div><b>${fmt.num(s.messages)}</b> messages</div>
    <div><b>${fmt.compact(s.tokens)}</b> tokens · <b>${fmt.cost(s.cost_usd)}</b></div>`;
}

// ---- router ---------------------------------------------------------------
let STATE = { summary: null };
// session currently open in the replay view + its bookmarks (keyed by seq), so
// per-message bookmark buttons know which session they belong to.
let CURRENT_SESSION = null;
let SESSION_BOOKMARKS = {};
const view = () => $('#view');

// a per-view keyboard handler, torn down whenever the route changes so handlers
// from a previous view (e.g. replay nav) never leak into the next one.
let _viewKey = null;
function setViewKey(fn) {
  if (_viewKey) document.removeEventListener('keydown', _viewKey);
  _viewKey = fn || null;
  if (_viewKey) document.addEventListener('keydown', _viewKey);
}

function go(route, params = {}) {
  const qs = new URLSearchParams(params).toString();
  location.hash = '#/' + route + (qs ? '?' + qs : '');
}

// a11y: a descriptive document title per route (announced on navigation).
function docTitleFor(route, parts, params) {
  const names = { sessions: 'Sessions', session: 'Session', ask: 'Ask', bookmarks: 'Bookmarks', timeline: 'Timeline', analytics: 'Analytics', efficiency: 'Efficiency', prompts: 'Prompt library', projects: 'Projects', compare: 'Compare', wrapped: 'Wrapped', search: 'Search', dev: 'Developer' };
  let label = names[route] || 'Sessions';
  if (route === 'session' && parts[1]) label = 'Session ' + String(parts[1]).slice(0, 8);
  return label + ' — ClaudeStudio';
}

async function router() {
  const raw = location.hash.replace(/^#\/?/, '') || 'sessions';
  const [path, query] = raw.split('?');
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  const parts = path.split('/');
  const route = parts[0] || 'sessions';
  highlightNav(['session'].includes(route) ? 'sessions' : route);
  setViewKey(null);  // drop any previous view's keyboard handler
  if (typeof closeBookmarkPopover === 'function') closeBookmarkPopover();
  // a11y: keep the document <title> in sync with the route so screen readers and
  // the browser history announce where you are ("Session: … — ClaudeStudio").
  document.title = docTitleFor(route, parts, params);
  try {
    if (route === 'session') return await viewSession(parts[1], params);
    if (route === 'ask') return await viewAsk(params);
    if (route === 'bookmarks') return await viewBookmarks(params);
    if (route === 'analytics') return await viewAnalytics();
    if (route === 'efficiency') return await viewEfficiency();
    if (route === 'prompts') return await viewPrompts();
    if (route === 'patterns') return await viewPatterns();
    if (route === 'projects') return await viewProjects();
    if (route === 'timeline') return await viewTimeline();
    if (route === 'compare') return await viewCompare(params);
    if (route === 'wrapped') return await viewWrapped(params);
    if (route === 'search') return await viewSearch(params);
    if (route === 'dev') return await viewDev();
    return await viewSessions(params);
  } catch (e) {
    view().innerHTML = '';
    view().appendChild(el('div', { class: 'view-pad' }, [el('div', { class: 'empty' }, [
      el('div', { class: 'big', text: 'Something went wrong' }),
      el('div', { text: String(e.message || e) }),
    ])]));
  }
}

// ---- view: sessions -------------------------------------------------------
const SORTS = [['recent', 'Recent'], ['oldest', 'Oldest'], ['messages', 'Most messages'], ['tools', 'Most tools'], ['cost', 'Costliest'], ['tokens', 'Most tokens'], ['duration', 'Longest']];
let sessionsState = { q: '', sort: 'recent', favorite: false, archived: 'exclude', since: '', until: '', offset: 0, limit: 50 };

async function viewSessions(params) {
  if (params.project) sessionsState.project = params.project;
  const root = el('div', { class: 'view-pad fade-in' });
  view().innerHTML = ''; view().appendChild(root);

  root.appendChild(el('div', { class: 'page-head' }, [
    el('div', {}, [
      el('h1', { class: 'page-title', text: 'Sessions' }),
      el('div', { class: 'page-sub', html: sessionsState.project ? `Project · <code style="font-family:var(--mono)">${esc(sessionsState.project)}</code> · <a href="#/sessions" onclick="event.preventDefault();delete (window.__cs.ss().project);window.__cs.go('sessions')">clear</a>` : 'Every conversation you have had with Claude Code' }),
    ]),
  ]));

  const searchBox = el('div', { class: 'search-box' }, [
    el('span', { html: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M21 21l-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' }),
    el('input', { class: 'input', placeholder: 'Filter sessions…', value: sessionsState.q }),
  ]);
  const sortSel = el('select', { class: 'input' }, SORTS.map(([v, l]) => el('option', { value: v, text: l, ...(v === sessionsState.sort ? { selected: 'selected' } : {}) })));
  const favChip = el('button', { class: 'chip toggle' + (sessionsState.favorite ? ' on' : '') }, ['★ Favorites']);
  const archChip = el('button', { class: 'chip toggle' + (sessionsState.archived === 'only' ? ' on' : '') }, ['Archived']);
  const saveBtn = el('button', { class: 'chip toggle', title: 'Save this filter as a smart collection' }, ['＋ Save search']);
  const sinceInp = el('input', { class: 'input date', type: 'date', title: 'Active on/after this date', value: sessionsState.since || '' });
  const untilInp = el('input', { class: 'input date', type: 'date', title: 'Started on/before this date', value: sessionsState.until || '' });
  const dateWrap = el('div', { class: 'date-range', title: 'Filter by date' }, [sinceInp, el('span', { class: 'date-sep', text: '→' }), untilInp]);

  const toolbar = el('div', { class: 'toolbar' }, [searchBox, sortSel, favChip, archChip, dateWrap, saveBtn]);
  root.appendChild(toolbar);
  const savedWrap = el('div', { class: 'saved-row' });
  root.appendChild(savedWrap);
  const listWrap = el('div', {});
  root.appendChild(listWrap);

  async function loadSaved() {
    savedWrap.innerHTML = '';
    let items = [];
    try { items = (await API.saved()).saved || []; } catch { return; }
    items.forEach((it) => {
      savedWrap.appendChild(el('span', { class: 'saved-chip' }, [
        el('span', { class: 'lbl', text: it.name, title: 'Apply saved search', onclick: () => applySaved(it) }),
        el('button', { class: 'x', title: 'Delete', onclick: async (e) => { e.stopPropagation(); await API.delSaved(it.id); toast('Deleted'); loadSaved(); } }, ['×']),
      ]));
    });
  }
  function applySaved(it) {
    const f = it.filters || {};
    sessionsState.q = it.query || '';
    sessionsState.sort = it.sort || 'recent';
    sessionsState.favorite = !!f.favorite;
    sessionsState.archived = f.archived || 'exclude';
    sessionsState.since = f.since || '';
    sessionsState.until = f.until || '';
    if (f.project) sessionsState.project = f.project; else delete sessionsState.project;
    sessionsState.offset = 0;
    viewSessions({});
  }
  saveBtn.addEventListener('click', async () => {
    const name = prompt('Name this saved search:', sessionsState.q || sessionsState.project || 'My search');
    if (!name) return;
    const filters = { favorite: sessionsState.favorite, archived: sessionsState.archived };
    if (sessionsState.project) filters.project = sessionsState.project;
    if (sessionsState.since) filters.since = sessionsState.since;
    if (sessionsState.until) filters.until = sessionsState.until;
    try { await API.addSaved({ name: name.trim(), query: sessionsState.q, sort: sessionsState.sort, filters }); toast('Search saved'); loadSaved(); }
    catch (e) { toast('Save failed: ' + e.message, 'err'); }
  });
  loadSaved();

  let timer;
  $('input', searchBox).addEventListener('input', (e) => { clearTimeout(timer); sessionsState.q = e.target.value; sessionsState.offset = 0; timer = setTimeout(load, 200); });
  sortSel.addEventListener('change', (e) => { sessionsState.sort = e.target.value; sessionsState.offset = 0; load(); });
  favChip.addEventListener('click', () => { sessionsState.favorite = !sessionsState.favorite; favChip.classList.toggle('on'); sessionsState.offset = 0; load(); });
  archChip.addEventListener('click', () => { sessionsState.archived = sessionsState.archived === 'only' ? 'exclude' : 'only'; archChip.classList.toggle('on'); sessionsState.offset = 0; load(); });
  sinceInp.addEventListener('change', (e) => { sessionsState.since = e.target.value; sessionsState.offset = 0; load(); });
  untilInp.addEventListener('change', (e) => { sessionsState.until = e.target.value; sessionsState.offset = 0; load(); });

  async function load() {
    listWrap.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    const q = { sort: sessionsState.sort, limit: sessionsState.limit, offset: sessionsState.offset, archived: sessionsState.archived };
    if (sessionsState.q) q.q = sessionsState.q;
    if (sessionsState.favorite) q.favorite = '1';
    if (sessionsState.project) q.project = sessionsState.project;
    if (sessionsState.since) q.since = sessionsState.since;
    if (sessionsState.until) q.until = sessionsState.until;
    const data = await API.sessions(q);
    listWrap.innerHTML = '';
    if (!data.sessions.length) {
      const noFilter = !sessionsState.q && !sessionsState.favorite && !sessionsState.project && !sessionsState.since && !sessionsState.until && sessionsState.archived !== 'only';
      const firstRun = noFilter && (STATE.summary ? STATE.summary.sessions === 0 : true);
      if (firstRun) {
        listWrap.appendChild(el('div', { class: 'onboard' }, [
          el('div', { class: 'onboard-icn', text: '✦' }),
          el('div', { class: 'big', text: 'No sessions indexed yet' }),
          el('div', { class: 'onboard-sub', text: 'ClaudeStudio reads your Claude Code logs from ~/.claude/projects — entirely on this machine. Sync to index them, or explore with realistic sample data first.' }),
          el('div', { class: 'onboard-actions' }, [
            el('button', { class: 'btn-primary', onclick: () => doReindex() }, ['↻ Sync my sessions']),
          ]),
          el('div', { class: 'onboard-hint', html: 'No real sessions yet? Run <code>claudestudio demo</code> in your terminal for a synthetic workspace.' }),
          el('div', { class: 'foot-privacy', style: 'justify-content:center', html: '<svg viewBox="0 0 24 24" width="12" height="12"><path d="M12 2l8 4v6c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg> 100% local · nothing leaves your machine' }),
        ]));
      } else {
        listWrap.appendChild(el('div', { class: 'empty' }, [el('div', { class: 'big', text: 'No sessions match' }), el('div', { text: 'Try a different filter, or hit Sync to re-scan.' })]));
      }
      return;
    }
    const list = el('div', { class: 'session-list' });
    data.sessions.forEach((s) => list.appendChild(sessionRow(s)));
    listWrap.appendChild(list);
    // pager
    const pageCount = Math.ceil(data.total / sessionsState.limit);
    const page = Math.floor(sessionsState.offset / sessionsState.limit) + 1;
    listWrap.appendChild(el('div', { class: 'pager' }, [
      el('button', { onclick: () => { sessionsState.offset = Math.max(0, sessionsState.offset - sessionsState.limit); load(); }, disabled: sessionsState.offset === 0 || null, text: '← Prev' }),
      el('span', { text: `${page} / ${pageCount || 1} · ${fmt.num(data.total)} sessions` }),
      el('button', { onclick: () => { if (sessionsState.offset + sessionsState.limit < data.total) { sessionsState.offset += sessionsState.limit; load(); } }, disabled: sessionsState.offset + sessionsState.limit >= data.total || null, text: 'Next →' }),
    ]));
  }
  await load();
}

function sessionRow(s) {
  const models = (s.models || []).slice(0, 3).map((m) => {
    const f = family(m);
    return el('span', { class: `chip model-chip fam-${f}` }, [el('span', { class: 'dot' }), shortModel(m)]);
  });
  const star = s.favorite ? el('span', { class: 'star', html: '★' }) : null;
  const dot = healthDot(s.health_score);
  const row = el('div', { class: 's-row', onclick: () => go('session/' + s.session_id) }, [
    el('div', { class: 's-main' }, [
      el('div', { class: 's-title' }, [dot, star, el('span', { class: 'txt', text: s.title || 'Untitled session' })].filter(Boolean)),
      el('div', { class: 's-meta' }, [
        el('span', { class: 'proj-chip', text: s.project_name || s.project }),
        el('span', { class: 'sep', text: '·' }),
        ...models.flatMap((m, i) => i ? [m] : [m]),
        s.git_branch ? el('span', { class: 'sep', text: '·' }) : null,
        s.git_branch ? el('span', { html: `<svg viewBox="0 0 24 24" width="11" height="11" style="vertical-align:-1px"><path d="M6 3v12M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 6a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 6c0 4-6 3-6 9" fill="none" stroke="currentColor" stroke-width="2"/></svg> ` + esc(s.git_branch) }) : null,
      ].filter(Boolean)),
      s.preview ? el('div', { class: 's-preview', text: s.preview }) : null,
    ].filter(Boolean)),
    el('div', { class: 's-side' }, [
      el('div', { class: 's-stats' }, [
        el('span', { html: `<b>${fmt.num(s.msg_count)}</b> msgs` }),
        el('span', { html: `<b>${fmt.num(s.tool_calls)}</b> tools` }),
      ]),
      el('div', { class: 's-cost', text: fmt.cost(s.cost_usd) }),
      el('div', { class: 's-time', text: fmt.rel(s.last_epoch) }),
    ]),
  ]);
  return row;
}

// ---- view: session detail / replay ----------------------------------------
// pull the files a session touched straight from its loaded timeline — instant,
// client-side, and consistent with the server-side ask engine's heuristic.
function briefFromTimeline(timeline) {
  const EDIT = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit']);
  const READ = new Set(['Read', 'NotebookRead']);
  const files = new Map();
  const tools = new Map();
  let errors = 0;
  for (const m of timeline) for (const t of (m.tools || [])) {
    tools.set(t.name, (tools.get(t.name) || 0) + 1);
    if (t.is_error) errors++;
    const inp = t.input || {};
    for (const key of ['file_path', 'path', 'notebook_path']) {
      const v = inp[key];
      if (typeof v === 'string' && /\.[A-Za-z0-9]{1,8}$/.test(v.replace(/\\/g, '/'))) {
        const name = v.replace(/\\/g, '/').split('/').pop();
        const f = files.get(name) || { name, ops: new Set(), seq: m.seq };
        f.ops.add(EDIT.has(t.name) ? 'edit' : READ.has(t.name) ? 'read' : 'use');
        files.set(name, f);
      }
    }
  }
  return {
    files: [...files.values()].sort((a, b) => (b.ops.has('edit') - a.ops.has('edit'))),
    tools: [...tools.entries()].sort((a, b) => b[1] - a[1]),
    errors,
  };
}

async function viewSession(id, params = {}) {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const s = await API.session(id);
  // bookmark context for this session's per-message buttons
  CURRENT_SESSION = id;
  SESSION_BOOKMARKS = {};
  try { (await API.bookmarks(id)).bookmarks.forEach((b) => { SESSION_BOOKMARKS[b.seq] = b; }); } catch (e) { /* ignore */ }
  const root = el('div', { class: 'view-pad fade-in' });

  // header
  const star = el('button', { class: 'iconbtn' + (s.favorite ? ' on' : ''), title: 'Favorite', 'aria-label': 'Toggle favorite', html: s.favorite ? '★' : '☆', onclick: async () => { const r = await API.state(id, { favorite: !s.favorite }); s.favorite = r.favorite; star.classList.toggle('on', r.favorite); star.innerHTML = r.favorite ? '★' : '☆'; toast(r.favorite ? 'Favorited' : 'Unfavorited'); } });
  const arch = el('button', { class: 'iconbtn' + (s.archived ? ' on' : ''), title: 'Archive', 'aria-label': 'Toggle archive', html: '🗄', onclick: async () => { const r = await API.state(id, { archived: !s.archived }); s.archived = r.archived; arch.classList.toggle('on', r.archived); toast(r.archived ? 'Archived' : 'Unarchived'); } });

  const askBtn = el('button', { class: 'btn-ghost accent', title: 'Ask the grounded companion about this session', onclick: () => go('ask', { session: id }) }, ['✦ Ask about this']);
  const expMd = el('button', { class: 'btn-ghost', title: 'Export to Markdown', onclick: () => { downloadFrom(API.exportUrl(id, 'md')); toast('Exported Markdown'); } }, ['⬇ .md']);
  const expHtml = el('button', { class: 'btn-ghost', title: 'Export to a standalone, shareable HTML file', onclick: () => { downloadFrom(API.exportUrl(id, 'html')); toast('Exported HTML'); } }, ['⬇ .html']);

  root.appendChild(el('div', { class: 'page-head' }, [
    el('a', { href: '#/sessions', class: 'btn-ghost', html: '← Sessions' }),
    el('div', { class: 'page-actions' }, [askBtn, expMd, expHtml, star, arch]),
  ]));

  const modelChips = (s.models || []).map((m) => { const f = family(m); return el('span', { class: `chip model-chip fam-${f}` }, [el('span', { class: 'dot' }), shortModel(m)]); });
  root.appendChild(el('div', { class: 'detail-head' }, [
    el('div', { class: 'detail-title', text: s.title || 'Untitled session' }),
    el('div', { class: 'detail-meta' }, [
      el('span', { html: `<span style="font-family:var(--mono);font-size:11px">${esc(s.project)}</span>` }),
      ...modelChips,
      el('span', { text: '·' }),
      el('span', { text: fmt.dt(s.first_epoch) }),
    ]),
    el('div', { class: 'detail-stats' }, [
      ds(fmt.num(s.msg_count), 'messages'),
      ds(fmt.num(s.user_msgs), 'prompts'),
      ds(fmt.num(s.tool_calls), 'tool calls'),
      ds(fmt.compact(s.input_tokens + s.output_tokens + s.cache_write + s.cache_read), 'tokens'),
      ds(fmt.cost(s.cost_usd), 'est. cost', 'accent'),
      ds(fmt.dur(s.duration_s), 'duration'),
    ]),
  ]));

  // v0.5.2: git context badge + health breakdown + session-level annotation
  root.appendChild(detailContext(s, id));

  // at-a-glance brief — files touched + tools, computed from the timeline
  const brief = briefFromTimeline(s.timeline);
  if (brief.files.length || brief.tools.length) {
    const chips = brief.files.slice(0, 8).map((f) => el('span', {
      class: 'file-chip' + (f.ops.has('edit') ? ' edited' : ''),
      title: [...f.ops].join(' + '),
      onclick: () => { const t = thread.children[f.seq]; if (t) spotlight(t); },
    }, [el('span', { class: 'op', text: f.ops.has('edit') ? '✎' : '◌' }), f.name]));
    root.appendChild(el('div', { class: 'brief' }, [
      el('div', { class: 'brief-row' }, [
        el('span', { class: 'brief-k', text: 'Files' }),
        brief.files.length ? el('div', { class: 'brief-chips' }, chips)
          : el('span', { class: 'brief-empty', text: 'none touched' }),
      ]),
      el('div', { class: 'brief-row' }, [
        el('span', { class: 'brief-k', text: 'Tools' }),
        el('div', { class: 'brief-chips' }, brief.tools.slice(0, 6).map(([n, c]) =>
          el('span', { class: 'tag-pill', text: `${n} ×${c}` }))),
        brief.errors ? el('span', { class: 'brief-err', text: `${brief.errors} error${brief.errors > 1 ? 's' : ''}` }) : null,
      ].filter(Boolean)),
    ]));
  }

  // v0.6.0: GitHub issue/PR references detected in this session (read-only links)
  const ghCard = githubRefsCard(s.github_refs || []);
  if (ghCard) root.appendChild(ghCard);

  // v0.6.0: cross-session references — prompts that point at an earlier session
  const xrefCard = crossRefCard(s.timeline, id);
  if (xrefCard) root.appendChild(xrefCard);

  // replay bar
  const replay = buildReplay(s.timeline);
  root.appendChild(replay.bar);
  root.appendChild(el('div', { class: 'replay-hint', html: '<kbd>Space</kbd> play · <kbd>J</kbd> / <kbd>K</kbd> step · <kbd>&lt;</kbd> / <kbd>&gt;</kbd> speed · <kbd>Home</kbd> / <kbd>End</kbd> jump · ⚠ jump to first error' }));

  // thread — one node per message (keeps thread.children[i] aligned to seq, which
  // the scrubber, file chips and deep-links all rely on). Consecutive same-role
  // turns are flagged `group-cont` so CSS can merge them into one visual block.
  const thread = el('div', { class: 'thread' });
  let prevRole = null;
  s.timeline.forEach((m, i) => {
    thread.appendChild(turnNode(m, i, m.role === prevRole));
    prevRole = m.role;
  });
  root.appendChild(thread);
  root.appendChild(replay.summary);
  replay.attach(thread);

  view().innerHTML = ''; view().appendChild(root);

  // keyboard navigation: a cursor that steps through messages independently of
  // the replay reveal state, so you can read a long transcript hands-on-keyboard.
  let cursor = -1;
  function moveCursor(to) {
    const n = thread.children.length;
    if (!n) return;
    to = Math.max(0, Math.min(n - 1, to));
    if (cursor >= 0 && thread.children[cursor]) thread.children[cursor].classList.remove('cursor');
    cursor = to;
    const node = thread.children[cursor];
    node.classList.add('cursor');
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  setViewKey((e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || CMDK.open) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea' || e.target.isContentEditable) return;
    const k = e.key;
    if (k === 'j' || k === 'PageDown') { e.preventDefault(); moveCursor(cursor < 0 ? 0 : cursor + 1); }
    else if (k === 'k' || k === 'PageUp') { e.preventDefault(); moveCursor(cursor < 0 ? 0 : cursor - 1); }
    else if (k === 'Home') { e.preventDefault(); moveCursor(0); }
    else if (k === 'End') { e.preventDefault(); moveCursor(thread.children.length - 1); }
    // replay transport (these must not fire while typing — guarded above)
    else if (k === ' ') { e.preventDefault(); replay.toggle(); }
    else if (k === 'ArrowLeft') { e.preventDefault(); replay.stepBack(); }
    else if (k === 'ArrowRight') { e.preventDefault(); replay.stepForward(); }
    else if (k === '<' || k === ',') { e.preventDefault(); replay.speedDown(); }
    else if (k === '>' || k === '.') { e.preventDefault(); replay.speedUp(); }
    else if (k === 'e' || k === 'E') { e.preventDefault(); replay.jumpToError(); }
    else if (k === 'b' || k === 'B') { e.preventDefault(); if (cursor >= 0) openBookmarkPopover(cursor, null); }
  });

  // when arriving from a search result, light up the matched terms in the body
  if (params.q) markTerms(thread, params.q);

  // deep-link: scroll to and spotlight a specific message (from search / Ask)
  const seq = params.seq != null ? parseInt(params.seq, 10) : null;
  if (seq != null && !Number.isNaN(seq) && thread.children[seq]) {
    cursor = seq;  // keyboard nav continues from where the link landed
    requestAnimationFrame(() => spotlight(thread.children[seq]));
  }
}

// wrap occurrences of the query terms inside each message body with <mark.hit>.
// Operates on textContent then re-escapes, so it can never inject markup.
function markTerms(container, q) {
  const terms = String(q || '').split(/\s+/)
    .filter((t) => t.length > 1)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!terms.length) return;
  const re = new RegExp('(' + terms.join('|') + ')', 'gi');
  container.querySelectorAll('.msg-text').forEach((node) => {
    const txt = node.textContent;
    if (!txt) return;
    const marked = esc(txt).replace(re, '<mark class="hit">$1</mark>');
    if (marked !== esc(txt)) node.innerHTML = marked;
  });
}

function spotlight(node) {
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  node.classList.add('flash');
  setTimeout(() => node.classList.remove('flash'), 1600);
}

function ds(v, k, cls = '') { return el('div', { class: 'ds' }, [el('span', { class: 'v ' + cls, text: v }), el('span', { class: 'k', text: k })]); }

const TOOL_ICON = { Read: '📖', Edit: '✏️', Write: '📝', Bash: '⌨️', Grep: '🔎', Glob: '🗂', Task: '🤖', WebSearch: '🌐', WebFetch: '🌐', PowerShell: '⌨️' };
function turnNode(m, i, cont = false) {
  const isUser = m.role === 'user';
  const node = el('div', { class: `turn ${m.role} ${cont ? 'group-cont' : 'group-start'}`, 'data-i': i });
  node.appendChild(el('div', { class: 'avatar ' + (isUser ? 'user' : 'assistant'), text: isUser ? 'U' : 'C' }));
  const bubble = el('div', { class: 'bubble' });
  const who = el('div', { class: 'who' }, [el('b', { text: isUser ? 'You' : 'Claude' })]);
  if (m.model) who.appendChild(el('span', { class: 'usage-tag', text: shortModel(m.model) }));
  if (!isUser && (m.input_tokens || m.output_tokens)) who.appendChild(el('span', { class: 'usage-tag', text: `${fmt.compact(m.input_tokens)}→${fmt.compact(m.output_tokens)} tok` }));
  if (m.cost_usd) who.appendChild(el('span', { class: 'usage-tag', text: fmt.cost(m.cost_usd) }));
  who.appendChild(bookmarkButton(i));
  bubble.appendChild(who);
  if (m.text) bubble.appendChild(el('div', { class: 'msg-text', text: m.text }));
  if (m.thinking) {
    bubble.appendChild(el('details', { class: 'thinking' }, [
      el('summary', { text: '✦ thinking' }),
      el('div', { class: 'tk', text: m.thinking }),
    ]));
  }
  (m.tools || []).forEach((t) => bubble.appendChild(toolCard(t)));
  node.appendChild(bubble);
  return node;
}

function toolCard(t) {
  const icon = TOOL_ICON[t.name] || '⚙️';
  const argText = Object.entries(t.input || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n');
  const head = el('div', { class: 'tool-head' }, [
    el('span', { class: 'ticon', text: icon }),
    el('span', { class: 'tname', text: t.name }),
    t.is_error ? el('span', { class: 'terr', text: '● error' }) : el('span', { class: 'tok', text: '● ok' }),
  ]);
  const body = el('div', { class: 'tool-body' });
  // Inline unified diff (edit/create tools): default to the diff, with a Diff/Raw
  // toggle back to the original tool-call args. Falls back to args when no diff.
  if (t.diff) {
    const diffView = diffNode(t.diff, t.diff_truncated);
    const rawView = el('div', { class: 'tool-arg', text: argText.slice(0, 1200), style: 'display:none' });
    const toggle = el('button', { class: 'diff-toggle', 'aria-label': 'Toggle diff and raw view', text: 'Raw' });
    let showingDiff = true;
    toggle.addEventListener('click', () => {
      showingDiff = !showingDiff;
      diffView.style.display = showingDiff ? '' : 'none';
      rawView.style.display = showingDiff ? 'none' : '';
      toggle.textContent = showingDiff ? 'Raw' : 'Diff';
    });
    head.appendChild(toggle);
    body.appendChild(diffView);
    body.appendChild(rawView);
  } else {
    if (argText) body.appendChild(el('div', { class: 'tool-arg', text: argText.slice(0, 1200) }));
  }
  if (t.result_preview) body.appendChild(el('div', { class: 'tool-result', text: t.result_preview.slice(0, 600) }));
  return el('div', { class: 'tool-card' + (t.is_error ? ' error' : '') }, [head, body]);
}

// Render a unified-diff string as a colored <pre>, escaping every line so a `<`
// in the code can never inject markup. + lines green, - lines red, @@ muted.
function diffNode(diff, truncated) {
  const pre = el('pre', { class: 'diff-view' });
  String(diff).split('\n').forEach((line) => {
    let cls = 'dl';
    if (line.startsWith('+') && !line.startsWith('+++')) cls = 'dl add';
    else if (line.startsWith('-') && !line.startsWith('---')) cls = 'dl del';
    else if (line.startsWith('@@')) cls = 'dl hunk';
    else if (line.startsWith('+++') || line.startsWith('---')) cls = 'dl meta';
    pre.appendChild(el('span', { class: cls, text: line + '\n' }));
  });
  if (truncated) pre.appendChild(el('span', { class: 'dl meta', text: '… diff truncated\n' }));
  return pre;
}

// ---- per-message bookmarks ------------------------------------------------
function bookmarkButton(seq) {
  const on = !!SESSION_BOOKMARKS[seq];
  const btn = el('button', {
    class: 'bm-btn' + (on ? ' on' : ''),
    'aria-label': on ? 'Edit bookmark on this message' : 'Bookmark this message',
    title: 'Bookmark (B)', html: on ? '🔖' : '🏷',
  });
  btn.addEventListener('click', (e) => { e.stopPropagation(); openBookmarkPopover(seq, btn); });
  return btn;
}

let _bmPop = null;
function closeBookmarkPopover() { if (_bmPop) { _bmPop.remove(); _bmPop = null; } }

function openBookmarkPopover(seq, btn) {
  closeBookmarkPopover();
  if (!CURRENT_SESSION) return;
  const existing = SESSION_BOOKMARKS[seq];
  const input = el('input', { type: 'text', placeholder: 'note (optional)', value: existing ? existing.note : '' });
  const save = el('button', { class: 'btn-ghost accent', text: existing ? 'Update' : 'Save' });
  const remove = existing ? el('button', { class: 'btn-ghost', text: 'Remove' }) : null;
  const pop = el('div', { class: 'bm-pop', role: 'dialog', 'aria-label': 'Bookmark note' }, [
    input, el('div', { class: 'bm-pop-actions' }, [save, remove].filter(Boolean)),
  ]);
  _bmPop = pop;
  document.body.appendChild(pop);
  const refBtn = btn || $(`.turn[data-i="${seq}"] .bm-btn`);
  if (refBtn) {
    const r = refBtn.getBoundingClientRect();
    pop.style.top = (r.bottom + 6) + 'px';
    pop.style.left = Math.max(8, r.left - 120) + 'px';
  }
  input.focus();
  const doSave = async () => {
    try {
      if (existing) await API.delBookmark(existing.id);
      const r = await API.addBookmark(CURRENT_SESSION, { seq, note: input.value });
      SESSION_BOOKMARKS[seq] = r;
      const b = btn || $(`.turn[data-i="${seq}"] .bm-btn`);
      if (b) { b.classList.add('on'); b.innerHTML = '🔖'; }
      toast('Bookmarked');
    } catch (e) { toast('Could not save bookmark', 'err'); }
    closeBookmarkPopover();
  };
  save.addEventListener('click', doSave);
  if (remove) remove.addEventListener('click', async () => {
    try { await API.delBookmark(existing.id); delete SESSION_BOOKMARKS[seq]; const b = btn || $(`.turn[data-i="${seq}"] .bm-btn`); if (b) { b.classList.remove('on'); b.innerHTML = '🏷'; } toast('Bookmark removed'); } catch (e) { toast('Could not remove', 'err'); }
    closeBookmarkPopover();
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSave(); else if (e.key === 'Escape') closeBookmarkPopover(); });
}

// ---- view: bookmarks ------------------------------------------------------
async function viewBookmarks() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const { bookmarks } = await API.bookmarks();
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Bookmarks' }),
    el('div', { class: 'page-sub', text: 'Starred moments inside your sessions — jump straight back to them' }),
  ])]));
  if (!bookmarks.length) {
    root.appendChild(el('div', { class: 'empty' }, [
      el('div', { class: 'big', text: 'No bookmarks yet' }),
      el('div', { text: 'Open a session and click the 🏷 next to any message to bookmark it.' }),
    ]));
  } else {
    const list = el('div', { class: 'bm-list' });
    bookmarks.forEach((b) => {
      const row = el('div', { class: 'bm-row', role: 'button', tabindex: '0' }, [
        el('div', { class: 'bm-main' }, [
          el('div', { class: 'bm-title', text: b.session_title || b.session_id }),
          b.note ? el('div', { class: 'bm-note', text: b.note }) : null,
          el('div', { class: 'bm-meta', text: `message #${b.seq} · ${fmt.rel(b.created_epoch)}` }),
        ].filter(Boolean)),
        el('button', { class: 'x', title: 'Delete bookmark', 'aria-label': 'Delete bookmark', text: '×', onclick: async (e) => { e.stopPropagation(); await API.delBookmark(b.id); toast('Deleted'); viewBookmarks(); } }),
      ]);
      const open = () => go('session/' + b.session_id, { seq: b.seq });
      row.addEventListener('click', open);
      row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
      list.appendChild(row);
    });
    root.appendChild(list);
  }
  view().innerHTML = ''; view().appendChild(root);
}

// speed steps for replay auto-advance. ∞ (Infinity) reveals instantly.
const REPLAY_SPEEDS = [0.5, 1, 2, 5, Infinity];
const speedLabel = (sp) => (sp === Infinity ? '∞' : sp + '×');

// index of the first message carrying a tool error / exception trace, or -1.
function firstErrorIndex(timeline) {
  for (let i = 0; i < timeline.length; i++) {
    const m = timeline[i];
    if ((m.tools || []).some((t) => t.is_error)) return i;
    if (/\b(traceback|exception|error:|fatal:)\b/i.test(m.text || '')) return i;
  }
  return -1;
}

function replaySummaryCard(timeline) {
  const tools = timeline.reduce((a, m) => a + (m.tools || []).length, 0);
  const errors = timeline.reduce((a, m) => a + (m.tools || []).filter((t) => t.is_error).length, 0);
  const prompts = timeline.filter((m) => m.role === 'user').length;
  const out = timeline.reduce((a, m) => a + (m.output_tokens || 0), 0);
  return el('div', { class: 'replay-summary', role: 'status', hidden: '' }, [
    el('div', { class: 'rs-title', text: '✦ Session replay complete' }),
    el('div', { class: 'rs-stats' }, [
      el('span', {}, [el('b', { text: String(timeline.length) }), ' messages']),
      el('span', {}, [el('b', { text: String(prompts) }), ' prompts']),
      el('span', {}, [el('b', { text: String(tools) }), ' tool calls']),
      el('span', { class: errors ? 'rs-err' : '' }, [el('b', { text: String(errors) }), ' errors']),
      el('span', {}, [el('b', { text: fmt.compact(out) }), ' output tokens']),
    ]),
  ]);
}

function buildReplay(timeline) {
  const n = timeline.length;
  let idx = n, playing = false, speedIdx = 2, timer = null, threadEl = null;
  const speed = () => REPLAY_SPEEDS[speedIdx];
  const errAt = firstErrorIndex(timeline);
  const restartBtn = el('button', { class: 'replay-btn', title: 'Restart', 'aria-label': 'Restart replay', html: ICON.restart });
  const prevBtn = el('button', { class: 'replay-btn', title: 'Step back', 'aria-label': 'Step back', html: ICON.prev });
  const playBtn = el('button', { class: 'play-btn', title: 'Play / pause', 'aria-label': 'Play or pause replay', html: playIcon(false) });
  const nextBtn = el('button', { class: 'replay-btn', title: 'Step forward', 'aria-label': 'Step forward', html: ICON.next });
  const jumpErrBtn = el('button', { class: 'replay-btn jump-error', title: 'Jump to first error', 'aria-label': 'Jump to first error', html: '⚠', disabled: errAt < 0 ? '' : null });
  const track = el('div', { class: 'replay-track', role: 'slider', tabindex: '0', 'aria-label': 'Replay position', 'aria-valuemin': '0', 'aria-valuemax': String(n) }, [el('div', { class: 'replay-prog' }), el('div', { class: 'replay-knob' })]);
  const pos = el('div', { class: 'replay-pos', text: `${n}/${n}` });
  // pill-segmented speed control (0.5× / 1× / 2× / 5× / ∞)
  const speeds = REPLAY_SPEEDS.map((sp, i) => el('button', { class: 'speed-pill' + (i === speedIdx ? ' on' : ''), text: speedLabel(sp), 'aria-label': 'Playback speed ' + speedLabel(sp), onclick: () => setSpeedIdx(i) }));
  const speedCtl = el('div', { class: 'replay-speed', role: 'group', 'aria-label': 'Playback speed' }, speeds);
  const bar = el('div', { class: 'replaybar' }, [restartBtn, prevBtn, playBtn, nextBtn, jumpErrBtn, track, pos, speedCtl]);
  const summary = replaySummaryCard(timeline);

  const prog = $('.replay-prog', track), knob = $('.replay-knob', track);
  function setSpeedIdx(i) {
    speedIdx = Math.max(0, Math.min(REPLAY_SPEEDS.length - 1, i));
    speeds.forEach((b, k) => b.classList.toggle('on', k === speedIdx));
  }
  function render() {
    if (!threadEl) return;
    const partial = idx < n;  // mid-replay → mark current turn; at end → full readable thread
    [...threadEl.children].forEach((c, i) => {
      c.classList.toggle('hidden-msg', i >= idx);
      const isCur = partial && i === idx - 1;
      c.classList.toggle('replay-current', isCur);
      // typewriter reveal on the current message's text while auto-advancing
      const txt = c.querySelector('.msg-text');
      if (txt) txt.classList.toggle('replay-typewriter', isCur && playing && speed() !== Infinity);
    });
    const frac = n ? idx / n : 1;
    prog.style.width = (frac * 100) + '%'; knob.style.left = (frac * 100) + '%';
    pos.textContent = `${Math.min(idx, n)}/${n}`;
    track.setAttribute('aria-valuenow', String(Math.min(idx, n)));
    prevBtn.disabled = idx <= 0; nextBtn.disabled = idx >= n;
    summary.hidden = idx < n;  // show the wrap-up card only at the very end
  }
  function focusCurrent() {
    const target = threadEl && idx > 0 && idx <= n ? threadEl.children[idx - 1] : null;
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function step() {
    if (idx >= n) { stop(); return; }
    idx++; render(); focusCurrent();
    const sp = speed();
    if (sp === Infinity) { timer = setTimeout(step, 0); return; }
    const gap = timeline[idx - 1]?.gap_s || 0.5;
    const delay = Math.min(Math.max(gap, 0.3), 6) * 300 / sp;
    timer = setTimeout(step, delay);
  }
  function play() { if (idx >= n) { idx = 0; render(); } playing = true; playBtn.innerHTML = playIcon(true); step(); }
  function stop() { playing = false; playBtn.innerHTML = playIcon(false); clearTimeout(timer); render(); }
  function seek(target) { stop(); idx = Math.max(0, Math.min(n, target)); render(); focusCurrent(); }
  playBtn.addEventListener('click', () => (playing ? stop() : play()));
  restartBtn.addEventListener('click', () => seek(0));
  prevBtn.addEventListener('click', () => seek(idx - 1));
  nextBtn.addEventListener('click', () => seek(idx + 1));
  jumpErrBtn.addEventListener('click', () => { if (errAt >= 0) seek(errAt + 1); });
  track.addEventListener('click', (e) => { const rect = track.getBoundingClientRect(); seek(Math.round(((e.clientX - rect.left) / rect.width) * n)); });
  track.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); seek(idx - 1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); seek(idx + 1); }
    else if (e.key === 'Home') { e.preventDefault(); seek(0); }
    else if (e.key === 'End') { e.preventDefault(); seek(n); }
  });

  return {
    bar, summary,
    attach(t) { threadEl = t; idx = n; render(); },
    toggle() { playing ? stop() : play(); },
    stepBack() { seek(idx - 1); },
    stepForward() { seek(idx + 1); },
    speedUp() { setSpeedIdx(speedIdx + 1); },
    speedDown() { setSpeedIdx(speedIdx - 1); },
    jumpToError() { if (errAt >= 0) seek(errAt + 1); },
  };
}
const ICON = {
  restart: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M12 5V2L7 7l5 5V8a4 4 0 1 1-4 4H6a6 6 0 1 0 6-7z" fill="currentColor"/></svg>',
  prev: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M7 5h2v14H7zM19 5v14l-9-7z" fill="currentColor"/></svg>',
  next: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M15 5h2v14h-2zM5 5l9 7-9 7z" fill="currentColor"/></svg>',
};
function playIcon(playing) {
  return playing ? '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M7 5h4v14H7zM13 5h4v14h-4z" fill="currentColor"/></svg>'
    : '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M7 4l13 8-13 8z" fill="currentColor"/></svg>';
}

// ---- view: analytics ------------------------------------------------------
async function viewAnalytics() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const a = await API.analytics();
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Analytics' }),
    el('div', { class: 'page-sub', text: 'Tokens, cost, models and tools across everything you have done' }),
  ])]));

  const stats = el('div', { class: 'stat-grid' });
  const cards = [
    ['Sessions', fmt.num(a.sessions), `${fmt.num(a.projects)} projects`],
    ['Messages', fmt.num(a.messages), `${fmt.num(a.tool_calls)} tool calls`],
    ['Tokens', fmt.compact(a.tokens), `${fmt.compact(a.cache_read)} from cache`],
    ['Est. spend', fmt.cost(a.cost_usd), 'public model prices', 'accent'],
    ['Time', fmt.dur(a.duration_s), 'summed session spans'],
  ];
  cards.forEach(([k, v, sub, cls]) => stats.appendChild(el('div', { class: 'stat' }, [
    el('div', { class: 'k', text: k }), el('div', { class: 'v ' + (cls || ''), text: v }), el('div', { class: 'sub', text: sub }),
  ])));
  root.appendChild(stats);

  if (a.unpriced_models && a.unpriced_models.length) {
    root.appendChild(el('div', { class: 'panel', style: 'margin-bottom:14px;border-color:var(--line-2)' }, [
      el('div', { class: 'page-sub', html: `⚠︎ ${a.unpriced_models.length} model(s) have no public price and are counted as $0: <code style="font-family:var(--mono)">${a.unpriced_models.map(esc).join(', ')}</code>` }),
    ]));
  }

  // models donut + legend, tools barlist
  const modelSegs = a.by_model.map((m) => ({ value: m.cost_usd || m.tokens || 1, color: FAM_COLOR[m.family] || '#6f7585', label: shortModel(m.model), ...m }));
  const top = el('div', { class: 'grid-2 wide' }, [
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-title', text: 'Cost by model' }),
      el('div', { class: 'donut-wrap' }, [
        el('div', { html: donut(modelSegs) }),
        el('div', { class: 'legend' }, modelSegs.slice(0, 8).map((m) => el('div', { class: 'legend-row' }, [
          el('span', { class: 'dot', style: `background:${m.color}` }),
          el('span', { class: 'lname', text: shortModel(m.model) }),
          el('span', { class: 'lval', text: fmt.cost(m.cost_usd) }),
        ]))),
      ]),
    ]),
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-title', text: 'Tool usage' }),
      barList(a.by_tool.map((t) => ({ label: t.name, value: t.calls, sub: t.errors ? `${t.errors} err` : '' })), { max: Math.max(1, ...a.by_tool.map((t) => t.calls)) }),
    ]),
  ]);
  root.appendChild(top);

  // daily activity area
  root.appendChild(el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title' }, [el('span', { text: 'Daily activity' }), el('span', { class: 'usage-tag', text: `${a.daily.length} active days` })]),
    areaChart(a.daily, { h: 170, key: 'messages' }),
  ]));

  // heatmap + projects
  root.appendChild(el('div', { class: 'grid-2', style: 'margin-top:14px' }, [
    el('div', { class: 'panel' }, [el('div', { class: 'panel-title', text: 'When you work (weekday × hour)' }), heatmapNode(a.heatmap)]),
    el('div', { class: 'panel' }, [el('div', { class: 'panel-title', text: 'Busiest projects' }),
      barList(a.top_projects.map((p) => ({ label: p.project_name, value: p.sessions, sub: fmt.cost(p.cost_usd) })), { max: Math.max(1, ...a.top_projects.map((p) => p.sessions)) }),
    ]),
  ]));

  // tool latency (p50/p95/p99) — fetched separately so the page renders fast
  const latencyPanel = el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title', text: 'Tool latency (p95)' }),
    el('div', { class: 'page-sub', text: 'loading…' }),
  ]);
  root.appendChild(latencyPanel);
  API.toolLatency().then(({ latency }) => { latencyPanel.lastChild.replaceWith(latencyChart(latency)); }).catch(() => {});

  // recurring prompt patterns
  const patternsPanel = el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title', text: 'Your recurring prompts — things you ask Claude again and again' }),
    el('div', { class: 'page-sub', text: 'loading…' }),
  ]);
  root.appendChild(patternsPanel);
  API.patterns().then(({ patterns }) => { patternsPanel.lastChild.replaceWith(patternsList(patterns)); }).catch(() => {});

  // report + CSV export toolbar
  root.appendChild(reportPanel());

  view().innerHTML = ''; view().appendChild(root);
}

// horizontal latency bars: width = p95, green <1s / amber 1-5s / red >5s
function latencyChart(latency) {
  const rows = Object.entries(latency || {}).map(([name, v]) => ({ name, ...v }));
  if (!rows.length) return el('div', { class: 'empty', text: 'No timed tool calls yet' });
  const max = Math.max(1, ...rows.map((r) => r.p95_ms));
  const band = (ms) => (ms < 1000 ? 'good' : ms < 5000 ? 'warn' : 'bad');
  return el('div', { class: 'latency' }, rows.map((r) => el('div', { class: 'lat-row' }, [
    el('span', { class: 'lat-name', text: r.name }),
    el('div', { class: 'lat-track', title: `p50 ${Math.round(r.p50_ms)}ms · p95 ${Math.round(r.p95_ms)}ms · p99 ${Math.round(r.p99_ms)}ms · ${r.count} calls` }, [
      el('div', { class: 'lat-fill ' + band(r.p95_ms), style: `width:${Math.max(3, (r.p95_ms / max) * 100)}%` }),
      el('div', { class: 'lat-tick', style: `left:${(r.p50_ms / max) * 100}%` }),
    ]),
    el('span', { class: 'lat-val', text: fmtMs(r.p95_ms) }),
  ])));
}
function fmtMs(ms) { return ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms'; }

function patternsList(patterns) {
  if (!patterns || !patterns.length) return el('div', { class: 'empty', text: 'No recurring prompts found yet' });
  return el('div', { class: 'patterns' }, patterns.map((p) => el('div', { class: 'pattern-row' }, [
    el('div', { class: 'pat-main' }, [
      el('div', { class: 'pat-text', text: p.canonical_text.length > 120 ? p.canonical_text.slice(0, 120) + '…' : p.canonical_text }),
      el('div', { class: 'pat-meta', text: `asked ${p.count}× · last ${fmt.rel(p.last_seen_epoch)}` }),
    ]),
    el('button', { class: 'btn-ghost', title: 'Copy this prompt', 'aria-label': 'Copy prompt', text: 'Copy', onclick: () => { navigator.clipboard?.writeText(p.canonical_text).then(() => toast('Prompt copied')); } }),
  ])));
}

function reportPanel() {
  const today = new Date();
  const monday = new Date(today); monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const iso = (d) => d.toISOString().slice(0, 10);
  const since = el('input', { type: 'date', value: iso(monday), 'aria-label': 'Report start date' });
  const until = el('input', { type: 'date', value: iso(today), 'aria-label': 'Report end date' });
  const dl = el('button', { class: 'btn-ghost accent', text: '⬇ Download HTML report', onclick: () => { downloadFrom(API.reportUrl(since.value, until.value, 'html')); toast('Report generated'); } });
  return el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title', text: 'Generate report' }),
    el('div', { class: 'report-bar' }, [
      el('label', { text: 'From' }), since,
      el('label', { text: 'to' }), until,
      dl,
      el('button', { class: 'btn-ghost', text: '⬇ Analytics CSV', onclick: () => { downloadFrom(API.analyticsCsvUrl()); toast('CSV exported'); } }),
      el('button', { class: 'btn-ghost', text: '⬇ Sessions CSV', onclick: () => { downloadFrom(API.sessionsCsvUrl()); toast('CSV exported'); } }),
    ]),
  ]);
}

function barList(rows, { max }) {
  return el('div', { class: 'barlist' }, rows.map((r) => el('div', { class: 'barrow' }, [
    el('span', { class: 'lbl', text: r.label }),
    el('div', { class: 'bartrack' }, [el('div', { class: 'barfill', style: `width:${Math.max(2, (r.value / max) * 100)}%` })]),
    el('span', { class: 'val', html: `${fmt.num(r.value)}${r.sub ? ` <small>${esc(r.sub)}</small>` : ''}` }),
  ])));
}

function heatmapNode(grid) {
  const days = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const max = Math.max(1, ...grid.flat());
  const labels = el('div', { class: 'heat-labels' }, days.map((d) => el('span', { text: d })));
  const weeks = el('div', { class: 'heat-grid' }, grid.map((row) => el('div', { class: 'heat-week' }, row.map((v) => {
    const a = v ? 0.15 + 0.85 * (v / max) : 0;
    return el('div', { class: 'heat-cell', style: v ? `background:rgba(255,138,91,${a.toFixed(2)})` : '', title: `${v}` });
  }))));
  return el('div', {}, [
    el('div', { class: 'heatmap' }, [labels, weeks]),
    el('div', { class: 'heat-foot' }, [el('span', { text: '12am' }), el('span', { text: '6am' }), el('span', { text: '12pm' }), el('span', { text: '6pm' }), el('span', { text: '11pm' })]),
  ]);
}

// ---- view: projects -------------------------------------------------------
async function viewProjects() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const { projects } = await API.projects();
  const a = await API.analytics().catch(() => ({ daily: [] }));
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Projects' }),
    el('div', { class: 'page-sub', text: `${projects.length} repositories Claude has touched` }),
  ])]));
  const grid = el('div', { class: 'proj-grid' });
  const maxC = Math.max(1, ...projects.map((p) => p.cost_usd));
  projects.forEach((p) => {
    grid.appendChild(el('div', { class: 'proj-card', onclick: () => { sessionsState.project = p.project; sessionsState.offset = 0; go('sessions', { project: p.project }); } }, [
      el('div', {}, [el('div', { class: 'proj-name', text: p.project_name || p.project }), el('div', { class: 'proj-path', text: p.project })]),
      el('div', { html: spark(new Array(12).fill(0).map((_, i) => (i + 1) * (p.sessions / 12) + Math.sin(i) * 2), { color: 'var(--accent)' }) }),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Sessions' }), el('b', { text: fmt.num(p.sessions) })]),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Messages' }), el('b', { text: fmt.num(p.messages) })]),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Est. cost' }), el('b', { text: fmt.cost(p.cost_usd) })]),
      el('div', { class: 'proj-row' }, [el('span', { text: 'Last active' }), el('b', { text: fmt.rel(p.last_epoch) })]),
    ]));
  });
  root.appendChild(grid);
  view().innerHTML = ''; view().appendChild(root);
}

// ---- view: timeline -------------------------------------------------------
async function viewTimeline() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const a = await API.analytics();
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Timeline' }),
    el('div', { class: 'page-sub', text: 'Your activity over time' }),
  ])]));

  root.appendChild(el('div', { class: 'panel' }, [
    el('div', { class: 'panel-title' }, [el('span', { text: 'Messages per day' }), el('span', { class: 'usage-tag', text: `${a.daily.length} days` })]),
    el('div', { class: 'tl-chart' }, [areaChart(a.daily, { h: 200, key: 'messages' })]),
  ]));
  root.appendChild(el('div', { class: 'panel', style: 'margin-top:14px' }, [
    el('div', { class: 'panel-title', text: 'Spend per day' }),
    el('div', { class: 'tl-chart' }, [areaChart(a.daily, { h: 200, key: 'cost_usd', color: 'var(--violet)' })]),
  ]));

  // month buckets
  const byMonth = {};
  a.daily.forEach((d) => { const mk = d.date.slice(0, 7); (byMonth[mk] = byMonth[mk] || { month: mk, sessions: 0, messages: 0, cost_usd: 0 }); byMonth[mk].sessions += d.sessions; byMonth[mk].messages += d.messages; byMonth[mk].cost_usd += d.cost_usd; });
  const months = Object.values(byMonth).sort((x, y) => y.month.localeCompare(x.month));
  const list = el('div', { class: 'session-list', style: 'margin-top:18px' });
  months.forEach((m) => {
    const dt = new Date(m.month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    list.appendChild(el('div', { class: 's-row', style: 'cursor:default' }, [
      el('div', { class: 's-main' }, [el('div', { class: 's-title' }, [el('span', { class: 'txt', text: dt })])]),
      el('div', { class: 's-side' }, [el('div', { class: 's-stats' }, [
        el('span', { html: `<b>${fmt.num(m.sessions)}</b> sessions` }),
        el('span', { html: `<b>${fmt.num(m.messages)}</b> msgs` }),
      ]), el('div', { class: 's-cost', text: fmt.cost(m.cost_usd) })]),
    ]));
  });
  root.appendChild(list);
  view().innerHTML = ''; view().appendChild(root);
}

// ---- view: compare --------------------------------------------------------
async function viewCompare(params) {
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Compare' }),
    el('div', { class: 'page-sub', text: 'Put two sessions side by side' }),
  ])]));
  view().innerHTML = ''; view().appendChild(root);

  const recent = (await API.sessions({ sort: 'recent', limit: 200 })).sessions;
  let a = params.a || '', b = params.b || '';
  const out = el('div', {});
  const mkPicker = (which, val) => {
    const sel = el('select', { class: 'input', style: 'width:100%' }, [el('option', { value: '', text: `Choose session ${which}…` }), ...recent.map((s) => el('option', { value: s.session_id, text: `${(s.title || 'Untitled').slice(0, 50)} · ${s.project_name}`, ...(s.session_id === val ? { selected: 'selected' } : {}) }))]);
    sel.addEventListener('change', (e) => { if (which === 'A') a = e.target.value; else b = e.target.value; draw(); });
    return sel;
  };
  root.appendChild(el('div', { class: 'cmp-pick' }, [mkPicker('A', a), mkPicker('B', b)]));
  root.appendChild(out);

  async function draw() {
    if (!a || !b) { out.innerHTML = '<div class="empty">Pick two sessions to compare.</div>'; return; }
    out.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    const data = await API.compare(a, b);
    const A = data.a, B = data.b;
    if (!A || !B) { out.innerHTML = '<div class="empty">Session not found.</div>'; return; }
    const rows = [
      ['Messages', A.msg_count, B.msg_count], ['Prompts', A.user_msgs, B.user_msgs],
      ['Tool calls', A.tool_calls, B.tool_calls], ['Input tokens', A.input_tokens, B.input_tokens],
      ['Output tokens', A.output_tokens, B.output_tokens], ['Cache read', A.cache_read, B.cache_read],
      ['Est. cost', A.cost_usd, B.cost_usd, true], ['Duration (s)', A.duration_s, B.duration_s],
    ];
    const panel = el('div', { class: 'panel' }, [
      el('div', { class: 'cmp-row' }, [el('span', { class: 'k', text: '' }), el('span', { class: 'a', text: (A.title || '').slice(0, 28) }), el('span', { class: 'b', text: (B.title || '').slice(0, 28) }), el('span', {})]),
      ...rows.map(([k, av, bv, money]) => {
        const fmtv = money ? fmt.cost : fmt.num;
        return el('div', { class: 'cmp-row' }, [
          el('span', { class: 'k', text: k }),
          el('span', { class: 'a' + (av > bv ? ' win' : ''), text: fmtv(av) }),
          el('span', { class: 'b' + (bv > av ? ' win' : ''), text: fmtv(bv) }),
          el('span', { class: 'usage-tag', text: av === bv ? '=' : (av > bv ? '◀' : '▶') }),
        ]);
      }),
    ]);
    out.innerHTML = ''; out.appendChild(panel);
  }
  draw();
}

// ---- view: wrapped --------------------------------------------------------
async function viewWrapped(params) {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const data = await API.wrapped(params.year);
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head', style: 'justify-content:center' }, [el('div', { style: 'text-align:center' }, [
    el('h1', { class: 'page-title', text: '✦ Claude Wrapped' }),
    el('div', { class: 'page-sub', text: data.label }),
  ])]));

  const yrs = el('div', { class: 'wrap-year' }, [
    el('button', { class: 'chip toggle' + (!params.year ? ' on' : ''), text: 'All time', onclick: () => go('wrapped') }),
    ...data.available_years.map((y) => el('button', { class: 'chip toggle' + (String(y) === String(params.year) ? ' on' : ''), text: String(y), onclick: () => go('wrapped', { year: y }) })),
  ]);
  root.appendChild(yrs);

  const wrap = el('div', { class: 'wrapped' });
  const stage = el('div', { class: 'wrap-stage' });
  const cards = data.cards.map((c, i) => el('div', { class: 'wrap-card' + (i === 0 ? ' show' : '') }, [
    el('div', { class: 'icn', text: c.icon }),
    el('div', { class: 'big', text: c.value }),
    el('div', { class: 'lab', text: c.label }),
    el('div', { class: 'sub', text: c.sub }),
  ]));
  cards.forEach((c) => stage.appendChild(c));
  const dots = el('div', { class: 'wrap-dots' }, cards.map((_, i) => el('span', { class: i === 0 ? 'on' : '', onclick: () => show(i) })));
  let cur = 0;
  function show(i) { cur = (i + cards.length) % cards.length; cards.forEach((c, j) => c.classList.toggle('show', j === cur)); $$('.wrap-dots span', wrap).forEach((d, j) => d.classList.toggle('on', j === cur)); }
  const nav = el('div', { class: 'wrap-nav' }, [
    el('button', { text: '← Back', onclick: () => show(cur - 1) }),
    el('button', { text: 'Copy summary', onclick: () => { const txt = `My Claude Code — ${data.label}\n` + data.cards.map((c) => `${c.icon} ${c.value} — ${c.label}`).join('\n'); navigator.clipboard?.writeText(txt).then(() => toast('Summary copied')); } }),
    el('button', { text: '⬇ Save card', title: 'Download a shareable PNG', onclick: () => saveWrappedCard(data) }),
    el('button', { text: 'Next →', onclick: () => show(cur + 1) }),
  ]);
  wrap.append(stage, dots, nav);
  root.appendChild(wrap);
  view().innerHTML = ''; view().appendChild(root);
  let auto = setInterval(() => show(cur + 1), 4200);
  stage.addEventListener('click', () => { clearInterval(auto); show(cur + 1); });
}

// render the Wrapped highlights to a shareable PNG (pure canvas, no deps)
function saveWrappedCard(data) {
  const W = 1080, H = 1350, FONT = "'Segoe UI', system-ui, -apple-system, Roboto, sans-serif";
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const x = cv.getContext('2d');

  const bg = x.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, '#16131b'); bg.addColorStop(1, '#0d0e12');
  x.fillStyle = bg; x.fillRect(0, 0, W, H);
  const glow = x.createRadialGradient(W / 2, 220, 30, W / 2, 220, 680);
  glow.addColorStop(0, 'rgba(255,138,91,0.22)'); glow.addColorStop(1, 'rgba(255,138,91,0)');
  x.fillStyle = glow; x.fillRect(0, 0, W, H);

  x.textAlign = 'center';
  x.fillStyle = '#ff8a5b';
  x.font = '700 38px ' + FONT;
  x.fillText('✦  CLAUDE WRAPPED', W / 2, 150);
  x.fillStyle = '#e7e9ee';
  x.font = '800 78px ' + FONT;
  x.fillText(String(data.label || 'All time'), W / 2, 248);

  const rr = (px, py, pw, ph, r) => {
    x.beginPath();
    x.moveTo(px + r, py);
    x.arcTo(px + pw, py, px + pw, py + ph, r);
    x.arcTo(px + pw, py + ph, px, py + ph, r);
    x.arcTo(px, py + ph, px, py, r);
    x.arcTo(px, py, px + pw, py, r);
    x.closePath();
  };

  const picks = (data.cards || []).slice(0, 6);
  const M = 60, GAP = 36, COLS = 2;
  const cw = (W - M * 2 - GAP * (COLS - 1)) / COLS;
  const ch = 250, top = 330;
  picks.forEach((c, i) => {
    const col = i % COLS, row = (i / COLS) | 0;
    const px = M + col * (cw + GAP), py = top + row * (ch + GAP);
    rr(px, py, cw, ch, 26);
    x.fillStyle = 'rgba(255,255,255,0.035)'; x.fill();
    x.lineWidth = 1.5; x.strokeStyle = 'rgba(255,255,255,0.07)'; x.stroke();
    x.textAlign = 'center';
    x.font = '38px ' + FONT;
    x.fillStyle = '#fff';
    x.fillText(String(c.icon || ''), px + cw / 2, py + 78);
    x.fillStyle = '#e7e9ee';
    let v = String(c.value || ''); x.font = '800 56px ' + FONT;
    while (x.measureText(v).width > cw - 48 && v.length > 6) { v = v.slice(0, -2) + '…'; }
    x.fillText(v, px + cw / 2, py + 150);
    x.fillStyle = '#aab0bd';
    x.font = '500 24px ' + FONT;
    let lab = String(c.label || '');
    while (x.measureText(lab).width > cw - 40 && lab.length > 6) { lab = lab.slice(0, -2) + '…'; }
    x.fillText(lab, px + cw / 2, py + 196);
  });

  x.textAlign = 'center';
  x.fillStyle = '#9a8cff';
  x.font = '700 30px ' + FONT;
  x.fillText('ClaudeStudio', W / 2, H - 86);
  x.fillStyle = '#727887';
  x.font = '400 22px ' + FONT;
  x.fillText('the desktop workspace for Claude Code · 100% local', W / 2, H - 48);

  cv.toBlob((blob) => {
    if (!blob) { toast('Could not render card', 'err'); return; }
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: `claude-wrapped-${String(data.label || 'alltime').replace(/\s+/g, '-').toLowerCase()}.png` });
    document.body.appendChild(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 100);
    toast('Saved Wrapped card');
  }, 'image/png');
}

// ---- view: search (full page) ---------------------------------------------
const KINDS = [['', 'Any role'], ['user', 'Prompts'], ['assistant', 'Responses'], ['tool', 'Tool calls']];
async function viewSearch(params) {
  const root = el('div', { class: 'view-pad fade-in' });
  const sub = el('div', { class: 'page-sub', text: 'Search every prompt, response, and tool call' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Search' }), sub,
  ])]));

  // ---- controls (text + structured filters the backend already understands) --
  const qBox = el('div', { class: 'search-box' }, [
    el('span', { html: '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M21 21l-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' }),
    el('input', { class: 'input', placeholder: 'Search prompts, responses, tool calls…', value: params.q || '', autocomplete: 'off' }),
  ]);
  const qInput = $('input', qBox);
  const kindSel = el('select', { class: 'input' }, KINDS.map(([v, l]) => el('option', { value: v, text: l, ...(v === (params.kind || '') ? { selected: 'selected' } : {}) })));
  const projInput = el('input', { class: 'input', placeholder: 'Project…', value: params.project || '', style: 'width:150px' });
  const sinceInput = el('input', { class: 'input', type: 'date', title: 'On or after', value: params.since || '' });
  const untilInput = el('input', { class: 'input', type: 'date', title: 'On or before', value: params.until || '' });
  const clearBtn = el('button', { class: 'chip toggle', title: 'Clear filters' }, ['Clear']);

  root.appendChild(el('div', { class: 'toolbar search-filters' }, [
    qBox, kindSel,
    el('div', { class: 'field' }, [el('span', { class: 'field-k', text: 'in' }), projInput]),
    el('div', { class: 'field' }, [el('span', { class: 'field-k', text: 'from' }), sinceInput, el('span', { class: 'field-k', text: 'to' }), untilInput]),
    clearBtn,
  ]));

  const list = el('div', { class: 'session-list' });
  root.appendChild(list);
  view().innerHTML = ''; view().appendChild(root);

  // run() fetches with the current control values and rewrites the URL *without*
  // a re-render (replaceState fires no hashchange), so the inputs keep focus and
  // the search stays shareable/bookmarkable.
  async function run() {
    const q = qInput.value.trim();
    const filters = { kind: kindSel.value, project: projInput.value.trim(), since: sinceInput.value, until: untilInput.value };
    const qs = new URLSearchParams();
    if (q) qs.set('q', q);
    for (const k of ['kind', 'project', 'since', 'until']) if (filters[k]) qs.set(k, filters[k]);
    history.replaceState(null, '', '#/search' + (qs.toString() ? '?' + qs : ''));

    if (!q) {
      sub.textContent = 'Search every prompt, response, and tool call';
      list.innerHTML = ''; list.appendChild(el('div', { class: 'empty', text: 'Type to search your sessions…' }));
      return;
    }
    list.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    let res;
    try { res = await API.search(q, 80, filters); }
    catch (e) { list.innerHTML = ''; list.appendChild(el('div', { class: 'empty', text: 'Search failed: ' + (e.message || e) })); return; }
    const n = res.results.length;
    sub.innerHTML = `<b>${n}</b> match${n === 1 ? '' : 'es'} for <b>${esc(q)}</b>`;
    list.innerHTML = '';
    if (!n) { list.appendChild(el('div', { class: 'empty' }, [el('div', { class: 'big', text: 'No matches' }), el('div', { text: 'Try fewer words or relax the filters.' })])); return; }
    res.results.forEach((r) => list.appendChild(searchResultRow(r, q)));
  }

  let t;
  const debounced = () => { clearTimeout(t); t = setTimeout(run, 200); };
  qInput.addEventListener('input', debounced);
  projInput.addEventListener('input', debounced);
  kindSel.addEventListener('change', run);
  sinceInput.addEventListener('change', run);
  untilInput.addEventListener('change', run);
  clearBtn.addEventListener('click', () => { kindSel.value = ''; projInput.value = ''; sinceInput.value = ''; untilInput.value = ''; run(); qInput.focus(); });

  qInput.focus();
  run();
}

function searchResultRow(r, q) {
  const snip = esc(r.snip || '').replace(/⟦/g, '<mark>').replace(/⟧/g, '</mark>');
  const params = {};
  if (r.seq != null) params.seq = r.seq;
  if (q) params.q = q;  // carry the query so the replay view highlights it too
  return el('div', { class: 's-row', onclick: () => go('session/' + r.session_id, params) }, [
    el('div', { class: 's-main' }, [
      el('div', { class: 's-title' }, [el('span', { class: 'txt', text: r.title || 'Untitled' })]),
      el('div', { class: 's-preview snip', html: snip }),
      el('div', { class: 's-meta' }, [el('span', { class: 'proj-chip', text: r.project_name }), el('span', { class: 'tag-pill', text: r.kind })]),
    ]),
    el('div', { class: 's-side' }, [el('div', { class: 's-time', text: fmt.rel(r.last_epoch) })]),
  ]);
}

// ---- view: ask (grounded local companion) ---------------------------------
async function viewAsk(params) {
  const sessionScope = params.session || '';
  let scopeTitle = '';
  if (sessionScope) {
    try { scopeTitle = (await API.session(sessionScope)).title || sessionScope.slice(0, 8); } catch { scopeTitle = sessionScope.slice(0, 8); }
  }
  const root = el('div', { class: 'view-pad fade-in ask-view' });
  root.appendChild(el('div', { class: 'page-head' }, [
    el('div', {}, [
      el('h1', { class: 'page-title', html: 'Ask <span class="ask-badge">grounded · local</span>' }),
      el('div', { class: 'page-sub', text: 'Ask your Claude Code history anything. Every answer is computed from your real sessions on this machine — no model calls, nothing uploaded.' }),
    ]),
  ]));

  if (sessionScope) {
    root.appendChild(el('div', { class: 'ask-scope' }, [
      el('span', { text: 'Scoped to ' }),
      el('b', { text: scopeTitle }),
      el('button', { class: 'x', title: 'Clear scope', onclick: () => go('ask') }, ['×']),
    ]));
  }

  const thread = el('div', { class: 'ask-thread' });
  root.appendChild(thread);

  const sugWrap = el('div', { class: 'ask-suggest' });
  root.appendChild(sugWrap);

  const input = el('input', { class: 'input', placeholder: sessionScope ? 'Ask about this session…' : 'Ask about your sessions, files, cost, what to reopen…', autocomplete: 'off' });
  const sendBtn = el('button', { class: 'btn-send', title: 'Ask', html: '✦ Ask' });
  const composer = el('div', { class: 'ask-composer' }, [input, sendBtn]);
  root.appendChild(composer);
  const syncSend = () => { sendBtn.disabled = !input.value.trim(); };
  input.addEventListener('input', syncSend); syncSend();

  function renderSuggestions(list) {
    sugWrap.innerHTML = '';
    if (!list || !list.length) return;
    sugWrap.appendChild(el('span', { class: 'ask-suggest-lbl', text: 'Try' }));
    list.forEach((q) => sugWrap.appendChild(el('button', { class: 'chip toggle', onclick: () => submit(q) }, [q])));
  }
  renderSuggestions(sessionScope
    ? ['What happened in this session?', 'Give me a handoff brief', 'Which files changed?', 'What are the most important tool calls?']
    : ['What should I reopen next?', 'Summarize my most recent session', 'Where did the tokens go?', 'Give me a handoff brief']);

  let busy = false;
  async function submit(q) {
    q = (q || input.value).trim();
    if (!q || busy) return;
    busy = true; input.value = '';
    thread.appendChild(el('div', { class: 'ask-q' }, [el('span', { class: 'ask-q-txt', text: q })]));
    const loading = el('div', { class: 'ask-a loading-a' }, [el('div', { class: 'spinner sm' })]);
    thread.appendChild(loading);
    loading.scrollIntoView({ behavior: 'smooth', block: 'end' });
    try {
      const ans = await API.ask(q, sessionScope || undefined);
      loading.replaceWith(answerCard(ans));
      if (ans.suggestions) renderSuggestions(ans.suggestions);
    } catch (e) {
      loading.replaceWith(el('div', { class: 'ask-a' }, [el('div', { class: 'ask-a-body' }, [el('div', { class: 'empty', text: 'Could not answer: ' + (e.message || e) })])]));
    } finally {
      busy = false;
      thread.lastChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      input.focus();
    }
  }
  sendBtn.addEventListener('click', () => submit());
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });

  view().innerHTML = ''; view().appendChild(root);
  input.focus();
  if (params.q) submit(params.q);
}

function answerCard(ans) {
  const card = el('div', { class: 'ask-a' });
  const body = el('div', { class: 'ask-a-body' });
  body.appendChild(el('div', { class: 'ask-a-title', text: ans.title || 'Answer' }));
  (ans.blocks || []).forEach((b) => { const n = askBlock(b); if (n) body.appendChild(n); });
  body.appendChild(el('div', { class: 'ask-a-foot' }, [
    el('span', { class: 'shield', html: '<svg viewBox="0 0 24 24" width="11" height="11"><path d="M12 2l8 4v6c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-4z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>' }),
    el('span', { text: ans.grounding || 'Computed locally · no model calls.' }),
  ]));
  card.appendChild(body);
  return card;
}

function askBlock(b) {
  if (b.type === 'stats') {
    return el('div', { class: 'ask-stats' }, (b.items || []).map((it) =>
      el('div', { class: 'ask-stat' + (it.tone === 'bad' && it.value ? ' bad' : '') }, [
        el('span', { class: 'v' + (it.accent ? ' accent' : ''), text: String(it.value) }),
        el('span', { class: 'k', text: it.label }),
      ])));
  }
  if (b.type === 'text') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-text', text: b.text || '' }),
    ].filter(Boolean));
  }
  if (b.type === 'list' || b.type === 'steps') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('ul', { class: b.type === 'steps' ? 'ask-steps' : 'ask-list' }, (b.items || []).map((x) => el('li', { text: x }))),
    ].filter(Boolean));
  }
  if (b.type === 'files') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-files' }, (b.items || []).map((f) => el('div', {
        class: 'ask-file' + (f.edited ? ' edited' : ''),
        title: (f.ops || []).join(' + '),
        onclick: () => f.session_id && go('session/' + f.session_id, f.seq != null ? { seq: f.seq } : {}),
      }, [
        el('span', { class: 'op', text: f.edited ? '✎' : '◌' }),
        el('span', { class: 'fname', text: f.name || f.path }),
        f.count ? el('span', { class: 'cnt', text: '×' + f.count }) : null,
        f.errors ? el('span', { class: 'ferr', text: f.errors + ' err' }) : null,
      ].filter(Boolean)))),
    ].filter(Boolean));
  }
  if (b.type === 'decisions') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-decisions' }, (b.items || []).map((d) => el('div', {
        class: 'ask-decision' + (d.session_id ? ' link' : ''),
        onclick: () => d.session_id && go('session/' + d.session_id, d.seq != null ? { seq: d.seq } : {}),
      }, [el('span', { class: 'q', text: '“' }), el('span', { class: 'd-txt', text: d.text }), d.session_id ? el('span', { class: 'jump', text: '↗' }) : null].filter(Boolean)))),
    ].filter(Boolean));
  }
  if (b.type === 'sessions') {
    return el('div', { class: 'ask-text-block' }, [
      b.label ? el('div', { class: 'ask-lbl', text: b.label }) : null,
      el('div', { class: 'ask-sessions' }, (b.items || []).map((s) => el('div', {
        class: 'ask-session', onclick: () => go('session/' + s.session_id, s.seq != null ? { seq: s.seq } : {}),
      }, [
        el('div', { class: 'as-main' }, [
          el('div', { class: 'as-title', text: s.title || 'Untitled' }),
          el('div', { class: 'as-reason', text: s.reason || '' }),
        ]),
        el('div', { class: 'as-side' }, [
          el('span', { class: 'proj-chip', text: s.project_name || '' }),
          s.last_epoch ? el('span', { class: 's-time', text: fmt.rel(s.last_epoch) }) : null,
        ].filter(Boolean)),
      ]))),
    ].filter(Boolean));
  }
  if (b.type === 'compare') {
    return el('div', { class: 'panel', style: 'margin-top:4px' }, [
      el('div', { class: 'cmp-row' }, [el('span', { class: 'k', text: '' }), el('span', { class: 'a', text: (b.a || '').slice(0, 26) }), el('span', { class: 'b', text: (b.b || '').slice(0, 26) }), el('span', {})]),
      ...(b.rows || []).map((r) => {
        const f = r.money ? fmt.cost : fmt.num;
        return el('div', { class: 'cmp-row' }, [
          el('span', { class: 'k', text: r.label }),
          el('span', { class: 'a' + (r.a > r.b ? ' win' : ''), text: f(r.a) }),
          el('span', { class: 'b' + (r.b > r.a ? ' win' : ''), text: f(r.b) }),
          el('span', { class: 'usage-tag', text: r.a === r.b ? '=' : (r.a > r.b ? '◀' : '▶') }),
        ]);
      }),
    ]);
  }
  return null;
}

// ---- command palette ------------------------------------------------------
const CMDK = { open: false, sel: 0, items: [] };
function openCmdk() {
  CMDK.open = true; $('#cmdk').hidden = false; const inp = $('#cmdk-input'); inp.value = ''; inp.focus(); cmdkRender('');
}
function closeCmdk() { CMDK.open = false; $('#cmdk').hidden = true; }
let cmdkTimer;
async function cmdkRender(q) {
  const navCmds = NAV.filter((n) => !q || n.label.toLowerCase().includes(q.toLowerCase())).map((n) => ({ type: 'nav', label: 'Go to ' + n.label, route: n.id, icon: '→' }));
  let results = [];
  if (q && q.length >= 2) {
    try { const r = await API.search(q, 18); results = r.results.map((x) => ({ type: 'result', label: x.title || 'Untitled', snip: x.snip, session: x.session_id, seq: x.seq, project: x.project_name, q, icon: '◷' })); } catch { /* ignore */ }
  }
  CMDK.items = [...navCmds, ...results];
  CMDK.sel = 0;
  const box = $('#cmdk-results'); box.innerHTML = '';
  if (navCmds.length) { box.appendChild(el('div', { class: 'cmdk-group', text: 'Navigate' })); navCmds.forEach((it, i) => box.appendChild(cmdkItem(it, i))); }
  if (results.length) { box.appendChild(el('div', { class: 'cmdk-group', text: 'Sessions' })); results.forEach((it, i) => box.appendChild(cmdkItem(it, navCmds.length + i))); }
  if (q && q.length >= 2) box.appendChild(el('div', { class: 'cmdk-item', onclick: () => { closeCmdk(); go('search', { q }); } }, [el('span', { class: 'ico', text: '⏎' }), el('div', { class: 't' }, [el('div', { class: 'main', html: `See all results for <b>${esc(q)}</b>` })])]));
  if (!CMDK.items.length && (!q || q.length < 2)) box.appendChild(el('div', { class: 'empty', style: 'padding:30px', text: 'Type to search your sessions…' }));
  updateSel();
}
function cmdkItem(it, i) {
  const snip = it.snip ? esc(it.snip).replace(/⟦/g, '<mark>').replace(/⟧/g, '</mark>') : (it.project || '');
  return el('div', { class: 'cmdk-item', 'data-i': i, onclick: () => runCmdk(it) }, [
    el('span', { class: 'ico', text: it.icon }),
    el('div', { class: 't' }, [el('div', { class: 'main', text: it.label }), snip ? el('div', { class: 'snip', html: snip }) : null].filter(Boolean)),
  ]);
}
function updateSel() { $$('#cmdk-results .cmdk-item').forEach((n) => n.classList.toggle('sel', +n.dataset.i === CMDK.sel)); }
function runCmdk(it) {
  closeCmdk();
  if (it.type === 'nav') return go(it.route);
  if (it.type === 'result') {
    const p = {};
    if (it.seq != null) p.seq = it.seq;
    if (it.q) p.q = it.q;
    go('session/' + it.session, p);
  }
}

// ---- reindex --------------------------------------------------------------
async function doReindex() {
  const btn = $('#btn-reindex'); btn.classList.add('spinning');
  try { const s = await API.reindex(); toast(`Synced — +${s.added} new, ${s.updated} updated`); await loadSummary(); router(); }
  catch (e) { toast('Sync failed: ' + e.message, 'err'); }
  finally { btn.classList.remove('spinning'); }
}

async function loadSummary() {
  try { STATE.summary = await API.summary(); renderNav(STATE.summary); renderFootStats(STATE.summary); }
  catch (e) { /* ignore */ }
}

// ---- live updates (Server-Sent Events) ------------------------------------
// Open a persistent EventSource to /api/events. When the server signals that the
// index changed (a reindex — via the hook, `watch`, or Sync), show a dismissible
// toast offering to refresh the current view in place (no full page reload).
let _liveToast = null;
function startLiveUpdates() {
  if (typeof EventSource === 'undefined') return;
  let es;
  try { es = new EventSource('/api/events'); } catch (e) { return; }
  es.addEventListener('message', (ev) => {
    let data = {};
    try { data = JSON.parse(ev.data); } catch (e) { return; }
    if (data.type === 'reindex') showReindexToast();
  });
  es.onerror = () => { /* browser auto-reconnects; nothing to do */ };
}

function showReindexToast() {
  if (_liveToast) return;  // one at a time
  const host = $('#toasts');
  const t = el('div', { class: 'toast live', role: 'status' }, [
    el('span', { class: 'dot' }),
    el('span', { text: 'New sessions available — click to reload.' }),
  ]);
  const dismiss = () => { if (_liveToast === t) _liveToast = null; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); };
  t.addEventListener('click', async () => { await loadSummary(); router(); toast('Refreshed'); dismiss(); });
  host.appendChild(t);
  _liveToast = t;
  setTimeout(dismiss, 8000);  // auto-dismiss after 8s
}

// ---- v0.5.2: health, git, annotations, budget, efficiency, prompts --------

function healthGrade(score) {
  return score >= 90 ? 'A' : score >= 80 ? 'B' : score >= 65 ? 'C' : score >= 50 ? 'D' : 'F';
}

// small coloured A–F badge shown next to a session in the list
function healthDot(score) {
  if (score == null) return null;
  const g = healthGrade(score);
  return el('span', { class: 'health-dot grade-' + g, title: `Health ${score}/100 (${g})`, text: g });
}

// git badge + health breakdown card + a session-level annotation editor
// v0.6.0: GitHub issue/PR references card (read-only external links).
function githubRefsCard(refs) {
  if (!refs || !refs.length) return null;
  const chips = refs.map((r) => {
    const label = r.owner ? `${r.owner}/${r.repo}#${r.number}` : `#${r.number}`;
    const attrs = { class: 'gh-ref ' + (r.kind === 'pr' ? 'pr' : 'issue'), text: (r.kind === 'pr' ? '⇄ ' : '⊙ ') + label };
    if (r.url) { attrs.href = r.url; attrs.target = '_blank'; attrs.rel = 'noopener noreferrer'; attrs.title = r.url; }
    return el(r.url ? 'a' : 'span', attrs);
  });
  return el('div', { class: 'gh-refs-card' }, [
    el('div', { class: 'ghc-k', text: 'GitHub references' }),
    el('div', { class: 'ghc-chips' }, chips),
  ]);
}

// phrases that signal a reference to an earlier session (mirror of cross_ref.py).
const CROSS_REF_RE = /\b(as we did last time|like (?:we did )?last time|in the \w+(?:[ -]\w+)? session|remember when you helped me|remember (?:when|that|how) we|continue from|pick up where|same (?:as|approach as|way as) (?:before|last time)|(?:as|like) (?:before|earlier|previously)|the (?:other|previous|last|earlier) (?:session|time|chat|conversation)|you helped me (?:with|fix|build|refactor|debug))\b/i;

// v0.6.0: cross-session references found in this session's prompts.
function crossRefCard(timeline, id) {
  const hits = [];
  (timeline || []).forEach((m, i) => {
    if (m.role !== 'user' || !m.text) return;
    const mt = m.text.match(CROSS_REF_RE);
    if (mt) hits.push({ seq: i, phrase: mt[0], text: m.text.slice(0, 120) });
  });
  if (!hits.length) return null;
  const rows = hits.slice(0, 5).map((h) => el('button', {
    class: 'xref-row', title: 'Jump to this prompt',
    onclick: () => go('session/' + id, { seq: h.seq }),
  }, [
    el('span', { class: 'xref-phrase', text: '“' + h.phrase + '”' }),
    el('span', { class: 'xref-text', text: h.text }),
  ]));
  return el('div', { class: 'xref-card' }, [
    el('div', { class: 'ghc-k', text: 'Cross-references' }),
    el('div', { class: 'xref-sub', text: 'This session points back at earlier work — open the global cross-reference map for candidates.' }),
    el('div', {}, rows),
  ]);
}

function detailContext(s, id) {
  const wrap = el('div', { class: 'detail-context' });
  if (s.git && s.git.short_sha) {
    const g = s.git;
    wrap.appendChild(el('button', {
      class: 'git-badge', title: 'Click to copy ' + g.sha,
      onclick: () => { try { navigator.clipboard.writeText(g.sha); } catch (e) { /* */ } toast('Copied ' + g.short_sha); },
    }, [el('span', { class: 'gi', text: '🔀' }),
        el('span', { text: (g.branch || 'detached') + ' @ ' + g.short_sha })]));
  }
  if (s.health) {
    const h = s.health, comp = h.components || {};
    const bars = Object.entries({
      'Tool success': comp.tool_success, 'Low errors': comp.error_density,
      'Token output': comp.token_efficiency, 'Completion': comp.completion_signal,
    }).map(([k, v]) => el('div', { class: 'hc-bar' }, [
      el('span', { class: 'hc-k', text: k }),
      el('span', { class: 'hc-track' }, [el('span', { class: 'hc-fill', style: 'width:' + Math.round((v || 0) * 100) + '%' })]),
    ]));
    wrap.appendChild(el('div', { class: 'health-card grade-' + h.grade }, [
      el('div', { class: 'hc-head' }, [
        el('span', { class: 'hc-grade', text: h.grade }),
        el('span', { class: 'hc-score', text: h.score + '/100 · ' + h.label }),
      ]),
      el('div', { class: 'hc-bars' }, bars),
    ]));
  }
  wrap.appendChild(annotationEditor(id));
  return wrap;
}

// auto-saving session-level note (message_idx = -1); survives reindexing
function annotationEditor(id) {
  const ta = el('textarea', { class: 'ann-input', rows: 1,
    placeholder: 'Add a personal note about this session… (saved on blur)' });
  API.annotations(id).then((d) => {
    const note = (d.annotations || []).find((a) => a.message_idx === -1);
    if (note) ta.value = note.note;
  }).catch(() => {});
  ta.addEventListener('blur', async () => {
    try { await API.saveAnnotation(id, { message_idx: -1, note: ta.value }); toast('Note saved'); }
    catch (e) { toast('Save failed', 'err'); }
  });
  return el('div', { class: 'annotate' }, [el('span', { class: 'ann-k', text: '✎ Note' }), ta]);
}

// sticky, dismissible budget-alert banner — shown on load when over threshold
function checkBudget() {
  API.budget().then((b) => {
    if (!b || !b.alert || document.querySelector('.budget-banner')) return;
    const banner = el('div', { class: 'budget-banner' }, [
      el('span', { html: `⚠ <b>Budget alert:</b> $${(b.spent_usd || 0).toFixed(2)} of $${(b.ceiling_usd || 0).toFixed(2)} this ${b.period} (${Math.round(b.percent)}%)` }),
      el('button', { class: 'bb-x', title: 'Dismiss', onclick: (e) => e.target.closest('.budget-banner').remove() }, ['×']),
    ]);
    document.body.appendChild(banner);
  }).catch(() => {});
}

// pure-SVG radial progress arc for the budget widget
function radialArc(percent, { size = 132, stroke = 14 } = {}) {
  const p = Math.max(0, Math.min(100, percent || 0));
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, cx = size / 2;
  const col = p >= 100 ? '#ff5b5b' : p >= 90 ? '#ff8a3d' : p >= 75 ? '#f5c451' : 'var(--accent)';
  const len = (p / 100) * c;
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" class="budget-arc">
    <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="var(--line)" stroke-width="${stroke}"/>
    <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${col}" stroke-width="${stroke}" stroke-linecap="round"
      stroke-dasharray="${len.toFixed(2)} ${(c - len).toFixed(2)}" transform="rotate(-90 ${cx} ${cx})"/>
    <text x="${cx}" y="${cx - 2}" text-anchor="middle" fill="var(--text)" font-size="22" font-weight="700">${Math.round(p)}%</text>
    <text x="${cx}" y="${cx + 16}" text-anchor="middle" fill="var(--text-3)" font-size="10">of budget</text></svg>`;
}

function kpiTile(v, label) {
  return el('div', { class: 'eff-kpi' }, [
    el('div', { class: 'eff-kpi-v', text: v }), el('div', { class: 'eff-kpi-l', text: label }),
  ]);
}

function budgetWidget(bud) {
  bud = bud || {};
  const left = el('div', { class: 'bw-arc', html: bud.has_budget ? radialArc(bud.percent) : '' });
  const form = el('div', { class: 'bw-form' });
  if (bud.has_budget) {
    form.appendChild(el('div', { class: 'bw-line', html: `<b>$${(bud.spent_usd || 0).toFixed(2)}</b> spent of <b>$${(bud.ceiling_usd || 0).toFixed(2)}</b> / ${bud.period}` }));
    form.appendChild(el('div', { class: 'bw-sub', text: `${bud.sessions_this_period} sessions · ${bud.days_remaining} days left in period` }));
  } else {
    form.appendChild(el('div', { class: 'bw-line', text: 'No budget set — track spend against a ceiling.' }));
  }
  const amt = el('input', { class: 'bw-input', type: 'number', min: '0', step: '1', placeholder: 'e.g. 50', value: bud.ceiling_usd || '' });
  const per = el('select', { class: 'bw-input' }, [el('option', { value: 'monthly', text: 'monthly' }), el('option', { value: 'weekly', text: 'weekly' })]);
  if (bud.period === 'weekly') per.value = 'weekly';
  const setb = el('button', { class: 'btn-primary', onclick: async () => { await API.setBudget({ ceiling_usd: parseFloat(amt.value) || 0, period: per.value }); toast('Budget set'); viewEfficiency(); } }, ['Set budget']);
  const clr = el('button', { class: 'btn-ghost', onclick: async () => { await API.clearBudget(); toast('Budget cleared'); viewEfficiency(); } }, ['Clear']);
  form.appendChild(el('div', { class: 'bw-controls' }, [amt, per, setb, clr]));
  return el('div', { class: 'budget-widget card' }, [left, form]);
}

async function viewEfficiency() {
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('div', { class: 'page-title', text: 'Efficiency' }),
    el('div', { class: 'page-sub', text: 'How effective your sessions are — output per dollar, tool-success rate, and spend vs budget. Click a project to generate its CLAUDE.md.' }),
  ])]));
  view().innerHTML = ''; view().appendChild(root);
  let eff, bud;
  try { [eff, bud] = await Promise.all([API.efficiency(), API.budget()]); }
  catch (e) { root.appendChild(el('div', { class: 'empty', text: 'Could not load efficiency data.' })); return; }
  root.appendChild(budgetWidget(bud));
  const o = eff.overall || {};
  root.appendChild(el('div', { class: 'eff-kpis' }, [
    kpiTile(fmt.num(Math.round(o.output_tokens_per_dollar || 0)), 'output tokens / $'),
    kpiTile(Math.round((o.tool_success_rate || 0) * 100) + '%', 'tool success'),
    kpiTile((o.avg_messages_per_session || 0).toFixed(1), 'msgs / session'),
    kpiTile(fmt.dur(o.median_session_duration_s || 0), 'median duration'),
  ]));
  const projs = eff.by_project || [];
  if (projs.length) {
    const max = Math.max(1, ...projs.map((p) => p.output_per_dollar || 0));
    root.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-h', text: 'Projects by efficiency' }),
      el('div', { class: 'eff-bars' }, projs.slice(0, 12).map((p) => el('div', {
        class: 'eff-row', title: 'Generate CLAUDE.md for ' + p.project,
        onclick: () => openClaudeMdModal(p.project),
      }, [
        el('span', { class: 'eff-name', text: p.project }),
        el('span', { class: 'eff-track' }, [el('span', { class: 'eff-fill', style: 'width:' + Math.round(((p.output_per_dollar || 0) / max) * 100) + '%' })]),
        el('span', { class: 'eff-val', text: Math.round((p.tool_success_rate || 0) * 100) + '% ok' }),
      ]))),
    ]));
  }
  const trend = eff.trend || [];
  if (trend.length) {
    root.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-h', text: 'Output per dollar — last ' + trend.length + ' weeks' }),
      el('div', { html: spark(trend.map((t) => t.output_per_dollar)) }),
    ]));
  }
}

async function viewPrompts() {
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [
    el('div', {}, [
      el('div', { class: 'page-title', text: 'Prompt Library' }),
      el('div', { class: 'page-sub', text: 'Your reusable prompts — auto-extracted from history, plus your own. Star, copy, search.' }),
    ]),
    el('div', { class: 'page-actions' }, [
      el('button', { class: 'btn-primary', onclick: async () => { const r = await API.extractPrompts(); toast('Found ' + r.extracted + ' reusable prompt(s)'); load(); } }, ['✦ Extract from history']),
    ]),
  ]));
  const search = el('input', { class: 'prompt-search', type: 'text', placeholder: 'Search prompts…' });
  const starOnly = el('button', { class: 'chip', onclick: () => { starOnly.classList.toggle('on'); load(); } }, ['★ Starred']);
  const addBox = el('input', { class: 'prompt-add', type: 'text', placeholder: 'Add your own prompt…' });
  const addBtn = el('button', { class: 'btn-ghost', onclick: async () => { if (!addBox.value.trim()) return; await API.addPrompt({ text: addBox.value.trim(), source: 'manual' }); addBox.value = ''; toast('Added'); load(); } }, ['+ Add']);
  root.appendChild(el('div', { class: 'prompt-tools' }, [search, starOnly, addBox, addBtn]));
  const grid = el('div', { class: 'prompt-grid' });
  root.appendChild(grid);
  view().innerHTML = ''; view().appendChild(root);
  let timer;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  async function load() {
    grid.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    const q = {};
    if (search.value.trim()) q.q = search.value.trim();
    if (starOnly.classList.contains('on')) q.starred = '1';
    let data;
    try { data = await API.prompts(q); } catch (e) { grid.innerHTML = ''; return; }
    grid.innerHTML = '';
    if (!(data.prompts || []).length) {
      grid.appendChild(el('div', { class: 'empty', text: 'No prompts yet — extract from history or add one.' }));
      return;
    }
    data.prompts.forEach((p) => grid.appendChild(promptCard(p, load)));
  }
  await load();
}

function promptCard(p, reload) {
  const star = el('button', { class: 'pc-star' + (p.starred ? ' on' : ''), title: 'Star',
    onclick: async (e) => { e.stopPropagation(); await API.addPrompt({ id: p.id, text: p.text, source: p.source, frequency: p.frequency, starred: !p.starred }); reload(); } }, [p.starred ? '★' : '☆']);
  return el('div', { class: 'prompt-card' }, [
    el('div', { class: 'pc-head' }, [star, el('span', { class: 'pc-src', text: p.source }),
      p.frequency > 1 ? el('span', { class: 'pc-freq', text: '×' + p.frequency }) : null].filter(Boolean)),
    el('div', { class: 'pc-text', text: p.text }),
    el('div', { class: 'pc-actions' }, [
      el('button', { class: 'btn-ghost', onclick: () => { try { navigator.clipboard.writeText(p.text); } catch (e) { /* */ } toast('Copied'); } }, ['⧉ Copy']),
      el('button', { class: 'btn-ghost', onclick: async () => { await API.delPrompt(p.id); toast('Deleted'); reload(); } }, ['Delete']),
    ]),
  ]);
}

// modal showing a generated CLAUDE.md with copy-to-clipboard
async function openClaudeMdModal(project) {
  let data;
  try { data = await API.claudeMd(project); } catch (e) { toast('Failed to generate', 'err'); return; }
  const overlay = el('div', { class: 'cs-modal-backdrop', onclick: (e) => { if (e.target === overlay) overlay.remove(); } });
  overlay.appendChild(el('div', { class: 'cs-modal' }, [
    el('div', { class: 'cs-modal-head' }, [
      el('span', { text: '✦ CLAUDE.md · ' + project }),
      el('button', { class: 'cs-modal-x', onclick: () => overlay.remove() }, ['×']),
    ]),
    el('pre', { class: 'cs-modal-body', text: data.markdown }),
    el('div', { class: 'cs-modal-foot' }, [
      el('button', { class: 'btn-primary', onclick: () => { try { navigator.clipboard.writeText(data.markdown); } catch (e) { /* */ } toast('Copied CLAUDE.md'); } }, ['⧉ Copy to clipboard']),
    ]),
  ]));
  document.body.appendChild(overlay);
}

// ---- view: patterns (workflows / debug loops / time-of-day / momentum) ----
// auto-generated mini-flowchart (pure SVG) for a tool sequence.
function workflowFlowchart(steps) {
  const boxW = 76, boxH = 30, gap = 22, h = 50;
  const w = steps.length * boxW + (steps.length - 1) * gap + 8;
  let x = 4, svg = '';
  steps.forEach((s, i) => {
    svg += `<rect x="${x}" y="10" width="${boxW}" height="${boxH}" rx="7" fill="var(--surface-2)" stroke="var(--accent)" stroke-width="1.2"/>`;
    svg += `<text x="${x + boxW / 2}" y="29" text-anchor="middle" fill="var(--text)" font-size="11" font-family="var(--mono)">${esc(s)}</text>`;
    if (i < steps.length - 1) {
      const ax = x + boxW, ax2 = ax + gap;
      svg += `<line x1="${ax}" y1="25" x2="${ax2 - 4}" y2="25" stroke="var(--accent)" stroke-width="1.4"/>`;
      svg += `<path d="M${ax2 - 4} 25 l-6 -3 v6 z" fill="var(--accent)"/>`;
    }
    x += boxW + gap;
  });
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" class="wf-svg">${svg}</svg>`;
}

async function viewPatterns() {
  view().innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  const [wf, dl, mo] = await Promise.all([
    API.patternWorkflows().catch(() => ({ workflows: [] })),
    API.patternDebugLoops().catch(() => ({ debug_loops: [], time_of_day: [] })),
    API.patternMomentum().catch(() => ({ momentum: [] })),
  ]);
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Patterns' }),
    el('div', { class: 'page-sub', text: 'How you actually work — recurring tool workflows, debugging loops, peak hours and project momentum.' }),
  ])]));

  // top workflows as mini-flowcharts
  const wfSec = el('div', { class: 'pat-section' }, [el('h2', { class: 'pat-h', text: '🔁 Top recurring workflows' })]);
  if ((wf.workflows || []).length) {
    wf.workflows.forEach((w) => wfSec.appendChild(el('div', { class: 'wf-row' }, [
      el('div', { class: 'wf-chart', html: workflowFlowchart(w.steps) }),
      el('div', { class: 'wf-meta', text: `×${w.count} · ${w.sessions.length} session${w.sessions.length > 1 ? 's' : ''}` }),
    ])));
  } else { wfSec.appendChild(el('div', { class: 'empty', text: 'No recurring workflows yet.' })); }
  root.appendChild(wfSec);

  // debug loops + time of day, side by side
  const dlSec = el('div', { class: 'pat-section' }, [el('h2', { class: 'pat-h', text: '🌀 Debugging loops' })]);
  if ((dl.debug_loops || []).length) {
    dl.debug_loops.forEach((d) => dlSec.appendChild(el('div', { class: 'loop-row' }, [
      el('span', { class: 'loop-tool', text: d.tool }),
      el('span', { class: 'loop-len', text: `×${d.length} in a row` }),
      el('button', { class: 'btn-ghost', onclick: () => go('session/' + d.session_id) }, ['open']),
    ])));
  } else { dlSec.appendChild(el('div', { class: 'empty', text: 'No tight debugging loops detected.' })); }
  root.appendChild(dlSec);

  const todSec = el('div', { class: 'pat-section' }, [el('h2', { class: 'pat-h', text: '⏰ Most productive hours' })]);
  (dl.time_of_day || []).forEach((t, i) => todSec.appendChild(el('div', { class: 'tod-row' }, [
    el('span', { class: 'tod-rank', text: '#' + (i + 1) }),
    el('span', { class: 'tod-emoji', text: t.emoji }),
    el('span', { class: 'tod-label', text: t.label }),
    el('span', { class: 'tod-count', text: t.sessions + ' sessions' }),
  ])));
  if (!(dl.time_of_day || []).length) todSec.appendChild(el('div', { class: 'empty', text: 'Not enough data yet.' }));
  root.appendChild(todSec);

  const moSec = el('div', { class: 'pat-section' }, [el('h2', { class: 'pat-h', text: '📈 Project momentum (last 4 weeks)' })]);
  (mo.momentum || []).slice(0, 10).forEach((m) => {
    const badge = { rising: '↑ rising', stalling: '↓ stalling', steady: '→ steady' }[m.momentum];
    moSec.appendChild(el('div', { class: 'mom-row ' + m.momentum }, [
      el('span', { class: 'mom-name', text: m.project_name }),
      el('span', { class: 'mom-counts', text: `${m.older} → ${m.recent}` }),
      el('span', { class: 'mom-badge ' + m.momentum, text: badge }),
    ]));
  });
  if (!(mo.momentum || []).length) moSec.appendChild(el('div', { class: 'empty', text: 'No recent project activity.' }));
  root.appendChild(moSec);

  view().innerHTML = ''; view().appendChild(root);
}

// ---- view: developer dashboard (hidden — ?dev=1 or Shift+D) ---------------
// Runs `python -m claudestudio --selftest` server-side and streams each line
// over SSE, rendering green ✓ / red ✗ rows. Development-only UX; invisible to
// users who don't know the shortcut. No impact on the index.
async function viewDev() {
  const root = el('div', { class: 'view-pad fade-in' });
  root.appendChild(el('div', { class: 'page-head' }, [el('div', {}, [
    el('h1', { class: 'page-title', text: 'Developer' }),
    el('div', { class: 'page-sub', text: 'Run the built-in self-test and watch it stream. Contributors + demo mode only.' }),
  ])]));
  const status = el('div', { class: 'dev-status', role: 'status', 'aria-live': 'polite', text: 'Idle' });
  const out = el('div', { class: 'dev-selftest-out', role: 'log' });
  const runBtn = el('button', { class: 'btn-primary', text: '▶ Run self-test' });
  root.appendChild(el('div', { class: 'dev-bar' }, [runBtn, status]));
  root.appendChild(out);
  view().innerHTML = ''; view().appendChild(root);

  let es = null;
  runBtn.addEventListener('click', () => {
    if (es) { es.close(); es = null; }
    out.innerHTML = ''; status.textContent = 'Running…'; runBtn.disabled = true;
    es = new EventSource('/api/dev/selftest');
    es.onmessage = (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch { return; }
      if (d.type === 'line') {
        const t = d.text || '';
        const cls = /FAIL|✗/.test(t) ? 'dev-line bad' : (/pass|ALLPASS|✓/.test(t) ? 'dev-line good' : 'dev-line');
        out.appendChild(el('div', { class: cls, text: t }));
        out.scrollTop = out.scrollHeight;
      } else if (d.type === 'done') {
        status.textContent = d.code === 0 ? '✓ ALLPASS' : '✗ FAILED (exit ' + d.code + ')';
        status.className = 'dev-status ' + (d.code === 0 ? 'good' : 'bad');
        runBtn.disabled = false; es.close(); es = null;
      } else if (d.type === 'error') {
        out.appendChild(el('div', { class: 'dev-line bad', text: 'launch error: ' + d.text }));
        runBtn.disabled = false; es.close(); es = null;
      }
    };
    es.onerror = () => { status.textContent = 'stream ended'; runBtn.disabled = false; if (es) { es.close(); es = null; } };
  });
}

// register the service worker so the static shell loads instantly (offline state
// with a retry when the Python server isn't up). Best-effort — never blocks boot.
function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('sw.js').catch(() => { /* offline shell is optional */ });
}

// ---- boot -----------------------------------------------------------------
window.__cs = { go, ss: () => sessionsState, claudeMd: openClaudeMdModal };
window.addEventListener('hashchange', router);
// close the bookmark popover on any outside click
document.addEventListener('click', (e) => {
  if (_bmPop && !_bmPop.contains(e.target) && !(e.target.closest && e.target.closest('.bm-btn'))) closeBookmarkPopover();
});
document.addEventListener('keydown', (e) => {
  // Shift+D opens the hidden Developer view (self-test dashboard).
  if (e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey && (e.key === 'D' || e.key === 'd')) {
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && !e.target.isContentEditable) { e.preventDefault(); go('dev'); return; }
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); CMDK.open ? closeCmdk() : openCmdk(); return; }
  if (!CMDK.open) return;
  if (e.key === 'Escape') closeCmdk();
  else if (e.key === 'ArrowDown') { e.preventDefault(); CMDK.sel = Math.min(CMDK.items.length - 1, CMDK.sel + 1); updateSel(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); CMDK.sel = Math.max(0, CMDK.sel - 1); updateSel(); }
  else if (e.key === 'Enter') { const it = CMDK.items[CMDK.sel]; if (it) runCmdk(it); else { const q = $('#cmdk-input').value; if (q) { closeCmdk(); go('search', { q }); } } }
});

(async function boot() {
  $('#cmdk-trigger').addEventListener('click', openCmdk);
  $('#btn-reindex').addEventListener('click', doReindex);
  $('#cmdk').addEventListener('click', (e) => { if (e.target.id === 'cmdk') closeCmdk(); });
  $('#cmdk-input').addEventListener('input', (e) => { clearTimeout(cmdkTimer); cmdkTimer = setTimeout(() => cmdkRender(e.target.value.trim()), 160); });
  renderNav(null);
  registerServiceWorker();
  await loadSummary();
  if (!location.hash) {
    // ?dev=1 deep-links straight into the hidden Developer view
    location.hash = new URLSearchParams(location.search).get('dev') === '1' ? '#/dev' : '#/sessions';
  }
  router();
  startLiveUpdates();
  checkBudget();
})();
